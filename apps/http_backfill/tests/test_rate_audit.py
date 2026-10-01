"""Read-only timing evidence, missing attempts, historical configs and live cache."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_core as base
from rate_audit import audit_connection, audit_database, tail_audit

APP = Path(__file__).resolve().parents[1]


class RateAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "collector.db"
        self.db = sqlite3.connect(self.db_path)
        self.db.executescript("""
          CREATE TABLE requests(id INTEGER PRIMARY KEY,job INTEGER,kind TEXT,page INTEGER,
            started REAL,finished REAL,outcome TEXT,purpose TEXT,probe INTEGER,network_attempted INTEGER);
          CREATE TABLE jobs(id INTEGER PRIMARY KEY,config TEXT);
          CREATE TABLE job_revisions(id INTEGER PRIMARY KEY,job INTEGER,revision INTEGER,changed REAL,config TEXT,UNIQUE(job,revision));
          CREATE TABLE posts(source_item_id TEXT,content TEXT);
        """)
        self.db.execute("INSERT INTO jobs VALUES(1,?)", (json.dumps({"interval_seconds": 60}),))
        self.db.execute("INSERT INTO job_revisions VALUES(1,1,1,0,?)", (json.dumps({"interval_seconds": 60}),))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def row(self, started, finished, *, kind="list", purpose="forward", outcome="real_data", flag=1, probe=0):
        self.db.execute("INSERT INTO requests(job,kind,page,started,finished,outcome,purpose,probe,network_attempted) VALUES(1,?,1,?,?,?,?,?,?)",
                        (kind, started, finished, outcome, purpose, probe, flag))
        self.db.commit()

    def test_post_count_is_not_request_count_all_source_kinds_are_classified(self):
        self.db.executemany("INSERT INTO posts VALUES(?,NULL)", [(str(i),) for i in range(5274)])
        self.db.commit()
        self.row(0, 1, outcome="redirect")
        self.row(61, 62, purpose="recovery")
        self.row(122, 123, kind="detail", probe=1, outcome="transport_error")
        report = audit_connection(self.db)
        self.assertEqual(report["ledger_rows"], 3)
        self.assertEqual(report["confirmed_requests"], 3)
        self.assertEqual(report["by_kind"], {"list": 2, "detail": 1})
        self.assertEqual(report["classification"], {"list_forward": 1, "list_recovery": 1, "detail": 1, "redirect": 1, "probe": 1})
        self.assertEqual(report["checked_pairs"], 2)
        self.assertEqual(report["min_finish_to_start_seconds"], 60)
        self.assertEqual(report["min_start_to_start_seconds"], 61)
        self.assertEqual(report["verdict"], "pass")
        self.assertEqual(report["scope"]["mode"], "all")

    def test_known_subthreshold_gaps_have_bounded_details_and_exact_count(self):
        for i in range(5):
            self.row(i * 60, i * 60 + 0.5, kind="detail")
        report = audit_connection(self.db, details_limit=2)
        self.assertEqual(report["verdict"], "fail")
        self.assertEqual(report["violations"]["count"], 4)
        self.assertEqual(report["violations"]["omitted"], 2)
        self.assertEqual(report["violations"]["items"][0]["previous_request_id"], 1)
        self.assertEqual(report["violations"]["items"][0]["gap_seconds"], 59.5)
        self.assertNotIn("content", json.dumps(report))
        self.assertNotIn("token", json.dumps(report))

    def test_unknown_attempt_flags_zero_and_none_and_unfinished_cannot_pass(self):
        self.row(0, 1)
        self.row(61, None, flag=0, outcome="reserved")
        self.row(122, 180, flag=None, outcome="interrupted_unknown")
        self.row(241, None, kind="detail")
        report = audit_connection(self.db)
        self.assertEqual(report["verdict"], "unknown")
        self.assertEqual(report["unknown"]["attempt_rows"], 2)
        self.assertEqual(report["unknown"]["unfinished_rows"], 3)
        self.assertEqual(report["unknown"]["timing_rows"], 1)
        self.assertEqual(report["unknown"]["pairs"], 1)
        self.assertEqual(report["violations"]["count"], 0)

    def test_missing_time_clock_anomaly_and_overlap_are_explicitly_unknown(self):
        self.row(100, 110)
        self.row(109, 108)
        self.row(None, 200)
        report = audit_connection(self.db)
        self.assertEqual(report["verdict"], "unknown")
        self.assertEqual(report["unknown"]["overlaps"], 1)
        self.assertEqual(report["unknown"]["clock_anomalies"], 1)
        self.assertEqual(report["min_finish_to_start_seconds"], -1)
        self.assertEqual(report["violations"]["count"], 0)
        self.assertEqual(report["unknown"]["pairs"], 2)

    def test_same_natural_minute_is_reference_not_equivalent_to_gap_check(self):
        self.row(10, 11)
        self.row(69, 70)
        report = audit_connection(self.db)
        self.assertEqual(report["minute_buckets"]["max_requests"], 1)
        self.assertEqual(report["violations"]["count"], 1)
        self.assertEqual(report["min_finish_to_start_seconds"], 58)

    def test_previous_request_revision_policy_is_distinct_from_current_config(self):
        self.db.execute("UPDATE jobs SET config=?", (json.dumps({"interval_seconds": 120}),))
        self.db.execute("INSERT INTO job_revisions VALUES(2,1,2,100,?)", (json.dumps({"interval_seconds": 120}),))
        self.db.commit()
        for start in (0, 61, 122, 183):
            self.row(start, start + 1)
        report = audit_connection(self.db, interval=60)
        self.assertEqual(report["verdict"], "pass")
        self.assertEqual(report["violations"]["count"], 0)
        self.assertEqual(report["config_policy"]["violations"]["count"], 1)
        violation = report["config_policy"]["violations"]["items"][0]
        self.assertEqual((violation["previous_request_id"], violation["request_id"], violation["required_seconds"]), (3, 4, 120))
        self.assertEqual([c["interval_seconds"] for c in report["config_history"]], [60, 120])
        with self.db:
            self.db.execute("UPDATE requests SET probe=1 WHERE id=3")
        self.assertEqual(audit_connection(self.db)["config_policy"]["unknown_pairs"], 1)

    def test_bounded_tail_scope_and_live_cache_never_claim_full_history(self):
        self.db.executemany("INSERT INTO requests VALUES(?,1,'list',1,?,?,'real_data','forward',0,1)",
                            [(i, i * 61, i * 61 + 1) for i in range(1, 1003)])
        self.db.commit()
        class EngineFixture:
            pass
        engine = EngineFixture()
        engine.db = self.db
        with patch("rate_audit.audit_connection", wraps=audit_connection) as audited:
            first = tail_audit(engine)
            self.assertIs(tail_audit(engine), first)
            self.assertEqual(audited.call_count, 1)
            self.assertEqual(first["scope"], {"mode": "tail", "limit": 1000, "first_request_id": 3, "last_request_id": 1002, "truncated": True})
            self.assertEqual(first["confirmed_requests"], 1000)
            self.row(1003 * 61, 1003 * 61 + 1)
            refreshed = tail_audit(engine)
            self.assertEqual(audited.call_count, 2)
            self.assertEqual(refreshed["scope"]["last_request_id"], 1003)

    def test_config_edit_invalidates_live_cache_without_new_source_request(self):
        self.row(0, 1)
        self.row(61, 62)
        class EngineFixture:
            pass
        engine = EngineFixture()
        engine.db = self.db
        with patch("rate_audit.audit_connection", wraps=audit_connection) as audited:
            first = tail_audit(engine)
            self.assertEqual([r["interval_seconds"] for r in first["config_history"]], [60])
            self.db.execute("UPDATE jobs SET config=?", (json.dumps({"interval_seconds": 90}),))
            self.db.execute("INSERT INTO job_revisions VALUES(2,1,2,100,?)", (json.dumps({"interval_seconds": 90}),))
            self.db.commit()
            after = tail_audit(engine)
            self.assertEqual(audited.call_count, 2)
            self.assertEqual(after["confirmed_requests"], first["confirmed_requests"])
            self.assertEqual([r["interval_seconds"] for r in after["config_history"]], [60, 90])

    def test_current_config_without_revision_history_does_not_prove_old_policy(self):
        self.db.execute("DELETE FROM job_revisions")
        self.db.commit()
        self.row(0, 1)
        self.row(61, 62)
        report = audit_connection(self.db)
        self.assertEqual(report["verdict"], "pass")
        self.assertEqual(report["config_policy"]["unknown_pairs"], 1)
        self.assertEqual(report["config_history"][0]["source"], "current_job_only")

    def test_snapshot_is_readonly_and_legacy_fallback_reports_actual_database(self):
        self.row(0, 1)
        self.row(61, 62)
        before = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        report = audit_database(self.db_path)
        self.assertTrue(report["read_only_snapshot"])
        self.assertFalse(report["legacy_fallback"])
        self.assertEqual(before, hashlib.sha256(self.db_path.read_bytes()).hexdigest())
        self.db.close()
        legacy = self.root / "experiment.sqlite3"
        self.db_path.rename(legacy)
        self.db = sqlite3.connect(legacy)
        with closing(sqlite3.connect(self.db_path)) as unrelated:
            unrelated.execute("CREATE TABLE posts(id TEXT)")
        report = audit_database(self.db_path)
        self.assertTrue(report["legacy_fallback"])
        self.assertEqual(report["db_path"], str(legacy.resolve()))
        self.assertEqual(report["confirmed_requests"], 2)

    def test_stdin_cli_works_outside_repo_without_engine_or_secret_access(self):
        self.row(0, 1)
        self.row(61, 62)
        secret = "private-sentinel-must-not-be-read-or-returned"
        (self.root / "console.token").write_text(secret)
        script = (APP / "rate_audit.py").read_text()
        result = subprocess.run([sys.executable, "-", "--db", str(self.db_path), "--interval", "60"],
                                input=script, cwd=self.root, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["verdict"], "pass")
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertFalse((self.root / "raw").exists())
        self.assertFalse((self.root / "worker.lock").exists())
        self.assertEqual((self.root / "console.token").read_text(), secret)
        self.db.execute("UPDATE requests SET started=30 WHERE id=2")
        self.db.commit()
        failed = subprocess.run([sys.executable, str(APP / "rate_audit.py"), "--db", str(self.db_path)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 1)
        self.assertEqual(json.loads(failed.stdout)["violations"]["count"], 1)

    def test_empty_no_ledger_and_old_flags_are_unknown_not_compliant(self):
        self.assertEqual(audit_connection(self.db)["verdict"], "unknown")
        self.db.execute("ALTER TABLE requests DROP COLUMN network_attempted")
        self.db.execute("INSERT INTO requests VALUES(1,1,'list',1,0,1,'real_data','forward',0)")
        self.db.commit()
        report = audit_connection(self.db)
        self.assertEqual(report["confirmed_requests"], 0)
        self.assertEqual(report["unknown"]["attempt_rows"], 1)
        self.assertEqual(report["verdict"], "unknown")
        with self.assertRaises(ValueError):
            audit_database(self.root / "nonexistent.db")
        self.assertFalse((self.root / "nonexistent.db").exists())

    def test_live_engine_status_does_not_invoke_source(self):
        wire = base.Wire()
        engine = base.Engine(self.root / "worker", transport=wire, clock=base.Clock())
        try:
            engine.create_job(base.CONFIG)
            report = engine.status()["rate_audit"]
            self.assertEqual(report["verdict"], "unknown")
            self.assertEqual(report["ledger_rows"], 0)
            self.assertEqual(wire.calls, [])
            self.assertEqual(engine.status()["state"], "paused")
        finally:
            engine.close()


if __name__ == "__main__":
    unittest.main()
