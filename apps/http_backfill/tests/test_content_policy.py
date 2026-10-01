"""Shared enrichment policy and v2 queue repair, entirely offline."""
import hashlib
import json
import unittest

import test_core as base
from test_core import Wire, row, list_html, detail_html, ok, CHALLENGE


class PolicyTests(unittest.TestCase):
    setUp = base.CoreTests.setUp
    tearDown = base.CoreTests.tearDown
    engine = base.CoreTests.engine
    started = base.CoreTests.started
    tick_due = base.CoreTests.tick_due

    def test_stripped_threshold_preserves_all_titles_and_only_queues_required_bodies(self):
        short = row("1001", post_title=" \t" + "短" * 39 + "\n ")
        exact = row("1002", post_title="  " + "长" * 40 + "  ")
        overflow = row("1003", post_title="溢" * 41)
        wire = Wire(ok(list_html([short, exact, overflow])), ok(detail_html(exact)), ok(detail_html(overflow, "")))
        e = self.started(wire)
        e.tick()
        counts = e.status()["aggregate"]
        self.assertEqual(counts["unique_posts"], 3)
        self.assertEqual(counts["list_only"], 1)
        self.assertEqual(counts["pending"], 2)
        self.assertEqual(counts["detail_required"], 2)
        self.assertEqual(counts["detail_policy_required"], 2)
        posts = {p["post_id"]: p for p in e.raw_posts()}
        self.assertEqual(posts["1001"]["content"], short["post_title"])
        self.assertEqual(posts["1001"]["content_source"], "list_title")
        self.assertEqual(posts["1001"]["source_metadata"]["list_title_length"], 39)
        self.assertFalse(posts["1001"]["body_complete"])
        self.assertIsNone(posts["1001"]["detail_enrichment_trigger"])
        self.assertEqual(posts["1002"]["detail_enrichment_trigger"], "list_title_length_eq_40")
        self.assertEqual(posts["1003"]["detail_enrichment_trigger"], "list_title_length_gt_40")
        self.tick_due(e)
        self.tick_due(e)
        self.assertEqual([url.rsplit(",", 1)[1] for url, _ in wire.calls if "/news," in url], ["1002.html", "1003.html"])
        counts = e.status()["aggregate"]
        self.assertEqual(counts["body_complete"], 2)
        self.assertEqual(counts["source_empty_body"], 1)
        self.assertEqual(counts["list_only"], 1)
        detail = e.raw_posts()[1]
        self.assertEqual(detail["content_source"], "detail_body")
        self.assertEqual(detail["source_metadata"]["detail_enrichment_trigger"], "list_title_length_eq_40")
        self.assertTrue(detail["body_complete"])
        self.assertEqual(e.status()["coverage"][0]["details"]["required"], 2)

    def legacy_pending(self, source, extra_responses=()):
        e = self.started(Wire(ok(list_html([source])), *extra_responses))
        e.tick()
        e.pause()
        post = e.db.execute("SELECT * FROM http_posts WHERE post_id=?", (source["post_id"],)).fetchone()
        data = json.loads(post["item"])
        data["source_metadata"].pop("content_source", None)
        with e.db:
            e.db.execute("UPDATE http_post_state SET status='pending',item=?,content_source=NULL WHERE post_id=?", (json.dumps(data), source["post_id"]))
            tid = e.db.execute("INSERT INTO tasks(job,kind,stock,page,post_id,url,original_url) VALUES(?,'detail',?,1,?,?,?)",
                               (e._get("job_id"), source["stockbar_code"], source["post_id"], data["url"], data["url"])).lastrowid
            e._set("content_policy_version", 2)
        return e, tid

    def test_old_pending_short_task_is_skipped_without_network_or_raw_changes(self):
        source = row(post_title="短标题")
        e, tid = self.legacy_pending(source)
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in e.raw_dir.iterdir()}
        requests = e.requests()
        due = e.status()["next_request_epoch"]
        e.close()
        wire = Wire()
        new = self.engine(wire)
        self.assertEqual(wire.calls, [])
        self.assertEqual(new.requests(), requests)
        self.assertEqual(new.status()["next_request_epoch"], due)
        self.assertEqual(new.status()["aggregate"]["list_only"], 1)
        self.assertEqual(new.status()["aggregate"]["pending"], 0)
        self.assertEqual(new.db.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0], "skipped")
        self.assertFalse(new.raw_posts()[0]["body_complete"])
        self.assertEqual(new.raw_posts()[0]["content"], source["post_title"])
        self.assertEqual({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in new.raw_dir.iterdir()}, before)
        self.assertTrue(any(ev["kind"] == "content_policy_migrated" and ev["evidence"]["short_detail_tasks_skipped"] == 1 for ev in new.events()))

    def test_old_complete_short_body_survives_migration_and_counts_truthfully(self):
        source = row(post_title="已有短标题正文")
        e, _ = self.legacy_pending(source, [ok(detail_html(source, "旧版已取得的真正文"))])
        with e.db:
            e._set("state", "running")  # Simulate old v2's queued short detail.
        self.tick_due(e)
        self.assertEqual(e.status()["aggregate"]["body_complete"], 1)
        raw = e.raw_posts()[0]["raw_refs"]
        e.close()
        new = self.engine(Wire())
        post = new.raw_posts()[0]
        self.assertEqual(post["content"], "旧版已取得的真正文")
        self.assertEqual(post["content_source"], "detail_body")
        self.assertTrue(post["body_complete"])
        self.assertFalse(post["detail_policy_required"])
        self.assertEqual(post["raw_refs"], raw)
        counts = new.status()["aggregate"]
        self.assertEqual(counts["body_complete"], 1)
        self.assertEqual(counts["detail_required"], 1)
        self.assertEqual(counts["detail_policy_required"], 0)
        self.assertEqual(counts["list_only"], 0)

    def test_old_blocked_short_target_keeps_one_probe_but_does_not_create_body(self):
        source = row(post_title="旧版短标题阻断")
        e, tid = self.legacy_pending(source, [ok(CHALLENGE.encode())])
        with e.db:
            e._set("state", "running")
        self.tick_due(e)
        self.assertEqual(e.status()["state"], "blocked")
        halt, due = e.status()["block_evidence"], e.status()["next_request_epoch"]
        e.close()
        wire = Wire(ok(detail_html(source, "探测验证响应，不补短标题正文")))
        new = self.engine(wire)
        self.assertEqual(new.status()["block_evidence"], halt)
        self.assertEqual(new.db.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0], "pending")
        self.assertEqual(new.status()["aggregate"]["list_only"], 1)
        with self.assertRaises(RuntimeError):
            new.start()
        new.retry()
        self.assertFalse(new.tick()["attempted"])
        self.assertEqual(new.status()["next_request_epoch"], due)
        self.tick_due(new)
        self.assertEqual(new.status()["state"], "paused")
        self.assertEqual(len(wire.calls), 1)
        self.assertTrue(new.requests()[0]["probe_only"])
        self.assertEqual(new.raw_posts()[0]["content_source"], "list_title")
        self.assertFalse(new.raw_posts()[0]["body_complete"])
        self.assertEqual(new.status()["aggregate"]["body_complete"], 0)
        self.clock.advance(600)
        self.assertFalse(new.tick()["attempted"])

    def test_scope_expansion_keeps_short_posts_without_adding_detail_tasks(self):
        short = row("1001", published="2025-01-05 10:00:00", post_title="短标题")
        long = row("1002", published="2025-01-16 10:00:00")
        config = {"stocks": ["601012"], "from_date": "2025-01-15", "to_date": "2025-01-31"}
        e = self.started(Wire(ok(list_html([long, short]))), config)
        e.tick()
        e.pause()
        e.update_job({**config, "from_date": "2025-01-01"})
        counts = e.status()["aggregate"]
        self.assertEqual(counts["unique_posts"], 2)
        self.assertEqual(counts["list_only"], 1)
        self.assertEqual(counts["pending"], 1)
        tasks = e.db.execute("SELECT post_id FROM tasks WHERE kind='detail' AND status='pending'").fetchall()
        self.assertEqual([r[0] for r in tasks], ["1002"])


if __name__ == "__main__":
    unittest.main()
