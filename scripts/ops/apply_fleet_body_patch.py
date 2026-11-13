#!/usr/bin/env python3
"""Apply a fleet-style body-patch SQL to the local training library.

WHY THIS EXISTS
---------------
The 02 node lost three detail bodies while it was being stripped: the ledger
kept the list title, the detail request never produced a row, and the posts
reached `data/collector.db` with `content IS NULL`. The fix was written as SQL
against the *fleet ledger* (`fleet/merge.sqlite3`, table `versions`), which is
where the evidence lives. That ledger is not on this machine -- what is here is
the training library, and its projection of a post is `posts`.

So this script does not blindly run the SQL. It parses it, proves that each body
belongs to the post the SQL names, and then writes only `posts.content`.

HOW THE TWO SIDES ARE TIED TOGETHER
-----------------------------------
* Each `UPDATE versions ... json_set(payload, '$.post.content', '<body>', ...)`
  carries the body and its `body_sha256` (plain sha256 of the UTF-8 body, which
  is what the control plane emits -- NOT the JSON-canonical hash the local
  `federation.py` uses for `versions.body_sha256`). A mismatch aborts.
* The ledger addresses rows by `(instance_id, seq)`, so the UPDATEs do not name
  a post at all; only the trailing
  `SELECT ... WHERE post_id IN (...)` does. That list is in the same order as
  the UPDATEs, and this script *verifies* that order instead of trusting it:
  a guba list title is the body's first 40 characters after the leading
  `$公司(代码)$` tag and whitespace are collapsed, so for every pair the title
  must be a prefix of the body, and no other post in the list may match that
  body equally well.
* Only rows whose `content IS NULL` are written. An existing body is never
  replaced without `--allow-overwrite` (same rule `training_import.py` uses for
  body conflicts: keep what is already there).

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
* It does not touch the fleet ledger, `backfill_coverage`, the skip ledger, or
  any evidence table. A filled body makes the post stop being an enrich
  candidate on its own (`content IS NULL` is the candidate predicate).
* It does not invent text: a body with a quote character, a hash mismatch, a
  missing post, or an ambiguous title bridge stops the whole run before the
  first write.

Usage:
  scripts/ops/apply_fleet_body_patch.py --sql ~/Downloads/backfill-details-300604.sql --dry-run
  scripts/ops/apply_fleet_body_patch.py --sql ~/Downloads/backfill-details-300604.sql
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from myresearcher_collector.simple_store import SimplePostStore  # noqa: E402

SOURCE = "eastmoney_guba"
MIN_TITLE_CHARS = 8          # shorter titles ("买", a bare tag) match everything
TITLE_CHARS = 40             # the list title is the body's first 40 chars

_UPDATE_RE = re.compile(
    r"UPDATE\s+versions\s+SET\s+payload\s*=\s*json_set\(\s*payload\s*,\s*"
    r"'\$\.post\.content'\s*,\s*'(?P<body>.*?)'\s*,\s*"
    r"'\$\.post\.body_complete'\s*,\s*1\s*\)[^;]*?"
    r"body_sha256\s*=\s*'(?P<sha>[0-9a-f]{64})'",
    re.IGNORECASE | re.DOTALL,
)
_SELECT_RE = re.compile(r"SELECT\b.*?WHERE\s+post_id\s+IN\s*\((?P<ids>[^)]*)\)", re.IGNORECASE | re.DOTALL)
_LEADING_TAG_RE = re.compile(r"^\$[^$]{1,40}\$")


def _norm(text: str) -> str:
    """Collapse whitespace the way the list-title builder does."""
    return re.sub(r"\s+", "", text or "")


def _body_key(body: str) -> str:
    """Body without its leading `$公司(代码)$` tag, whitespace collapsed."""
    return _norm(_LEADING_TAG_RE.sub("", body.strip()))


def parse_patch(path: Path) -> list[dict]:
    sql = path.read_text(encoding="utf-8")
    updates = [{"body": m.group("body"), "sha256": m.group("sha").lower()} for m in _UPDATE_RE.finditer(sql)]
    select = _SELECT_RE.search(sql)
    if not select:
        raise SystemExit("REFUSED reason=no_post_id_list 说明：SQL 里没有 WHERE post_id IN (...) 校验语句，无法把 seq 映射到帖子")
    post_ids = re.findall(r"'([^']+)'", select.group("ids"))
    if not updates:
        raise SystemExit("REFUSED reason=no_update_statements")
    if len(updates) != len(post_ids):
        raise SystemExit(f"REFUSED reason=count_mismatch updates={len(updates)} post_ids={len(post_ids)}")
    for item, post_id in zip(updates, post_ids):
        body = item["body"]
        if "'" in body:
            raise SystemExit(f"REFUSED reason=quote_in_body post_id={post_id} 说明：解析器不做 '' 反转义，避免写进半条正文")
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if digest != item["sha256"]:
            raise SystemExit(f"REFUSED reason=sha256_mismatch post_id={post_id} sql={item['sha256']} computed={digest}")
        item["post_id"] = post_id
        item["length"] = len(body)
    return updates


def _titles(conn: sqlite3.Connection, post_ids: list[str]) -> dict[str, dict]:
    rows = {}
    for post_id in post_ids:
        row = conn.execute(
            "SELECT source,stock_code,title,content,published_at FROM posts WHERE source=? AND source_item_id=?",
            (SOURCE, post_id),
        ).fetchone()
        if row is None:
            raise SystemExit(f"REFUSED reason=post_missing post_id={post_id} 说明：训练库里没有这条帖子")
        rows[post_id] = {"source": row[0], "stock_code": row[1], "title": row[2],
                         "content": row[3], "published_at": row[4]}
    return rows


def verify_bridge(items: list[dict], titles: dict[str, dict]) -> None:
    """Prove the positional mapping instead of trusting it.

    Each body must begin with *its* post's list title, and must not begin with
    any other selected post's title just as convincingly. One strong match per
    body and per post is the only accepted shape.
    """
    scores: dict[str, dict[str, int]] = {}
    for item in items:
        key = _body_key(item["body"])
        per_post = {}
        for post_id, meta in titles.items():
            title = _norm(meta["title"])
            per_post[post_id] = len(title) if len(title) >= MIN_TITLE_CHARS and key.startswith(title) else -1
        scores[item["post_id"]] = per_post
        best = max(per_post.values())
        if per_post[item["post_id"]] < 0:
            raise SystemExit(
                f"REFUSED reason=title_bridge_failed post_id={item['post_id']} "
                f"说明：正文开头不是该帖标题（库内标题={titles[item['post_id']]['title']!r}）")
        rivals = {pid: s for pid, s in per_post.items() if pid != item["post_id"] and s >= per_post[item["post_id"]]}
        if rivals:
            raise SystemExit(
                f"REFUSED reason=title_bridge_ambiguous post_id={item['post_id']} rivals={sorted(rivals)} "
                f"说明：同一段正文同时像别的帖子，按 seq 顺序映射不可靠")
        if best < 0:
            raise SystemExit(f"REFUSED reason=no_title_match post_id={item['post_id']}")
    for item in items:
        key = _body_key(item["body"])
        title = _norm(titles[item["post_id"]]["title"])
        print(f"BRIDGE_OK post_id={item['post_id']} title_prefix={len(title)} body_chars={len(key)} "
              f"stock={titles[item['post_id']]['stock_code']} title={title[:16]}…")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sql", required=True, help="fleet body-patch SQL (UPDATE versions ... SELECT post_id IN ...)")
    parser.add_argument("--db", default=str(REPO / "data" / "collector.db"))
    parser.add_argument("--dry-run", action="store_true", help="validate and print, write nothing")
    parser.add_argument("--allow-overwrite", action="store_true", help="replace a non-empty content (default: refuse)")
    parser.add_argument("--audit-log", default=str(REPO / "runtime" / "logs" / "manual-body-patches.jsonl"))
    args = parser.parse_args(argv)

    sql_path = Path(args.sql).expanduser()
    if not sql_path.is_file():
        raise SystemExit(f"REFUSED reason=sql_missing path={sql_path}")
    db_path = Path(args.db)
    if not db_path.is_file():
        raise SystemExit(f"REFUSED reason=db_missing path={db_path}")

    items = parse_patch(sql_path)
    print(f"SQL_PARSED path={sql_path} updates={len(items)} sha256=verified")

    ro = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        titles = _titles(ro, [item["post_id"] for item in items])
    finally:
        ro.close()

    verify_bridge(items, titles)

    writable = [item for item in items if titles[item["post_id"]]["content"] is None]
    present = [item for item in items if titles[item["post_id"]]["content"] is not None]
    for item in present:
        current = titles[item["post_id"]]["content"]
        if not args.allow_overwrite:
            print(f"PATCH_SKIP post_id={item['post_id']} reason=content_present current_chars={len(current)}")
        else:
            print(f"PATCH_REPLACE post_id={item['post_id']} current_chars={len(current)}")
    length_checks = [
        ("title_is_body_prefix", lambda it: _body_key(it["body"]).startswith(_norm(titles[it["post_id"]]["title"]))),
        ("title_at_most_40", lambda it: len(_norm(titles[it["post_id"]]["title"])) <= TITLE_CHARS),
    ]
    for name, check in length_checks:
        bad = [it["post_id"] for it in items if not check(it)]
        if bad:
            raise SystemExit(f"REFUSED reason={name} post_ids={bad}")

    if args.dry_run:
        for item in writable:
            print(f"DRY_RUN post_id={item['post_id']} chars={item['length']} sha256={item['sha256'][:12]}")
        print(f"DRY_RUN_TOTAL to_write={len(writable)} skipped={len(present)}")
        return 0
    if not writable:
        print(f"PATCH_DONE written=0 skipped={len(present)}")
        return 0

    store = SimplePostStore(db_path)
    written = []
    try:
        with store.transaction():
            for item in writable:
                ok = store.update_content(SOURCE, item["post_id"], item["body"])
                if not ok:
                    raise SystemExit(f"REFUSED reason=update_affected_zero post_id={item['post_id']}")
                written.append(item)
    finally:
        store.close()

    # Read back through a fresh connection: the digest must survive the round trip.
    check = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    audit = []
    try:
        for item in written:
            row = check.execute(
                "SELECT content,updated_at FROM posts WHERE source=? AND source_item_id=?",
                (SOURCE, item["post_id"]),
            ).fetchone()
            digest = hashlib.sha256(row[0].encode("utf-8")).hexdigest()
            if digest != item["sha256"]:
                print(f"VERIFY_FAILED post_id={item['post_id']} stored={digest[:12]} expected={item['sha256'][:12]}")
                return 2
            print(f"PATCH_OK post_id={item['post_id']} chars={len(row[0])} sha256={digest[:12]} updated_at={row[1]}")
            audit.append({"at": stamp, "post_id": item["post_id"], "chars": len(row[0]),
                          "sha256": digest, "sql": str(sql_path), "db": str(db_path)})
    finally:
        check.close()

    log_path = Path(args.audit_log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        for entry in audit:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"PATCH_DONE written={len(written)} skipped={len(present)} audit={log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
