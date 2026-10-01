"""Offline lifecycle invariants; fake transport and clock only."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "tests"))
from core import Engine
from test_core import Clock, Wire, row, list_html, detail_html, ok, CONFIG, CHALLENGE

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.engines = []

    def tearDown(self):
        for engine in self.engines:
            engine.close()
        self.tmp.cleanup()

    def engine(self, wire=None):
        engine = Engine(self.tmp.name, transport=wire or Wire(), clock=self.clock)
        self.engines.append(engine)
        return engine

    def created(self, wire=None, config=None):
        engine = self.engine(wire)
        engine.create_job(config or CONFIG)
        return engine

    def first_page(self, *rows, config=None):
        wire = Wire(ok(list_html(list(rows))))
        engine = self.created(wire, config)
        engine.start()
        engine.tick()
        engine.pause()
        return engine, wire

    @staticmethod
    def rows(engine, table):
        return [dict(r) for r in engine.db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()]

    @staticmethod
    def raw_hashes(engine):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in engine.raw_dir.iterdir() if p.is_file()}

    def due_tick(self, engine):
        self.clock.now = max(self.clock.now, engine._get("next_due", 0))
        return engine.tick()

    def test_unstarted_edit_replaces_scope_without_requests(self):
        wire = Wire()
        engine = self.created(wire)
        status = engine.update_job({**CONFIG, "stocks": ["600519", "300750"], "client": "urllib", "interval_seconds": 90})
        self.assertEqual(status["config"]["stocks"], ["600519", "300750"])
        self.assertEqual(status["state"], "paused")
        self.assertEqual({r["stock"] for r in self.rows(engine, "tasks") if r["status"] == "pending"}, {"600519", "300750"})
        self.assertEqual(wire.calls, [])
        self.assertEqual(engine.jobs()[0]["revision"], 2)

    def test_invalid_edit_is_atomic(self):
        engine = self.created()
        before = self.rows(engine, "tasks")
        with self.assertRaises(ValueError):
            engine.update_job({**CONFIG, "interval_seconds": 59})
        self.assertEqual(engine.status()["config"]["from_date"], CONFIG["from_date"])
        self.assertEqual(self.rows(engine, "tasks"), before)

    def test_running_rejects_edit_remove_and_archive(self):
        engine = self.created(config={**CONFIG, "stocks": ["601012", "600519"]})
        engine.start()
        for action in (lambda: engine.update_job({**CONFIG, "client": "urllib"}),
                       lambda: engine.remove_stock("600519"), engine.delete_job):
            with self.assertRaisesRegex(RuntimeError, "暂停"):
                action()
        self.assertIsNotNone(engine.status()["job"])

    def test_pause_with_inflight_still_rejects_edit(self):
        engine = self.created()
        engine._inflight = True
        try:
            engine.pause()
            for action in (lambda: engine.update_job({**CONFIG, "client": "urllib"}), engine.delete_job):
                with self.assertRaisesRegex(RuntimeError, "请求结束"):
                    action()
        finally:
            engine._inflight = False

    def test_transport_only_edit_preserves_queue_cutoff_and_due(self):
        engine, _ = self.first_page(row())
        before, cutoff, due = self.rows(engine, "tasks"), engine._get("effective_to_epoch"), engine._get("next_due")
        associations, coverage = self.rows(engine, "associations"), self.rows(engine, "coverage")
        engine.update_job({**CONFIG, "client": "urllib", "interval_seconds": 120})
        self.assertEqual(self.rows(engine, "tasks"), before)
        self.assertEqual(self.rows(engine, "associations"), associations)
        self.assertEqual(self.rows(engine, "coverage"), coverage)
        self.assertEqual(engine._get("effective_to_epoch"), cutoff)
        self.assertEqual(engine._get("next_due"), due)

    def test_shrinking_window_retains_raw_and_supersedes_outside_details(self):
        engine, _ = self.first_page(row())
        raw, requests, observations = self.raw_hashes(engine), self.rows(engine, "requests"), self.rows(engine, "observations")
        engine.update_job({**CONFIG, "from_date": "2025-01-15"})
        self.assertEqual(engine.status()["aggregate"]["unique_posts"], 0)
        self.assertEqual(self.raw_hashes(engine), raw)
        self.assertEqual(self.rows(engine, "requests"), requests)
        self.assertEqual(self.rows(engine, "observations"), observations)
        self.assertEqual(len(self.rows(engine, "posts")), 1)
        self.assertFalse(any(r["kind"] == "detail" and r["status"] == "pending" for r in self.rows(engine, "tasks")))
        revisions = self.rows(engine, "job_revisions")
        snapshot = json.loads(revisions[-1]["snapshot"])
        self.assertEqual(snapshot["before"]["association_counts"][0]["eligible"], 1)
        self.assertEqual(snapshot["after"]["association_counts"][0]["eligible"], 0)
        self.assertEqual(engine.status()["coverage"][0]["pages"], 0)

    def test_expansion_uses_retained_exact_list_link_and_reuses_complete_body(self):
        outside, inside = row("1001", stock="600519"), row("1002", published="2025-01-16 10:00:00")
        old = {**CONFIG, "from_date": "2025-01-15", "to_date": "2025-01-20"}
        wire = Wire(ok(list_html([outside, inside])), ok(detail_html(inside)))
        engine = self.created(wire, old)
        engine.start()
        engine.tick()
        self.due_tick(engine)
        engine.pause()
        before_raw = self.raw_hashes(engine)
        engine.update_job({**old, "from_date": "2025-01-01"})
        self.assertEqual(engine.status()["aggregate"]["body_complete"], 1)
        self.assertEqual(engine.status()["aggregate"]["pending"], 1)
        pending = [r for r in self.rows(engine, "tasks") if r["kind"] == "detail" and r["status"] == "pending"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["post_id"], "1001")
        self.assertEqual(pending[0]["url"], "https://guba.eastmoney.com/news,600519,1001.html")
        self.assertEqual(self.raw_hashes(engine), before_raw)
        self.assertEqual(len(wire.calls), 2)

    def test_expansion_with_corrupt_evidence_rolls_back_all_changes(self):
        old = {**CONFIG, "from_date": "2025-01-15"}
        engine, _ = self.first_page(row(), config=old)
        engine.jobs()
        before = {table: self.rows(engine, table) for table in ("jobs", "tasks", "coverage", "associations", "posts", "job_revisions", "job_lifecycle", "meta")}
        raw = next(engine.raw_dir.glob("*.body"))
        raw.write_bytes(b"corrupted fixture")
        with self.assertRaisesRegex(ValueError, "哈希"):
            engine.update_job(CONFIG)
        for table, rows in before.items():
            self.assertEqual(self.rows(engine, table), rows, table)

    def test_remove_stock_preserves_evidence_and_records_previous_associations(self):
        config = {**CONFIG, "stocks": ["601012", "600519"]}
        engine, _ = self.first_page(row(), config=config)
        raw = self.raw_hashes(engine)
        engine.remove_stock("601012")
        self.assertEqual(engine.status()["config"]["stocks"], ["600519"])
        self.assertEqual(engine.status()["aggregate"]["unique_posts"], 0)
        self.assertEqual(len(self.rows(engine, "posts")), 1)
        self.assertEqual(self.raw_hashes(engine), raw)
        snapshot = json.loads(self.rows(engine, "job_revisions")[-1]["snapshot"])
        self.assertTrue(any(r["stock"] == "601012" for r in snapshot["before"]["association_counts"]))

    def test_unknown_stock_cannot_be_removed(self):
        engine = self.created()
        with self.assertRaises(ValueError):
            engine.remove_stock("600519")
        self.assertEqual(engine.status()["config"]["stocks"], CONFIG["stocks"])

    def test_last_stock_removal_archives_job_and_preserves_evidence(self):
        engine, _ = self.first_page(row())
        raw = self.raw_hashes(engine)
        status = engine.remove_stock("601012")
        self.assertIsNone(status["job"])
        self.assertTrue(engine.jobs()[0]["archived"])
        self.assertEqual(self.raw_hashes(engine), raw)
        self.assertEqual(len(self.rows(engine, "posts")), 1)

    def test_unstarted_archive_clears_current_and_allows_new_job(self):
        wire = Wire()
        engine = self.created(wire)
        status = engine.delete_job()
        self.assertIsNone(status["job"])
        self.assertIsNone(status["config"])
        self.assertFalse(engine.tick()["attempted"])
        self.assertTrue(engine.jobs()[0]["archived"])
        self.assertTrue(all(r["status"] == "cancelled" for r in self.rows(engine, "tasks")))
        engine.create_job(CONFIG)
        jobs = engine.jobs()
        self.assertTrue(jobs[0]["current"])
        self.assertFalse(jobs[0]["archived"])
        self.assertTrue(jobs[1]["archived"])
        self.assertEqual(wire.calls, [])

    def test_archive_retains_acquired_data_coverage_and_due(self):
        engine, _ = self.first_page(row())
        before = {table: self.rows(engine, table) for table in ("posts", "coverage", "associations", "observations", "requests")}
        raw, due = self.raw_hashes(engine), engine._get("next_due")
        engine.delete_job()
        for table, rows in before.items():
            self.assertEqual(self.rows(engine, table), rows, table)
        self.assertEqual(self.raw_hashes(engine), raw)
        self.assertEqual(engine._get("next_due"), due)
        self.assertEqual(engine.raw_posts(), [])
        engine.create_job(CONFIG)
        self.assertEqual(engine._get("next_due"), due)

    def test_blocked_archive_keeps_probe_target_halt_and_cooldown(self):
        wire = Wire(ok(list_html([], extra=CHALLENGE)))
        engine = self.created(wire)
        engine.start()
        engine.tick()
        halt, due = engine._get("active_halt"), engine._get("next_due")
        failed = engine._target()["id"]
        engine.delete_job()
        self.assertIsNone(engine._get("job_id"))
        self.assertEqual(engine._get("active_halt"), halt)
        self.assertEqual(engine._get("next_due"), due)
        self.assertEqual(engine._get("halted_task_id"), failed)
        self.assertTrue(engine._get("halted_probe_only"))
        self.assertEqual(engine.db.execute("SELECT status FROM tasks WHERE id=?", (failed,)).fetchone()[0], "pending")
        self.assertTrue(engine.jobs()[0]["archived"])
        with self.assertRaises(RuntimeError):
            engine.create_job(CONFIG)
        self.assertEqual(len(wire.calls), 1)

    def test_blocked_stock_can_be_removed_but_its_probe_target_remains(self):
        config = {**CONFIG, "stocks": ["601012", "600519"]}
        wire = Wire(ok(list_html([], extra=CHALLENGE)))
        engine = self.created(wire, config)
        engine.start()
        engine.tick()
        failed = engine._target()["id"]
        engine.remove_stock("601012")
        self.assertEqual(engine.status()["config"]["stocks"], ["600519"])
        self.assertEqual(engine.status()["state"], "blocked")
        self.assertEqual(engine._get("halted_task_id"), failed)
        self.assertTrue(engine._get("halted_probe_only"))
        self.assertEqual(engine._get("halted_config")["stocks"], ["601012", "600519"])
        self.assertEqual(engine.db.execute("SELECT status FROM tasks WHERE id=?", (failed,)).fetchone()[0], "pending")
        with self.assertRaises(RuntimeError):
            engine.start()

    def test_blocked_transport_edit_does_not_clear_block_or_restart_queue(self):
        wire = Wire(ok(list_html([], extra=CHALLENGE)))
        engine = self.created(wire)
        engine.start()
        engine.tick()
        before, due = self.rows(engine, "tasks"), engine._get("next_due")
        engine.update_job({**CONFIG, "interval_seconds": 120, "client": "urllib"})
        self.assertEqual(engine.status()["state"], "blocked")
        self.assertEqual(self.rows(engine, "tasks"), before)
        self.assertEqual(engine._get("next_due"), due)
        self.assertFalse(engine._get("halted_probe_only", False))

    def test_legacy_block_without_target_meta_uses_failure_ledger(self):
        wire = Wire(ok(list_html([], extra=CHALLENGE)))
        engine = self.created(wire)
        engine.start()
        engine.tick()
        failed = engine._halt_target()["id"]
        with engine.db:
            engine._set("halted_task_id", None)
            engine._set("halt_task_id", None)
            engine.db.execute("INSERT INTO tasks(job,kind,stock,post_id,url,original_url) VALUES(1,'detail','601012','9999',?,?)",
                              ("https://guba.eastmoney.com/news,601012,9999.html",) * 2)
        engine.delete_job()
        self.assertEqual(engine._get("halted_task_id"), failed)
        pending = [r["id"] for r in self.rows(engine, "tasks") if r["status"] == "pending"]
        self.assertEqual(pending, [failed])

    def test_archived_halt_probe_only_records_evidence_then_allows_new_job(self):
        wire = Wire(ok(list_html([], extra=CHALLENGE)), ok(list_html([row()])))
        engine = self.created(wire)
        engine.start()
        engine.tick()
        engine.delete_job()
        retained = {table: self.rows(engine, table) for table in ("posts", "coverage", "associations", "observations")}
        task_count, due = len(self.rows(engine, "tasks")), engine._get("next_due")
        engine.retry()
        self.assertFalse(engine.tick()["attempted"])
        self.assertEqual(engine._get("next_due"), due)
        self.due_tick(engine)
        self.assertIsNone(engine.status()["job"])
        self.assertIsNone(engine.status()["active_halt"])
        self.assertEqual(engine.status()["state"], "paused")
        self.assertEqual(len(self.rows(engine, "tasks")), task_count)
        for table, rows in retained.items():
            self.assertEqual(self.rows(engine, table), rows, table)
        self.assertEqual(engine.requests()[0]["probe_only"], 1)
        self.assertEqual(len(self.raw_hashes(engine)), 2)
        self.assertFalse(engine.tick()["attempted"])
        engine.create_job(CONFIG)
        self.assertEqual(len(wire.calls), 2)

    def test_removed_halted_stock_probe_does_not_rejoin_active_scope(self):
        config = {**CONFIG, "stocks": ["601012", "600519"]}
        wire = Wire(ok(list_html([], extra=CHALLENGE)), ok(list_html([row()])))
        engine = self.created(wire, config)
        engine.start()
        engine.tick()
        engine.remove_stock("601012")
        retained = {table: self.rows(engine, table) for table in ("posts", "coverage", "associations", "observations")}
        engine.retry()
        self.due_tick(engine)
        self.assertEqual(engine.status()["config"]["stocks"], ["600519"])
        self.assertIsNone(engine.status()["active_halt"])
        for table, rows in retained.items():
            self.assertEqual(self.rows(engine, table), rows, table)
        self.assertEqual(engine.status()["current"]["stock"], "600519")
        self.assertEqual(len(wire.calls), 2)

    def test_window_edit_halt_probe_cannot_advance_new_window(self):
        wire = Wire(ok(list_html([], extra=CHALLENGE)), ok(list_html([row()])))
        engine = self.created(wire)
        engine.start()
        engine.tick()
        original_config = engine.status()["config"]
        engine.update_job({**CONFIG, "from_date": "2025-01-15"})
        self.assertEqual(engine._get("halt_config"), original_config)
        self.assertEqual(engine._get("halted_config"), original_config)
        retained = {table: self.rows(engine, table) for table in ("posts", "coverage", "associations", "observations")}
        pending = [r["id"] for r in self.rows(engine, "tasks") if r["status"] == "pending" and r["id"] != engine._get("halted_task_id")]
        engine.retry()
        self.due_tick(engine)
        for table, rows in retained.items():
            self.assertEqual(self.rows(engine, table), rows, table)
        self.assertEqual(engine.status()["coverage"][0]["pages"], 0)
        self.assertTrue(all(engine.db.execute("SELECT status FROM tasks WHERE id=?", (task,)).fetchone()[0] == "pending" for task in pending))
        self.assertEqual(engine.status()["state"], "paused")

    def test_last_halted_stock_removal_is_archive_with_one_probe_available(self):
        wire = Wire(ok(list_html([], extra=CHALLENGE)), ok(list_html([row()])))
        engine = self.created(wire)
        engine.start()
        engine.tick()
        failed = engine._target()["id"]
        engine.remove_stock("601012")
        self.assertIsNone(engine.status()["job"])
        self.assertTrue(engine.jobs()[0]["archived"])
        self.assertEqual(engine._target()["id"], failed)
        engine.retry()
        self.due_tick(engine)
        self.assertIsNone(engine.status()["active_halt"])
        self.assertIsNone(engine.status()["job"])

    def test_removing_other_stock_preserves_existing_coverage_and_pending_body(self):
        config = {**CONFIG, "stocks": ["601012", "600519"]}
        engine, _ = self.first_page(row(), config=config)
        old = dict(engine.db.execute("SELECT * FROM coverage WHERE stock='601012'").fetchone())
        engine.remove_stock("600519")
        self.assertEqual(dict(engine.db.execute("SELECT * FROM coverage WHERE stock='601012'").fetchone()), old)
        self.assertEqual(engine.status()["aggregate"]["pending"], 1)
        self.assertTrue(engine.db.execute("SELECT 1 FROM frontiers WHERE stock='601012'").fetchone())

    def test_adding_stock_preserves_existing_coverage(self):
        engine, _ = self.first_page(row())
        old = dict(engine.db.execute("SELECT * FROM coverage WHERE stock='601012'").fetchone())
        engine.update_job({**CONFIG, "stocks": ["601012", "600519"]})
        self.assertEqual(dict(engine.db.execute("SELECT * FROM coverage WHERE stock='601012'").fetchone()), old)
        self.assertEqual(engine.db.execute("SELECT pages FROM coverage WHERE stock='600519'").fetchone()[0], 0)
        self.assertEqual(engine.status()["aggregate"]["pending"], 1)

    def test_same_config_is_noop(self):
        engine = self.created()
        engine.jobs()
        before = self.rows(engine, "job_revisions")
        engine.update_job(engine.status()["config"])
        self.assertEqual(self.rows(engine, "job_revisions"), before)

    def test_order_only_change_does_not_rebuild_scope(self):
        engine = self.created(config={**CONFIG, "stocks": ["601012", "600519"]})
        before = self.rows(engine, "tasks")
        engine.update_job({**CONFIG, "stocks": ["600519", "601012"]})
        self.assertEqual(self.rows(engine, "tasks"), before)

    def test_revision_and_archive_survive_restart(self):
        engine = self.created()
        engine.update_job({**CONFIG, "client": "urllib"})
        engine.delete_job()
        engine.close()
        reopened = self.engine()
        self.assertIsNone(reopened.status()["job"])
        history = reopened.jobs()[0]
        self.assertTrue(history["archived"])
        self.assertEqual(history["revision"], 3)
        self.assertEqual([r["reason"] for r in history["revisions"]], ["initial", "config_updated", "job_archived"])

    def test_v1_migration_is_additive_and_preserves_source_evidence(self):
        engine, _ = self.first_page(row())
        cutoff, raw = engine._get("effective_to_epoch"), self.raw_hashes(engine)
        engine.close()
        # Construct the pre-lifecycle version of this temporary offline DB.
        with closing(sqlite3.connect(Path(self.tmp.name) / "collector.db")) as db:
            with db:
                db.execute("DROP TABLE job_revisions")
                db.execute("DROP TABLE job_lifecycle")
        reopened = self.engine()
        self.assertEqual(self.raw_hashes(reopened), raw)
        self.assertEqual(len(self.rows(reopened, "requests")), 1)
        self.assertEqual(len(self.rows(reopened, "observations")), 1)
        self.assertEqual(reopened._get("effective_to_epoch"), cutoff)
        self.assertEqual(reopened.jobs()[0]["revision"], 1)

    def test_future_window_edit_does_not_mutate_existing_job(self):
        engine = self.created()
        before = engine.status()["config"]
        with self.assertRaises(ValueError):
            engine.update_job({**CONFIG, "from_date": "2099-01-01", "to_date": "2099-01-31"})
        self.assertEqual(engine.status()["config"], before)

    def test_no_current_job_cannot_edit_or_archive_again(self):
        engine = self.created()
        engine.delete_job()
        with self.assertRaises(RuntimeError):
            engine.update_job(CONFIG)
        with self.assertRaises(RuntimeError):
            engine.delete_job()


if __name__ == "__main__":
    unittest.main()
