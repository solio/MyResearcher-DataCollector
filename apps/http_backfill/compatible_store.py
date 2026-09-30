"""Project HTTP evidence into the existing browser posts storage contract.

collector.db is an isolated SimplePostStore, not a production promotion. The
experiment ledger owns queue/provenance and the recoverable projection cursor.
No new coverage/checkpoints are inferred and no raw bytes are copied.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlparse

from myresearcher_collector.models import SourceItem
from myresearcher_collector.detail_enrichment import _SkipLedger, _skip_ledger_path, SKIP_REASON_NOT_FOUND
from myresearcher_collector.simple_store import SCHEMA as SIMPLE_SCHEMA, SimplePostStore
from myresearcher_collector.sources.eastmoney_guba import parser as guba
from myresearcher_collector.sources.eastmoney_guba.content_rules import (
    detail_body_metadata, detail_enrichment_trigger, list_title_metadata,
)

VERSION = "http-backfill.simple-posts.v1"
_NAVIGATION_ERRORS = {
    "无法建立非置顶来源行锚点，不能猜测下一历史页",
    "校准无法定位锚点：来源空页/无可用发布时间；已暂停",
    "校准无法定位普通帖时间锚点；当前页只有非标准行，已暂停",
    "校准列表不符合非置顶来源行发布时间降序，无法安全定位",
    "校准无法定位 ID 锚点；非标准页时间次序未知，已暂停",
    "分页无进展：重新定位后仍未发现更深历史，不能反复猜测下一页",
    "分页没有新增源 ID，且没有可恢复锚点",
}


class CompatibleStorageError(RuntimeError):
    """An offline projection cannot satisfy the existing storage contract."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=lambda x: x.isoformat())


def _time(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc)


def _utc(epoch):
    return _time(epoch).isoformat(timespec="microseconds").replace("+00:00", "Z")


@lru_cache(maxsize=1)
def _expected_schema():
    with closing(sqlite3.connect(":memory:")) as reference:
        reference.executescript(SIMPLE_SCHEMA)
        tables = {r[0] for r in reference.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {table: reference.execute(f"PRAGMA table_info({table})").fetchall() for table in tables}


class CompatibleDataStore:
    def __init__(self, data_dir, instance_id):
        self.data_dir = Path(data_dir).resolve()
        production = Path(__file__).resolve().parents[2] / "data"
        if self.data_dir == production.resolve():
            raise CompatibleStorageError("不能向仓库原生产 data 目录投影实验数据")
        if not isinstance(instance_id, str) or not instance_id.strip():
            raise CompatibleStorageError("兼容存储需要稳定的实例 ID")
        self.instance_id = instance_id
        self.db_path = self.data_dir / "collector.db"
        self._raw_cache = OrderedDict()
        self._list_cache = OrderedDict()

    @staticmethod
    @contextmanager
    def _store(path):
        # The existing SQLite store is opened on the calling thread and closed
        # even if its initializer encounters a filesystem/SQLite error.
        store = SimplePostStore.__new__(SimplePostStore)
        try:
            store.__init__(path)
            yield store
        finally:
            if getattr(store, "conn", None) is not None:
                store.close()

    def _prepare(self, engine):
        if Path(engine.data_dir).resolve() != self.data_dir:
            raise CompatibleStorageError("兼容数据目录与实验台账目录不一致")
        if engine._get("instance_id") != self.instance_id:
            raise CompatibleStorageError("兼容存储实例 ID 不一致")
        owner = engine._get("compatible_storage_instance")
        if owner is not None and owner != self.instance_id:
            raise CompatibleStorageError("兼容数据库属于另一个实例")
        if self.db_path.is_symlink():
            raise CompatibleStorageError("兼容数据库不能使用指向其他目录的符号链接")
        if self.db_path.exists() and owner != self.instance_id:
            raise CompatibleStorageError("现有 collector.db 未标记为该实验实例所有，拒绝写入")
        if self.db_path.exists():
            if not self.db_path.is_file():
                raise CompatibleStorageError("collector.db 必须是文件")
            with closing(sqlite3.connect(f"{self.db_path.as_uri()}?mode=ro", uri=True)) as db:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                expected = {"posts", "backfill_resume", "backfill_coverage", "backfill_page_anchors"}
                if tables != expected:
                    raise CompatibleStorageError("collector.db 不是原 SimplePostStore 数据结构")
                for table, columns in _expected_schema().items():
                    if db.execute(f"PRAGMA table_info({table})").fetchall() != columns:
                        raise CompatibleStorageError("collector.db 字段或主键与原 SimplePostStore 不一致")
        # Owner is committed before publication so a crash after creating the
        # file does not leave an unowned database that cannot be resumed.
        with engine.db:
            engine.db.execute("""CREATE TABLE IF NOT EXISTS compatible_posts(
                post_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,
                stock_code TEXT NOT NULL,list_request INTEGER NOT NULL,
                detail_request INTEGER,content_source TEXT NOT NULL,
                body_complete INTEGER NOT NULL,metadata TEXT NOT NULL,
                updated_at TEXT NOT NULL)""")
            engine.db.execute("CREATE INDEX IF NOT EXISTS idx_compatible_observations_request ON observations(request_id,post_id)")
            engine.db.execute("CREATE INDEX IF NOT EXISTS idx_compatible_observations_post ON observations(post_id,id DESC)")
            engine._set("compatible_storage_instance", self.instance_id)
            engine._set("compatible_storage_version", VERSION)

    @staticmethod
    def _permitted_request(request, allowed):
        if request["outcome"] in allowed:
            return True
        if "real_data" not in allowed or request["kind"] != "list" or request["outcome"] != "schema_error":
            return False
        analysis = json.loads(request.get("analysis") or "{}")
        if not isinstance(analysis, dict):
            return False
        if "list_structure_validated" in analysis:
            return analysis["list_structure_validated"] is True
        # Older navigation failures have no explicit validation checkpoint.
        # Only known post-validation navigation errors may enter the full
        # offline validation below; an unknown schema failure never does.
        return request.get("error") in _NAVIGATION_ERRORS

    def _raw_request(self, engine, request_id, allowed=("real_data",)):
        if request_id in self._raw_cache:
            self._raw_cache.move_to_end(request_id)
            value = self._raw_cache[request_id]
            if not self._permitted_request(value[0], allowed):
                raise CompatibleStorageError("帖子来源响应未成功校验")
            return value
        request = engine.db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if request is None:
            raise CompatibleStorageError("帖子缺少成功来源响应证据")
        request = dict(request)
        if not self._permitted_request(request, allowed) or not request["raw_ref"]:
            raise CompatibleStorageError("帖子缺少成功来源响应证据")
        path = (self.data_dir / request["raw_ref"]).resolve()
        raw_root = (self.data_dir / "raw").resolve()
        if not path.is_relative_to(raw_root) or not path.is_file():
            raise CompatibleStorageError("来源原始响应缺失或路径越界")
        body = path.read_bytes()
        if len(body) != request["response_bytes"] or hashlib.sha256(body).hexdigest() != request["sha256"]:
            raise CompatibleStorageError("来源原始响应字节数或 SHA-256 不一致")
        if request["finished"] is None:
            raise CompatibleStorageError("来源成功响应缺少采集完成时间")
        html = body.decode("utf-8-sig", errors="strict")
        # Imported lazily because Engine imports this compatibility module.
        from core import Engine, challenge_evidence, _allowed
        Engine._validate_framing(body, json.loads(request.get("headers") or "{}"))
        if challenge_evidence(html)["challenge"]:
            raise CompatibleStorageError("来源响应包含验证页/验证遮罩，拒绝投影")
        if request["outcome"] != "detail_unavailable" and request["http_status"] != 200:
            raise CompatibleStorageError("有效帖子响应必须为 HTTP 200")
        if not _allowed(request["final_url"]) or request["final_url"] != request["url"]:
            raise CompatibleStorageError("来源响应最终地址与原请求不符")
        if request["kind"] == "list":
            p = urlparse(request["final_url"])
            if p.hostname != "guba.eastmoney.com" or not re.fullmatch(
                r"/list," + re.escape(request["stock"]) + r",f(?:_[1-9][0-9]*)?\.html", p.path
            ):
                raise CompatibleStorageError("来源列表股票/页码链接不符合已确认路径")
        value = (request, html)
        self._raw_cache[request_id] = value
        while len(self._raw_cache) > 4:
            self._raw_cache.popitem(last=False)
        return value

    def _list_item(self, engine, html, stock, post_id, request_id):
        key = (request_id, stock)
        if key not in self._list_cache:
            payload = guba._embedded_json(html, "article_list")
            if type(payload.get("rc")) is not int or payload["rc"] != 1 or not isinstance(payload.get("re"), list):
                raise CompatibleStorageError("article_list rc/re 不符合成功列表契约")
            source_rows = {}
            for row in payload["re"]:
                if (not isinstance(row, dict) or isinstance(row.get("post_id"), bool)
                        or not re.fullmatch(r"[0-9]+", str(row.get("post_id", "")))
                        or type(row.get("post_type")) is not int):
                    raise CompatibleStorageError("列表行源 ID/post_type 无效")
                pid = str(row["post_id"])
                published = guba.parse_source_time(row.get("post_publish_time"), "post_publish_time", required=True)
                if published.strftime("%Y-%m-%d %H:%M:%S") != row["post_publish_time"]:
                    raise CompatibleStorageError("列表发布时间必须为完整来源时间")
                if pid in source_rows:
                    raise CompatibleStorageError("同页出现重复源 ID")
                source_rows[pid] = row
            items = {item.source_item_id: item for item in guba.parse_list_page(html, stock).rows}
            for pid, item in items.items():
                p = urlparse(item.url)
                if (p.scheme != "https" or p.hostname != "guba.eastmoney.com" or p.username or p.password
                        or p.port not in (None, 443) or not re.fullmatch(r"/news,[A-Za-z0-9]+," + re.escape(pid) + r"\.html", p.path)):
                    raise CompatibleStorageError("列表中的标准详情链接不符合来源路径")
            observations = engine.db.execute("SELECT stock,post_id,source_row FROM observations WHERE request_id=?", (request_id,)).fetchall()
            if len(observations) != len(source_rows):
                raise CompatibleStorageError("已留存列表观察行与来源行数不一致")
            observed_ids = set()
            for observation in observations:
                pid = observation["post_id"]
                if (pid in observed_ids or observation["stock"] != stock
                        or json.loads(observation["source_row"]) != source_rows.get(pid)):
                    raise CompatibleStorageError("已留存列表观察行与原响应身份不一致")
                observed_ids.add(pid)
            self._list_cache[key] = items
            while len(self._list_cache) > 4:
                self._list_cache.popitem(last=False)
        self._list_cache.move_to_end(key)
        item = self._list_cache[key].get(post_id)
        if item is None:
            raise CompatibleStorageError("帖子不在其留存列表响应中")
        return item

    @staticmethod
    def _source_item(item, collected_at, content, metadata, raw_ref, final_url):
        values = asdict(item) if not isinstance(item, dict) else dict(item)
        values.update(source=guba.SOURCE, schema_version=guba.SCHEMA_VERSION,
                      content=content, collected_at=collected_at,
                      source_metadata=metadata, raw_ref=raw_ref, final_url=final_url)
        return SourceItem(**{name: values[name] for name in SourceItem.__dataclass_fields__ if name in values})

    def _project_post(self, engine, store, post, request_id):
        initial_request, initial_html = self._raw_request(engine, post["list_request"])
        initial_item = self._list_item(engine, initial_html, initial_request["stock"], post["post_id"], initial_request["id"])
        observed_request, observed_item = initial_request, initial_item
        if request_id is None:
            latest = engine.db.execute("SELECT request_id FROM observations WHERE post_id=? ORDER BY id DESC LIMIT 1", (post["post_id"],)).fetchone()
            if latest and latest[0] != initial_request["id"]:
                observed_request, html = self._raw_request(engine, latest[0])
                observed_item = self._list_item(engine, html, observed_request["stock"], post["post_id"], observed_request["id"])
        else:
            request = engine.db.execute("SELECT kind,outcome FROM requests WHERE id=?", (request_id,)).fetchone()
            if request and request["kind"] == "list":
                observed_request, html = self._raw_request(engine, request_id)
                observed_item = self._list_item(engine, html, observed_request["stock"], post["post_id"], observed_request["id"])
        for field in ("published_at", "canonical_bar_code", "author_id", "author_name"):
            if getattr(initial_item, field) != getattr(observed_item, field):
                raise CompatibleStorageError(f"同一源 ID 的列表 {field} 身份不一致")
        if initial_item.title and observed_item.title and initial_item.title != observed_item.title:
            raise CompatibleStorageError("同一源 ID 的列表标题不一致")
        metadata = list_title_metadata(observed_item.source_metadata, observed_item.title)
        content, detail_request = None, None
        item = self._source_item(observed_item, _time(observed_request["finished"]), observed_item.title or "", metadata,
                                 {"list": observed_request["raw_ref"]}, observed_request["final_url"])
        updated = observed_request["finished"]
        if post["status"] == "complete":
            if post["detail_request"] is None or not isinstance(post["content"], str):
                raise CompatibleStorageError("已完成正文缺少真实详情来源")
            detail_request, detail_html = self._raw_request(engine, post["detail_request"])
            detail = guba.parse_detail_page(detail_html)
            merged = guba.merge_list_and_detail(observed_item, detail)
            if merged["content"] != post["content"]:
                raise CompatibleStorageError("正文与留存详情原始响应不一致")
            content = merged["content"]
            metadata = detail_body_metadata(merged["source_metadata"], title=observed_item.title,
                                            trigger=detail_enrichment_trigger(observed_item.title))
            updated = max(updated, detail_request["finished"])
            # The original legacy enrich operation updates content only; list
            # columns (including engagement snapshots) keep their list origin.
            item = self._source_item(observed_item, _time(updated), content, metadata,
                                     {"list": observed_request["raw_ref"], "detail": detail_request["raw_ref"]}, detail_request["final_url"])
        elif post["status"] == "removed":
            detail_request, missing_html = self._raw_request(engine, post["detail_request"], allowed=("detail_unavailable",))
            if detail_request["http_status"] not in {200, 404} or not guba.is_not_found_page(missing_html):
                raise CompatibleStorageError("缺正文状态没有来源明确不存在/不可访问页面支持")
            skips = _SkipLedger(_skip_ledger_path(self.db_path))
            if post["post_id"] not in skips.skipped_ids(guba.SOURCE):
                skips.record(source=guba.SOURCE, source_item_id=post["post_id"],
                             stock_code=observed_request["stock"], reason=SKIP_REASON_NOT_FOUND,
                             observed_at=_time(detail_request["finished"]))
        provenance = {"content_source": "detail_body" if content is not None else "list_title",
                      "detail_required": detail_enrichment_trigger(item.title) is not None,
                      "detail_enrichment_trigger": detail_enrichment_trigger(item.title),
                      "canonical_bar_code": item.canonical_bar_code,
                      "requested_bar_code": observed_request["stock"],
                      "body_complete": content is not None, "research_only": True,
                      "model_database_eligible": False, "source_status": post["status"],
                      "list_sha256": observed_request["sha256"],
                      "detail_sha256": detail_request["sha256"] if detail_request else None}
        facts = {"item": asdict(item), "legacy_content": content, "provenance": provenance,
                 "list_request": observed_request["id"], "detail_request": detail_request["id"] if detail_request else None}
        fingerprint = hashlib.sha256(_json(facts).encode()).hexdigest()
        prior = engine.db.execute("SELECT fingerprint FROM compatible_posts WHERE post_id=?", (post["post_id"],)).fetchone()
        existing = store.conn.execute("SELECT content FROM posts WHERE source=? AND source_item_id=?", (guba.SOURCE, post["post_id"])).fetchone()
        if prior and prior[0] == fingerprint and existing is not None:
            return False
        if content is None and existing is not None and existing[0] is not None:
            raise CompatibleStorageError("兼容库已有正文但实验台账没有对应详情证据，拒绝覆盖来源标记")
        # Rebuilds of already enriched posts retain the original list acquisition
        # as created_at. Subsequent detail/list snapshots only update updated_at.
        if existing is None:
            initial_metadata = list_title_metadata(initial_item.source_metadata, initial_item.title)
            initial = self._source_item(initial_item, _time(initial_request["finished"]), initial_item.title or "", initial_metadata,
                                        {"list": initial_request["raw_ref"]}, initial_request["final_url"])
            store.upsert_source_item(initial, stock_code=initial_request["stock"], content=None,
                                     updated_at=_utc(initial_request["finished"]))
        store.upsert_source_item(item, stock_code=observed_request["stock"], content=content, updated_at=_utc(updated))
        engine.db.execute("""INSERT INTO compatible_posts(post_id,fingerprint,stock_code,list_request,detail_request,content_source,body_complete,metadata,updated_at)
                            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(post_id) DO UPDATE SET
                            fingerprint=excluded.fingerprint,stock_code=excluded.stock_code,list_request=excluded.list_request,
                            detail_request=excluded.detail_request,content_source=excluded.content_source,body_complete=excluded.body_complete,
                            metadata=excluded.metadata,updated_at=excluded.updated_at""",
                          (post["post_id"], fingerprint, observed_request["stock"], observed_request["id"],
                           detail_request["id"] if detail_request else None, provenance["content_source"],
                           int(content is not None), _json(provenance), _utc(updated)))
        return True

    def sync(self, engine, request_id=None):
        """Repair at startup or project one committed response, without I/O to source."""
        with engine._mutex:
            if engine._closed or engine._inflight:
                raise CompatibleStorageError("请等待来源请求完成后同步兼容数据")
            if engine.db.in_transaction:
                raise CompatibleStorageError("原始响应及主台账必须先提交，再同步兼容数据")
            try:
                self._prepare(engine)
            except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
                raise CompatibleStorageError(f"兼容数据库初始化失败: {type(exc).__name__}: {exc}") from exc
            self._raw_cache.clear()
            self._list_cache.clear()
            if request_id is None:
                query, args = "SELECT * FROM posts ORDER BY rowid", ()
            else:
                if isinstance(request_id, bool) or not isinstance(request_id, int) or request_id < 1:
                    raise ValueError("request_id 必须为正整数")
                request = engine.db.execute("SELECT kind,post_id FROM requests WHERE id=?", (request_id,)).fetchone()
                if request is None:
                    raise ValueError("请求记录不存在")
                if request["kind"] == "detail":
                    query, args = "SELECT * FROM posts WHERE post_id=?", (request["post_id"],)
                else:
                    query = """SELECT p.* FROM posts p JOIN (SELECT DISTINCT post_id FROM observations WHERE request_id=?) o
                               ON o.post_id=p.post_id"""
                    args = (request_id,)
            processed, projected = 0, 0
            try:
                # If the cache commit fails, the compatible write already exists
                # and can be safely replayed. A cursor must never lead its data.
                with self._store(self.db_path) as store, engine.db, store.transaction():
                    for post in engine.db.execute(query, args):
                        processed += 1
                        projected += int(self._project_post(engine, store, post, request_id))
                return {"storage_version": VERSION, "db_path": str(self.db_path),
                        "processed": processed, "projected": projected,
                        "body_provenance_table": "experiment.sqlite3:compatible_posts",
                        "research_only": True, "model_database_eligible": False}
            except CompatibleStorageError:
                raise
            except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
                raise CompatibleStorageError(f"兼容数据投影失败: {type(exc).__name__}: {exc}") from exc
            finally:
                self._raw_cache.clear()
                self._list_cache.clear()
