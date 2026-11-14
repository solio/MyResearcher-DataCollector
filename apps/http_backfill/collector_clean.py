"""Strip collected data while keeping the control plane intact.

Collection data grows without bound: posts, observations, requests, raw bodies and the immutable
export journal. After the operator exports everything, this module moves that data out and leaves
a small database that still knows the job, its tasks, coverage and resume points.

Contract:
  * control tables are copied row for row and compared by sha256 before anything is deleted;
  * data tables are dropped, not filtered, so the new file starts at a few hundred KB;
  * the old file is kept under tmp/clean-<stamp>/ until `cleanup`;
  * the swap happens inside the running server: pause, close, replace, reopen.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

STAGE_ORDER = ("running", "paused", "detached", "verified", "cleaned")

# Everything the collector needs to resume. Copied verbatim.
CONTROL_TABLES = (
    "jobs", "job_revisions", "job_lifecycle", "tasks", "coverage", "frontiers",
    "stock_runtime", "recoveries", "run_segments", "meta", "events",
    "backfill_page_anchors", "backfill_resume", "detail_enrichment_skips", "backfill_coverage",
)
# Everything that is either output or evidence. Emptied.
DATA_TABLES = (
    "posts", "http_post_state", "compatible_posts", "observations",
    "page_observations", "requests", "export_journal", "associations",
)
# The event log is an audit trail, not a history archive: the last few thousand rows answer
# "what happened recently" and everything older is noise nobody reads.
EVENTS_KEEP = 2000
# Watermarks describe the old file's extent; the new file must not inherit them.
RESET_META = ("storage_sync_rowid", "clean_shutdown", "storage_sync_at")


def _connect(path, readonly=False):
    if readonly:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _retained_rows(db, table):
    """Rows the new database should inherit. Only `events` is trimmed."""
    if table == "events":
        return db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (EVENTS_KEEP,)).fetchall()[::-1]
    return db.execute(f"SELECT * FROM {table}").fetchall()


def _digest(db, table):
    """Stable fingerprint of the rows we intend to keep, not of the whole table."""
    columns = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
    if not columns:
        return None
    rows = _retained_rows(db, table)
    payload = json.dumps([{c: r[c] for c in columns} for r in rows], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest(), len(rows)


# Keys the strip deletes on purpose: RESET_META watermarks, plus the evidence that points at
# posts which no longer exist. Their absence in the new database is the intended outcome.
_STRIP_DROPPED_KEYS = set(RESET_META) | {"block_evidence", "probe", "probe_pending"}


def _data_precedes_strip(root, marker_at):
    """Live rows older than the strip mean the OLD data came back, not new collection.

    `inconsistent` used to fire as soon as any row existed, which made every strip a
    dead end: the node resumes collecting within minutes, `posts` gets its first new
    row, and from then on the console offers no action at all -- the copy under
    tmp/clean-<stamp>/ (600 MB on the real instance) could never be reclaimed.
    """
    if not isinstance(marker_at, (int, float)):
        return True  # 没有时间戳就无法排除，按最坏情况处理
    try:
        with _connect(Path(root) / "collector.db", readonly=True) as db:
            oldest = db.execute("SELECT MIN(created_at) FROM posts").fetchone()[0]
    except sqlite3.Error:
        return True
    if not oldest:
        return False
    try:
        created = datetime.fromisoformat(str(oldest).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return True
    return created < float(marker_at)


def control_deltas(old_path, new_path):
    """Control data the strip lost, ignoring everything that keeps moving afterwards.

    Comparing the live database against a digest taken at strip time can never pass: the
    console starts writing the moment the swap lands, so `meta` gains runtime keys
    (storage_sync_at, clean_shutdown), `events` grows and `coverage`/`run_segments` advance.
    On 2026-11-13 that made 剥离校验 fail forever with "meta before=38 after=40", which is
    what a healthy strip looks like.

    The invariant that matters is "nothing disappeared". Row counts cannot grow back on their
    own, and the strip itself already proved the copy byte-for-byte ("控制表逐行复制一致")
    before it swapped -- so a post-hoc check only has to catch loss:

    * meta: every key still there, except the ones the strip drops on purpose;
    * events: the trimmed tail (last EVENTS_KEEP by id) is still present;
    * tasks: every task id survives (pending detail tasks may be "cancelled" now);
    * everything else: no fewer rows than the original.

    Values are deliberately not compared: after the strip the engine owns these rows again.
    """
    diffs = []
    with _connect(old_path, readonly=True) as old, _connect(new_path, readonly=True) as new:
        old_tables, new_tables = _tables(old), _tables(new)
        for table in CONTROL_TABLES:
            if table not in old_tables or table not in new_tables:
                diffs.append(f"{table}: 新库缺这张表")
                continue
            if table == "meta":
                before = {r["key"] for r in old.execute("SELECT key FROM meta")}
                after = {r["key"] for r in new.execute("SELECT key FROM meta")}
                lost = sorted(before - after - _STRIP_DROPPED_KEYS)
                if lost:
                    diffs.append(f"丢键 {lost[:5]}")
            elif table == "events":
                keep = {r["id"] for r in old.execute(
                    "SELECT id FROM events ORDER BY id DESC LIMIT ?", (EVENTS_KEEP,))}
                live = {r["id"] for r in new.execute("SELECT id FROM events")}
                missing = sorted(keep - live)
                if missing:
                    diffs.append(f"丢 {len(missing)} 条（如 {missing[:5]}）")
            elif table == "tasks":
                before = {r["id"] for r in old.execute("SELECT id FROM tasks")}
                after = {r["id"] for r in new.execute("SELECT id FROM tasks")}
                lost = sorted(before - after)
                if lost:
                    diffs.append(f"丢 {len(lost)} 条（如 {lost[:5]}）")
            else:
                before = old.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                after = new.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                if after < before:
                    diffs.append(f"{after}/{before} 行")
    return diffs


def inspect(root, full=False):
    """What is here right now, and is the split still exhaustive?

    `full=False` answers "is there any row" with EXISTS: the console polls this through a 3 s
    proxy timeout, and count(*) over a gigabyte does not fit in it. Only the strip path needs
    exact numbers.
    """
    root = Path(root)
    db_path = root / "collector.db"
    out = {"db": str(db_path), "exists": db_path.is_file(), "stage": "running"}
    if not db_path.is_file():
        return out
    with _connect(db_path, readonly=True) as db:
        tables = _tables(db)
        unknown = sorted(tables - set(CONTROL_TABLES) - set(DATA_TABLES))
        out["unknown_tables"] = unknown
        out["counts"] = {}
        for name in sorted(tables & (set(CONTROL_TABLES) | set(DATA_TABLES))):
            try:
                if full:
                    out["counts"][name] = db.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                else:
                    out["counts"][name] = 1 if db.execute(f"SELECT 1 FROM {name} LIMIT 1").fetchone() else 0
            except sqlite3.Error:
                out["counts"][name] = None
        try:
            out["journal_max"] = db.execute("SELECT COALESCE(MAX(seq),0) FROM export_journal").fetchone()[0]
        except sqlite3.Error:
            out["journal_max"] = 0
        out["inflight"] = db.execute("SELECT count(*) FROM tasks WHERE status='inflight'").fetchone()[0]
        out["pending_detail"] = db.execute(
            "SELECT count(*) FROM tasks WHERE status='pending' AND kind='detail'").fetchone()[0]
    out["bytes"] = db_path.stat().st_size
    # Walking thousands of raw files takes seconds; only the strip report needs that number.
    if full and (root / "raw").is_dir():
        out["raw_bytes"] = sum(p.stat().st_size for p in (root / "raw").rglob("*") if p.is_file())
    return out


def _copy_control(old_db, new_db):
    """Recreate the schema from the old file, then copy control tables verbatim.

    Order matters: views (http_posts is a view, not a table) can only be created after the tables
    they select from, and a silently skipped view breaks detail collection afterwards.
    """
    copied = {}
    errors = []
    for kind in ("table", "view", "index"):
        for (name, sql) in old_db.execute(
                "SELECT name, sql FROM sqlite_master WHERE type=? AND sql IS NOT NULL ORDER BY name", (kind,)):
            if sql.startswith("CREATE TRIGGER"):
                continue
            try:
                new_db.execute(sql)
            except sqlite3.Error as exc:
                errors.append(f"{kind} {name}: {exc}")
    for table in CONTROL_TABLES:
        if table not in _tables(old_db):
            continue
        rows = _retained_rows(old_db, table)
        columns = [r[1] for r in old_db.execute(f"PRAGMA table_info({table})")]
        if rows:
            new_db.executemany(
                f"INSERT INTO {table}(" + ",".join(columns) + ") VALUES(" + ",".join("?" * len(columns)) + ")",
                [tuple(r) for r in rows])
        copied[table] = len(rows)
    for (sql,) in old_db.execute("SELECT sql FROM sqlite_master WHERE sql LIKE 'CREATE TRIGGER%'"):
        new_db.execute(sql)
    return copied, errors


def export_all(root, stamp=""):
    """Archive both sides of the data: the post projection and the immutable export journal.

    The record it writes is what `strip` verifies — an operator confirmation cannot prove that
    nothing arrived after the export, these three numbers can.
    """
    root = Path(root)
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    out = root / "tmp" / ("export-" + stamp)
    out.mkdir(parents=True, mode=0o700)
    db = _connect(root / "collector.db", readonly=True)
    started = db.execute("SELECT COALESCE(MAX(started),0) FROM requests").fetchone()[0]
    posts = db.execute("SELECT count(*) FROM posts").fetchone()[0]
    journal_max = db.execute("SELECT COALESCE(MAX(seq),0) FROM export_journal").fetchone()[0]
    # One row per post: the latest version is the state worth keeping. Older versions of the same
    # post are history the hub already holds, and exporting them triples the archive for nothing.
    latest = """SELECT seq,post_id,fingerprint,payload,payload_sha256 FROM export_journal e
                WHERE seq=(SELECT MAX(seq) FROM export_journal WHERE post_id=e.post_id)
                ORDER BY seq"""
    journal_rows = db.execute("SELECT count(*) FROM export_journal").fetchone()[0]
    latest_rows = db.execute("SELECT count(*) FROM (" + latest + ")").fetchone()[0]
    db.close()

    with _connect(root / "collector.db", readonly=True) as src:
        with gzip.open(out / "posts.jsonl.gz", "wt", encoding="utf-8") as handle:
            columns = [r[1] for r in src.execute("PRAGMA table_info(posts)")]
            for row in src.execute("SELECT * FROM posts"):
                handle.write(json.dumps({c: row[c] for c in columns}, ensure_ascii=False) + "\n")
        with gzip.open(out / "journal.jsonl.gz", "wt", encoding="utf-8") as handle:
            for row in src.execute(latest):
                handle.write(json.dumps({"seq": row[0], "post_id": row[1], "fingerprint": row[2],
                                         "payload": row[3], "payload_sha256": row[4]}, ensure_ascii=False) + "\n")

    # Detail tasks that were never completed: their bodies do not exist yet, so record what is
    # missing instead of letting the strip silently cancel them.
    with _connect(root / "collector.db", readonly=True) as src:
        pending = src.execute(
            "SELECT t.post_id, t.url, t.stock, p.title FROM tasks t "
            "LEFT JOIN posts p ON p.source_item_id=t.post_id "
            "WHERE t.kind='detail' AND t.status='pending'").fetchall()
    with open(out / "pending_details.jsonl", "w", encoding="utf-8") as handle:
        for row in pending:
            handle.write(json.dumps({"post_id": row[0], "url": row[1], "stock": row[2],
                                     "title": row[3]}, ensure_ascii=False) + "\n")

    record = {"stamp": stamp, "at": time.time(), "last_write": started, "posts": posts,
              "journal_max": journal_max, "journal_rows": journal_rows, "journal_latest": latest_rows,
              "pending_details": len(pending),
              "posts_bytes": (out / "posts.jsonl.gz").stat().st_size,
              "journal_bytes": (out / "journal.jsonl.gz").stat().st_size}
    (out / "export.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
    # Only the newest archive is kept: an older one is superseded the moment a strip consumes it,
    # and leaving them behind quietly fills the disk.
    for old_dir in (root / "tmp").glob("export-*"):
        if old_dir.resolve() != out.resolve():
            shutil.rmtree(old_dir, ignore_errors=True)
    return record


def _latest_export(root):
    base = Path(root) / "tmp"
    found = sorted(base.glob("export-*/export.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return json.loads(found[0].read_text()) if found else None


def export_is_current(root):
    """True when an export exists and nothing has been written since it was taken."""
    record = _latest_export(root)
    if not record:
        return False, {"name": "存在全量导出", "ok": False, "detail": "先导出 posts 与 journal"}
    info = inspect(root)
    with _connect(Path(root) / "collector.db", readonly=True) as db:
        started = db.execute("SELECT COALESCE(MAX(started),0) FROM requests").fetchone()[0]
        posts = db.execute("SELECT count(*) FROM posts").fetchone()[0]
    stale = []
    if record["at"] < started:
        stale.append("导出早于最后一次请求")
    if record["journal_max"] != info.get("journal_max"):
        stale.append("journal %s→%s" % (record["journal_max"], info.get("journal_max")))
    if record["posts"] != posts:
        stale.append("posts %s→%s" % (record["posts"], posts))
    return (not stale), {"name": "全量导出仍是最新", "ok": not stale,
                         "detail": "；".join(stale) or "posts %d / journal %d" % (posts, record["journal_max"])}


def strip(root, stamp, exported=None, hub_cursor=None, allow_missing_details=False, dry_run=False):
    """Move the data out and leave a control-only database.

    Everything between `tmp.mkdir()` and the swap is junk if it raises: by then a
    VACUUM copy of the whole database is already on disk, and `leftovers()` only
    counts directories that carry `marker.json`, so that half-built copy is
    invisible to the console and to `cleanup` forever. On 2026-11-13 a NameError
    in `_strip_impl` (43bf726) made the strip fail on every click and each retry
    left another full-size copy behind -- on the 600 MB instance that is 600 MB
    per click. The guard below deletes the directory, unless the original
    database has already been moved into it.
    """
    try:
        return _strip_impl(root, stamp, exported=exported, hub_cursor=hub_cursor,
                           allow_missing_details=allow_missing_details, dry_run=dry_run)
    except BaseException:
        tmp = Path(root) / "tmp" / ("clean-" + stamp)
        # Past this point `tmp/collector.db` IS the only copy of the live data:
        # deleting the directory would be the one unrecoverable act in the flow.
        if tmp.is_dir() and not (tmp / "collector.db").exists():
            shutil.rmtree(tmp, ignore_errors=True)
        raise


def _strip_impl(root, stamp, exported=None, hub_cursor=None, allow_missing_details=False, dry_run=False):
    """Move the data out and leave a control-only database. Returns the check report."""
    root = Path(root)
    db_path = root / "collector.db"
    tmp = root / "tmp" / ("clean-" + stamp)
    checks = []

    def check(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return ok

    if tmp.exists():
        return {"ok": False, "checks": [{"name": "目标目录未占用", "ok": False, "detail": str(tmp)}]}
    before = inspect(root, full=True)
    if not check("采集库存在", before["exists"], before["db"]):
        return {"ok": False, "checks": checks}
    check("控制表/数据表划分完整", not before.get("unknown_tables"), str(before.get("unknown_tables")))
    if not check("没有执行中的请求", before["inflight"] == 0, f"inflight={before['inflight']}"):
        return {"ok": False, "checks": checks}
    # The journal is the hub's only source. Deleting it before the hub has consumed every seq
    # silently strands the tail: the next export page comes back empty and the merge looks done.
    if hub_cursor is not None:
        lag = (before.get("journal_max") or 0) - int(hub_cursor)
        if not check("主控已追平（未同步 %d 条）" % max(lag, 0), lag <= 0,
                     "journal_max=%s hub_cursor=%s" % (before.get("journal_max"), hub_cursor)):
            return {"ok": False, "checks": checks}
    fresh, detail = export_is_current(root)
    if not check(detail.get("name", "导出仍是最新"), fresh, detail.get("detail", "")):
        return {"ok": False, "checks": checks}

    # 待补详情在剥离后就永远补不上了：库里没有列表行，任务也会被取消。所以这是硬条件，
    # 不是“记一笔就算”。要放弃必须由调用方显式声明。
    pending = before.get("pending_detail") or 0
    if not check("待补详情已补完（剩余 %d 条）" % pending,
                 pending == 0 or allow_missing_details,
                 "这 %d 条剥离后无法再补；如确认放弃请显式声明" % pending if pending else ""):
        return {"ok": False, "checks": checks}
    tmp.mkdir(parents=True, mode=0o700)
    old = _connect(db_path, readonly=True)
    digests = {}
    for table in CONTROL_TABLES:
        if table in _tables(old):
            digests[table] = _digest(old, table)

    # A consistent copy first: it is both the rollback point and the VACUUM compaction.
    backup = tmp / "collector-backup.db"
    conn = _connect(db_path)
    conn.execute("VACUUM INTO ?", (str(backup),))
    conn.close()
    check("一致性快照已生成", backup.is_file(), f"{backup.stat().st_size//1024//1024} MB")

    new_path = tmp / "collector-new.db"
    new = _connect(new_path)
    copied, schema_errors = _copy_control(old, new)
    # 旧库的全部表/视图/索引都必须在新库里存在；缺一个（尤其是 http_posts 视图）后续采集会直接失败。
    old_names = {(r[0], r[1]) for r in old.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table','view')")}
    new_names = {(r[0], r[1]) for r in new.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table','view')")}
    missing = sorted(name for name, kind in old_names - new_names)
    # Prove the copy before touching it: a silent truncation here would lose the job itself.
    copied_digests = {t: _digest(new, t) for t in CONTROL_TABLES if t in _tables(new)}
    check("控制表逐行复制一致", copied_digests == digests,
          str([t for t in digests if copied_digests.get(t) != digests[t]]))
    for key in RESET_META:
        new.execute("DELETE FROM meta WHERE key=?", (key,))
    cancelled = new.execute(
        "UPDATE tasks SET status='cancelled' WHERE kind='detail' AND status IN ('pending','inflight')").rowcount
    # 记下剥离前的最大序号：新库的 journal 从 1 开始的话，主控游标永远追不上，数据等于白采。
    new.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('journal_seq_hint',?)",
                (str(before.get("journal_max") or 0),))
    # 阻断证据指向的是已经删掉的帖子，留着只会让探测反复拿它去重试并报错。
    for key in ("block_evidence", "probe", "probe_pending"):
        new.execute("DELETE FROM meta WHERE key=?", (key,))
    # 保留页号、丢掉整页帖子 JSON：那些行连同 http_post_state 一起被清空了，留着只会让引擎
    # 为一个查不到来源的帖子排详情，报“详情缺少已获取的列表来源”。
    trimmed = 0
    for row in new.execute("SELECT job, stock, payload FROM frontiers").fetchall():
        try:
            payload = json.loads(row[2] or "{}")
        except ValueError:
            continue
        if payload.get("rows"):
            new.execute("UPDATE frontiers SET payload=? WHERE job=? AND stock=?",
                        (json.dumps({"page": payload.get("page")}), row[0], row[1]))
            trimmed += 1
    new.commit()
    # The reference for `verify` is the state we deliberately keep, not the pre-cancel one.
    kept_digests = {t: _digest(new, t) for t in CONTROL_TABLES if t in _tables(new)}
    new_integrity = new.execute("PRAGMA integrity_check").fetchone()[0]
    new_counts = {t: new.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in DATA_TABLES if t in _tables(new)}
    new.close()
    old.close()

    check("新库完整性", new_integrity == "ok", new_integrity)
    check("表与视图齐全", not missing, ", ".join(missing) or f"{len(new_names)} 个")
    check("schema 无创建错误", not schema_errors, "; ".join(schema_errors)[:200])
    check("控制表行数一致", copied == {t: d[1] for t, d in digests.items() if d}, str(copied))
    leftover = {t: n for t, n in new_counts.items() if n}
    check("数据表为空", not leftover, str(leftover))
    check("待补详情任务已取消", True, f"cancelled={cancelled}")
    check("页缓存已清空（仅保留页码）", True, f"{trimmed} 只股票")

    # Verify first, swap second. Once the file is replaced the running server writes into the
    # moved copy, so a failure here must leave the original untouched.
    if not all(c["ok"] for c in checks):
        new_path.unlink(missing_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)
        return {"ok": False, "checks": checks, "stage": "paused"}
    if dry_run:
        # Same checks, same work, no swap: the operator gets the verdict without the change.
        shutil.rmtree(tmp, ignore_errors=True)
        return {"ok": True, "checks": checks, "dry_run": True, "stage": "paused"}

    os.replace(db_path, tmp / "collector.db")
    os.replace(new_path, db_path)
    for suffix in ("-wal", "-shm"):
        stale = root / ("collector.db" + suffix)
        if stale.exists():
            os.replace(stale, tmp / ("collector.db" + suffix))
    if (root / "raw").is_dir():
        os.replace(root / "raw", tmp / "raw")
    (root / "raw").mkdir(mode=0o700, exist_ok=True)

    dropped_events = (before["counts"].get("events") or 0) - copied.get("events", 0)
    (root / "clean-current.json").write_text(json.dumps(
        {"stamp": stamp, "at": time.time(), "before_counts": before.get("counts", {})},
        ensure_ascii=False, indent=2))
    marker = {"stamp": stamp, "at": time.time(), "before": before, "copied": copied,
              "stripped_counts": new_counts,
              "events_kept": copied.get("events", 0), "events_dropped": max(dropped_events, 0),
              "cancelled_detail_tasks": cancelled, "digests": {k: v for k, v in kept_digests.items() if v},
              "verified": False}
    (tmp / "marker.json").write_text(json.dumps(marker, ensure_ascii=False, indent=2))
    return {"ok": all(c["ok"] for c in checks), "checks": checks, "stage": "detached", "stamp": stamp}


def rollback(root, stamp):
    """Put the original database back. Only valid before `cleanup`."""
    root = Path(root)
    tmp = root / "tmp" / ("clean-" + stamp)
    original = tmp / "collector.db"
    if not original.is_file():
        return {"ok": False, "checks": [{"name": "原库副本存在", "ok": False, "detail": str(original)}]}
    (root / "collector.db").unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        (root / ("collector.db" + suffix)).unlink(missing_ok=True)
    (root / "clean-current.json").unlink(missing_ok=True)
    os.replace(original, root / "collector.db")
    if (tmp / "raw").is_dir():
        shutil.rmtree(root / "raw", ignore_errors=True)
        os.replace(tmp / "raw", root / "raw")
    return {"ok": True, "checks": [{"name": "已回滚到原库", "ok": True}]}


def verify(root, stamp):
    root = Path(root)
    tmp = root / "tmp" / ("clean-" + stamp)
    checks = []

    def check(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return ok

    marker_path = tmp / "marker.json"
    if not marker_path.is_file():
        dangling = (root / "clean-current.json").is_file()
        detail = ("剥离副本已被删除，只剩下 clean-current.json 这条记录；"
                  "数据在导出里，点「清理数据」清掉记录即可回到可用状态"
                  if dangling else f"这次剥离没有副本：{tmp}")
        return {"ok": False, "checks": [{"name": "剥离副本与标记存在", "ok": False, "detail": detail}]}
    marker = json.loads(marker_path.read_text())
    # 剥离之后引擎会继续采集，`posts` 很快就不是空的 —— 判“剥离时清空了没有”，
    # 只能看剥离当时记下的计数，不能看现在的库。
    stripped = marker.get("stripped_counts")
    if isinstance(stripped, dict):
        still = {t: n for t, n in stripped.items() if n}
        check("剥离时数据表为空", not still, str(still) or "0 行")
    else:
        check("剥离时数据表为空", True, "旧记录没有剥离时的计数，跳过这一项")
    original = tmp / "collector.db"
    if original.is_file():
        diffs = control_deltas(original, root / "collector.db")
        check("控制数据已保留", not diffs,
              "；".join(diffs) if diffs else f"{len(CONTROL_TABLES)} 张表（剥离后引擎新增的写入不计）")
    else:
        # 旧版本的剥离点没有留下原件；只能退回“与剥离时的摘要完全一致”，而它只在
        # 引擎还没来得及写入时才成立。
        diffs = []
        with _connect(root / "collector.db", readonly=True) as db:
            for table, (digest, count) in marker["digests"].items():
                now = _digest(db, table)
                if not now or now[0] != digest:
                    diffs.append({"table": table, "before": count, "after": now[1] if now else None})
        check("控制表逐行一致", not diffs, str(diffs) or f"{len(marker['digests'])} 张")
    check("快照可用于回滚", (tmp / "collector-backup.db").is_file())
    check("旧库原件保留", (tmp / "collector.db").is_file())
    ok = all(c["ok"] for c in checks)
    if ok:
        marker["verified"] = True
        marker_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2))
    return {"ok": ok, "checks": checks, "stage": "verified" if ok else "detached"}


def leftovers(root):
    """Strip directories still on disk. They are backups, so they may only be removed while the
    live database still has its data."""
    return [p for p in sorted((Path(root) / "tmp").glob("clean-*")) if (p / "marker.json").is_file()]


def cleanup(root, stamp=""):
    """Delete this run's copy, or every leftover copy when no run is active."""
    root = Path(root)
    info = inspect(root)
    has_data = any((info.get("counts") or {}).get(name) for name in DATA_TABLES)
    if not stamp:
        # No active strip: the operator is reclaiming space after a rollback. Only the copies are
        # deleted; the live database must still hold the data, otherwise this is the last copy.
        dirs = leftovers(root)
        if not dirs:
            if (root / "clean-current.json").is_file() and not has_data:
                (root / "clean-current.json").unlink(missing_ok=True)
                return {"ok": True, "stage": "paused",
                        "checks": [{"name": "已清除失效的剥离记录", "ok": True,
                                    "detail": "副本已不存在、线上库为空，控制台恢复可用"}]}
            return {"ok": False, "checks": [{"name": "存在可清理的副本", "ok": False}]}
        if not has_data:
            return {"ok": False, "checks": [{"name": "线上库有数据才允许删除副本", "ok": False,
                                             "detail": "当前库为空，删除副本会丢失唯一副本"}]}
        removed = []
        for path in dirs:
            shutil.rmtree(path)
            removed.append(path.name)
        (root / "clean-current.json").unlink(missing_ok=True)
        return {"ok": True, "checks": [{"name": "已清理遗留副本", "ok": not leftovers(root),
                                        "detail": ", ".join(removed)}], "stage": "paused"}
    root = Path(root)
    tmp = root / "tmp" / ("clean-" + stamp)
    marker_path = tmp / "marker.json"
    checks = []
    if not marker_path.is_file():
        # 控制台永远带着 state() 解析出来的 stamp 调这里，所以“副本被删掉”只会走到这条分支，
        # 而不是上面那个没有 stamp 的。线上库为空、记录还在、目录也没了，就回收这条记录。
        if (root / "clean-current.json").is_file() and not has_data and not tmp.exists():
            (root / "clean-current.json").unlink(missing_ok=True)
            return {"ok": True, "stage": "paused",
                    "checks": [{"name": "已清除失效的剥离记录", "ok": True,
                                "detail": "副本已不存在、线上库为空，控制台恢复可用"}]}
        return {"ok": False, "checks": [{"name": "标记存在", "ok": False, "detail": str(marker_path)}]}
    marker = json.loads(marker_path.read_text())
    if not marker.get("verified"):
        return {"ok": False, "checks": [{"name": "verify 已通过", "ok": False}]}
    base = (root / "tmp").resolve()
    if not str(tmp.resolve()).startswith(str(base) + os.sep):
        return {"ok": False, "checks": [{"name": "路径受控", "ok": False, "detail": str(tmp)}]}
    shutil.rmtree(tmp)
    (root / "clean-current.json").unlink(missing_ok=True)
    checks.append({"name": "已删除", "ok": not tmp.exists(), "detail": str(tmp)})
    return {"ok": True, "checks": checks, "stage": "cleaned"}


def state(root, running=None):
    """paused -> detached -> verified, derived from disk plus the engine's own job state.

    `running` must come from the engine: an idle worker between requests also reports inflight=0,
    so disk alone cannot tell "paused" from "running but quiet".
    """
    root = Path(root)
    info = inspect(root)
    if running is None:
        stage = "paused" if info.get("inflight") == 0 else "running"
    else:
        stage = "running" if running or info.get("inflight") else "paused"
    # One file decides: a strip exists or it does not. Leftover directories and markers from an
    # earlier or rolled-back attempt must never change what the buttons do.
    current = root / "clean-current.json"
    stamp, marker_at, verified = "", None, False
    marker = None
    if current.is_file():
        try:
            record = json.loads(current.read_text())
        except ValueError:
            record = {}
        stamp = record.get("stamp", "")
        marker_at = record.get("at")
        if stamp:
            marker = root / "tmp" / ("clean-" + stamp) / "marker.json"
            if marker.is_file():
                try:
                    verified = json.loads(marker.read_text()).get("verified") is True
                except ValueError:
                    verified = False
    has_data = any((info.get("counts") or {}).get(name) for name in DATA_TABLES)
    if current.is_file() and has_data and _data_precedes_strip(root, marker_at):
        # Rows older than the strip: the old database is back (rollback or a half-finished
        # restore). Nothing may be deleted -- least of all the last copy.
        stage = "inconsistent"
    elif current.is_file():
        if marker is not None and not marker.is_file() and not has_data:
            # 副本被手工删掉或随磁盘清理消失，记录还留着：唯一的出路是回收这条记录。
            # 停在 detached 只会让校验永远失败、三个按钮全灰（2026-11-13 发生过）。
            stage = "leftovers"
        else:
            stage = "verified" if verified else "detached"
    elif has_data and leftovers(root):
        # Copies from an earlier attempt or a rollback: the button must offer to clean them.
        stage = "leftovers"
    else:
        stage = "running" if running else "paused"
    info.update({"stage": stage, "stamp": stamp})
    return info
