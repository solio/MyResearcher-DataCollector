"""One app-local SQLite layout and stopped-worker staging conversion helpers.

Helpers never construct Engine, request a source, or publish a live database.
The caller owns transactions, backups, the process lock and atomic publication.
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import threading
import uuid

from myresearcher_collector.simple_store import SCHEMA as SIMPLE_SCHEMA, SimplePostStore
from myresearcher_collector.sources.eastmoney_guba import parser as guba

LAYOUT = "unified.v1"
STATE_COLUMNS = ("post_id", "item", "source_row", "status", "list_request", "detail_request", "detail_payload", "content_source")
LEGACY_TABLES = ("posts", "backfill_resume", "backfill_coverage", "backfill_page_anchors")
OPERATIONAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY,config TEXT NOT NULL,created REAL NOT NULL,started REAL);
CREATE TABLE IF NOT EXISTS coverage(job INTEGER,stock TEXT,pages INTEGER DEFAULT 0,rows INTEGER DEFAULT 0,
  earliest TEXT,latest TEXT,under_pages INTEGER DEFAULT 0,list_complete INTEGER DEFAULT 0,
  boundary INTEGER DEFAULT 0,stop_reason TEXT,source_count INTEGER,gaps TEXT DEFAULT '[]',PRIMARY KEY(job,stock));
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY,job INTEGER,kind TEXT,stock TEXT,page INTEGER,
  post_id TEXT,url TEXT,original_url TEXT,status TEXT DEFAULT 'pending',hops INTEGER DEFAULT 0,
  purpose TEXT DEFAULT 'forward',recovery_id INTEGER);
CREATE TABLE IF NOT EXISTS http_post_state(post_id TEXT PRIMARY KEY,item TEXT NOT NULL,source_row TEXT NOT NULL,
  status TEXT DEFAULT 'pending',list_request INTEGER,detail_request INTEGER,detail_payload TEXT,content_source TEXT);
CREATE TABLE IF NOT EXISTS associations(job INTEGER,stock TEXT,post_id TEXT,eligible INTEGER,
  request_id INTEGER,PRIMARY KEY(job,stock,post_id));
CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY,job INTEGER,request_id INTEGER,stock TEXT,
  page INTEGER,post_id TEXT,source_row TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS obs_stock_id ON observations(job,stock,post_id);
CREATE INDEX IF NOT EXISTS obs_request_rows ON observations(request_id,id);
CREATE TABLE IF NOT EXISTS page_observations(request_id INTEGER PRIMARY KEY,job INTEGER,stock TEXT,
  page INTEGER,source_count INTEGER,rows INTEGER,new_ids INTEGER,overlap INTEGER,id_sha256 TEXT,
  earliest TEXT,latest TEXT,purpose TEXT DEFAULT 'forward');
CREATE INDEX IF NOT EXISTS page_observation_stock ON page_observations(stock,request_id);
CREATE TABLE IF NOT EXISTS run_segments(id INTEGER PRIMARY KEY,job INTEGER,started REAL,ended REAL,
  probe INTEGER,stop_reason TEXT,attempts_at_start INTEGER);
CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY,job INTEGER,task INTEGER,kind TEXT,stock TEXT,
  page INTEGER,post_id TEXT,url TEXT,started REAL,finished REAL,outcome TEXT DEFAULT 'reserved',
  http_status INTEGER,response_bytes INTEGER,sha256 TEXT,raw_ref TEXT,error TEXT,analysis TEXT,
  headers TEXT,final_url TEXT,probe INTEGER DEFAULT 0,network_attempted INTEGER,
  purpose TEXT DEFAULT 'forward',probe_only INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS requests_task_id ON requests(task,id DESC);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,job INTEGER,created REAL,kind TEXT,message TEXT,evidence TEXT);
CREATE TABLE IF NOT EXISTS frontiers(job INTEGER,stock TEXT,payload TEXT NOT NULL,PRIMARY KEY(job,stock));
CREATE TABLE IF NOT EXISTS recoveries(job INTEGER,stock TEXT,payload TEXT NOT NULL,PRIMARY KEY(job,stock));
CREATE TABLE IF NOT EXISTS compatible_posts(post_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,
  stock_code TEXT NOT NULL,list_request INTEGER NOT NULL,detail_request INTEGER,content_source TEXT NOT NULL,
  body_complete INTEGER NOT NULL,metadata TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_compatible_observations_request ON observations(request_id,post_id);
CREATE INDEX IF NOT EXISTS idx_compatible_observations_post ON observations(post_id,id DESC);
CREATE TABLE IF NOT EXISTS detail_enrichment_skips(source TEXT NOT NULL,source_item_id TEXT NOT NULL,
  stock_code TEXT NOT NULL,reason TEXT NOT NULL,first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,
  attempts INTEGER NOT NULL,PRIMARY KEY(source,source_item_id));
"""
VIEW_SQL = """CREATE VIEW IF NOT EXISTS http_posts AS
SELECT s.rowid AS rowid,s.post_id,s.item,s.source_row,s.status,s.list_request,s.detail_request,s.detail_payload,
  CASE WHEN s.status='complete' THEN p.content ELSE json_extract(s.item,'$.title') END AS content,
  s.content_source
FROM http_post_state s LEFT JOIN posts p
ON p.source='eastmoney_guba' AND p.source_item_id=s.post_id"""


def _get(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _set(conn, key, value):
    conn.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, json.dumps(value, ensure_ascii=False, separators=(",", ":"))))


def _statements(conn, schema):
    # These schemas contain no trigger bodies. execute() preserves the caller's
    # transaction, unlike sqlite3.executescript()'s implicit early commit.
    for statement in schema.split(";"):
        if statement.strip():
            conn.execute(statement)


def simple_store_adapter(conn, db_path):
    """Borrow the unified connection; caller must never close this adapter."""
    store = SimplePostStore.__new__(SimplePostStore)
    store.db_path, store.conn = Path(db_path), conn
    store._transaction_depth = 1
    return store


def initialize_schema(conn, instance_id=None):
    """Create the unified layout without changing collection state or committing."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    _statements(conn, SIMPLE_SCHEMA)
    _statements(conn, OPERATIONAL_SCHEMA)
    for table, column, declaration in (("tasks", "purpose", "TEXT DEFAULT 'forward'"), ("tasks", "recovery_id", "INTEGER"),
                                       ("requests", "purpose", "TEXT DEFAULT 'forward'"), ("requests", "probe_only", "INTEGER DEFAULT 0"),
                                       ("page_observations", "purpose", "TEXT DEFAULT 'forward'")):
        if column not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
    conn.execute(VIEW_SQL)
    current = _get(conn, "instance_id")
    if current and instance_id and current != instance_id:
        raise ValueError("实例 UUID 与统一数据库所有者不一致")
    identity = current or instance_id or str(uuid.uuid4())
    if not isinstance(identity, str) or str(uuid.UUID(identity)) != identity:
        raise ValueError("统一数据库需要合法稳定实例 UUID")
    owner = _get(conn, "compatible_storage_instance")
    if owner is not None and owner != identity:
        raise ValueError("统一数据库属于另一个实例")
    _set(conn, "instance_id", identity)
    _set(conn, "compatible_storage_instance", identity)
    _set(conn, "storage_layout", LAYOUT)
    return identity


def validate_layout(conn, instance_id=None):
    """Read-only ownership/schema check before any runtime write."""
    names = {r[0]: r[1] for r in conn.execute("SELECT name,type FROM sqlite_master")}
    if names.get("meta") != "table" or _get(conn, "storage_layout") != LAYOUT:
        raise ValueError("collector.db 尚未迁移到单库布局；请停止服务并执行 ./migrate-storage.sh")
    identity = _get(conn, "instance_id")
    if not isinstance(identity, str) or str(uuid.UUID(identity)) != identity or _get(conn, "compatible_storage_instance") != identity:
        raise ValueError("collector.db 缺少有效实例所有权，拒绝写入")
    if instance_id is not None and identity != instance_id:
        raise ValueError("collector.db 实例所有权不一致")
    with closing(sqlite3.connect(":memory:")) as expected:
        _statements(expected, SIMPLE_SCHEMA)
        _statements(expected, OPERATIONAL_SCHEMA)
        required = [r[0] for r in expected.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in required:
            if names.get(table) != "table" or conn.execute(f"PRAGMA table_info({table})").fetchall() != expected.execute(f"PRAGMA table_info({table})").fetchall():
                # Connection row_factory must not affect schema comparison.
                actual = [tuple(r) for r in conn.execute(f"PRAGMA table_info({table})")]
                desired = [tuple(r) for r in expected.execute(f"PRAGMA table_info({table})")]
                if actual != desired:
                    raise ValueError(f"统一数据库 {table} 字段/主键不符合契约")
        if names.get("http_posts") != "view":
            raise ValueError("统一数据库缺少 http_posts 只读视图")
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='http_posts'").fetchone()[0]
        normal = lambda value: " ".join(value.lower().split()).replace("if not exists ", "")
        if normal(sql) != normal(VIEW_SQL):
            raise ValueError("统一数据库 http_posts 视图不符合契约")
    return identity


def guard_runtime_path(data_dir):
    directory = Path(data_dir).resolve()
    if directory == (Path(__file__).resolve().parents[2] / "data").resolve():
        raise ValueError("实验数据目录不能使用仓库生产 data 目录")
    if any((directory / name).exists() or (directory / name).is_symlink() for name in ("experiment.sqlite3", "experiment.sqlite3-wal", "experiment.sqlite3-shm")):
        raise RuntimeError("检测到旧版 experiment.sqlite3；请停止 worker 并在应用目录执行 ./migrate-storage.sh，再启动 v5")
    receipt = directory / "storage-migration.json"
    if receipt.is_symlink():
        raise ValueError("迁移回执不能是符号链接")
    if receipt.exists():
        try:
            phase = json.loads(receipt.read_text(encoding="utf-8")).get("phase")
        except (ValueError, OSError, AttributeError) as exc:
            raise RuntimeError("迁移回执不完整；请停止服务并执行 ./migrate-storage.sh") from exc
        if phase != "complete":
            raise RuntimeError("单库迁移尚未完成；请停止服务并执行 ./migrate-storage.sh 恢复迁移")
    for name in ("collector.db", "collector.db-wal", "collector.db-shm", "worker.lock", "raw"):
        if (directory / name).is_symlink():
            raise ValueError(f"{name} 不能是符号链接")
    path = directory / "collector.db"
    if path.is_symlink():
        raise ValueError("collector.db 不能是符号链接")
    if path.exists():
        if not path.is_file():
            raise ValueError("collector.db 必须是文件")
        try:
            with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as conn:
                validate_layout(conn)
        except sqlite3.Error as exc:
            raise ValueError("collector.db 结构不可验证，拒绝写入；请检查备份或执行 ./migrate-storage.sh") from exc
    return path


class _OfflineLedger:
    def __init__(self, conn, data_dir):
        self.db, self.data_dir = conn, Path(data_dir).resolve()
        self.raw_dir, self._mutex = self.data_dir / "raw", threading.RLock()
        self._closed, self._inflight = False, False

    def _get(self, key, default=None):
        return _get(self.db, key, default)

    def _set(self, key, value):
        _set(self.db, key, value)


def convert_legacy_snapshot(staged_db_path, legacy_collector_snapshot_path=None, data_dir=None):
    """Convert only a caller-owned backup staging file; no Engine/state transition."""
    stage = Path(staged_db_path).resolve()
    directory = Path(data_dir).resolve() if data_dir is not None else stage.parent
    if stage in {directory / "collector.db", directory / "experiment.sqlite3"}:
        raise ValueError("转换只能操作离线 staging 副本，不能直接改活动数据库")
    if not stage.is_file() or Path(staged_db_path).is_symlink():
        raise ValueError("迁移 staging 数据库必须是独立文件")
    with closing(sqlite3.connect(stage)) as conn:
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "http_post_state" in tables and _get(conn, "storage_layout") == LAYOUT:
            validate_layout(conn)
            return {"storage_layout": LAYOUT, "instance_id": _get(conn, "instance_id"), "already_unified": True}
        columns = {r[1] for r in conn.execute("PRAGMA table_info(posts)")}
        if not {"post_id", "item", "source_row", "status", "content"}.issubset(columns):
            raise ValueError("旧台账 posts 不符合来源状态结构")
        count = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        with conn:
            conn.execute("ALTER TABLE posts RENAME TO _http_legacy_posts")
            identity = initialize_schema(conn)
            if legacy_collector_snapshot_path is not None:
                snapshot = Path(legacy_collector_snapshot_path).resolve()
                if Path(legacy_collector_snapshot_path).is_symlink() or not snapshot.is_file() or snapshot == stage:
                    raise ValueError("旧兼容库快照路径无效")
                with closing(sqlite3.connect(f"{snapshot.as_uri()}?mode=ro", uri=True)) as source:
                    for table in LEGACY_TABLES:
                        actual = [tuple(r) for r in source.execute(f"PRAGMA table_info({table})")]
                        expected = [tuple(r) for r in conn.execute(f"PRAGMA table_info({table})")]
                        if actual != expected:
                            raise ValueError(f"旧兼容库 {table} 结构不符合原 SimplePostStore")
                        placeholders = ",".join("?" for _ in actual)
                        conn.executemany(f"INSERT INTO {table} VALUES({placeholders})", source.execute(f"SELECT * FROM {table}"))
            for row in conn.execute("SELECT rowid AS legacy_rowid,* FROM _http_legacy_posts ORDER BY rowid"):
                post = dict(row)
                payload = json.loads(post["detail_payload"]) if post.get("detail_payload") else None
                if payload is not None:
                    if not isinstance(payload, dict):
                        raise ValueError("旧详情 metadata 必须是 JSON 对象")
                    payload.pop("post_content", None)
                content_source = post.get("content_source") or ("detail_body" if post["status"] == "complete" else "list_title")
                conn.execute("INSERT INTO http_post_state(rowid,post_id,item,source_row,status,list_request,detail_request,detail_payload,content_source) VALUES(?,?,?,?,?,?,?,?,?)",
                             (post["legacy_rowid"], post["post_id"], post["item"], post["source_row"], post["status"],
                              post["list_request"], post["detail_request"], json.dumps(payload, ensure_ascii=False, separators=(",", ":")) if payload is not None else None, content_source))
        from compatible_store import CompatibleDataStore
        ledger = _OfflineLedger(conn, directory)
        projection = CompatibleDataStore(directory, identity)
        projection.db_path = stage
        projection._prepare(ledger)
        # The legacy temporary rows carry the only staging body copy until each
        # body has been verified against raw and placed in original posts.
        with conn:
            store = simple_store_adapter(conn, stage)
            for post in conn.execute("SELECT * FROM _http_legacy_posts ORDER BY rowid"):
                projection._project_post(ledger, store, post, None)
            conn.execute("DROP TABLE _http_legacy_posts")
            ledger._set("data_storage", {"schema_version": "legacy_posts", "storage_layout": LAYOUT, "status": "ready",
                                         "db_path": str(directory / "collector.db"), "body_provenance_table": "collector.db:compatible_posts",
                                         "single_runtime_database": True, "last_error": None, "research_only": True, "model_database_eligible": False})
        validate_layout(conn, identity)
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("统一 staging 数据库完整性检查失败")
        return {"storage_layout": LAYOUT, "instance_id": identity, "posts": count,
                "body_complete": conn.execute("SELECT COUNT(*) FROM posts WHERE content IS NOT NULL").fetchone()[0], "already_unified": False}
