#!/usr/bin/env python3
"""Explicit, offline imports into the original training posts database."""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import uuid
import zipfile
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from data_export import COLUMNS, EXTRA
from transfer import MAX_RECORD, TransferError, _loads, _verified_bundle, export_bundle
from federation import FederationError

DEFAULT_TARGET = HERE.parents[1] / "data" / "collector.db"
IDENTITY_FIELDS = ("stock_code", "published_at", "url", "author_id", "author_name", "title")
CONFLICT_FIELDS = ("content",)
IMPORT_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS collector_import_runs(
        import_id TEXT PRIMARY KEY, imported_at TEXT NOT NULL, input_kind TEXT NOT NULL,
        input_sha256 TEXT NOT NULL, input_path TEXT NOT NULL, instance_id TEXT NOT NULL,
        evidence_path TEXT NOT NULL, backup_path TEXT NOT NULL, summary TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS collector_import_versions(
        instance_id TEXT NOT NULL, sequence INTEGER NOT NULL, evidence_sha256 TEXT NOT NULL,
        source TEXT NOT NULL, source_item_id TEXT NOT NULL, input_kind TEXT NOT NULL,
        payload TEXT NOT NULL, import_id TEXT NOT NULL,
        PRIMARY KEY(instance_id,sequence))""",
    """CREATE TABLE IF NOT EXISTS collector_import_conflicts(
        import_id TEXT NOT NULL, source TEXT NOT NULL, source_item_id TEXT NOT NULL,
        field TEXT NOT NULL, existing_value TEXT NOT NULL, incoming_value TEXT NOT NULL,
        blocks_update INTEGER NOT NULL,
        PRIMARY KEY(import_id,source,source_item_id,field))""",
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _path(value):
    path = Path(value).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("导入路径及其父目录不能使用符号链接")
    return path.resolve()


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("发布时间和采集时间必须包含时区")
    return parsed


def _validate_post(post):
    if not isinstance(post, dict) or set(post) != set(COLUMNS):
        raise ValueError("输入帖子必须包含原 posts 表的 15 个字段")
    required = {"source", "source_item_id", "stock_code", "published_at", "url", "created_at", "updated_at"}
    counts = {"read_count", "reply_count", "like_count", "forward_count"}
    for name, value in post.items():
        if name in counts:
            if value is not None and (type(value) is not int or not 0 <= value < 2**63):
                raise ValueError(f"{name} 必须为非负整数或 NULL")
        elif value is not None and not isinstance(value, str):
            raise ValueError(f"{name} 必须为字符串或 NULL")
        if name in required and not value:
            raise ValueError(f"{name} 不能为空")
    if post["source"] != "eastmoney_guba" or not re.fullmatch(r"[0-9]+", post["source_item_id"]):
        raise ValueError("输入必须为股吧原始帖子 ID")
    if not re.fullmatch(r"[A-Za-z0-9]+", post["stock_code"]):
        raise ValueError("stock_code 格式无效")
    url = urlparse(post["url"])
    if (url.scheme != "https" or url.hostname != "guba.eastmoney.com" or url.port not in (None, 443)
            or url.username or url.password or url.query or url.fragment
            or not re.fullmatch(r"/news,[A-Za-z0-9]+," + re.escape(post["source_item_id"]) + r"\.html", url.path)):
        raise ValueError("帖子 URL 与股吧原始 ID 不一致")
    for name in ("published_at", "created_at", "updated_at"):
        _time(post[name])
    if _time(post["created_at"]) > _time(post["updated_at"]):
        raise ValueError("帖子更新时间早于首次采集时间")


class StagedInput:
    def __init__(self, db, kind, path, digest, identity, manifest=None, bundle=None):
        self.db, self.kind, self.path = db, kind, path
        self.digest, self.identity, self.manifest, self.bundle = digest, identity, manifest, bundle

    def add(self, sequence, digest, post, payload):
        _validate_post(post)
        self.db.execute("INSERT INTO versions VALUES(?,?,?,?,?,?)",
                        (sequence, digest, post["source"], post["source_item_id"], _json(post), _json(payload)))
        self.db.execute("INSERT INTO latest VALUES(?,?,?) ON CONFLICT(source,id) DO UPDATE SET sequence=excluded.sequence",
                        (post["source"], post["source_item_id"], sequence))

    def posts(self):
        for row in self.db.execute("""SELECT v.post FROM latest l JOIN versions v ON v.sequence=l.sequence
                                      ORDER BY l.source,l.id"""):
            yield json.loads(row[0])


@contextmanager
def _stage(path, kind):
    path = _path(path)
    if not path.is_file():
        raise ValueError("导入文件不存在")
    with tempfile.TemporaryDirectory(prefix="collector-training-import-", dir=Path(tempfile.gettempdir()).resolve()) as directory, closing(
        sqlite3.connect(Path(directory) / "stage.sqlite3")
    ) as db:
        db.executescript("""CREATE TABLE versions(sequence INTEGER PRIMARY KEY,digest TEXT,source TEXT,id TEXT,post TEXT,payload TEXT);
                            CREATE TABLE latest(source TEXT,id TEXT,sequence INTEGER,PRIMARY KEY(source,id));""")
        # Keep an immutable private copy: preflight, digest and retained input
        # must refer to the same bytes even if the caller replaces their file.
        captured = Path(directory) / ("input.zip" if kind == "bundle" else "input.jsonl")
        shutil.copyfile(path, captured)
        digest = _digest(captured)
        if kind == "bundle":
            with _verified_bundle(captured) as verified:
                staged = StagedInput(db, kind, captured, digest, verified.manifest["instance_id"],
                                     verified.manifest, verified)
                for record in verified.records():
                    staged.add(record["seq"], record["evidence_sha256"], record["post"], record)
                db.commit()
                yield staged
        else:
            staged = StagedInput(db, kind, captured, digest, "jsonl:" + digest)
            with captured.open("rb") as stream:
                sequence = 0
                while True:
                    line = stream.readline(MAX_RECORD + 1)
                    if not line:
                        break
                    if len(line) > MAX_RECORD:
                        raise ValueError("JSONL 单条记录超过大小限制")
                    sequence += 1
                    item = _loads(line)
                    if not isinstance(item, dict) or set(item) - set(COLUMNS) - set(EXTRA):
                        raise ValueError(f"JSONL 第 {sequence} 行包含未知字段")
                    if "content_missing" in item and (type(item["content_missing"]) is not bool
                                                      or item["content_missing"] != (item.get("content") is None)):
                        raise ValueError("content_missing 与真实 NULL/空串不一致")
                    for field in ("research_only", "model_database_eligible"):
                        if field in item and type(item[field]) is not bool:
                            raise ValueError(f"{field} 必须为布尔值")
                    post = {key: item[key] for key in COLUMNS}
                    if db.execute("SELECT 1 FROM latest WHERE source=? AND id=?",
                                  (post["source"], post["source_item_id"])).fetchone():
                        raise ValueError("JSONL 导出包含重复帖子 ID，拒绝猜测版本顺序")
                    staged.add(sequence, hashlib.sha256(_json(item).encode()).hexdigest(), post, item)
            db.commit()
            yield staged


def _schema(db):
    info = db.execute("PRAGMA table_info(posts)").fetchall()
    if (tuple(row[1] for row in info) != COLUMNS
            or tuple(row[1] for row in sorted(info, key=lambda r: r[5]) if row[5]) != ("source", "source_item_id")):
        raise ValueError("目标必须是原有 posts 结构和 (source,source_item_id) 主键的 collector.db")
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if tables & {"http_posts", "requests", "compatible_posts", "fleet_meta", "versions"}:
        raise ValueError("目标是 HTTP 运行库或 fleet 库；请指定训练使用的原 collector.db")
    return tables


def _different(name, old, new):
    return _time(old) != _time(new) if name == "published_at" else old != new


def _decision(old, incoming):
    if old is None:
        return "insert", {}, []
    conflicts = []
    for field in IDENTITY_FIELDS + CONFLICT_FIELDS:
        a, b = old[field], incoming[field]
        if a is not None and b is not None and _different(field, a, b):
            conflicts.append((field, _json(a), _json(b), int(field in IDENTITY_FIELDS)))
    if any(row[3] for row in conflicts):
        return "blocked", {}, conflicts
    changes = {field: incoming[field] for field in COLUMNS
               if field not in {"source", "source_item_id", "created_at", "updated_at"}
               and old[field] is None and incoming[field] is not None}
    if changes:
        # Original acquisition times are not replaced by this import's time.
        if _time(incoming["updated_at"]) > _time(old["updated_at"]):
            changes["updated_at"] = incoming["updated_at"]
        return "update", changes, conflicts
    return "unchanged", {}, conflicts


def _plan(db, staged, tables):
    counts = {key: 0 for key in ("source_posts", "source_bodies", "new_posts", "updated_posts", "bodies_added",
                                 "unchanged_posts", "identity_blocked_posts", "conflicted_posts", "conflict_fields",
                                 "new_evidence_versions")}
    for incoming in staged.posts():
        counts["source_posts"] += 1
        counts["source_bodies"] += int(incoming["content"] is not None)
        old = db.execute("SELECT * FROM posts WHERE source=? AND source_item_id=?",
                         (incoming["source"], incoming["source_item_id"])).fetchone()
        action, changes, conflicts = _decision(old, incoming)
        counts[{"insert": "new_posts", "update": "updated_posts", "unchanged": "unchanged_posts",
                "blocked": "identity_blocked_posts"}[action]] += 1
        counts["bodies_added"] += int("content" in changes)
        counts["conflicted_posts"] += int(bool(conflicts))
        counts["conflict_fields"] += len(conflicts)
    for sequence, digest in staged.db.execute("SELECT sequence,digest FROM versions"):
        old = db.execute("SELECT evidence_sha256 FROM collector_import_versions WHERE instance_id=? AND sequence=?",
                         (staged.identity, sequence)).fetchone() if "collector_import_versions" in tables else None
        if old is not None and old[0] != digest:
            raise ValueError("已导入的实例/序号出现不同证据，拒绝覆盖不可变版本")
        counts["new_evidence_versions"] += int(old is None)
    counts["evidence_records"] = staged.db.execute("SELECT count(*) FROM versions").fetchone()[0]
    counts["target_posts_before"] = db.execute("SELECT count(*) FROM posts").fetchone()[0]
    counts["target_posts_after"] = counts["target_posts_before"] + counts["new_posts"]
    return counts


def _publish(source, destination):
    destination = _path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not destination.is_file() or _digest(source) != _digest(destination):
            raise ValueError("已有导入证据文件与输入不同")
        return
    fd, name = tempfile.mkstemp(prefix=".evidence-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as stream:
            shutil.copyfileobj(stream, output)
            output.flush()
            os.fsync(output.fileno())
        os.link(name, destination)
        dirfd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        Path(name).unlink(missing_ok=True)


def import_file(path, target, *, kind="bundle", dry_run=False, source_dir=None):
    original, target = _path(path), _path(target)
    if not target.is_file() or original == target:
        raise ValueError("目标必须为已存在的训练数据库，且不能作为输入")
    if (target.parent / "merge.sqlite3").exists():
        raise ValueError("目标旁有 fleet 汇总台账 merge.sqlite3；请指定训练使用的原 collector.db")
    with _stage(original, kind) as staged:
        connection = sqlite3.connect(target.as_uri() + ("?mode=ro" if dry_run else "?mode=rw"),
                                     uri=True, timeout=30, isolation_level=None)
        with closing(connection) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN" if dry_run else "BEGIN IMMEDIATE")
            try:
                tables = _schema(db)
                summary = _plan(db, staged, tables)
                summary.update(target_db=str(target), input_path=str(original), input_kind=kind,
                               input_sha256=staged.digest, instance_id=staged.identity, dry_run=dry_run,
                               coverage_modified=False, linked_raw_supplied=kind == "bundle")
                if source_dir is not None:
                    summary.update(source_dir=str(_path(source_dir)), input_path=str(_path(source_dir)))
                if staged.manifest:
                    summary["unexported_source_posts"] = staged.manifest["unexported_source_posts"]
                needs_write = bool(summary["new_posts"] or summary["updated_posts"] or summary["new_evidence_versions"])
                if dry_run or not needs_write:
                    db.rollback()
                    return summary | {"applied": False, "no_changes": not needs_write}
                imported = datetime.now(timezone.utc)
                import_id = imported.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
                backup = _path(target.parent / (target.name + ".import-backups") / (import_id + ".db"))
                backup.parent.mkdir(parents=True, exist_ok=True)
                # This writer holds RESERVED before any mutation. A separate
                # read connection can back up the committed database, including
                # WAL, while other writers queue rather than changing it.
                with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as before, closing(
                    sqlite3.connect(backup)
                ) as copy:
                    before.backup(copy)
                with backup.open("rb") as stream:
                    os.fsync(stream.fileno())
                backup.chmod(0o600)
                directory_fd = os.open(backup.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                evidence = _path(target.parent / (target.name + ".imports") / "inputs" /
                                 (staged.digest + (".zip" if kind == "bundle" else ".jsonl")))
                _publish(staged.path, evidence)
                summary.update(import_id=import_id, imported_at=imported.isoformat(), backup_path=str(backup),
                               evidence_path=str(evidence), applied=True, no_changes=False)
                for statement in IMPORT_SCHEMA:
                    db.execute(statement)
                for incoming in staged.posts():
                    key = (incoming["source"], incoming["source_item_id"])
                    old = db.execute("SELECT * FROM posts WHERE source=? AND source_item_id=?", key).fetchone()
                    action, changes, conflicts = _decision(old, incoming)
                    if action == "insert":
                        db.execute("INSERT INTO posts(" + ",".join(COLUMNS) + ") VALUES(" +
                                   ",".join("?" for _ in COLUMNS) + ")", tuple(incoming[c] for c in COLUMNS))
                    elif action == "update":
                        db.execute("UPDATE posts SET " + ",".join(field + "=?" for field in changes) +
                                   " WHERE source=? AND source_item_id=?", (*changes.values(), *key))
                    for field, a, b, blocked in conflicts:
                        db.execute("INSERT INTO collector_import_conflicts VALUES(?,?,?,?,?,?,?)",
                                   (import_id, *key, field, a, b, blocked))
                for sequence, digest, source, pid, _, payload in staged.db.execute("SELECT * FROM versions"):
                    db.execute("INSERT OR IGNORE INTO collector_import_versions VALUES(?,?,?,?,?,?,?,?)",
                               (staged.identity, sequence, digest, source, pid, kind, payload, import_id))
                db.execute("INSERT INTO collector_import_runs VALUES(?,?,?,?,?,?,?,?,?)",
                           (import_id, summary["imported_at"], kind, staged.digest, summary["input_path"], staged.identity,
                            str(evidence), str(backup), _json(summary)))
                db.commit()
                return summary
            except BaseException:
                db.rollback()
                raise


def main(argv=None):
    parser = argparse.ArgumentParser(description="将节点帖子直接合入训练 collector.db；自动备份、去重、补正文并保留来源")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--bundle", type=Path, help="节点 transfer.py 导出的含 raw 证据 ZIP")
    source.add_argument("--source-dir", type=Path, help="本地节点数据目录；只读快照导出后直接导入")
    source.add_argument("--jsonl", type=Path, help="已有 H5 JSONL 导出；保留原文件，未包含 raw")
    parser.add_argument("--target-db", type=Path, default=DEFAULT_TARGET, help="默认仓库根目录 data/collector.db")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不写目标库、不创建备份")
    args = parser.parse_args(argv)
    try:
        if args.source_dir:
            with tempfile.TemporaryDirectory(prefix="collector-node-snapshot-", dir=Path(tempfile.gettempdir()).resolve()) as directory:
                bundle = Path(directory) / "node.zip"
                export_bundle(args.source_dir, bundle)
                result = import_file(bundle, args.target_db, dry_run=args.dry_run, source_dir=args.source_dir)
        else:
            result = import_file(args.bundle or args.jsonl, args.target_db,
                                 kind="bundle" if args.bundle else "jsonl", dry_run=args.dry_run)
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, zipfile.BadZipFile, FederationError, TransferError) as exc:
        print(_json({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
