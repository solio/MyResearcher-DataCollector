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

    def test_enrichment_updates_same_post_row_and_retains_its_list_title(self):
        short = row("1001", post_title="保留的短标题")
        long = row("1002", post_title="列表标题" * 10)
        body = "这是从同一个帖子的详情页补充的正文"
        e = self.started(Wire(ok(list_html([short, long])), ok(detail_html(long, body))))
        e.tick()
        before = {r["source_item_id"]: dict(r) for r in e.db.execute("SELECT rowid,* FROM posts")}
        self.assertEqual(len(before), 2)
        self.assertEqual(before["1002"]["title"], long["post_title"])
        self.assertIsNone(before["1002"]["content"])
        self.assertEqual(e.status()["aggregate"]["unique_posts"], 2)
        self.tick_due(e)
        after = {r["source_item_id"]: dict(r) for r in e.db.execute("SELECT rowid,* FROM posts")}
        self.assertEqual(set(after), set(before))
        self.assertEqual(after["1002"]["rowid"], before["1002"]["rowid"])
        self.assertEqual(after["1002"]["title"], before["1002"]["title"])
        self.assertEqual(after["1002"]["content"], body)
        self.assertEqual(after["1001"], before["1001"])
        counts = e.status()["aggregate"]
        self.assertEqual((counts["unique_posts"], counts["list_only"], counts["body_complete"], counts["pending"]), (2, 1, 1, 0))
        coverage = e.status()["coverage"][0]["details"]
        self.assertEqual((coverage["observed"], coverage["list_only"], coverage["complete"], coverage["pending"]), (2, 1, 1, 0))

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
