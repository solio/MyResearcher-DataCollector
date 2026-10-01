#!/usr/bin/env python3
"""Offline, verified collector bundles; no worker construction or source traffic.

export reads a consistent committed v5 snapshot while the collector runs.
merge targets an isolated fleet root, and refuses a running worker there.
"""
from __future__ import annotations

import argparse
import base64
from collections import OrderedDict
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile
import threading
import uuid
import zipfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "src"))
from federation import FederationError, MergeStore, SCHEMA_VERSION, _hash, _json, export_page, export_raw
from unified_store import validate_layout

BUNDLE_VERSION = "http-backfill.bundle.v1"
MAX_RAW = 16 * 1024 * 1024
MAX_RECORD = 30 * 1024 * 1024 + 1024
MAX_MANIFEST = 32 * 1024 * 1024
MAX_UNCOMPRESSED = 32 * 1024 * 1024 * 1024
MAX_MEMBERS = 250_002
PRODUCTION_DATA = HERE.parents[1] / "data"
_CREDENTIAL_KEYS = {"token", "console-token", "authorization", "proxy-authorization", "cookie", "set-cookie",
                    "password", "secret", "api-key", "access-token", "refresh-token", "credentials"}


class TransferError(RuntimeError):
    """A local package or destination failed validation; nothing is fetched."""


def _integer(value, name, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise TransferError(f"{name} 必须是 {minimum}..{maximum} 的整数")
    return value


def _path(value):
    path = Path(value).absolute()
    # Reject links before resolve(), including parent-directory links.
    for part in (path, *path.parents):
        if part.is_symlink():
            raise TransferError("路径及其父目录不能使用符号链接")
    path = path.resolve()
    if path == PRODUCTION_DATA.resolve() or path.is_relative_to(PRODUCTION_DATA.resolve()):
        raise TransferError("不能读取或写入仓库生产 data 目录")
    return path


def _loads(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise TransferError("JSON 含重复字段")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(TransferError("JSON 含非有限数值")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise TransferError("证据必须是完整 UTF-8 JSON") from exc


def _no_credentials(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower().replace("_", "-") in _CREDENTIAL_KEYS:
                raise TransferError("导出证据含凭据字段，拒绝传输")
            _no_credentials(child)
    elif isinstance(value, list):
        for child in value:
            _no_credentials(child)


@contextmanager
def _snapshot(data_dir):
    directory = _path(data_dir)
    if not directory.is_dir():
        raise TransferError("采集数据目录不存在")
    path = _path(directory / "collector.db")
    if not path.is_file():
        raise TransferError("需要已迁移的 v5 collector.db；请先完成存储迁移")
    for name in ("collector.db-wal", "collector.db-shm", "raw", "storage-migration.json"):
        _path(directory / name)
    receipt = directory / "storage-migration.json"
    if receipt.exists() and _loads(receipt.read_bytes()).get("phase") != "complete":
        raise TransferError("存储迁移尚未完成")
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        identity = validate_layout(db)
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='export_journal'").fetchone() is None:
            raise TransferError("缺少已提交的 export_journal；请先升级并修复本地投影")
        class Reader:
            _mutex = threading.RLock()
            data_dir = directory
            def _get(self, key, default=None):
                row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
                return _loads(row[0]) if row else default
        reader = Reader()
        reader.db = db
        reader.instance_id = identity
        yield reader


class _Index:
    def __init__(self, directory):
        self.db = sqlite3.connect(Path(directory) / "index.sqlite3")
        self.db.executescript("""CREATE TABLE requests(id INTEGER PRIMARY KEY,sha256 TEXT,size INTEGER,descriptor_sha TEXT);
          CREATE TABLE raw(sha256 TEXT PRIMARY KEY,size INTEGER);
          CREATE TABLE posts(source TEXT,id TEXT,body INTEGER,PRIMARY KEY(source,id));""")

    def record(self, record, identity, sequence):
        if (not isinstance(record, dict) or record.get("instance_id") != identity
                or record.get("schema_version") != SCHEMA_VERSION
                or type(record.get("seq")) is not int or record["seq"] != sequence
                or record.get("coverage_complete") is not False or record.get("model_database_eligible") is not False):
            raise TransferError("导出记录实例/契约/连续序列/局部数据标记不一致")
        _no_credentials(record)
        payload = {k: v for k, v in record.items() if k not in {"seq", "evidence_sha256"}}
        if _hash(payload) != record.get("evidence_sha256"):
            raise TransferError("不可变导出记录 SHA-256 不一致")
        requests = record.get("requests")
        if not isinstance(requests, list) or not 1 <= len(requests) <= 3:
            raise TransferError("原请求描述集合无效")
        for descriptor in requests:
            rid = _integer(descriptor["request_id"], "request_id", 1)
            digest, size = descriptor["sha256"], _integer(descriptor["response_bytes"], "response_bytes", 0, MAX_RAW)
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise TransferError("raw SHA-256 格式无效")
            old = self.db.execute("SELECT sha256,size,descriptor_sha FROM requests WHERE id=?", (rid,)).fetchone()
            expected = (digest, size, _hash(descriptor))
            if old is not None and old != expected:
                raise TransferError("同一实例原请求描述发生冲突")
            self.db.execute("INSERT OR IGNORE INTO requests VALUES(?,?,?,?)", (rid, *expected))
            prior_size = self.db.execute("SELECT size FROM raw WHERE sha256=?", (digest,)).fetchone()
            if prior_size and prior_size[0] != size:
                raise TransferError("同一 raw 哈希对应不同字节数")
            self.db.execute("INSERT OR IGNORE INTO raw VALUES(?,?)", (digest, size))
        post = record["post"]
        self.db.execute("INSERT INTO posts VALUES(?,?,?) ON CONFLICT(source,id) DO UPDATE SET body=excluded.body",
                        (post["source"], post["source_item_id"], int(post["content"] is not None)))

    def counts(self):
        return {"unique_posts": self.db.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
                "body_complete": self.db.execute("SELECT COALESCE(SUM(body),0) FROM posts").fetchone()[0],
                "raw_responses": self.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0],
                "raw_files": self.db.execute("SELECT COUNT(*) FROM raw").fetchone()[0]}

    def close(self):
        self.db.close()


def _zip_info(name):
    info = zipfile.ZipInfo(name)
    info.compress_type, info.create_system = zipfile.ZIP_DEFLATED, 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    return info


def _member(name, body):
    return {"path": name, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def _fsync_directory(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def export_bundle(data_dir, output):
    """Publish a new private ZIP only after complete byte/source validation."""
    destination = _path(output)
    if destination.suffix.lower() != ".zip" or destination.exists():
        raise TransferError("output 必须是尚不存在的 .zip 文件，不会覆盖旧包")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".collector-export-", suffix=".zip", dir=destination.parent)
    os.close(fd)
    temporary = Path(temporary)
    published = False
    try:
        with tempfile.TemporaryDirectory(prefix="collector-export-index-") as directory, closing(_Index(directory)) as index:
            with _snapshot(data_dir) as reader, zipfile.ZipFile(temporary, "w", allowZip64=True) as archive:
                first = export_page(reader, limit=100)
                snapshot, identity = first["snapshot"], reader.instance_id
                post_rows = reader.db.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
                digest, size, after = hashlib.sha256(), 0, 0
                with archive.open(_zip_info("records.jsonl"), "w", force_zip64=True) as out:
                    page = first
                    while True:
                        for record in page["items"]:
                            index.record(record, identity, after + 1)
                            data = (_json(record) + "\n").encode()
                            if len(data) > MAX_RECORD:
                                raise TransferError("单条导出记录超出传输上限")
                            out.write(data)
                            digest.update(data)
                            size += len(data)
                            after += 1
                        if not page["has_more"]:
                            break
                        if page["next_after"] != after or not page["items"]:
                            raise TransferError("导出序列存在间隙")
                        page = export_page(reader, after=after, limit=100, snapshot=snapshot)
                if after != snapshot:
                    raise TransferError("导出序列未达到固定快照")
                members = [{"path": "records.jsonl", "bytes": size, "sha256": digest.hexdigest()}]
                saved = set()
                for rid, raw_sha, raw_size in index.db.execute("SELECT id,sha256,size FROM requests ORDER BY id"):
                    source = reader.db.execute("SELECT raw_ref FROM requests WHERE id=?", (rid,)).fetchone()
                    if source is None or not isinstance(source[0], str):
                        raise TransferError("来源 raw 引用缺失")
                    raw_path = _path(reader.data_dir / source[0])
                    if not raw_path.is_relative_to(reader.data_dir / "raw"):
                        raise TransferError("来源 raw 引用越界")
                    raw = export_raw(reader, rid)
                    if raw["sha256"] != raw_sha or raw["response_bytes"] != raw_size:
                        raise TransferError("不可变记录与来源请求 raw 描述不一致")
                    if raw_sha not in saved:
                        body = base64.b64decode(raw["body_base64"], validate=True)
                        name = f"raw/{raw_sha}.body"
                        archive.writestr(_zip_info(name), body)
                        members.append(_member(name, body))
                        saved.add(raw_sha)
                counts = index.counts()
                unexported = post_rows - counts["unique_posts"]
                if unexported < 0:
                    raise TransferError("已拥有导出版本超过当前 posts 身份数量，需先检查本地存储")
                manifest = {"bundle_schema": BUNDLE_VERSION, "export_schema": SCHEMA_VERSION, "instance_id": identity,
                            "after": 0, "snapshot": snapshot, "record_count": snapshot,
                            "created_at": datetime.now(timezone.utc).isoformat(), "members": members,
                            "export_sequence_complete": True, "coverage_complete": False,
                            "dataset_complete": False, "model_database_eligible": False,
                            "scope": "post_evidence_export_sequence",
                            "counts_scope": "latest_version_per_source_post_in_bundle",
                            "source_posts_at_snapshot": post_rows, "unexported_source_posts": unexported, "counts": counts}
                data = _json(manifest).encode()
                if len(data) > MAX_MANIFEST:
                    raise TransferError("包清单超出传输上限")
                archive.writestr(_zip_info("manifest.json"), data)
            with _verified_bundle(temporary) as verified:
                summary = _bundle_summary(verified.manifest)
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            # Hard link is atomic and refuses a concurrent existing destination.
            os.link(temporary, destination)
            published = True
            _fsync_directory(destination.parent)
            return summary | {"output": str(destination), "bundle_bytes": destination.stat().st_size}
    except (ValueError, TypeError, KeyError, sqlite3.Error, zipfile.BadZipFile, OSError, FederationError) as exc:
        if isinstance(exc, TransferError):
            raise
        raise TransferError(f"导出失败: {type(exc).__name__}") from exc
    finally:
        if published and sys.exc_info()[0] is not None and destination.exists() and os.path.samefile(temporary, destination):
            destination.unlink()
        temporary.unlink(missing_ok=True)


def _bundle_summary(manifest):
    return {key: manifest[key] for key in ("instance_id", "snapshot", "record_count", "counts", "counts_scope", "scope",
                                           "source_posts_at_snapshot", "unexported_source_posts",
                                           "export_sequence_complete", "coverage_complete", "dataset_complete",
                                           "model_database_eligible")}


class _VerifiedBundle:
    def __init__(self, archive, manifest, index):
        self.archive, self.manifest, self.index = archive, manifest, index

    def records(self):
        with self.archive.open("records.jsonl") as handle:
            while True:
                line = handle.readline(MAX_RECORD + 1)
                if not line:
                    return
                if len(line) > MAX_RECORD or not line.endswith(b"\n"):
                    raise TransferError("导出记录过大/JSONL 行不完整")
                yield _loads(line)

    def raw_loader(self, request_id):
        row = self.index.db.execute("SELECT sha256,size FROM requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            raise TransferError("原请求不存在于验证后的包")
        body = self.archive.read(f"raw/{row[0]}.body")
        return {"instance_id": self.manifest["instance_id"], "request_id": request_id,
                "sha256": row[0], "response_bytes": row[1], "body_base64": base64.b64encode(body).decode("ascii")}


@contextmanager
def _verified_bundle(path):
    """Reject the complete archive before any destination database is opened."""
    path = _path(path)
    if not path.is_file():
        raise TransferError("bundle 必须是已存在的普通文件")
    with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory(prefix="collector-import-index-") as directory, closing(_Index(directory)) as index:
        infos = archive.infolist()
        by_name = {i.filename: i for i in infos}
        if len(infos) > MAX_MEMBERS or len({i.filename for i in infos}) != len(infos):
            raise TransferError("ZIP 文件数量过大或路径重复")
        total = 0
        for info in infos:
            if not (info.filename in {"manifest.json", "records.jsonl"} or re.fullmatch(r"raw/[0-9a-f]{64}\.body", info.filename)):
                raise TransferError("ZIP 含未知/越界路径；不能传输数据库、令牌或注册表")
            mode = info.external_attr >> 16
            if (info.is_dir() or (stat.S_IFMT(mode) not in {0, stat.S_IFREG}) or info.flag_bits & 1
                    or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
                raise TransferError("ZIP 只允许普通未加密文件")
            limit = MAX_MANIFEST if info.filename == "manifest.json" else MAX_RAW if info.filename.startswith("raw/") else MAX_UNCOMPRESSED
            total += info.file_size
            if info.file_size > limit or total > MAX_UNCOMPRESSED or info.file_size > max(info.compress_size, 1) * 1000:
                raise TransferError("ZIP 展开大小/压缩比超出上限")
        if {"manifest.json", "records.jsonl"} - {i.filename for i in infos}:
            raise TransferError("ZIP 缺少清单或导出记录")
        manifest = _loads(archive.read("manifest.json"))
        if (not isinstance(manifest, dict) or manifest.get("bundle_schema") != BUNDLE_VERSION
                or manifest.get("export_schema") != SCHEMA_VERSION
                or manifest.get("export_sequence_complete") is not True
                or any(manifest.get(k) is not False for k in ("coverage_complete", "dataset_complete", "model_database_eligible"))):
            raise TransferError("包契约/局部数据标记无效")
        identity = manifest["instance_id"]
        if not isinstance(identity, str) or str(uuid.UUID(identity)) != identity:
            raise TransferError("包缺少合法稳定实例 UUID")
        snapshot = _integer(manifest["snapshot"], "snapshot")
        if (manifest.get("after") != 0 or type(manifest.get("after")) is not int
                or _integer(manifest.get("record_count"), "record_count") != snapshot
                or manifest.get("scope") != "post_evidence_export_sequence"):
            raise TransferError("离线包必须包含从序列 1 开始的完整已拥有快照")
        _integer(manifest.get("source_posts_at_snapshot"), "source_posts_at_snapshot")
        try:
            if datetime.fromisoformat(manifest["created_at"]).tzinfo is None:
                raise ValueError
        except (ValueError, TypeError):
            raise TransferError("包创建时间必须带明确时区")
        _no_credentials(manifest)
        members = manifest.get("members")
        if not isinstance(members, list) or len(members) != len(infos) - 1:
            raise TransferError("包清单文件数量不一致")
        declared = {}
        for member in members:
            name, size, digest = member["path"], _integer(member["bytes"], "member.bytes"), member["sha256"]
            if name in declared or name == "manifest.json" or name not in by_name or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise TransferError("包清单路径/哈希格式无效")
            info = archive.getinfo(name)
            if info.file_size != size:
                raise TransferError("ZIP 与清单字节数不一致")
            h, received = hashlib.sha256(), 0
            with archive.open(info) as raw:
                for chunk in iter(lambda: raw.read(1024 * 1024), b""):
                    received += len(chunk)
                    if received > size:
                        raise TransferError("ZIP 实际展开大小超出声明")
                    h.update(chunk)
            if received != size or h.hexdigest() != digest or (name.startswith("raw/") and name != f"raw/{digest}.body"):
                raise TransferError("包成员 SHA-256/字节数不一致")
            declared[name] = member
        if set(declared) != {i.filename for i in infos} - {"manifest.json"}:
            raise TransferError("包包含未声明文件")
        bundle = _VerifiedBundle(archive, manifest, index)
        sequence, parsed = 0, OrderedDict()
        for record in bundle.records():
            sequence += 1
            index.record(record, identity, sequence)
            bodies = {}
            for descriptor in record["requests"]:
                name = f"raw/{descriptor['sha256']}.body"
                if name not in declared or declared[name]["bytes"] != descriptor["response_bytes"]:
                    raise TransferError("导出记录引用的 raw 缺失/大小不符")
                bodies[descriptor["request_id"]] = archive.read(name)
            MergeStore._validate_record(identity, record, bodies, parsed)
            while len(parsed) > 8:
                parsed.popitem(last=False)
        expected_raw = {f"raw/{r[0]}.body" for r in index.db.execute("SELECT sha256 FROM raw")}
        counts = index.counts()
        if (sequence != snapshot or expected_raw != set(declared) - {"records.jsonl"} or manifest.get("counts") != counts
                or manifest.get("counts_scope") != "latest_version_per_source_post_in_bundle"
                or _integer(manifest.get("unexported_source_posts"), "unexported_source_posts") != manifest["source_posts_at_snapshot"] - counts["unique_posts"]):
            raise TransferError("包序列数量、raw 引用或汇总计数不一致")
        yield bundle


@contextmanager
def _destination_lock(data_dir):
    directory = _path(data_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = _path(directory / "worker.lock")
    with path.open("a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise TransferError("目标目录有运行中的 worker；请导入新的隔离汇总目录，或先停止目标 hub") from exc
        try:
            yield directory
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def merge_bundle(bundle, data_dir):
    """Preflight every record/raw first, then replay pages through MergeStore."""
    try:
        _path(data_dir)  # Reject production/links even before package work.
        with _verified_bundle(bundle) as verified, _destination_lock(data_dir) as directory:
            store = MergeStore(directory)
            try:
                manifest = verified.manifest
                identity, snapshot = manifest["instance_id"], manifest["snapshot"]
                before = store.cursor(identity)
                page_items, after = [], 0
                def apply(items, cursor):
                    next_after = items[-1]["seq"] if items else cursor
                    store.merge_page(identity, {"schema_version": SCHEMA_VERSION, "instance_id": identity,
                                               "after": cursor, "snapshot": snapshot, "next_after": next_after,
                                               "has_more": next_after < snapshot, "items": items}, verified.raw_loader)
                    return next_after
                for record in verified.records():
                    page_items.append(record)
                    if len(page_items) == 100:
                        after = apply(page_items, after)
                        page_items = []
                if page_items or not snapshot:
                    apply(page_items, after)
                return _bundle_summary(manifest) | {"cursor_before": before, "cursor_after": store.cursor(identity),
                                                    "merged": store.status(), "bundle": str(Path(bundle).absolute())}
            finally:
                # MergeStore opens/closes a connection per operation, rather
                # than keeping a process-wide connection to close here.
                closer = getattr(store, "close", None)
                if closer is not None:
                    closer()
    except TransferError:
        raise
    except (ValueError, TypeError, KeyError, sqlite3.Error, zipfile.BadZipFile, OSError, FederationError) as exc:
        raise TransferError(f"合并失败: {type(exc).__name__}") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="只读运行库导出已拥有证据，无需停止采集")
    export.add_argument("--data-dir", required=True, type=Path)
    export.add_argument("--output", required=True, type=Path)
    merge = commands.add_parser("merge", help="离线导入隔离 fleet 汇总目录")
    merge.add_argument("--bundle", required=True, type=Path)
    merge.add_argument("--data-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = export_bundle(args.data_dir, args.output) if args.command == "export" else merge_bundle(args.bundle, args.data_dir)
    except (TransferError, ValueError, TypeError, KeyError, sqlite3.Error, zipfile.BadZipFile, OSError, FederationError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
