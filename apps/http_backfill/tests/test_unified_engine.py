"""One current body, atomic detail acceptance, and replayable raw API evidence."""
import json
import unittest

import test_core as base
from test_core import Wire, row, list_html, detail_html, ok


class UnifiedEngineTests(unittest.TestCase):
    setUp = base.CoreTests.setUp
    tearDown = base.CoreTests.tearDown
    engine = base.CoreTests.engine
    started = base.CoreTests.started
    tick_due = base.CoreTests.tick_due

    def test_detail_state_failure_rolls_back_body_and_restart_retains_raw(self):
        source = row()
        e = self.started(Wire(ok(list_html([source])), ok(detail_html(source))))
        e.tick()
        e.db.execute("""CREATE TRIGGER fail_detail_state BEFORE UPDATE OF status ON http_post_state
                        WHEN NEW.status='complete' BEGIN SELECT RAISE(ABORT,'fixture disk failure'); END""")
        self.tick_due(e)
        self.assertIsNone(e.db.execute("SELECT content FROM posts WHERE source_item_id=?", (source["post_id"],)).fetchone()[0])
        self.assertEqual(e.db.execute("SELECT status FROM http_post_state WHERE post_id=?", (source["post_id"],)).fetchone()[0], "pending")
        rid = e.requests()[0]["id"]
        self.assertEqual(e.requests()[0]["outcome"], "internal_error")
        self.assertTrue(e.requests()[0]["raw_ref"])
        self.assertTrue((e.raw_dir / f"{rid:09d}.body").is_file())
        e.close()
        wire = Wire()
        reopened = self.engine(wire)
        self.assertEqual(reopened.status()["active_halt"], "internal_error")
        self.assertEqual(reopened.requests()[0]["outcome"], "internal_error")
        self.assertTrue(reopened.requests()[0]["raw_ref"])
        self.assertFalse(reopened.tick()["attempted"])
        self.assertEqual(wire.calls, [])

    def test_body_has_one_current_column_and_full_payload_reconstructs_from_raw(self):
        source = row()
        raw_body = "<p>保留原始格式的正文</p>"
        e = self.started(Wire(ok(list_html([source])), ok(detail_html(source, raw_body))))
        e.tick()
        self.tick_due(e)
        post = e.raw_posts()[0]
        self.assertEqual(post["content"], raw_body)
        self.assertEqual(post["detail_payload"]["post_content"], raw_body)
        state = e.db.execute("SELECT * FROM http_post_state").fetchone()
        self.assertNotIn("content", state.keys())
        self.assertNotIn("post_content", json.loads(state["detail_payload"]))
        self.assertEqual(e.db.execute("SELECT content FROM posts").fetchone()[0], post["content"])
        self.assertEqual(e.status()["data_storage"]["body_provenance_table"], "collector.db:compatible_posts")
        self.assertTrue(e.status()["data_storage"]["single_runtime_database"])
        self.assertFalse((e.data_dir / "experiment.sqlite3").exists())

    def test_payload_api_rechecks_raw_hash_after_prior_successful_read(self):
        source = row()
        wire = Wire(ok(list_html([source])), ok(detail_html(source)))
        e = self.started(wire)
        e.tick()
        self.tick_due(e)
        self.assertTrue(e.raw_posts()[0]["body_complete"])
        request = e.requests()[0]
        (e.data_dir / request["raw_ref"]).write_bytes(b"changed raw bytes")
        with self.assertRaisesRegex(RuntimeError, "SHA-256"):
            e.raw_posts()
        self.assertEqual(len(wire.calls), 2)


if __name__ == "__main__":
    unittest.main()
