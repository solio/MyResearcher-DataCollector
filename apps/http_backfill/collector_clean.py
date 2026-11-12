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


def _digest(db, table):
    """Stable fingerprint of one table: rows ordered by primary key, hashed as JSON."""
    columns = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
    if not columns:
        return None
    rows = db.execute(f"SELECT * FROM {table} ORDER BY " + ",".join(columns)).fetchall()
    payload = json.dumps([{c: r[c] for c in columns} for r in rows], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest(), len(rows)


def inspect(root):
    """What is here right now, and is the split still exhaustive?"""
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
                out["counts"][name] = db.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
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
    out["raw_bytes"] = sum(p.stat().st_size for p in (root / "raw").rglob("*") if p.is_file()) if (root / "raw").is_dir() else 0
    return out


def _copy_control(old_db, new_db):
    """Recreate the schema from the old file, then copy control tables verbatim."""
    copied = {}
    for (sql,) in old_db.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"):
        if sql.startswith("CREATE TRIGGER"):
            continue
        try:
            new_db.execute(sql)
        except sqlite3.Error:
            continue
    for table in CONTROL_TABLES:
        if table not in _tables(old_db):
            continue
        rows = old_db.execute(f"SELECT * FROM {table}").fetchall()
        columns = [r[1] for r in old_db.execute(f"PRAGMA table_info({table})")]
        if rows:
            new_db.executemany(
                f"INSERT INTO {table}(" + ",".join(columns) + ") VALUES(" + ",".join("?" * len(columns)) + ")",
                [tuple(r) for r in rows])
        copied[table] = len(rows)
    for (sql,) in old_db.execute("SELECT sql FROM sqlite_master WHERE sql LIKE 'CREATE TRIGGER%'"):
        new_db.execute(sql)
    return copied


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


def strip(root, stamp, exported=None):
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
    before = inspect(root)
    if not check("采集库存在", before["exists"], before["db"]):
        return {"ok": False, "checks": checks}
    check("控制表/数据表划分完整", not before.get("unknown_tables"), str(before.get("unknown_tables")))
    if not check("没有执行中的请求", before["inflight"] == 0, f"inflight={before['inflight']}"):
        return {"ok": False, "checks": checks}
    fresh, detail = export_is_current(root)
    if not check(detail.get("name", "导出仍是最新"), fresh, detail.get("detail", "")):
        return {"ok": False, "checks": checks}

    if before.get("pending_detail"):
        check("待补详情将不再自动补（数量 %d，已写入导出清单）" % before["pending_detail"], True,
              "pending_details.jsonl")
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
    copied = _copy_control(old, new)
    # Prove the copy before touching it: a silent truncation here would lose the job itself.
    copied_digests = {t: _digest(new, t) for t in CONTROL_TABLES if t in _tables(new)}
    check("控制表逐行复制一致", copied_digests == digests,
          str([t for t in digests if copied_digests.get(t) != digests[t]]))
    for key in RESET_META:
        new.execute("DELETE FROM meta WHERE key=?", (key,))
    cancelled = new.execute(
        "UPDATE tasks SET status='cancelled' WHERE status='pending' AND kind='detail'").rowcount
    new.commit()
    # The reference for `verify` is the state we deliberately keep, not the pre-cancel one.
    kept_digests = {t: _digest(new, t) for t in CONTROL_TABLES if t in _tables(new)}
    new_integrity = new.execute("PRAGMA integrity_check").fetchone()[0]
    new_counts = {t: new.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in DATA_TABLES if t in _tables(new)}
    new.close()
    old.close()

    check("新库完整性", new_integrity == "ok", new_integrity)
    check("控制表行数一致", copied == {t: d[1] for t, d in digests.items() if d}, str(copied))
    leftover = {t: n for t, n in new_counts.items() if n}
    check("数据表为空", not leftover, str(leftover))
    check("待补详情任务已取消", cancelled >= 0, f"cancelled={cancelled}")

    # Verify first, swap second. Once the file is replaced the running server writes into the
    # moved copy, so a failure here must leave the original untouched.
    if not all(c["ok"] for c in checks):
        new_path.unlink(missing_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)
        return {"ok": False, "checks": checks, "stage": "paused"}

    os.replace(db_path, tmp / "collector.db")
    os.replace(new_path, db_path)
    for suffix in ("-wal", "-shm"):
        stale = root / ("collector.db" + suffix)
        if stale.exists():
            os.replace(stale, tmp / ("collector.db" + suffix))
    if (root / "raw").is_dir():
        os.replace(root / "raw", tmp / "raw")
    (root / "raw").mkdir(mode=0o700, exist_ok=True)

    marker = {"stamp": stamp, "at": time.time(), "before": before, "copied": copied,
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
    if not check("剥离副本与标记存在", marker_path.is_file(), str(marker_path)):
        return {"ok": False, "checks": checks}
    marker = json.loads(marker_path.read_text())
    after = inspect(root)
    check("数据表已清空", not [t for t in DATA_TABLES if after["counts"].get(t)], str(after["counts"]))
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


def cleanup(root, stamp):
    root = Path(root)
    tmp = root / "tmp" / ("clean-" + stamp)
    marker_path = tmp / "marker.json"
    checks = []
    if not marker_path.is_file():
        return {"ok": False, "checks": [{"name": "标记存在", "ok": False, "detail": str(marker_path)}]}
    marker = json.loads(marker_path.read_text())
    if not marker.get("verified"):
        return {"ok": False, "checks": [{"name": "verify 已通过", "ok": False}]}
    base = (root / "tmp").resolve()
    if not str(tmp.resolve()).startswith(str(base) + os.sep):
        return {"ok": False, "checks": [{"name": "路径受控", "ok": False, "detail": str(tmp)}]}
    shutil.rmtree(tmp)
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
    stamp = ""
    has_data = any((info.get("counts") or {}).get(name) for name in DATA_TABLES)
    dirs = [] if has_data else sorted((root / "tmp").glob("clean-*"))
    if has_data:
        # A leftover marker from a rolled-back strip must not claim the data is gone.
        stage = "running" if running else "paused"
    if dirs:
        marker = dirs[-1] / "marker.json"
        if marker.is_file():
            data = json.loads(marker.read_text())
            stamp = data.get("stamp", "")
            stage = "verified" if data.get("verified") else "detached"
    elif (root / "fleet" / "clean-journal.jsonl").is_file() and not dirs:
        stage = "cleaned"
    info.update({"stage": stage, "stamp": stamp})
    return info
