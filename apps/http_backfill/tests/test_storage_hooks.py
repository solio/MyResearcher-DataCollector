"""Primary commit precedes local projection; repairing storage never hits source."""
from pathlib import Path
import tempfile
import sqlite3
import unittest
from unittest.mock import patch

import test_core as base
from test_core import Engine, Wire, row, list_html, ok, CONFIG, CHALLENGE


class StorageHookTests(unittest.TestCase):
    setUp = base.CoreTests.setUp
    tearDown = base.CoreTests.tearDown
    engine = base.CoreTests.engine
    started = base.CoreTests.started
    tick_due = base.CoreTests.tick_due

    def test_projection_failure_follows_durable_source_commit_and_start_only_repairs_locally(self):
        source = row()
        wire = Wire(ok(list_html([source])))
        e = self.started(wire)
        def fail_projection(engine, request_id=None):
            self.assertFalse(engine._inflight)
            self.assertFalse(engine.db.in_transaction)
            request = engine.requests()[0]
            self.assertEqual(request["id"], request_id)
            self.assertEqual(request["outcome"], "real_data")
            self.assertTrue((engine.data_dir / request["raw_ref"]).is_file())
            raise OSError("disk temporarily unavailable")
        with patch.object(e.compatible_store, "sync", side_effect=fail_projection):
            e.tick()
        self.assertEqual(e.status()["state"], "error")
        self.assertEqual(e.status()["data_storage"]["status"], "error")
        self.assertIsNone(e.status()["active_halt"])
        self.assertEqual(e.status()["aggregate"]["unique_posts"], 1)
        due = e.status()["next_request_epoch"]
        self.clock.advance(600)
        self.assertFalse(e.tick()["attempted"])
        result = e.start()
        self.assertEqual(result["state"], "paused")
        self.assertEqual(result["data_storage"]["status"], "ready")
        self.assertIsNone(result["storage_halt"])
        self.assertEqual(result["next_request_epoch"], due)
        self.assertEqual(len(wire.calls), 1)
        self.assertFalse(e.tick()["attempted"])

    def test_retry_repairs_projection_locally_and_does_not_probe_source(self):
        wire = Wire(ok(list_html([row()])))
        e = self.started(wire)
        with patch.object(e.compatible_store, "sync", side_effect=OSError("offline local filesystem")):
            e.tick()
        result = e.retry()
        self.assertEqual(result["state"], "paused")
        self.assertFalse(result["probe_pending"])
        self.assertEqual(len(wire.calls), 1)
        self.clock.advance(600)
        self.assertFalse(e.tick()["attempted"])

    def test_local_repair_retains_independent_source_challenge_and_cooldown(self):
        wire = Wire(ok(CHALLENGE.encode()))
        e = self.started(wire)
        with patch.object(e.compatible_store, "sync", side_effect=OSError("local projection failure")):
            e.tick()
        evidence = e.status()["block_evidence"]
        due = e.status()["next_request_epoch"]
        self.assertEqual(e.status()["active_halt"], "challenge")
        result = e.retry()
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(result["active_halt"], "challenge")
        self.assertEqual(result["block_evidence"], evidence)
        self.assertEqual(result["next_request_epoch"], due)
        self.assertFalse(result["probe_pending"])
        with self.assertRaises(RuntimeError):
            e.start()
        self.assertEqual(len(wire.calls), 1)

    def test_restart_automatically_repairs_committed_projection_without_source_request(self):
        e = self.started(Wire(ok(list_html([row(post_title="短标题")]))))
        with patch.object(e.compatible_store, "sync", side_effect=OSError("temporary disk error")):
            e.tick()
        requests = e.requests()
        e.close()
        wire = Wire()
        reopened = self.engine(wire)
        self.assertEqual(reopened.requests(), requests)
        self.assertEqual(reopened.status()["state"], "paused")
        self.assertEqual(reopened.status()["data_storage"]["status"], "ready")
        self.assertEqual(reopened.status()["data_storage"]["schema_version"], "legacy_posts")
        self.assertIsNone(reopened.status()["storage_halt"])
        self.assertEqual(wire.calls, [])

    def test_owned_app_directory_named_data_reopens_and_unowned_database_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            app_data = Path(root) / "data"
            first = Engine(app_data, transport=Wire(), clock=self.clock)
            instance = first.status()["instance_id"]
            first.close()
            second = Engine(app_data, transport=Wire(), clock=self.clock)
            try:
                self.assertEqual(second.status()["instance_id"], instance)
                self.assertEqual(second.status()["data_storage"]["status"], "ready")
            finally:
                second.close()
        with tempfile.TemporaryDirectory() as foreign:
            path = Path(foreign) / "collector.db"
            payload = b"foreign collector bytes: never overwrite"
            path.write_bytes(payload)
            with self.assertRaises((ValueError, RuntimeError, sqlite3.DatabaseError)):
                Engine(foreign, transport=Wire(), clock=self.clock)
            self.assertEqual(path.read_bytes(), payload)
            self.assertFalse((Path(foreign) / "experiment.sqlite3").exists())



if __name__ == "__main__":
    unittest.main()
