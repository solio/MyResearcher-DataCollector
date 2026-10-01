"""Immutable collector evidence export and replayable, isolated fleet merge.

This module does not fetch source URLs. ``raw_loader`` obtains evidence from a
configured collector API; all merge decisions are structural and deterministic.
"""
from __future__ import annotations

import base64
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import uuid

from myresearcher_collector.simple_store import SimplePostStore
from myresearcher_collector.sources.eastmoney_guba import parser as guba

SCHEMA_VERSION = "http-backfill.export.v1"
_POST_COLUMNS = {"source", "source_item_id", "stock_code", "title", "content", "author_id", "author_name",
                 "published_at", "url", "read_count", "reply_count", "like_count", "forward_count", "created_at", "updated_at"}


class FederationError(RuntimeError):
    """Transfer or merge evidence failed validation; source collection is separate."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _integer(value, name, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{name} 必须为 {minimum}..{maximum or '∞'} 整数")
    return value


def init_export_schema(engine):
    for statement in ("""CREATE TABLE IF NOT EXISTS export_journal(seq INTEGER PRIMARY KEY,post_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL,payload TEXT NOT NULL,payload_sha256 TEXT NOT NULL,
        UNIQUE(post_id,fingerprint))""",
      """CREATE TRIGGER IF NOT EXISTS export_journal_no_update BEFORE UPDATE ON export_journal
        BEGIN SELECT RAISE(ABORT,'immutable export journal'); END""",
      """CREATE TRIGGER IF NOT EXISTS export_journal_no_delete BEFORE DELETE ON export_journal
        BEGIN SELECT RAISE(ABORT,'immutable export journal'); END"""):
        engine.db.execute(statement)


def journal_projection(engine, store, post_id, fingerprint, list_request, detail_request, provenance, initial_request=None):
    """Called within the compatibility ledger transaction; cursor follows data.

    The node's post projection and this export record share one collector.db
    transaction. Baselines for older rows use the same idempotent path.
    """
    if engine.db.execute("SELECT 1 FROM export_journal WHERE post_id=? AND fingerprint=?", (post_id, fingerprint)).fetchone():
        return
    row = store.conn.execute("SELECT * FROM posts WHERE source=? AND source_item_id=?", (guba.SOURCE, post_id)).fetchone()
    names = [r[1] for r in store.conn.execute("PRAGMA table_info(posts)")]
    post = dict(zip(names, row))
    requests = []
    seen = set()
    for request in (initial_request, list_request, detail_request):
        if request is None:
            continue
        if request["id"] in seen:
            continue
        seen.add(request["id"])
        # Every request belongs to a node-local namespace. The payload retains
        # original facts; neither task IDs nor raw filenames become global IDs.
        original = dict(engine.db.execute("SELECT * FROM requests WHERE id=?", (request["id"],)).fetchone())
        fields = {"id", "job", "task", "kind", "stock", "page", "post_id", "url", "started", "finished", "outcome",
                  "http_status", "response_bytes", "sha256", "raw_ref", "error", "analysis", "headers", "final_url",
                  "probe", "network_attempted", "purpose", "probe_only"}
        original = {key: value for key, value in original.items() if key in fields}
        for field in ("headers", "analysis"):
            if isinstance(original.get(field), str):
                original[field] = json.loads(original[field])
        requests.append({"request_id": request["id"], "sha256": request["sha256"],
                         "response_bytes": request["response_bytes"], "request": original})
    job_id = list_request.get("job")
    job, coverage = None, []
    tables = {r[0] for r in engine.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if job_id is not None and "jobs" in tables:
        original_job = engine.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if original_job:
            job = dict(original_job)
            job["config"] = json.loads(job["config"])
        if "coverage" in tables:
            coverage = [dict(r) for r in engine.db.execute("SELECT * FROM coverage WHERE job=? AND stock=?", (job_id, list_request["stock"]))]
            for cov in coverage:
                cov["gaps"] = json.loads(cov["gaps"] or "[]")
    payload = {"schema_version": SCHEMA_VERSION, "instance_id": engine._get("instance_id"),
               "fingerprint": fingerprint, "post": post, "provenance": provenance,
               "requests": requests, "job": job, "coverage": coverage,
               "request_links": {"initial_list": (initial_request or list_request)["id"],
                                 "list": list_request["id"], "detail": detail_request["id"] if detail_request else None},
               "coverage_complete": False, "model_database_eligible": False}
    engine.db.execute("INSERT INTO export_journal(post_id,fingerprint,payload,payload_sha256) VALUES(?,?,?,?)",
                      (post_id, fingerprint, _json(payload), _hash(payload)))


def export_page(engine, after=0, limit=100, snapshot=None):
    _integer(after, "after")
    _integer(limit, "limit", 1, 200)
    if snapshot is not None:
        _integer(snapshot, "snapshot")
    with engine._mutex:
        maximum = engine.db.execute("SELECT COALESCE(MAX(seq),0) FROM export_journal").fetchone()[0]
        snapshot = maximum if snapshot is None else snapshot
        if snapshot > maximum or after > snapshot:
            raise ValueError("export 游标/快照超出已提交序列")
        rows = engine.db.execute("SELECT seq,payload,payload_sha256 FROM export_journal WHERE seq>? AND seq<=? ORDER BY seq LIMIT ?",
                                 (after, snapshot, limit))
        items, byte_count = [], 1024
        for row in rows:
            size = len(row["payload"].encode()) + 200
            if items and byte_count + size > 8 * 1024 * 1024:
                break
            # One maximum-size source body must still be transferable through
            # the remote client's 32 MiB limit; never return 20 such bodies.
            if size > 30 * 1024 * 1024:
                raise FederationError("单条导出证据超出可传输上限")
            items.append(dict(json.loads(row["payload"]), seq=row["seq"], evidence_sha256=row["payload_sha256"]))
            byte_count += size
        next_after = items[-1]["seq"] if items else after
        return {"schema_version": SCHEMA_VERSION, "instance_id": engine._get("instance_id"), "after": after,
                "snapshot": snapshot, "next_after": next_after, "has_more": next_after < snapshot, "items": items}


def export_raw(engine, request_id):
    _integer(request_id, "request_id", 1)
    with engine._mutex:
        row = engine.db.execute("SELECT raw_ref,sha256,response_bytes,finished FROM requests WHERE id=?", (request_id,)).fetchone()
        if row is None or not row["raw_ref"] or row["finished"] is None:
            raise FederationError("来源原始响应证据不存在/尚未提交")
        root = (Path(engine.data_dir) / "raw").resolve()
        path = (Path(engine.data_dir) / row["raw_ref"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise FederationError("原始响应路径越界/缺失")
        body = path.read_bytes()
        if len(body) != row["response_bytes"] or hashlib.sha256(body).hexdigest() != row["sha256"]:
            raise FederationError("原始响应 SHA-256/字节数不一致")
        return {"instance_id": engine._get("instance_id"), "request_id": request_id, "sha256": row["sha256"],
                "response_bytes": len(body), "body_base64": base64.b64encode(body).decode("ascii")}


class MergeStore:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).resolve()
        if self.data_dir == (Path(__file__).resolve().parents[2] / "data").resolve():
            raise FederationError("不能在生产 data 目录建立汇总库")
        self.root = self.data_dir / "fleet"
        if self.root.is_symlink():
            raise FederationError("fleet 目录不能为符号链接")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.raw_dir = self.root / "raw"
        if self.raw_dir.is_symlink():
            raise FederationError("fleet raw 目录不能为符号链接")
        self.raw_dir.mkdir(exist_ok=True, mode=0o700)
        self.db_path = self.root / "collector.db"
        self.ledger_path = self.root / "merge.sqlite3"
        self._mutex = threading.RLock()
        self._recovery_errors = {}
        if self.db_path.is_symlink() or self.ledger_path.is_symlink():
            raise FederationError("汇总数据库不能为符号链接")
        if self.db_path.exists() and not self.ledger_path.exists():
            raise FederationError("未标记所有权的汇总 collector.db，拒绝写入")
        if self.ledger_path.exists():
            with closing(sqlite3.connect(f"{self.ledger_path.as_uri()}?mode=ro", uri=True)) as db:
                marker = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='merge_meta'").fetchone()
                row = db.execute("SELECT value FROM merge_meta WHERE key='storage'").fetchone() if marker else None
                if not row or row[0] != "http-backfill.fleet.v1":
                    raise FederationError("未知汇总台账契约，拒绝修改")
        with self._ledger() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS merge_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS versions(instance_id TEXT,seq INTEGER,source TEXT,post_id TEXT,
                evidence_sha256 TEXT,payload TEXT,identity_sha256 TEXT,body_sha256 TEXT,
                PRIMARY KEY(instance_id,seq));
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
            """)
            db.execute("INSERT OR IGNORE INTO merge_meta VALUES('storage','http-backfill.fleet.v1')")
            if db.execute("SELECT value FROM merge_meta WHERE key='storage'").fetchone()[0] != "http-backfill.fleet.v1":
                raise FederationError("未知汇总台账契约")
            db.commit()
        self._validate_store()
        with self._store() as _:
            pass
        # All pending records have independently verified and fsynced raw. A
        # crash between posts commit and cursor commit is repaired locally.
        with self._mutex, self._ledger() as db:
            for node in db.execute("SELECT instance_id FROM pending").fetchall():
                try:
                    self._finish(db, node[0])
                except (FederationError, ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
                    cursor = db.execute("SELECT seq FROM cursors WHERE instance_id=?", (node[0],)).fetchone()
                    self._recovery_errors[node[0]] = {"kind": "local_merge_recovery_error", "error": f"{type(exc).__name__}: {exc}",
                                                      "cursor": cursor[0] if cursor else 0}

    def _ledger(self):
        db = sqlite3.connect(self.ledger_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        return closing(db)

    def _store(self):
        from compatible_store import CompatibleDataStore
        return CompatibleDataStore._store(self.db_path)

    def _validate_store(self):
        if self.db_path.exists():
            from compatible_store import _expected_schema
            with closing(sqlite3.connect(f"{self.db_path.as_uri()}?mode=ro", uri=True)) as db:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                if tables != set(_expected_schema()) or any(db.execute(f"PRAGMA table_info({t})").fetchall() != c for t, c in _expected_schema().items()):
                    raise FederationError("汇总 collector.db 不符合原 SimplePostStore 结构")

    def cursor(self, instance_id):
        with self._mutex, self._ledger() as db:
            row = db.execute("SELECT seq FROM cursors WHERE instance_id=?", (instance_id,)).fetchone()
            return row[0] if row else 0

    def _save_raw(self, instance_id, descriptor, raw_loader):
        request_id = _integer(descriptor["request_id"], "request_id", 1)
        digest = descriptor["sha256"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise FederationError("raw SHA-256 格式无效")
        size = _integer(descriptor["response_bytes"], "response_bytes", 0, 16 * 1024 * 1024)
        path = self.raw_dir / f"{digest}.body"
        if path.is_symlink():
            raise FederationError("raw 文件不能为符号链接")
        body, corrupted = None, False
        if path.exists():
            retained = path.read_bytes()
            if len(retained) == size and hashlib.sha256(retained).hexdigest() == digest:
                body = retained
            else:
                corrupted = True
        if body is None:
            try:
                response = raw_loader(request_id)
            except Exception as exc:
                if corrupted:
                    raise FederationError(f"本地 raw SHA-256/字节数损坏: {path}; 节点证据未能重取: {exc}") from exc
                raise
            if (response.get("instance_id") != instance_id or response.get("request_id") != request_id
                    or response.get("sha256") != digest or response.get("response_bytes") != size):
                raise FederationError("raw 传输实例/请求/哈希身份不一致")
            body = base64.b64decode(response["body_base64"], validate=True)
        if len(body) != size or hashlib.sha256(body).hexdigest() != digest:
            raise FederationError("raw 传输 SHA-256/字节数不一致")
        if not path.exists() or corrupted:
            if corrupted:
                os.replace(path, path.with_name(f"{digest}.corrupt-{uuid.uuid4().hex}.body"))
            temporary = path.with_suffix(".tmp")
            with open(temporary, "wb") as out:
                out.write(body)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, path)
            fd = os.open(self.raw_dir, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return body

    @staticmethod
    def _validate_record(instance_id, record, bodies, parsed_requests):
        from core import Engine, challenge_evidence, _allowed, SAFE_HEADERS
        from urllib.parse import urlparse
        post, provenance = record["post"], record["provenance"]
        if set(post) != _POST_COLUMNS or post["source"] != guba.SOURCE or not re.fullmatch(r"[0-9]+", post["source_item_id"]):
            raise FederationError("兼容帖子结构/来源身份无效")
        if type(provenance.get("body_complete")) is not bool or provenance["body_complete"] != (post["content"] is not None):
            raise FederationError("正文缺失/空串来源标记不一致")
        if post["content"] is not None and not isinstance(post["content"], str):
            raise FederationError("正文必须为实际字符串或 NULL")
        if provenance.get("content_source") != ("detail_body" if post["content"] is not None else "list_title"):
            raise FederationError("正文来源标记与实际采集证据不一致")
        for timestamp in ("published_at", "created_at", "updated_at"):
            if datetime.fromisoformat(post[timestamp].replace("Z", "+00:00")).tzinfo is None:
                raise FederationError("来源及采集时间缺少时区")
        links, requests = record["request_links"], {}
        if not isinstance(record["requests"], list) or not 1 <= len(record["requests"]) <= 3:
            raise FederationError("帖子原请求集合无效")
        for descriptor in record["requests"]:
            request = descriptor["request"]
            if (request["id"] != descriptor["request_id"] or request["sha256"] != descriptor["sha256"]
                    or request["response_bytes"] != descriptor["response_bytes"] or request["finished"] is None):
                raise FederationError("原请求事实与 raw 描述不一致")
            request_id = descriptor["request_id"]
            if request_id in requests or type(request["finished"]) not in (float, int) or not math.isfinite(request["finished"]):
                raise FederationError("重复原请求或采集时间无效")
            requests[request_id] = request
            if request_id in parsed_requests:
                continue
            body = bodies[request_id]
            html = body.decode("utf-8-sig", errors="strict")
            headers = request.get("headers") or {}
            if (not isinstance(headers, dict) or set(headers) - SAFE_HEADERS
                    or any(not isinstance(v, str) for v in headers.values())):
                raise FederationError("原响应头结构无效")
            Engine._validate_framing(body, headers)
            if challenge_evidence(html)["challenge"] or not _allowed(request["url"]) or request["final_url"] != request["url"]:
                raise FederationError("原请求为验证页/异常最终地址")
            p = urlparse(request["url"])
            if request["kind"] == "list":
                from compatible_store import CompatibleDataStore
                if (request["http_status"] != 200 or p.hostname != "guba.eastmoney.com"
                        or not re.fullmatch(r"/list," + re.escape(request["stock"]) + r",f(?:_[1-9][0-9]*)?\.html", p.path)
                        or not CompatibleDataStore._permitted_request(dict(request, analysis=_json(request.get("analysis") or {})), ("real_data",))):
                    raise FederationError("列表原请求未通过成功/导航事实校验")
                payload = guba._embedded_json(html, "article_list")
                if type(payload.get("rc")) is not int or payload["rc"] != 1 or not isinstance(payload.get("re"), list):
                    raise FederationError("列表 rc/re 无效")
                ids = set()
                for row in payload["re"]:
                    if (not isinstance(row, dict) or isinstance(row.get("post_id"), bool)
                            or not re.fullmatch(r"[0-9]+", str(row.get("post_id", ""))) or type(row.get("post_type")) is not int):
                        raise FederationError("来源列表行 ID/type 无效")
                    pid = str(row["post_id"])
                    if pid in ids:
                        raise FederationError("来源列表重复 ID")
                    ids.add(pid)
                    published = guba.parse_source_time(row.get("post_publish_time"), "post_publish_time", required=True)
                    if published.strftime("%Y-%m-%d %H:%M:%S") != row["post_publish_time"]:
                        raise FederationError("来源列表时间格式不完整")
                page = guba.parse_list_page(html, request["stock"])
                by_id = {}
                for item in page.rows:
                    url = urlparse(item.url)
                    if (not _allowed(item.url) or url.hostname != "guba.eastmoney.com"
                            or not re.fullmatch(r"/news,[A-Za-z0-9]+," + re.escape(item.source_item_id) + r"\.html", url.path)):
                        raise FederationError("来源列表标准详情链接异常")
                    by_id[item.source_item_id] = item
                parsed_requests[request_id] = by_id
            elif request["kind"] == "detail":
                if (p.hostname != "guba.eastmoney.com" or not isinstance(request.get("post_id"), str)
                        or not re.fullmatch(r"/news,[A-Za-z0-9]+," + re.escape(request["post_id"]) + r"\.html", p.path)):
                    raise FederationError("详情原请求源 URL/ID 不一致")
                if request["outcome"] == "real_data" and request["http_status"] == 200:
                    parsed_requests[request_id] = guba.parse_detail_page(html)
                elif request["outcome"] == "detail_unavailable" and request["http_status"] in {200, 404} and guba.is_not_found_page(html):
                    parsed_requests[request_id] = None
                else:
                    raise FederationError("详情原请求缺少有效来源证据")
            else:
                raise FederationError("未知来源请求类型")
        if (set(requests) != {v for v in links.values() if v is not None}
                or set(links) != {"initial_list", "list", "detail"}
                or any(type(links[k]) is not int or requests[links[k]]["kind"] != "list" for k in ("initial_list", "list"))):
            raise FederationError("帖子原请求关联不一致")
        listed = parsed_requests[links["list"]].get(post["source_item_id"])
        initial = parsed_requests[links["initial_list"]].get(post["source_item_id"])
        detailed = parsed_requests[links["detail"]] if links["detail"] is not None else None
        if listed is None:
            raise FederationError("帖子不在原始列表中")
        if initial is None:
            raise FederationError("帖子不在原始首次列表中")
        for name in ("published_at", "canonical_bar_code", "author_id", "author_name"):
            if getattr(initial, name) != getattr(listed, name):
                raise FederationError("首次与当前列表身份发生冲突")
        for name in ("title", "author_id", "author_name", "url", "read_count", "reply_count", "like_count", "forward_count"):
            if post[name] != getattr(listed, name):
                raise FederationError(f"兼容帖子 {name} 与列表来源不一致")
        if (post["published_at"] != listed.published_at.isoformat().replace("+00:00", "Z")
                or post["stock_code"] != listed.requested_bar_code
                or provenance.get("canonical_bar_code") != listed.canonical_bar_code):
            raise FederationError("兼容帖子发布时间/股吧归属不一致")
        if post["content"] is not None:
            if detailed is None or guba.merge_list_and_detail(listed, detailed)["content"] != post["content"]:
                raise FederationError("兼容正文与实际详情来源不一致")
        elif detailed is not None:
            raise FederationError("实际正文不能伪装为缺失")
        if links["detail"] is not None and (requests[links["detail"]]["kind"] != "detail" or requests[links["detail"]]["post_id"] != post["source_item_id"]):
            raise FederationError("正文原请求关联不一致")
        acquired = requests[links["list"]]["finished"]
        if post["content"] is not None:
            acquired = max(acquired, requests[links["detail"]]["finished"])
        def original_time(epoch):
            return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        if (post["created_at"] != original_time(requests[links["initial_list"]]["finished"])
                or post["updated_at"] != original_time(acquired)):
            raise FederationError("兼容采集时间与原请求事实不一致")

    def merge_page(self, instance_id, page, raw_loader):
        """Validate an entire export page, retain versions, then advance its cursor."""
        with self._mutex:
            try:
                return self._merge_page(instance_id, page, raw_loader)
            except FederationError:
                raise
            except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
                raise FederationError(f"汇总证据校验/存储失败: {type(exc).__name__}: {exc}") from exc

    def _merge_page(self, instance_id, page, raw_loader):
        if not isinstance(instance_id, str) or not instance_id or page.get("instance_id") != instance_id or page.get("schema_version") != SCHEMA_VERSION:
            raise FederationError("导出实例/契约不一致")
        after = _integer(page["after"], "after")
        snapshot = _integer(page["snapshot"], "snapshot")
        next_after = _integer(page["next_after"], "next_after")
        items = page["items"]
        if not isinstance(items, list) or len(items) > 200 or type(page["has_more"]) is not bool:
            raise FederationError("导出页格式无效")
        expected = list(range(after + 1, after + len(items) + 1))
        if [r.get("seq") for r in items] != expected or any(type(r.get("seq")) is not int for r in items) or next_after != (expected[-1] if expected else after) or next_after > snapshot:
            raise FederationError("导出序列存在间隙/次序错误")
        if page["has_more"] != (next_after < snapshot):
            raise FederationError("导出快照存在未声明的数据间隙")
        with self._ledger() as db:
            current = db.execute("SELECT seq FROM cursors WHERE instance_id=?", (instance_id,)).fetchone()
            current = current[0] if current else 0
            if after > current:
                raise FederationError("导出页跳过了尚未同步序列")
            bodies, raw_descriptors, parsed_requests = {}, {}, {}
            for record in items:
                payload = {k: v for k, v in record.items() if k not in {"seq", "evidence_sha256"}}
                if record.get("instance_id") != instance_id or record.get("schema_version") != SCHEMA_VERSION or _hash(payload) != record["evidence_sha256"]:
                    raise FederationError("不可变导出记录 SHA-256/身份不一致")
                previous = db.execute("SELECT evidence_sha256 FROM versions WHERE instance_id=? AND seq=?", (instance_id, record["seq"])).fetchone()
                if previous and previous[0] != record["evidence_sha256"]:
                    raise FederationError("同一不可变节点序列返回不同事实")
                if record["seq"] <= current and previous is None:
                    raise FederationError("同步游标缺少对应版本证据")
                for descriptor in record["requests"]:
                    request_id = _integer(descriptor["request_id"], "request_id", 1)
                    _integer(descriptor["request"]["id"], "原 request.id", 1)
                    if request_id in raw_descriptors and raw_descriptors[request_id] != descriptor:
                        raise FederationError("同一节点请求描述存在冲突")
                    old_raw = db.execute("SELECT sha256,descriptor FROM raw_evidence WHERE instance_id=? AND request_id=?", (instance_id, request_id)).fetchone()
                    if old_raw and (old_raw[0] != descriptor["sha256"] or old_raw[1] != _json(descriptor)):
                        raise FederationError("既有节点原请求证据发生变化")
                    raw_descriptors[request_id] = descriptor
                    if request_id not in bodies:
                        bodies[request_id] = self._save_raw(instance_id, descriptor, raw_loader)
                self._validate_record(instance_id, record, bodies, parsed_requests)
            # Durable redo records precede the compatible projection. A local
            # failure leaves cursor unchanged and all validated evidence intact.
            with db:
                for request_id, descriptor in raw_descriptors.items():
                    db.execute("INSERT OR IGNORE INTO raw_evidence VALUES(?,?,?,?,?)",
                               (instance_id, request_id, descriptor["sha256"], descriptor["response_bytes"], _json(descriptor)))
                for record in items:
                    post, provenance = record["post"], record["provenance"]
                    identity = {name: post[name] for name in ("title", "author_id", "author_name", "published_at")}
                    identity["canonical_bar_code"] = provenance.get("canonical_bar_code")
                    db.execute("INSERT OR IGNORE INTO versions VALUES(?,?,?,?,?,?,?,?)",
                               (instance_id, record["seq"], post["source"], post["source_item_id"], record["evidence_sha256"],
                                _json(record), _hash(identity), _hash(post["content"]) if post["content"] is not None else None))
                    db.execute("INSERT OR IGNORE INTO pending_posts VALUES(?,?,?)", (instance_id, post["source"], post["source_item_id"]))
                db.execute("INSERT INTO pending VALUES(?,?) ON CONFLICT(instance_id) DO UPDATE SET next_after=max(next_after,excluded.next_after)",
                           (instance_id, max(current, next_after)))
            self._finish(db, instance_id)
            self._recovery_errors.pop(instance_id, None)
            return {"instance_id": instance_id, "cursor": max(current, next_after), "received": len(items),
                    "raw_verified": len(raw_descriptors), "research_only": True, "model_database_eligible": False}

    def _finish(self, db, instance_id):
        pending = db.execute("SELECT next_after FROM pending WHERE instance_id=?", (instance_id,)).fetchone()
        if not pending:
            return
        self._validate_store()
        current = db.execute("SELECT seq FROM cursors WHERE instance_id=?", (instance_id,)).fetchone()
        current = current[0] if current else 0
        bodies, parsed = {}, {}
        def missing_raw(_):
            raise FederationError("待恢复汇总批次的本地 raw 缺失；游标保持不变")
        for row in db.execute("SELECT payload FROM versions WHERE instance_id=? AND seq>? AND seq<=? ORDER BY seq", (instance_id, current, pending[0])):
            record = json.loads(row[0])
            for descriptor in record["requests"]:
                request_id = descriptor["request_id"]
                if request_id not in bodies:
                    bodies[request_id] = self._save_raw(instance_id, descriptor, missing_raw)
            self._validate_record(instance_id, record, bodies, parsed)
        affected = db.execute("SELECT source,post_id FROM pending_posts WHERE instance_id=?", (instance_id,)).fetchall()
        selections, conflicts = [], []
        with self._store() as store, store.transaction():
            for source, post_id in affected:
                versions = [dict(r) for r in db.execute("""SELECT v.* FROM versions v LEFT JOIN cursors c ON v.instance_id=c.instance_id
                  WHERE v.source=? AND v.post_id=? AND (v.seq<=COALESCE(c.seq,0) OR (v.instance_id=? AND v.seq<=?))""",
                                                       (source, post_id, instance_id, pending[0]))]
                def ranking(version):
                    post = json.loads(version["payload"])["post"]
                    acquired = datetime.fromisoformat(post["updated_at"].replace("Z", "+00:00")).timestamp()
                    return (post["content"] is not None, acquired, version["instance_id"], version["seq"])
                winner = max(versions, key=ranking)
                post = json.loads(winner["payload"])["post"]
                # Project one entire selected observation. Do not blend fields
                # from an identity-conflicting list and a different body.
                store.upsert_post(**post)
                store.conn.execute("UPDATE posts SET created_at=? WHERE source=? AND source_item_id=?", (post["created_at"], source, post_id))
                selections.append((source, post_id, winner["instance_id"], winner["seq"]))
                for kind, column in (("identity", "identity_sha256"), ("body", "body_sha256")):
                    groups = {}
                    for version in versions:
                        digest = version[column]
                        if digest is not None:
                            groups.setdefault(digest, []).append({"instance_id": version["instance_id"], "seq": version["seq"]})
                    if len(groups) > 1:
                        conflicts.append((source, post_id, kind, _json(groups)))
        with db:
            for selected in selections:
                db.execute("INSERT INTO selected VALUES(?,?,?,?) ON CONFLICT(source,post_id) DO UPDATE SET instance_id=excluded.instance_id,seq=excluded.seq", selected)
            for conflict in conflicts:
                db.execute("INSERT INTO conflicts VALUES(?,?,?,?) ON CONFLICT(source,post_id,kind) DO UPDATE SET variants=excluded.variants", conflict)
            db.execute("INSERT INTO cursors VALUES(?,?) ON CONFLICT(instance_id) DO UPDATE SET seq=max(seq,excluded.seq)", (instance_id, pending[0]))
            db.execute("DELETE FROM pending WHERE instance_id=?", (instance_id,))
            db.execute("DELETE FROM pending_posts WHERE instance_id=?", (instance_id,))

    def versions(self, source, source_item_id):
        with self._mutex, self._ledger() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM versions WHERE source=? AND post_id=? ORDER BY instance_id,seq", (source, source_item_id))]

    def conflicts(self):
        with self._mutex, self._ledger() as db:
            return [dict(r, variants=json.loads(r["variants"])) for r in db.execute("SELECT * FROM conflicts ORDER BY source,post_id,kind")]

    def status(self):
        with self._mutex, self._ledger() as db:
            with self._store() as store:
                count, bodies = store.conn.execute("SELECT COUNT(*),COALESCE(SUM(content IS NOT NULL),0) FROM posts").fetchone()
            observations = db.execute("SELECT COUNT(*) FROM versions").fetchone()[0]
            cursors = {r[0]: r[1] for r in db.execute("SELECT * FROM cursors")}
            return {"db_path": str(self.db_path), "ledger_path": str(self.ledger_path), "raw_dir": str(self.raw_dir),
                    "unique_posts": count, "body_complete": bodies, "list_only": count - bodies,
                    "observations": observations, "instances": len(cursors),
                    "posts": count, "versions": observations,
                    "conflicts": db.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0],
                    "raw_responses": db.execute("SELECT COUNT(*) FROM raw_evidence").fetchone()[0],
                    "cursors": cursors,
                    "pending_instances": [r[0] for r in db.execute("SELECT instance_id FROM pending")],
                    "recovery_errors": dict(self._recovery_errors),
                    "coverage_complete": False, "model_database_eligible": False}
