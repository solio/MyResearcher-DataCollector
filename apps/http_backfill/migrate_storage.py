"""Stopped-worker-only, offline and recoverable migration to one collector.db.

Run: python apps/http_backfill/migrate_storage.py --data-dir /data
No Engine is constructed and no source request is made. Old files are removed
only after an independently validated atomic publication and durable receipt.
"""
from __future__ import annotations

import argparse
import base64
from collections import OrderedDict
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from dataclasses import asdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

APP = Path(__file__).resolve().parent
REPO = APP.parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

RECEIPT_NAME = "storage-migration.json"
RECEIPT_VERSION = "http-backfill.storage-migration.v1"
_META_CHANGES = {"storage_layout", "compatible_storage_instance", "compatible_storage_version", "data_storage"}
_MIN_TABLES = {"meta", "posts", "requests", "observations", "tasks", "jobs", "coverage"}


class MigrationError(RuntimeError):
    """Migration did not meet the evidence contract; retain legacy files."""


def _quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      default=lambda x: {"sqlite_blob_base64": base64.b64encode(x).decode("ascii")})


def _readonly(path):
    return closing(sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True))


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def _columns(db, table):
    return [r[1] for r in db.execute(f"PRAGMA table_info({_quote(table)})")]


def _integrity(db):
    result = [r[0] for r in db.execute("PRAGMA integrity_check")]
    if result != ["ok"]:
        raise MigrationError("SQLite 完整性检查失败")


def _get(db, key):
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def _row_manifest(db, table, columns=None):
    columns = columns or _columns(db, table)
    fields = ",".join(_quote(c) for c in columns)
    # Source keys and ledger IDs are retained. Ordering by all columns also
    # verifies tables with composite nullable keys and duplicate rows exactly.
    rows = db.execute(f"SELECT {fields} FROM {_quote(table)} ORDER BY {fields}")
    count, digest = 0, hashlib.sha256()
    for row in rows:
        value = _json(tuple(row)).encode()
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
        count += 1
    return {"columns": columns, "count": count, "sha256": digest.hexdigest()}


def _manifest(path):
    with _readonly(path) as db:
        _integrity(db)
        return {table: _row_manifest(db, table) for table in sorted(_tables(db))}


def _fsync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_json(path, value):
    if path.is_symlink():
        raise MigrationError("迁移 receipt 不能是符号链接")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with open(temporary, "x", encoding="utf-8") as out:
        os.chmod(temporary, 0o600)
        out.write(_json(value) + "\n")
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _backup(source, destination):
    if source.is_symlink() or not source.is_file() or destination.exists():
        raise MigrationError("迁移 SQLite 备份路径无效")
    with _readonly(source) as old, closing(sqlite3.connect(destination)) as snapshot:
        old.backup(snapshot)
        # A backup includes committed WAL pages. Keep each backup self-contained
        # rather than depending on a copied live -wal/-shm pair.
        snapshot.execute("PRAGMA journal_mode=DELETE")
        snapshot.commit()
        _integrity(snapshot)
    os.chmod(destination, 0o600)
    with open(destination, "rb") as file:
        os.fsync(file.fileno())
    _fsync_directory(destination.parent)


def _verify_raw(db, directory):
    root = directory / "raw"
    if root.is_symlink():
        raise MigrationError("原始响应目录不能是符号链接")
    root = root.resolve()
    count, seen = 0, {}
    for request_id, reference, size, expected in db.execute("SELECT id,raw_ref,response_bytes,sha256 FROM requests WHERE raw_ref IS NOT NULL"):
        if not isinstance(reference, str) or not reference:
            raise MigrationError("请求 raw_ref 无效")
        original = directory / reference
        path = original.resolve()
        if original.is_symlink() or not path.is_relative_to(root) or not path.is_file():
            raise MigrationError(f"请求 {request_id} 的 raw 路径越界/缺失")
        if type(size) is not int or size < 0 or not isinstance(expected, str):
            raise MigrationError(f"请求 {request_id} 缺少原始响应哈希/长度")
        if path not in seen:
            actual, digest = 0, hashlib.sha256()
            with open(path, "rb") as body:
                while chunk := body.read(1024 * 1024):
                    digest.update(chunk)
                    actual += len(chunk)
            seen[path] = (actual, digest.hexdigest())
        if seen[path] != (size, expected):
            raise MigrationError(f"请求 {request_id} 的 raw SHA-256/字节数不一致")
        count += 1
    return {"referenced_requests": count, "verified_raw_files": len(seen)}


def _check_legacy(source, collector=None):
    with _readonly(source) as db:
        _integrity(db)
        if not _MIN_TABLES.issubset(_tables(db)) or not {"post_id", "item", "source_row", "status", "list_request", "detail_request", "detail_payload", "content"}.issubset(_columns(db, "posts")):
            raise MigrationError("experiment.sqlite3 不是已知 HTTP 采集台账")
        identity = _get(db, "instance_id")
        if collector is not None:
            if not identity or _get(db, "compatible_storage_instance") != identity:
                raise MigrationError("旧 collector.db 未标记为此采集实例所有，拒绝写入")
            from compatible_store import _expected_schema
            with _readonly(collector) as old:
                if _tables(old) != set(_expected_schema()):
                    raise MigrationError("旧 collector.db 不是原 SimplePostStore 结构")
                if any(old.execute(f"PRAGMA table_info({_quote(t)})").fetchall() != columns for t, columns in _expected_schema().items()):
                    raise MigrationError("旧 collector.db 字段/主键与原结构不符")
        return identity


def _copy_skip(snapshot, stage):
    if snapshot is None:
        return
    with _readonly(snapshot) as old, closing(sqlite3.connect(stage)) as target:
        if _tables(old) != {"detail_enrichment_skips"}:
            raise MigrationError("旧详情跳过记录不是已知结构")
        with closing(sqlite3.connect(":memory:")) as expected:
            expected.execute("""CREATE TABLE detail_enrichment_skips(source TEXT NOT NULL,source_item_id TEXT NOT NULL,
              stock_code TEXT NOT NULL,reason TEXT NOT NULL,first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,
              attempts INTEGER NOT NULL,PRIMARY KEY(source,source_item_id))""")
            if old.execute("PRAGMA table_info(detail_enrichment_skips)").fetchall() != expected.execute("PRAGMA table_info(detail_enrichment_skips)").fetchall():
                raise MigrationError("旧详情跳过记录字段/主键不是已知结构")
        sql = old.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='detail_enrichment_skips'").fetchone()[0]
        if "detail_enrichment_skips" not in _tables(target):
            target.execute(sql)
        if _columns(old, "detail_enrichment_skips") != _columns(target, "detail_enrichment_skips"):
            raise MigrationError("详情跳过记录字段不一致")
        columns = _columns(old, "detail_enrichment_skips")
        values = ",".join("?" for _ in columns)
        target.executemany(f"INSERT INTO detail_enrichment_skips VALUES({values})", old.execute("SELECT * FROM detail_enrichment_skips"))
        target.commit()


def _validate_conversion(source, target, directory, legacy_collector=None, skips=None):
    from unified_store import validate_layout
    with _readonly(source) as old, _readonly(target) as new:
        identity = validate_layout(new)
        _integrity(new)
        tables = _tables(new)
        if "_http_legacy_posts" in tables or "content" in _columns(new, "http_post_state"):
            raise MigrationError("统一库仍有重复当前正文表/列")
        counts = {}
        for table in sorted(_tables(old) - {"posts", "meta"}):
            columns = _columns(old, table)
            if table not in tables or not set(columns).issubset(_columns(new, table)):
                raise MigrationError(f"原台账 {table} 表/字段没有完整保留")
            original, converted = _row_manifest(old, table), _row_manifest(new, table, columns)
            if original != converted:
                raise MigrationError(f"原台账 {table} 行/ID/事实不一致")
            counts[table] = original["count"]
        for key, value in old.execute("SELECT key,value FROM meta"):
            if key in _META_CHANGES:
                continue
            actual = new.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            if actual is None or actual[0] != value:
                raise MigrationError(f"原实例状态 meta.{key} 没有完整保留")
        old.row_factory = new.row_factory = sqlite3.Row
        raw = _verify_raw(new, directory)
        state_count = new.execute("SELECT COUNT(*) FROM http_post_state").fetchone()[0]
        if state_count != old.execute("SELECT COUNT(*) FROM posts").fetchone()[0]:
            raise MigrationError("来源帖子状态数量不一致")
        body_count = 0
        parsed_lists = OrderedDict()
        from myresearcher_collector.sources.eastmoney_guba import parser as guba
        def request_html(request_id):
            request = new.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if request is None or not request["raw_ref"]:
                raise MigrationError("帖子缺少原始请求证据")
            return request, (directory / request["raw_ref"]).read_bytes().decode("utf-8-sig", errors="strict")
        def list_items(request_id):
            if request_id not in parsed_lists:
                request, html = request_html(request_id)
                parsed_lists[request_id] = (request, {item.source_item_id: item for item in guba.parse_list_page(html, request["stock"]).rows},
                                           {str(row["post_id"]): row for row in guba._embedded_json(html, "article_list")["re"]})
                while len(parsed_lists) > 4:
                    parsed_lists.popitem(last=False)
            parsed_lists.move_to_end(request_id)
            return parsed_lists[request_id]
        for post in old.execute("SELECT rowid AS preserved_rowid,* FROM posts ORDER BY rowid"):
            state = new.execute("SELECT rowid AS preserved_rowid,* FROM http_post_state WHERE post_id=?", (post["post_id"],)).fetchone()
            if state is None or any(state[name] != post[name] for name in ("preserved_rowid", "post_id", "item", "source_row", "status", "list_request", "detail_request")):
                raise MigrationError("来源帖子状态/ID/list/detail 关联没有完整保留")
            previous = json.loads(post["detail_payload"]) if post["detail_payload"] else None
            if previous is not None:
                previous.pop("post_content", None)
            current = json.loads(state["detail_payload"]) if state["detail_payload"] else None
            if previous != current:
                raise MigrationError("详情 metadata 变化超过移除重复正文的范围")
            expected_source = post["content_source"] if "content_source" in post.keys() and post["content_source"] else ("detail_body" if post["status"] == "complete" else "list_title")
            if state["content_source"] != expected_source:
                raise MigrationError("正文来源 metadata 没有保留")
            _, initial_items, initial_rows = list_items(post["list_request"])
            initial_item = initial_items.get(post["post_id"])
            captured_item = json.loads(post["item"])
            if initial_item is None or json.loads(post["source_row"]) != initial_rows.get(post["post_id"]):
                raise MigrationError("来源帖子 state.source_row 与初始原始响应不一致")
            initial_fields = json.loads(json.dumps(asdict(initial_item), default=lambda value: value.isoformat()))
            if any(captured_item.get(name) != value for name, value in initial_fields.items() if name != "source_metadata"):
                raise MigrationError("来源帖子 state.item 字段与初始原始响应不一致")
            body = new.execute("SELECT * FROM posts WHERE source='eastmoney_guba' AND source_item_id=?", (post["post_id"],)).fetchone()
            expected_body = post["content"] if post["status"] == "complete" else None
            if body is None or body["content"] != expected_body:
                raise MigrationError("规范 posts 正文缺失/空串/真实正文不一致")
            latest = new.execute("SELECT request_id FROM observations WHERE post_id=? ORDER BY id DESC LIMIT 1", (post["post_id"],)).fetchone()
            list_id = latest[0] if latest else post["list_request"]
            list_request, items, _ = list_items(list_id)
            item = items.get(post["post_id"])
            if item is None or any(body[name] != getattr(item, name) for name in ("title", "author_id", "author_name", "url", "read_count", "reply_count", "like_count", "forward_count")):
                raise MigrationError("规范 posts 字段与留存列表原始证据不一致")
            if body["stock_code"] != list_request["stock"] or body["published_at"] != item.published_at.isoformat().replace("+00:00", "Z"):
                raise MigrationError("规范 posts 股吧归属/发布时间与原始证据不一致")
            if expected_body is not None:
                _, detail_html = request_html(post["detail_request"])
                if guba.merge_list_and_detail(item, guba.parse_detail_page(detail_html))["content"] != expected_body:
                    raise MigrationError("规范 posts 正文与原始详情证据不一致")
            body_count += expected_body is not None
        if legacy_collector is not None:
            with _readonly(legacy_collector) as legacy:
                for table in ("backfill_resume", "backfill_coverage", "backfill_page_anchors"):
                    if _row_manifest(legacy, table) != _row_manifest(new, table, _columns(legacy, table)):
                        raise MigrationError(f"原兼容库 {table} 数据没有完整保留")
                for source_name, post_id, content, created in legacy.execute("SELECT source,source_item_id,content,created_at FROM posts"):
                    copied = new.execute("SELECT content,created_at FROM posts WHERE source=? AND source_item_id=?", (source_name, post_id)).fetchone()
                    if copied is None or copied[1] != created or content is not None and copied[0] != content:
                        raise MigrationError("原兼容帖子身份/已有正文/首次采集时间没有保留")
        if skips is not None:
            with _readonly(skips) as original:
                columns = _columns(original, "detail_enrichment_skips")
                # Helper may add missing source-unavailable evidence; every old
                # skip row including attempts/first_seen must remain identical.
                for row in original.execute("SELECT * FROM detail_enrichment_skips"):
                    actual = new.execute(f"SELECT {','.join(_quote(c) for c in columns)} FROM detail_enrichment_skips WHERE source=? AND source_item_id=?", row[:2]).fetchone()
                    if actual is None or tuple(actual) != row:
                        raise MigrationError("原详情跳过记录没有完整保留")
        return {"instance_id": identity, "state_posts": state_count, "body_complete": body_count,
                "ledger_counts": counts, **raw}


def _checkpoint(path):
    if path is None or not path.exists():
        return
    with closing(sqlite3.connect(path)) as db:
        result = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if result[0]:
            raise MigrationError("旧兼容库仍有 SQLite 使用者，不能安全发布")
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm")):
        raise MigrationError("旧兼容库 WAL/SHM 尚未释放，不能安全发布")
    with open(path, "rb") as file:
        os.fsync(file.fileno())


@contextmanager
def _worker_lock(directory):
    path = directory / "worker.lock"
    if path.is_symlink():
        raise MigrationError("worker.lock 不能是符号链接")
    with open(path, "a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MigrationError("worker 正在运行；请先停止服务再迁移") from exc
        yield


def _paths(directory):
    for name in ("collector.db", "experiment.sqlite3", "collector.detail_enrichment_skips.db", RECEIPT_NAME,
                 "collector.db-wal", "collector.db-shm", "experiment.sqlite3-wal", "experiment.sqlite3-shm", "migration-backups"):
        path = directory / name
        if path.is_symlink() or path.exists() and not (path.is_dir() if name == "migration-backups" else path.is_file()):
            raise MigrationError(f"迁移路径 {name} 为符号链接/错误文件类型")


def _receipt_paths(directory, receipt):
    if receipt.get("version") != RECEIPT_VERSION or receipt.get("phase") not in {"prepared", "published", "complete"}:
        raise MigrationError("迁移 receipt 契约/阶段无效")
    result = {}
    stage = receipt.get("stage")
    if (not isinstance(stage, str) or Path(stage).name != stage
            or not stage.startswith(".collector-migration-") or not stage.endswith(".sqlite3")):
        raise MigrationError("迁移 staging 必须为本采集目录的专用单一文件名")
    base = (directory / "migration-backups").resolve()
    for name, reference in receipt["backups"].items():
        original = directory / reference
        path = original.resolve()
        if original.is_symlink() or not path.is_relative_to(base) or not path.is_file():
            raise MigrationError("迁移备份路径越界/缺失")
        result[name] = path
    return result


def _delete_legacy(directory, receipt, fault):
    receipt["phase"] = "published"
    _atomic_json(directory / RECEIPT_NAME, receipt)
    fault("after_published_receipt")
    source = directory / "experiment.sqlite3"
    if source.exists():
        source.unlink()
        _fsync_directory(directory)
    fault("after_legacy_unlink")
    for suffix in ("-wal", "-shm"):
        sidecar = directory / f"experiment.sqlite3{suffix}"
        if sidecar.exists():
            sidecar.unlink()
    _fsync_directory(directory)
    receipt["phase"] = "complete"
    receipt["completed_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_json(directory / RECEIPT_NAME, receipt)
    return {"status": "migrated", "storage_layout": "unified.v1", "database": str(directory / "collector.db"),
            "backup_directory": str(directory / receipt["backup_directory"]), "receipt": str(directory / RECEIPT_NAME),
            "experiment_removed": True, **receipt["validation"]}


def migrate(data_dir, *, fault=None):
    """Perform or finish one stopped-worker migration; ``fault`` is test-only."""
    original = Path(data_dir)
    if original.is_symlink():
        raise MigrationError("采集目录不能为符号链接")
    directory = original.resolve()
    if directory.is_relative_to((REPO / "data").resolve()):
        raise MigrationError("拒绝迁移仓库生产 data 目录")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fault = fault or (lambda point: None)
    with _worker_lock(directory):
        _paths(directory)
        source, target, receipt_path = directory / "experiment.sqlite3", directory / "collector.db", directory / RECEIPT_NAME
        receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
        if not source.exists():
            if target.exists():
                from unified_store import validate_layout
                with _readonly(target) as db:
                    identity = validate_layout(db)
                    _integrity(db)
                if receipt:
                    backups = _receipt_paths(directory, receipt)
                    if receipt["validation"]["instance_id"] != identity:
                        raise MigrationError("已发布实例与迁移 receipt 不符")
                    if receipt["phase"] == "complete" and any((directory / f"experiment.sqlite3{x}").exists() for x in ("-wal", "-shm")):
                        raise MigrationError("已完成迁移后出现未知旧 WAL/SHM，拒绝删除")
                    if receipt["phase"] != "complete":
                        if _manifest(target) != receipt["target_manifest"]:
                            raise MigrationError("未完成迁移的统一库与已验证 staging 不符；旧 sidecars 保留")
                        _validate_conversion(backups["experiment"], target, directory, backups.get("collector"), backups.get("skips"))
                        result = _delete_legacy(directory, receipt, fault)
                        result["status"] = "migration_completed"
                        return result
                elif any((directory / f"experiment.sqlite3{x}").exists() for x in ("-wal", "-shm")):
                    raise MigrationError("孤立旧 WAL/SHM 缺少已验证迁移 receipt，拒绝删除")
                return {"status": "already_unified", "storage_layout": "unified.v1", "database": str(target), "instance_id": identity,
                        "experiment_removed": True, "receipt": str(receipt_path) if receipt else None}
            if receipt or any((directory / f"experiment.sqlite3{x}").exists() for x in ("-wal", "-shm")):
                raise MigrationError("迁移状态残缺，缺少可验证数据库；旧证据未删除")
            return {"status": "fresh_directory", "experiment_removed": False, "database": str(target)}
        if receipt:
            backups = _receipt_paths(directory, receipt)
            if receipt["phase"] == "complete":
                raise MigrationError("已完成迁移后再次出现旧台账，拒绝删除未知文件")
            if _manifest(source) != receipt["source_manifest"]:
                raise MigrationError("旧台账已变化，与迁移备份不符；拒绝删除")
            final = _manifest(target) if target.exists() else None
            if final == receipt["target_manifest"]:
                _validate_conversion(backups["experiment"], target, directory, backups.get("collector"), backups.get("skips"))
                return _delete_legacy(directory, receipt, fault)
            if receipt["phase"] == "published":
                raise MigrationError("已发布统一库发生意外变化，旧台账保留")
            if final != receipt.get("legacy_collector_manifest"):
                raise MigrationError("目标库与迁移前状态不符，拒绝覆盖")
            stage = directory / receipt["stage"]
            if stage.is_symlink() or not stage.is_file() or _manifest(stage) != receipt["target_manifest"]:
                raise MigrationError("已验证 staging 缺失/变化；旧数据库保留")
            _validate_conversion(backups["experiment"], stage, directory, backups.get("collector"), backups.get("skips"))
            _checkpoint(target if target.exists() else None)
            os.replace(stage, target)
            _fsync_directory(directory)
            fault("after_publish")
            return _delete_legacy(directory, receipt, fault)
        _check_legacy(source, target if target.exists() else None)
        with _readonly(source) as db:
            _verify_raw(db, directory)
        run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex
        backup_base = directory / "migration-backups"
        backup_base.mkdir(mode=0o700, exist_ok=True)
        backup_dir = backup_base / run
        backup_dir.mkdir(mode=0o700)
        snapshots = {"experiment": backup_dir / "experiment.sqlite3"}
        _backup(source, snapshots["experiment"])
        if target.exists():
            snapshots["collector"] = backup_dir / "collector.db"
            _backup(target, snapshots["collector"])
        skips = directory / "collector.detail_enrichment_skips.db"
        if skips.exists():
            snapshots["skips"] = backup_dir / skips.name
            _backup(skips, snapshots["skips"])
        fault("after_backups")
        stage = directory / f".collector-migration-{uuid.uuid4().hex}.sqlite3"
        _backup(snapshots["experiment"], stage)
        _copy_skip(snapshots.get("skips"), stage)
        from unified_store import convert_legacy_snapshot
        convert_legacy_snapshot(stage, snapshots.get("collector"), data_dir=directory)
        _checkpoint(stage)
        validation = _validate_conversion(snapshots["experiment"], stage, directory, snapshots.get("collector"), snapshots.get("skips"))
        source_manifest = _manifest(snapshots["experiment"])
        original_target = _manifest(snapshots["collector"]) if "collector" in snapshots else None
        if _manifest(source) != source_manifest or (target.exists() and _manifest(target) != original_target):
            raise MigrationError("停止期间仍有数据库写入，拒绝发布/删除")
        receipt = {"version": RECEIPT_VERSION, "phase": "prepared", "created_at": datetime.now(timezone.utc).isoformat(),
                   "stage": stage.name, "backup_directory": str(backup_dir.relative_to(directory)),
                   "backups": {k: str(v.relative_to(directory)) for k, v in snapshots.items()},
                   "source_manifest": source_manifest, "legacy_collector_manifest": original_target,
                   "target_manifest": _manifest(stage), "validation": validation}
        _atomic_json(receipt_path, receipt)
        fault("after_prepared_receipt")
        _checkpoint(target if target.exists() else None)
        os.replace(stage, target)
        _fsync_directory(directory)
        fault("after_publish")
        if _manifest(target) != receipt["target_manifest"]:
            raise MigrationError("原子发布后的统一库与 staging 不一致；旧台账保留")
        _validate_conversion(snapshots["experiment"], target, directory, snapshots.get("collector"), snapshots.get("skips"))
        return _delete_legacy(directory, receipt, fault)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    try:
        print(_json(migrate(args.data_dir)))
    except (MigrationError, ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
        print(_json({"status": "error", "error": f"{type(exc).__name__}: {exc}", "experiment_removed": False}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
