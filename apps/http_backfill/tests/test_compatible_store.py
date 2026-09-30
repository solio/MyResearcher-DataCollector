"""Offline compatibility with the existing browser storage and enrich query."""
from contextlib import closing
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "tests"))
from compatible_store import CompatibleDataStore, CompatibleStorageError
from myresearcher_collector.detail_enrichment import _legacy_candidates, _SkipLedger, _skip_ledger_path
from myresearcher_collector.simple_store import SimplePostStore
from myresearcher_collector.sources.eastmoney_guba import parser as guba
from test_core import Clock, row, list_html, detail_html


class Ledger:
    """Committed v1/v2 app evidence fixture; no source transport exists."""
    def __init__(self, directory):
        self.data_dir = Path(directory)
        self.raw_dir = self.data_dir / "raw"
        self.raw_dir.mkdir()
        self._mutex = threading.RLock()
        self._inflight = self._closed = False
        self.clock = Clock()
        self.db = sqlite3.connect(self.data_dir / "experiment.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE posts(post_id TEXT PRIMARY KEY,item TEXT,source_row TEXT,status TEXT,
                             list_request INTEGER,detail_request INTEGER,detail_payload TEXT,content TEXT);
          CREATE TABLE requests(id INTEGER PRIMARY KEY,kind TEXT,stock TEXT,post_id TEXT,url TEXT,
                                started REAL,finished REAL,outcome TEXT,http_status INTEGER,
                                response_bytes INTEGER,sha256 TEXT,raw_ref TEXT,final_url TEXT,
                                analysis TEXT,error TEXT,headers TEXT);
          CREATE TABLE observations(id INTEGER PRIMARY KEY,request_id INTEGER,stock TEXT,post_id TEXT,source_row TEXT);
        """)
        with self.db:
            self._set("instance_id", "offline-instance")

    def _get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _set(self, key, value):
        self.db.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def request(self, kind, body, stock="601012", post_id=None, outcome="real_data", http_status=200):
        url = (f"https://guba.eastmoney.com/news,{stock},{post_id}.html" if kind == "detail"
               else f"https://guba.eastmoney.com/list,{stock},f.html")
        started = self.clock()
        self.clock.advance(1)
        with self.db:
            request = self.db.execute("""INSERT INTO requests(kind,stock,post_id,url,started,finished,outcome,http_status,
                                      response_bytes,sha256,final_url) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                                      (kind, stock, post_id, url, started, self.clock(), outcome, http_status,
                                       len(body), hashlib.sha256(body).hexdigest(), url)).lastrowid
            ref = f"raw/{request:09d}.body"
            (self.data_dir / ref).write_bytes(body)
            self.db.execute("UPDATE requests SET raw_ref=? WHERE id=?", (ref, request))
        return request

    def list(self, rows, stock="601012", old_pending=True):
        body = list_html(rows)
        request = self.request("list", body, stock)
        parsed = {r.source_item_id: r for r in guba.parse_list_page(body.decode(), stock).rows}
        with self.db:
            for raw in rows:
                post_id = str(raw["post_id"])
                self.db.execute("INSERT INTO observations(request_id,stock,post_id,source_row) VALUES(?,?,?,?)",
                                (request, stock, post_id, json.dumps(raw)))
                if post_id in parsed:
                    item = parsed[post_id]
                    self.db.execute("INSERT OR IGNORE INTO posts(post_id,item,source_row,status,list_request) VALUES(?,?,?,?,?)",
                                    (post_id, json.dumps(asdict(item), default=lambda x: x.isoformat()), json.dumps(raw),
                                     "pending" if old_pending else "list_only", request))
        return request

    def detail(self, source_row, content="真实正文"):
        post_id = str(source_row["post_id"])
        body = detail_html(source_row, content)
        request = self.request("detail", body, source_row["stockbar_code"], post_id)
        with self.db:
            self.db.execute("UPDATE posts SET status='complete',content=?,detail_request=?,detail_payload=? WHERE post_id=?",
                            (content, request, json.dumps(guba._embedded_json(body.decode(), "post_article")), post_id))
        return request

    def removed(self, source_row):
        body = (REPO / "tests/fixtures/eastmoney_guba/detail_enrichment_404.html").read_bytes()
        post_id = str(source_row["post_id"])
        request = self.request("detail", body, source_row["stockbar_code"], post_id,
                               outcome="detail_unavailable", http_status=404)
        with self.db:
            self.db.execute("UPDATE posts SET status='removed',detail_request=? WHERE post_id=?", (request, post_id))
        return request

    def close(self):
        self.db.close()
        self._closed = True


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(self.tmp.name)
        self.writer = CompatibleDataStore(self.tmp.name, "offline-instance")

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    @staticmethod
    def source(post_id="1001", length=9, **fields):
        return row(post_id, post_title="源" * length, **fields)

    def posts(self):
        with closing(sqlite3.connect(Path(self.tmp.name) / "collector.db")) as db:
            db.row_factory = sqlite3.Row
            return {r["source_item_id"]: dict(r) for r in db.execute("SELECT * FROM posts")}

    def raw_hashes(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.ledger.raw_dir.iterdir()}

    def candidates(self, skipped=frozenset()):
        store = SimplePostStore(Path(self.tmp.name) / "collector.db")
        try:
            return [c.source_item_id for c in _legacy_candidates(store, "601012", include_short_titles=False, skipped=skipped)]
        finally:
            store.close()

    def test_original_candidate_rule_9_40_41_and_no_title_as_content(self):
        self.ledger.list([self.source("1001", 9), self.source("1002", 40), self.source("1003", 41)])
        self.writer.sync(self.ledger)
        self.assertEqual(set(self.candidates()), {"1002", "1003"})
        for record in self.posts().values():
            self.assertIsNone(record["content"])
        metadata = [json.loads(r[0]) for r in self.ledger.db.execute("SELECT metadata FROM compatible_posts ORDER BY post_id")]
        self.assertEqual([r["detail_required"] for r in metadata], [False, True, True])

    def test_legacy_complete_short_body_is_retained_and_empty_body_is_not_pending(self):
        short, long = self.source("1001", 9), self.source("1002", 40)
        self.ledger.list([short, long])
        self.ledger.detail(short, "已有短帖真实正文")
        self.ledger.detail(long, "")
        self.writer.sync(self.ledger)
        posts = self.posts()
        self.assertEqual(posts["1001"]["content"], "已有短帖真实正文")
        self.assertEqual(posts["1002"]["content"], "")
        self.assertEqual(self.candidates(), [])
        self.assertEqual(self.ledger.db.execute("SELECT SUM(body_complete) FROM compatible_posts").fetchone()[0], 2)

    def test_original_schema_and_no_invented_backfill_coverage(self):
        self.ledger.list([self.source()])
        self.writer.sync(self.ledger)
        with closing(sqlite3.connect(self.writer.db_path)) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(tables, {"posts", "backfill_resume", "backfill_coverage", "backfill_page_anchors"})
            for table in ("backfill_resume", "backfill_coverage", "backfill_page_anchors"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            pk = {r[1]: r[5] for r in db.execute("PRAGMA table_info(posts)") if r[5]}
            self.assertEqual(pk, {"source": 1, "source_item_id": 2})

    def test_idempotent_migration_does_not_copy_raw_or_update_timestamp_to_now(self):
        source = self.source(length=40)
        list_request = self.ledger.list([source])
        list_time = self.ledger.db.execute("SELECT finished FROM requests WHERE id=?", (list_request,)).fetchone()[0]
        self.ledger.clock.advance(60)
        detail_request = self.ledger.detail(source)
        detail_time = self.ledger.db.execute("SELECT finished FROM requests WHERE id=?", (detail_request,)).fetchone()[0]
        raw = self.raw_hashes()
        self.ledger.clock.advance(100000)
        first = self.writer.sync(self.ledger)
        before = self.posts()
        second = self.writer.sync(self.ledger)
        self.assertEqual(first["projected"], 1)
        self.assertEqual(second["projected"], 0)
        self.assertEqual(self.posts(), before)
        self.assertEqual(self.raw_hashes(), raw)
        self.assertEqual(before["1001"]["created_at"], datetime_utc(list_time))
        self.assertEqual(before["1001"]["updated_at"], datetime_utc(detail_time))

    def test_future_list_does_not_overwrite_body_and_restart_keeps_latest_snapshot(self):
        source = self.source(length=40, post_click_count=2)
        request = self.ledger.list([source])
        self.writer.sync(self.ledger, request)
        detail = self.ledger.detail(source, "保留正文")
        self.writer.sync(self.ledger, detail)
        newer = {**source, "post_click_count": 8}
        request = self.ledger.list([newer])
        self.writer.sync(self.ledger, request)
        before = self.posts()
        self.assertEqual(before["1001"]["content"], "保留正文")
        self.assertEqual(before["1001"]["read_count"], 8)
        self.writer.sync(self.ledger)
        self.assertEqual(self.posts(), before)

    def test_cross_bar_stock_uses_requested_scope_and_keeps_canonical_metadata(self):
        self.ledger.list([self.source(stock="600519")], stock="601012")
        self.writer.sync(self.ledger)
        self.assertEqual(self.posts()["1001"]["stock_code"], "601012")
        metadata = json.loads(self.ledger.db.execute("SELECT metadata FROM compatible_posts").fetchone()[0])
        self.assertEqual(metadata["canonical_bar_code"], "600519")
        self.assertEqual(metadata["requested_bar_code"], "601012")

    def test_incremental_request_processes_only_affected_posts(self):
        self.ledger.list([self.source("1001"), self.source("1002", 40)])
        self.writer.sync(self.ledger)
        request = self.ledger.detail(self.source("1002", 40))
        result = self.writer.sync(self.ledger, request)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(self.posts()["1002"]["content"], "真实正文")
        request = self.ledger.list([self.source("1003")])
        result = self.writer.sync(self.ledger, request)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(len(self.posts()), 3)

    def test_one_list_page_is_parsed_once_per_sync(self):
        self.ledger.list([self.source(str(1000 + n)) for n in range(80)])
        original = guba.parse_list_page
        with patch.object(guba, "parse_list_page", wraps=original) as parse:
            self.writer.sync(self.ledger)
            self.assertEqual(parse.call_count, 1)

    def _navigation_failure(self, request, analysis=None, error="无法建立非置顶来源行锚点，不能猜测下一历史页"):
        with self.ledger.db:
            self.ledger.db.execute("UPDATE requests SET outcome='schema_error',analysis=?,error=? WHERE id=?",
                                   (json.dumps(analysis) if analysis is not None else None, error, request))
            self.ledger._set("active_halt", "schema_error")
        return request

    def test_validated_navigation_failure_projects_acquired_list_and_keeps_source_halt(self):
        request = self._navigation_failure(self.ledger.list([self.source()]), {"list_structure_validated": True})
        result = self.writer.sync(self.ledger, request)
        self.assertEqual(result["projected"], 1)
        self.assertIsNone(self.posts()["1001"]["content"])
        self.assertEqual(self.ledger._get("active_halt"), "schema_error")
        self.assertEqual(self.writer.sync(self.ledger)["projected"], 0)

    def test_v2_navigation_failure_requires_full_raw_and_observation_identity_validation(self):
        request = self._navigation_failure(self.ledger.list([self.source()]))
        self.writer.sync(self.ledger)
        self.assertEqual(len(self.posts()), 1)
        with self.ledger.db:
            self.ledger.db.execute("UPDATE observations SET source_row=? WHERE request_id=?", (json.dumps({**self.source(), "post_title": "假标题"}), request))
        with self.assertRaisesRegex(CompatibleStorageError, "观察行.*身份"):
            self.writer.sync(self.ledger)
        self.assertEqual(self.ledger._get("active_halt"), "schema_error")

    def test_unknown_schema_error_and_non_boolean_validation_flag_do_not_project(self):
        request = self.ledger.list([self.source()])
        for analysis in (None, {"list_structure_validated": False}, {"list_structure_validated": 1}, {"list_structure_validated": "true"}):
            with self.subTest(analysis=analysis):
                self._navigation_failure(request, analysis, error="unknown future schema")
                with self.assertRaisesRegex(CompatibleStorageError, "成功来源响应"):
                    self.writer.sync(self.ledger)
                self.assertEqual(self.posts(), {})

    def test_navigation_validation_flag_never_bypasses_framing_challenge_status_or_rc(self):
        request = self.ledger.list([self.source()])
        self._navigation_failure(request, {"list_structure_validated": True})
        record = self.ledger.db.execute("SELECT * FROM requests WHERE id=?", (request,)).fetchone()
        path = self.ledger.data_dir / record["raw_ref"]
        cases = [
            (list_html([self.source()], rc=True), 200, {}, "rc/re"),
            (list_html([self.source()], extra='<div id="emcaptcha">拖动滑块</div>'), 200, {}, "验证遮罩"),
            (list_html([self.source()]), 429, {}, "HTTP 200"),
            (list_html([self.source()]), 200, {"content-length": "99999"}, "Content-Length"),
        ]
        for body, status, headers, message in cases:
            with self.subTest(message=message):
                path.write_bytes(body)
                with self.ledger.db:
                    self.ledger.db.execute("UPDATE requests SET sha256=?,response_bytes=?,http_status=?,headers=? WHERE id=?",
                                           (hashlib.sha256(body).hexdigest(), len(body), status, json.dumps(headers), request))
                with self.assertRaisesRegex(CompatibleStorageError, message):
                    self.writer.sync(self.ledger)
                self.assertEqual(self.posts(), {})

    def test_raw_hash_failure_preserves_primary_data_and_can_repair_offline(self):
        request = self.ledger.list([self.source()])
        path = self.ledger.data_dir / self.ledger.db.execute("SELECT raw_ref FROM requests WHERE id=?", (request,)).fetchone()[0]
        body = path.read_bytes()
        path.write_bytes(body + b"tampered")
        with self.assertRaisesRegex(CompatibleStorageError, "SHA-256"):
            self.writer.sync(self.ledger)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 1)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 1)
        self.assertEqual(self.posts(), {})
        path.write_bytes(body)
        self.writer.sync(self.ledger)
        self.assertEqual(len(self.posts()), 1)

    def test_unknown_existing_collector_is_rejected_without_mutation(self):
        with closing(sqlite3.connect(self.writer.db_path)) as db:
            db.execute("CREATE TABLE protected(value TEXT)")
            db.commit()
        before = self.writer.db_path.read_bytes()
        with self.assertRaisesRegex(CompatibleStorageError, "未标记"):
            self.writer.sync(self.ledger)
        self.assertEqual(self.writer.db_path.read_bytes(), before)

    def test_owned_schema_drift_is_rejected_before_simple_store_scripts(self):
        self.writer.sync(self.ledger)
        with closing(sqlite3.connect(self.writer.db_path)) as db:
            db.execute("ALTER TABLE posts ADD COLUMN unexpected TEXT")
            db.commit()
        before = self.writer.db_path.read_bytes()
        with self.assertRaisesRegex(CompatibleStorageError, "字段或主键"):
            self.writer.sync(self.ledger)
        self.assertEqual(self.writer.db_path.read_bytes(), before)

    def test_root_production_path_rejected_without_opening_database(self):
        with self.assertRaisesRegex(CompatibleStorageError, "原生产"):
            CompatibleDataStore(REPO / "data", "offline-instance")

    def test_original_skip_ledger_excludes_source_unavailable_not_deleted_claim(self):
        source = self.source(length=40)
        self.ledger.list([source])
        self.ledger.removed(source)
        self.writer.sync(self.ledger)
        skipped = _SkipLedger(_skip_ledger_path(self.writer.db_path)).skipped_ids(guba.SOURCE)
        self.assertEqual(skipped, {"1001"})
        self.assertEqual(self.candidates(skipped), [])
        self.assertIsNone(self.posts()["1001"]["content"])
        with closing(sqlite3.connect(_skip_ledger_path(self.writer.db_path))) as db:
            before = db.execute("SELECT first_seen_at,attempts FROM detail_enrichment_skips").fetchone()
        self.writer.sync(self.ledger)
        with closing(sqlite3.connect(_skip_ledger_path(self.writer.db_path))) as db:
            self.assertEqual(db.execute("SELECT first_seen_at,attempts FROM detail_enrichment_skips").fetchone(), before)

    def test_thread_affinity_uses_new_simple_store_on_worker_thread(self):
        self.ledger.list([self.source()])
        errors = []
        def write():
            try:
                self.writer.sync(self.ledger)
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=write)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(self.posts()), 1)

    def test_projection_after_compatible_commit_is_replayable_if_cursor_is_lost(self):
        source = self.source(length=40)
        self.ledger.list([source])
        self.ledger.detail(source)
        self.writer.sync(self.ledger)
        before = self.posts()
        with self.ledger.db:
            self.ledger.db.execute("DELETE FROM compatible_posts")
        self.writer.sync(self.ledger)
        self.assertEqual(self.posts(), before)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM compatible_posts").fetchone()[0], 1)

    def test_sqlite_projection_failure_rolls_back_cache_and_can_retry_locally(self):
        self.ledger.list([self.source()])
        original = SimplePostStore.upsert_source_item
        def fail(*args, **kwargs):
            raise sqlite3.OperationalError("offline write failure")
        with patch.object(SimplePostStore, "upsert_source_item", fail):
            with self.assertRaisesRegex(CompatibleStorageError, "offline write failure"):
                self.writer.sync(self.ledger)
        self.assertEqual(self.posts(), {})
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM compatible_posts").fetchone()[0], 0)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 1)
        self.writer.sync(self.ledger)
        self.assertEqual(len(self.posts()), 1)


def datetime_utc(epoch):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


if __name__ == "__main__":
    unittest.main()
