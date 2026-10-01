"""Offline legacy snapshots, stopped-worker publication and crash recovery."""
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

APP = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "tests"))
from migrate_storage import migrate, MigrationError, RECEIPT_NAME, _manifest
from compatible_store import CompatibleDataStore, _json, _utc
from federation import init_export_schema, journal_projection
from myresearcher_collector.simple_store import SimplePostStore
from myresearcher_collector.detail_enrichment import _SkipLedger, _skip_ledger_path
from myresearcher_collector.sources.eastmoney_guba import parser as guba
from myresearcher_collector.sources.eastmoney_guba.content_rules import list_title_metadata, detail_body_metadata, detail_enrichment_trigger
from test_core import Clock, row, list_html, detail_html


class Crash(BaseException):
    pass


class LegacyLedger:
    """Frozen former physical schema; independent of the new runtime fixtures."""
    def __init__(self, directory):
        self.data_dir = Path(directory)
        self.raw_dir = self.data_dir / "raw"
        self.raw_dir.mkdir()
        self._mutex = threading.RLock()
        self._closed = self._inflight = False
        self.clock = Clock()
        self.db = sqlite3.connect(self.data_dir / "experiment.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE posts(post_id TEXT PRIMARY KEY,item TEXT,source_row TEXT,status TEXT,
            list_request INTEGER,detail_request INTEGER,detail_payload TEXT,content TEXT);
          CREATE TABLE requests(id INTEGER PRIMARY KEY,job INTEGER,task INTEGER,kind TEXT,stock TEXT,page INTEGER,
            post_id TEXT,url TEXT,started REAL,finished REAL,outcome TEXT DEFAULT 'reserved',http_status INTEGER,
            response_bytes INTEGER,sha256 TEXT,raw_ref TEXT,error TEXT,analysis TEXT,headers TEXT,final_url TEXT,
            probe INTEGER DEFAULT 0,network_attempted INTEGER);
          CREATE TABLE observations(id INTEGER PRIMARY KEY,job INTEGER,request_id INTEGER,stock TEXT,page INTEGER,
            post_id TEXT,source_row TEXT NOT NULL);
        """)

    def _get(self, key, default=None):
        value = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(value[0]) if value else default

    def _set(self, key, value):
        self.db.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def request(self, kind, body, stock="601012", post_id=None, outcome="real_data", http_status=200):
        url = (f"https://guba.eastmoney.com/news,{stock},{post_id}.html" if kind == "detail"
               else f"https://guba.eastmoney.com/list,{stock},f.html")
        started = self.clock()
        self.clock.advance(1)
        with self.db:
            request = self.db.execute("""INSERT INTO requests(kind,stock,post_id,url,started,finished,outcome,http_status,
              response_bytes,sha256,final_url) VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (kind, stock, post_id, url, started,
                  self.clock(), outcome, http_status, len(body), hashlib.sha256(body).hexdigest(), url)).lastrowid
            reference = f"raw/{request:09d}.body"
            (self.data_dir / reference).write_bytes(body)
            self.db.execute("UPDATE requests SET raw_ref=? WHERE id=?", (reference, request))
        return request

    def list(self, rows):
        body = list_html(rows)
        request = self.request("list", body)
        parsed = {i.source_item_id: i for i in guba.parse_list_page(body.decode(), "601012").rows}
        with self.db:
            for raw in rows:
                post_id = str(raw["post_id"])
                self.db.execute("INSERT INTO observations(request_id,stock,post_id,source_row) VALUES(?,'601012',?,?)", (request, post_id, json.dumps(raw)))
                self.db.execute("INSERT INTO posts(post_id,item,source_row,status,list_request) VALUES(?,?,?,'pending',?)",
                                (post_id, json.dumps(asdict(parsed[post_id]), default=lambda x: x.isoformat()), json.dumps(raw), request))

    def detail(self, source_row, content):
        post_id = str(source_row["post_id"])
        body = detail_html(source_row, content)
        request = self.request("detail", body, post_id=post_id)
        with self.db:
            self.db.execute("UPDATE posts SET status='complete',content=?,detail_request=?,detail_payload=? WHERE post_id=?",
                            (content, request, json.dumps(guba._embedded_json(body.decode(), "post_article")), post_id))

    def removed(self, source_row):
        body = (REPO / "tests/fixtures/eastmoney_guba/detail_enrichment_404.html").read_bytes()
        request = self.request("detail", body, post_id=source_row["post_id"], outcome="detail_unavailable", http_status=404)
        with self.db:
            self.db.execute("UPDATE posts SET status='removed',detail_request=? WHERE post_id=?", (request, source_row["post_id"]))

    def close(self):
        self.db.close()
        self._closed = True


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.source = None
        self.sources = []

    def tearDown(self):
        for source in self.sources:
            if not source._closed:
                source.close()
        self.tmp.cleanup()

    def fixture(self, version=4):
        """Construct former physical layouts directly, without new Engine."""
        source = self.source = LegacyLedger(self.directory)
        self.sources.append(source)
        source.db.executescript("""
          CREATE TABLE jobs(id INTEGER PRIMARY KEY,config TEXT NOT NULL,created REAL NOT NULL,started REAL);
          CREATE TABLE tasks(id INTEGER PRIMARY KEY,job INTEGER,kind TEXT,stock TEXT,page INTEGER,post_id TEXT,
            url TEXT,original_url TEXT,status TEXT DEFAULT 'pending',hops INTEGER DEFAULT 0);
          CREATE TABLE coverage(job INTEGER,stock TEXT,pages INTEGER DEFAULT 0,rows INTEGER DEFAULT 0,earliest TEXT,latest TEXT,
            under_pages INTEGER DEFAULT 0,list_complete INTEGER DEFAULT 0,boundary INTEGER DEFAULT 0,stop_reason TEXT,
            source_count INTEGER,gaps TEXT DEFAULT '[]',PRIMARY KEY(job,stock));
          CREATE TABLE associations(job INTEGER,stock TEXT,post_id TEXT,eligible INTEGER,request_id INTEGER,
            PRIMARY KEY(job,stock,post_id));
          CREATE TABLE page_observations(request_id INTEGER PRIMARY KEY,job INTEGER,stock TEXT,page INTEGER,source_count INTEGER,
            rows INTEGER,new_ids INTEGER,overlap INTEGER,id_sha256 TEXT,earliest TEXT,latest TEXT);
          CREATE TABLE run_segments(id INTEGER PRIMARY KEY,job INTEGER,started REAL,ended REAL,probe INTEGER,
            stop_reason TEXT,attempts_at_start INTEGER);
          CREATE TABLE events(id INTEGER PRIMARY KEY,job INTEGER,created REAL,kind TEXT,message TEXT,evidence TEXT);
          CREATE TABLE frontiers(job INTEGER,stock TEXT,payload TEXT NOT NULL,PRIMARY KEY(job,stock));
          CREATE TABLE recoveries(job INTEGER,stock TEXT,payload TEXT NOT NULL,PRIMARY KEY(job,stock));
        """)
        self.identity = str(uuid.uuid4())
        with source.db:
            source._set("instance_id", self.identity)
            source._set("state", "blocked")
            source._set("active_halt", "challenge")
            source._set("next_due", source.clock() + 9999)
            source._set("reason", "原验证阻断须保留")
            source._set("halted_task_id", 7)
            source._set("job_id", 1)
            source.db.execute("INSERT INTO jobs VALUES(1,?,?,?)", (json.dumps({"stocks": ["601012"], "from_date": "2025-01-01", "to_date": "2025-01-31", "client": "curl", "interval_seconds": 60}), source.clock(), source.clock()))
            source.db.execute("INSERT INTO tasks(id,job,kind,stock,page,url,original_url) VALUES(7,1,'list','601012',2,?,?)", ("https://guba.eastmoney.com/list,601012,f_2.html",) * 2)
            source.db.execute("INSERT INTO coverage(job,stock,pages,rows,earliest,latest,source_count,gaps) VALUES(1,'601012',1,4,'2025-01-10','2025-01-10',4,'[]')")
            source.db.execute("INSERT INTO events VALUES(42,1,?,'challenge','阻断原记录','{}')", (source.clock(),))
            source.db.execute("INSERT INTO frontiers VALUES(1,'601012',?)", (json.dumps({"page": 1, "anchors": ["1001"]}),))
            source.db.execute("INSERT INTO recoveries VALUES(1,'601012',?)", (json.dumps({"phase": "seek", "generation": 3}),))
        rows = [row("1001"), row("1002"), row("1003", post_title="短标题九字保持原样"), row("1004")]
        source.list(rows)
        source.detail(rows[0], "必须完整保留的真实正文")
        source.detail(rows[1], "")
        source.removed(rows[3])
        source.request("list", '<title>身份核实</title><div id="emcaptcha">拖动滑块</div>'.encode(), outcome="access_block")
        with source.db:
            source.db.execute("UPDATE requests SET job=1,task=7,page=1,network_attempted=1")
            source.db.execute("UPDATE observations SET job=1,page=1")
            for post_id in ("1001", "1002", "1003", "1004"):
                source.db.execute("INSERT INTO associations VALUES(1,'601012',?,1,1)", (post_id,))
        if version == 1:
            with source.db:
                source.db.execute("DELETE FROM meta WHERE key='instance_id'")
            return source
        source.db.execute("ALTER TABLE posts ADD COLUMN content_source TEXT")
        source.db.execute("""CREATE TABLE compatible_posts(post_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,
          stock_code TEXT NOT NULL,list_request INTEGER NOT NULL,detail_request INTEGER,content_source TEXT NOT NULL,
          body_complete INTEGER NOT NULL,metadata TEXT NOT NULL,updated_at TEXT NOT NULL)""")
        with source.db:
            source._set("compatible_storage_instance", self.identity)
            source._set("compatible_storage_version", "http-backfill.simple-posts.v1")
            source.db.execute("UPDATE posts SET content_source=CASE WHEN status='complete' THEN 'detail_body' ELSE 'list_title' END")
            source.db.execute("UPDATE posts SET status='list_only',content=? WHERE post_id='1003'", (rows[2]["post_title"],))
            init_export_schema(source)
        parsed = {item.source_item_id: item for item in guba.parse_list_page((source.data_dir / "raw/000000001.body").read_text(), "601012").rows}
        store = SimplePostStore(source.data_dir / "collector.db")
        try:
            with source.db, store.transaction():
                for post in source.db.execute("SELECT * FROM posts ORDER BY rowid"):
                    self._legacy_projection(source, store, post, parsed[post["post_id"]])
                store.conn.execute("INSERT INTO backfill_resume VALUES('eastmoney_guba','601012','2024-01-01','2024-12-31',7)")
        finally:
            store.close()
        skips = _SkipLedger(_skip_ledger_path(source.data_dir / "collector.db"))
        for _ in range(2):
            skips.record(source=guba.SOURCE, source_item_id="1004", stock_code="601012", reason="detail_not_found",
                         observed_at=datetime.fromtimestamp(source.clock(), timezone.utc))
        return source

    def _legacy_projection(self, source, store, post, item):
        first = dict(source.db.execute("SELECT * FROM requests WHERE id=?", (post["list_request"],)).fetchone())
        detailed = dict(source.db.execute("SELECT * FROM requests WHERE id=?", (post["detail_request"],)).fetchone()) if post["detail_request"] else None
        metadata = list_title_metadata(item.source_metadata, item.title)
        acquired = first["finished"]
        content = None
        initial = CompatibleDataStore._source_item(item, datetime.fromtimestamp(acquired, timezone.utc), item.title or "", metadata,
                                                   {"list": first["raw_ref"]}, first["final_url"])
        store.upsert_source_item(initial, stock_code="601012", content=None, updated_at=_utc(acquired))
        current = initial
        if post["status"] == "complete":
            detail = guba.parse_detail_page((self.directory / detailed["raw_ref"]).read_text())
            merged = guba.merge_list_and_detail(item, detail)
            content = merged["content"]
            metadata = detail_body_metadata(merged["source_metadata"], title=item.title, trigger=detail_enrichment_trigger(item.title))
            acquired = max(acquired, detailed["finished"])
            current = CompatibleDataStore._source_item(item, datetime.fromtimestamp(acquired, timezone.utc), content, metadata,
                                                       {"list": first["raw_ref"], "detail": detailed["raw_ref"]}, detailed["final_url"])
            store.upsert_source_item(current, stock_code="601012", content=content, updated_at=_utc(acquired))
        provenance = {"content_source": "detail_body" if content is not None else "list_title",
                      "detail_required": detail_enrichment_trigger(item.title) is not None,
                      "detail_enrichment_trigger": detail_enrichment_trigger(item.title),
                      "canonical_bar_code": item.canonical_bar_code, "requested_bar_code": "601012",
                      "body_complete": content is not None, "research_only": True, "model_database_eligible": False,
                      "source_status": post["status"], "list_sha256": first["sha256"], "detail_sha256": detailed["sha256"] if detailed else None}
        fingerprint = hashlib.sha256(_json({"item": asdict(current), "legacy_content": content, "provenance": provenance,
                                           "list_request": first["id"], "detail_request": detailed["id"] if detailed else None}).encode()).hexdigest()
        source.db.execute("INSERT INTO compatible_posts VALUES(?,?,?,?,?,?,?,?,?)", (post["post_id"], fingerprint, "601012", first["id"],
                          detailed["id"] if detailed else None, provenance["content_source"], int(content is not None), _json(provenance), _utc(acquired)))
        journal_projection(source, store, post["post_id"], fingerprint, first, detailed, provenance, initial_request=first)

    def unified(self):
        with closing(sqlite3.connect(self.directory / "collector.db")) as db:
            db.row_factory = sqlite3.Row
            return {r["source_item_id"]: dict(r) for r in db.execute("SELECT * FROM posts")}

    def test_v4_preserves_halt_queue_coverage_skips_and_exact_immutable_export(self):
        source = self.fixture()
        source_manifest = _manifest(self.directory / "experiment.sqlite3")
        result = migrate(self.directory)
        self.assertEqual(result["status"], "migrated")
        self.assertTrue(result["experiment_removed"])
        self.assertEqual(result["state_posts"], 4)
        self.assertEqual(result["body_complete"], 2)
        self.assertEqual(result["verified_raw_files"], 5)
        self.assertFalse((self.directory / "experiment.sqlite3").exists())
        self.assertEqual(self.unified()["1001"]["content"], "必须完整保留的真实正文")
        self.assertEqual(self.unified()["1002"]["content"], "")
        self.assertIsNone(self.unified()["1003"]["content"])
        target_manifest = _manifest(self.directory / "collector.db")
        for table in ("tasks", "jobs", "requests", "coverage", "export_journal"):
            self.assertEqual(target_manifest[table]["count"], source_manifest[table]["count"])
        with closing(sqlite3.connect(self.directory / "collector.db")) as db:
            self.assertEqual(json.loads(db.execute("SELECT value FROM meta WHERE key='instance_id'").fetchone()[0]), self.identity)
            self.assertEqual(json.loads(db.execute("SELECT value FROM meta WHERE key='active_halt'").fetchone()[0]), "challenge")
            self.assertEqual(db.execute("SELECT attempts FROM detail_enrichment_skips WHERE source_item_id='1004'").fetchone()[0], 2)
            self.assertNotIn("content", {r[1] for r in db.execute("PRAGMA table_info(http_post_state)")})
            self.assertTrue(all("post_content" not in json.loads(r[0]) for r in db.execute("SELECT detail_payload FROM http_post_state WHERE detail_payload IS NOT NULL")))
        backup = Path(result["backup_directory"])
        self.assertEqual(_manifest(backup / "experiment.sqlite3"), source_manifest)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)

    def test_v1_without_compatible_store_or_uuid_builds_unified_and_keeps_all_old_rows(self):
        self.fixture(version=1)
        result = migrate(self.directory)
        self.assertEqual(result["state_posts"], 4)
        self.assertEqual(str(uuid.UUID(result["instance_id"])), result["instance_id"])
        self.assertEqual(result["body_complete"], 2)
        self.assertEqual(result["ledger_counts"]["requests"], 5)
        self.assertFalse((self.directory / "experiment.sqlite3").exists())

    def test_active_worker_unowned_collector_and_symlink_refuse_without_deletion(self):
        source = self.fixture()
        with open(self.directory / "worker.lock", "a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(MigrationError, "worker 正在运行"):
                migrate(self.directory)
        with source.db:
            source.db.execute("DELETE FROM meta WHERE key='compatible_storage_instance'")
        with self.assertRaisesRegex(MigrationError, "未标记"):
            migrate(self.directory)
        self.assertTrue((self.directory / "experiment.sqlite3").exists())
        with self.assertRaisesRegex(MigrationError, "生产"):
            migrate(REPO / "data")
        link = self.directory / "alias"
        link.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaisesRegex(MigrationError, "符号链接"):
            migrate(link)

    def test_bad_raw_or_tampered_compatibility_body_never_deletes_legacy(self):
        self.fixture()
        raw = self.directory / "raw/000000002.body"
        original = raw.read_bytes()
        raw.write_bytes(original + b"bad")
        with self.assertRaisesRegex(MigrationError, "SHA-256"):
            migrate(self.directory)
        self.assertTrue((self.directory / "experiment.sqlite3").exists())
        raw.write_bytes(original)
        with closing(sqlite3.connect(self.directory / "collector.db")) as db:
            db.execute("UPDATE posts SET content='tampered' WHERE source_item_id='1001'")
            db.commit()
        with self.assertRaises((MigrationError, RuntimeError, ValueError)):
            migrate(self.directory)
        self.assertTrue((self.directory / "experiment.sqlite3").exists())

    def test_validation_detects_helper_ledger_mutation_and_preserves_both_originals(self):
        self.fixture()
        before = {name: _manifest(self.directory / name) for name in ("collector.db", "experiment.sqlite3")}
        import unified_store
        actual = unified_store.convert_legacy_snapshot
        def altered(*args, **kwargs):
            result = actual(*args, **kwargs)
            with closing(sqlite3.connect(args[0])) as db:
                db.execute("UPDATE tasks SET page=999 WHERE id=7")
                db.commit()
            return result
        with patch.object(unified_store, "convert_legacy_snapshot", side_effect=altered):
            with self.assertRaisesRegex(MigrationError, "tasks"):
                migrate(self.directory)
        self.assertEqual({name: _manifest(self.directory / name) for name in before}, before)

    def test_crash_at_prepared_publication_and_unlink_is_resumable(self):
        for point in ("after_prepared_receipt", "after_publish", "after_published_receipt", "after_legacy_unlink"):
            with self.subTest(point=point), tempfile.TemporaryDirectory() as directory:
                previous, self.directory = self.directory, Path(directory)
                source = self.fixture()
                def fail(current):
                    if current == point:
                        raise Crash(point)
                try:
                    with self.assertRaises(Crash):
                        migrate(self.directory, fault=fail)
                    result = migrate(self.directory)
                    self.assertTrue(result["experiment_removed"])
                    self.assertFalse((self.directory / "experiment.sqlite3").exists())
                    self.assertEqual(self.unified()["1001"]["content"], "必须完整保留的真实正文")
                    self.assertEqual(migrate(self.directory)["status"], "already_unified")
                finally:
                    source.close()
                    self.source = None
                    self.directory = previous

    def test_incomplete_receipt_cannot_cleanup_sidecars_after_final_body_changes(self):
        self.fixture()
        def fail(point):
            if point == "after_legacy_unlink":
                raise Crash(point)
        with self.assertRaises(Crash):
            migrate(self.directory, fault=fail)
        with closing(sqlite3.connect(self.directory / "collector.db")) as db:
            db.execute("UPDATE posts SET content='unexpected-change' WHERE source_item_id='1001'")
            db.commit()
        sidecar = self.directory / "experiment.sqlite3-wal"
        sidecar.write_bytes(b"keep-evidence")
        with self.assertRaisesRegex(MigrationError, "staging"):
            migrate(self.directory)
        self.assertEqual(sidecar.read_bytes(), b"keep-evidence")

    def test_completed_receipt_allows_later_runtime_updates_and_fresh_directory_noop(self):
        self.fixture()
        migrate(self.directory)
        with closing(sqlite3.connect(self.directory / "collector.db")) as db:
            db.execute("INSERT INTO events VALUES(500,1,123,'new','later runtime record','{}')")
            db.commit()
        self.assertEqual(migrate(self.directory)["status"], "already_unified")
        with tempfile.TemporaryDirectory() as fresh:
            self.assertEqual(migrate(fresh)["status"], "fresh_directory")
            self.assertFalse((Path(fresh) / "experiment.sqlite3").exists())

    def test_sqlite_backup_includes_committed_wal_pages(self):
        source = self.fixture(version=1)
        source.db.execute("PRAGMA journal_mode=WAL")
        with source.db:
            source.db.execute("INSERT INTO events VALUES(201,1,124,'wal','committed only in WAL','{}')")
        self.assertTrue((self.directory / "experiment.sqlite3-wal").exists())
        result = migrate(self.directory)
        self.assertEqual(result["ledger_counts"]["events"], 2)
        with closing(sqlite3.connect(Path(result["backup_directory"]) / "experiment.sqlite3")) as backup:
            self.assertEqual(backup.execute("SELECT message FROM events WHERE id=201").fetchone()[0], "committed only in WAL")


if __name__ == "__main__":
    unittest.main()
