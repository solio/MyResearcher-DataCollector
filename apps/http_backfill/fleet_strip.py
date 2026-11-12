#!/usr/bin/env python3
"""Ledger strip: export, detach, verify, cleanup — a one-way state machine.

The merged ledger grows forever (versions + raw evidence). Operators need to archive it and
start a fresh one without re-downloading history and without losing what is already merged.

Contract:
  * only renamed, never deleted, until `cleanup`;
  * `cursors` is copied verbatim — a stripped ledger continues exactly where the old one was;
  * `pending` is reset to the cursor and `pending_posts` is emptied: a half-merged page is not
    carried over, it is simply fetched again from the node's export journal.

Every step prints PASS/FAIL. `cleanup` refuses to run unless `verify` recorded success.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

MARK = "http-backfill.fleet.v1"
CONTENT_TABLES = ("versions", "raw_evidence", "selected", "conflicts")
PROTOCOL_TABLES = ("merge_meta", "cursors", "pending", "pending_posts")
SCHEMA = """
CREATE TABLE IF NOT EXISTS merge_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS versions(instance_id TEXT,seq INTEGER,source TEXT,post_id TEXT,
  evidence_sha256 TEXT,payload TEXT,identity_sha256 TEXT,body_sha256 TEXT,PRIMARY KEY(instance_id,seq));
CREATE INDEX IF NOT EXISTS versions_post ON versions(source,post_id);
CREATE TABLE IF NOT EXISTS raw_evidence(instance_id TEXT,request_id INTEGER,sha256 TEXT,
  response_bytes INTEGER,descriptor TEXT,PRIMARY KEY(instance_id,request_id));
CREATE TABLE IF NOT EXISTS cursors(instance_id TEXT PRIMARY KEY,seq INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS pending(instance_id TEXT PRIMARY KEY,next_after INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS pending_posts(instance_id TEXT,source TEXT,post_id TEXT,
  PRIMARY KEY(instance_id,source,post_id));
CREATE TABLE IF NOT EXISTS selected(source TEXT,post_id TEXT,instance_id TEXT,seq INTEGER,
  PRIMARY KEY(source,post_id));
CREATE TABLE IF NOT EXISTS conflicts(source TEXT,post_id TEXT,kind TEXT,variants TEXT,
  PRIMARY KEY(source,post_id,kind));
"""

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS  " if ok else "FAIL  ") + name + (("  | " + detail) if detail else ""), flush=True)
    return ok


def open_ro(path):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def counts(db):
    out = {}
    for table in CONTENT_TABLES + ("pending", "pending_posts", "cursors"):
        out[table] = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    return out


def cursors_of(db):
    return {row["instance_id"]: row["seq"] for row in db.execute("SELECT instance_id,seq FROM cursors")}


def ledger_path(root: Path) -> Path:
    return root / "fleet" / "merge.sqlite3"


def cmd_status(args):
    root = Path(args.data)
    ledger = ledger_path(root)
    if not ledger.is_file():
        return check("台账存在", False, str(ledger))
    check("台账存在", True, str(ledger))
    with open_ro(ledger) as db:
        check("汇总台账契约", db.execute("SELECT value FROM merge_meta WHERE key='storage'").fetchone()[0] == MARK)
        data = counts(db)
        print("  行数:", data)
        print("  游标:", cursors_of(db))
        pending = db.execute("SELECT instance_id,next_after FROM pending").fetchall()
        if pending:
            print("  在途:", [(r["instance_id"][:8], r["next_after"]) for r in pending])
    free = shutil.disk_usage(root).free
    trees = {"ledger": ledger.stat().st_size,
             "raw": sum(p.stat().st_size for p in (root / "fleet" / "raw").rglob("*") if p.is_file()) if (root / "fleet" / "raw").is_dir() else 0,
             "side": (root / "fleet" / "collector.db").stat().st_size if (root / "fleet" / "collector.db").is_file() else 0}
    total = sum(trees.values())
    print("  占用:", {k: f"{v/1024**3:.2f}G" for k, v in trees.items()}, f"合计 {total/1024**3:.2f}G")
    # 改名搬移不需要空间；导出需要。按“导出约为台账的一半”给一个保守判断。
    check("导出所需空间充足", free > total * 0.6 + 1024 ** 3,
          f"free={free/1024**3:.1f}G 预计导出≈{total*0.5/1024**3:.1f}G")
    return all(ok for _, ok, _ in results)


def _ledger_state(root):
    with open_ro(ledger_path(root)) as db:
        return counts(db), cursors_of(db)


def cmd_export(args):
    """Export the merged posts and record what the ledger looked like at that instant."""
    root = Path(args.data)
    stamp = args.stamp or time.strftime("%Y%m%d-%H%M%S")
    out = root / "tmp" / ("export-" + stamp)
    out.mkdir(parents=True, mode=0o700)
    target = out / "fleet.jsonl"
    posts = None
    if args.offline_path:
        # Test/offline hook: the same assertions run against a local file instead of the API.
        shutil.copyfile(args.offline_path, target)
        posts = sum(1 for line in target.read_text(encoding="utf-8").splitlines() if line.strip())
    else:
        import urllib.request
        token = (root / "console.token").read_text().strip()
        request = urllib.request.Request(
            "http://127.0.0.1:8790/api/download/posts?scope=fleet&format=jsonl",
            headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(request, timeout=600) as response:
            header = response.headers.get("X-Collector-Post-Count")
            body = response.read()
        target.write_bytes(body)
        if header is None:
            return check("导出接口返回计数头", False)
        posts = int(header)
    lines = sum(1 for line in target.read_text(encoding="utf-8").splitlines() if line.strip())
    check("导出行数与接口计数一致", lines == posts, f"lines={lines} header={posts}")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    ledger_counts, cursors = _ledger_state(root)
    record = {"stamp": stamp, "ok": lines == posts, "at": time.time(), "posts": lines,
              "bytes": target.stat().st_size, "sha256": digest,
              "cursors": cursors, "ledger_counts": ledger_counts}
    (out / "export.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print("  导出目录:", out)
    return all(ok for _, ok, _ in results)


def _latest_export(root):
    base = root / "tmp"
    candidates = sorted(base.glob("export-*/export.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def cmd_detach(args):
    root = Path(args.data)
    fleet = root / "fleet"
    ledger = fleet / "merge.sqlite3"
    if not ledger.is_file():
        return check("台账存在", False, str(ledger))
    check("台账存在", True, str(ledger))

    stamp = args.stamp or time.strftime("%Y%m%d-%H%M%S")
    tmp = root / "tmp" / ("strip-" + stamp)
    if tmp.exists():
        return check("目标目录未占用", False, str(tmp))
    if os.stat(root).st_dev != os.stat(tmp.parent if tmp.parent.exists() else root).st_dev:
        return check("同文件系统（改名才是原子操作）", False)
    check("同文件系统（改名才是原子操作）", True)

    registry = root / "fleet" / "registry.json"
    try:
        paused = json.loads(registry.read_text()).get("auto_sync") is False
    except (OSError, ValueError):
        paused = False
    if not check("合并已暂停（registry.auto_sync=false）", paused,
                 "先在控制台点“暂停合并”，等本轮传输结束"):
        return False

    record_path = Path(args.export) if args.export else _latest_export(root)
    if not check("存在导出产物", bool(record_path and Path(record_path).is_file()),
                 str(record_path or "先运行 export")):
        return False
    record = json.loads(Path(record_path).read_text())
    if not check("导出本身校验通过", record.get("ok") is True):
        return False

    with open_ro(ledger) as db:
        ok = check("汇总台账契约", db.execute("SELECT value FROM merge_meta WHERE key='storage'").fetchone()[0] == MARK)
        before = counts(db)
        cursors = cursors_of(db)
        pending = {r["instance_id"]: r["next_after"] for r in db.execute("SELECT instance_id,next_after FROM pending")}
        index_sql = [r["sql"] for r in db.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL AND tbl_name IN (%s)"
            % ",".join("'%s'" % t for t in CONTENT_TABLES))]
    if not ok:
        return False
    print("  剥离前:", before, "游标:", cursors)
    if not check("导出后台账没有变化（游标一致）", record.get("cursors") == cursors,
                 f"export={record.get('cursors')} now={cursors}"):
        return False
    if not check("导出后行数没有变化", record.get("ledger_counts") == before,
                 f"export={record.get('ledger_counts')} now={before}"):
        return False
    if pending:
        print("  在途页:", [(k[:8], v) for k, v in pending.items()])

    tmp.mkdir(parents=True, mode=0o700)
    moved = []
    for name in ("merge.sqlite3", "merge.sqlite3-wal", "merge.sqlite3-shm", "collector.db"):
        src = fleet / name
        if src.exists():
            os.replace(src, tmp / name)
            moved.append((name, (tmp / name).stat().st_size))
    if (fleet / "raw").is_dir():
        os.replace(fleet / "raw", tmp / "raw")
        moved.append(("raw", sum(p.stat().st_size for p in (tmp / "raw").rglob("*") if p.is_file())))
    check("原始文件已改名为副本", all(not (fleet / n).exists() for n, _ in moved), str([n for n, _ in moved]))

    # Once the files move, every failure has to put them back: an operator who sees FAIL must be
    # able to rerun the same command, not repair a half-stripped directory by hand.
    new = None
    try:
        new = sqlite3.connect(ledger)
        new.row_factory = sqlite3.Row
        new.executescript(SCHEMA)
        for sql in index_sql:
            new.execute(sql.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1))
        new.execute("INSERT OR REPLACE INTO merge_meta VALUES('storage',?)", (MARK,))
        for instance_id, seq in cursors.items():
            new.execute("INSERT OR REPLACE INTO cursors VALUES(?,?)", (instance_id, seq))
            # A half-merged page is not carried over: the page is fetched again from cursor+1.
            new.execute("INSERT OR REPLACE INTO pending VALUES(?,?)", (instance_id, seq))
        new.commit()
        integrity = new.execute("PRAGMA integrity_check").fetchone()[0]
        after = counts(new)
        new_cursors = cursors_of(new)
        new_pending = {r["instance_id"]: r["next_after"] for r in new.execute("SELECT instance_id,next_after FROM pending")}
    except Exception as exc:  # noqa: BLE001 - any failure must restore the original layout
        if new is not None:
            new.close()
        ledger.unlink(missing_ok=True)
        for name, _ in moved:
            os.replace(tmp / name, fleet / name)
        shutil.rmtree(tmp, ignore_errors=True)
        return check("建新台账失败，已回滚原文件", False, f"{type(exc).__name__}: {exc}")
    finally:
        if new is not None:
            new.close()

    check("新台账完整性", integrity == "ok", integrity)
    check("游标逐行一致", new_cursors == cursors, f"{len(cursors)} 个实例")
    check("在途页已重置到游标", all(new_pending.get(k) == v for k, v in cursors.items()), str(new_pending))
    check("内容表为空", all(after[t] == 0 for t in CONTENT_TABLES), str({t: after[t] for t in CONTENT_TABLES}))
    check("pending_posts 为空", after["pending_posts"] == 0)

    marker = {"stamp": stamp, "phase": "detached", "verified": False, "created_at": time.time(),
              "before": before, "cursors": cursors, "moved": moved, "ledger_bytes": (tmp / "merge.sqlite3").stat().st_size}
    (tmp / "marker.json").write_text(json.dumps(marker, ensure_ascii=False, indent=2))
    print("  剥离副本:", tmp)
    return all(ok for _, ok, _ in results)


def cmd_verify(args):
    root = Path(args.data)
    fleet = root / "fleet"
    ledger = fleet / "merge.sqlite3"
    stamp = args.stamp
    tmp = root / "tmp" / ("strip-" + stamp)
    marker_path = tmp / "marker.json"
    if not check("剥离副本与标记存在", marker_path.is_file(), str(marker_path)):
        return False
    marker = json.loads(marker_path.read_text())
    with open_ro(ledger) as db:
        ok = check("新台账契约", db.execute("SELECT value FROM merge_meta WHERE key='storage'").fetchone()[0] == MARK)
        check("游标与剥离前一致", cursors_of(db) == marker["cursors"], str(cursors_of(db)))
        data = counts(db)
        check("内容表仍为空", all(data[t] == 0 for t in CONTENT_TABLES), str({t: data[t] for t in CONTENT_TABLES}))
    check("副本文件齐全", (tmp / "merge.sqlite3").is_file())
    if ok:
        marker["verified"] = True
        marker["verified_at"] = time.time()
        marker_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2))
    check("已标记 verify 通过", marker.get("verified") is True)
    return all(ok for _, ok, _ in results)


def cmd_cleanup(args):
    root = Path(args.data)
    if not args.stamp:
        return check("必须显式指定 --stamp", False)
    tmp = root / "tmp" / ("strip-" + args.stamp)
    marker_path = tmp / "marker.json"
    if not check("标记存在", marker_path.is_file(), str(marker_path)):
        return False
    marker = json.loads(marker_path.read_text())
    if not check("verify 已通过", marker.get("verified") is True):
        return False
    if not str(tmp.resolve()).startswith(str((root / "tmp").resolve()) + os.sep):
        return check("路径受控", False, str(tmp))
    shutil.rmtree(tmp)
    check("已删除", not tmp.exists(), str(tmp))
    return all(ok for _, ok, _ in results)




JOURNAL = "strip-journal.jsonl"


def _journal(root, entry):
    path = root / "fleet" / JOURNAL
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _last_journal(root):
    path = root / "fleet" / JOURNAL
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def state(root="/data"):
    """Derive the stage from disk facts; the journal only records terminal steps.

    running -> paused -> exported -> detached -> verified -> cleaned
    Each stage is a precondition of the next, so the four buttons cannot be pressed out of order.
    """
    root = Path(root)
    registry = root / "fleet" / "registry.json"
    try:
        paused = json.loads(registry.read_text()).get("auto_sync") is False
    except (OSError, ValueError):
        paused = False
    ledger = ledger_path(root)
    stage, stamp, detail = ("paused" if paused else "running"), "", {}
    if ledger.is_file():
        with open_ro(ledger) as db:
            data = counts(db)
            cursors = cursors_of(db)
        detail = {"counts": data, "cursors": cursors}
        empty = all(data[t] == 0 for t in CONTENT_TABLES)
        export_path = _latest_export(root)
        export = json.loads(export_path.read_text()) if export_path else None
        strip_dirs = sorted((root / "tmp").glob("strip-*"))
        journal = _last_journal(root)
        cleaned = any(e.get("action") == "cleanup" and e.get("ok") for e in journal)
        if empty and strip_dirs:
            marker = json.loads((strip_dirs[-1] / "marker.json").read_text()) if (strip_dirs[-1] / "marker.json").is_file() else {}
            stage, stamp = ("verified" if marker.get("verified") else "detached"), marker.get("stamp", "")
        elif empty and cleaned:
            stage = "cleaned"
        elif export and export.get("ok") and export.get("cursors") == cursors and export.get("ledger_counts") == data:
            stage, stamp = "exported", export.get("stamp", "")
    detail.update({"stage": stage, "stamp": stamp, "paused": paused})
    return detail


def run(root, action, stamp=""):
    """One entry point for both the CLI and the console; refuses any out-of-order transition."""
    root = Path(root)
    current = state(root)
    expected = {"export": "paused", "detach": "exported", "strip": "paused",
                "verify": "detached", "cleanup": "verified"}
    if action not in expected:
        return {"ok": False, "stage": current["stage"], "error": "未知动作"}
    if current["stage"] != expected[action]:
        return {"ok": False, "stage": current["stage"],
                "error": "当前阶段是 %s，%s 需要先完成 %s" % (current["stage"], action, expected[action])}
    if action == "strip":
        # One button = "export, then move": a strip without an export is exactly what we refuse.
        args = argparse.Namespace(data=str(root), stamp=stamp, export="", offline_path="")
        results.clear()
        ok = cmd_export(args) and cmd_detach(args)
        action = "detach"
    else:
        handlers = {"export": cmd_export, "detach": cmd_detach, "verify": cmd_verify, "cleanup": cmd_cleanup}
        args = argparse.Namespace(data=str(root), stamp=stamp or current["stamp"], export="", offline_path="")
        results.clear()
        ok = handlers[action](args)
    out = {"ok": bool(ok), "action": action, "stage_after": state(root)["stage"],
           "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in results]}
    if ok:
        _journal(root, {"action": action, "ok": True, "at": time.time(), "stamp": args.stamp,
                        "counts": current.get("counts")})
    return out
