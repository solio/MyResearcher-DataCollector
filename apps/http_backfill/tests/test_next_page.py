"""Next-page-first navigation replays; temporary databases, no source network."""
from datetime import datetime, timedelta
import json
import tempfile
import unittest
from unittest.mock import patch

from test_core import Clock, CONFIG, Engine, Response, CHALLENGE, row, list_html, ok
from test_recovery import MovingSite


def source_rows(n=18, long=False):
    start = datetime(2025, 1, 10, 12)
    return [row(str(2000-i), published=(start-timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
                post_title="标题" * 20 if long else "短标题") for i in range(n)]


class NextPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.engines = []

    def tearDown(self):
        for engine in self.engines:
            engine.close()
        self.tmp.cleanup()

    def engine(self, site):
        engine = Engine(self.tmp.name, transport=site, clock=self.clock)
        self.engines.append(engine)
        return engine

    def started(self, site, page=1):
        engine = self.engine(site)
        engine.create_job(CONFIG)
        if page != 1:
            engine.set_start_page("601012", page)
        engine.start()
        self.tick(engine)
        return engine

    def tick(self, engine):
        self.clock.now = max(self.clock.now, engine.status()["next_request_epoch"])
        return engine.tick()

    def resume(self, engine):
        engine.pause()
        engine.start()

    def rec(self, engine):
        return engine._recovery(1, "601012")

    def test_details_delay_fetches_next_then_current_once_without_repeating_candidate(self):
        site = MovingSite(source_rows(long=True), page_size=3)
        engine = self.started(site)
        for _ in range(3):
            self.tick(engine)  # All three bodies in the first batch.
        self.clock.advance(400)
        self.tick(engine)
        self.assertEqual(engine.requests()[0]["page"], 2)
        self.assertEqual(engine.requests()[0]["purpose"], "forward")
        self.assertEqual(self.rec(engine)["phase"], "rollback_frontier")
        self.assertEqual(engine._frontier(1, "601012")["page"], 1)
        self.tick(engine)
        self.assertEqual(self.rec(engine)["phase"], "complete")
        self.assertEqual(engine._frontier(1, "601012")["page"], 2)
        lists = [r for r in reversed(engine.requests()) if r["kind"] == "list"]
        self.assertEqual([r["page"] for r in lists], [1, 2, 1])
        self.assertEqual(engine.status()["coverage"][0]["pages"], 2)
        self.assertFalse(engine.status()["coverage_complete"])

    def test_small_insertion_joins_next_without_current_or_previous_page(self):
        old = source_rows()
        site = MovingSite(old, page_size=3)
        engine = self.started(site, page=2)
        site.rows = [row("3000", published="2025-01-10 12:01:00", post_title="短标题")] + old
        self.resume(engine)
        self.assertEqual(engine.status()["current"]["page"], 3)
        self.tick(engine)
        self.assertEqual(self.rec(engine)["proof_level"], "sequential_frontier_join")
        self.assertEqual([r["page"] for r in reversed(engine.requests())], [2, 3])
        self.assertEqual(engine.status()["current"]["page"], 4)

    def test_deletion_rechecks_current_and_retains_missing_rows(self):
        old = source_rows()
        site = MovingSite(old, page_size=3)
        engine = self.started(site, page=2)
        site.rows = old[1:]
        self.resume(engine)
        self.tick(engine)  # Next page first.
        self.tick(engine)  # Current now contains the first unseen row.
        self.assertEqual(self.rec(engine)["phase"], "complete")
        self.assertEqual([r["page"] for r in reversed(engine.requests())], [2, 3, 2])
        ids = {r["post_id"] for r in engine.raw_posts()}
        self.assertIn(old[6]["post_id"], ids)
        self.assertEqual(engine.status()["current"]["page"], 4)

    def test_two_page_deletion_recurses_until_real_sequential_anchor(self):
        old = source_rows(24)
        site = MovingSite(old, page_size=3)
        engine = self.started(site, page=4)
        site.rows = old[6:]
        self.resume(engine)
        for _ in range(4):
            self.tick(engine)
        self.assertEqual([r["page"] for r in reversed(engine.requests())], [4, 5, 4, 3, 2])
        self.assertEqual(self.rec(engine)["phase"], "complete")
        ids = {r["post_id"] for r in engine.raw_posts()}
        self.assertTrue({r["post_id"] for r in old[12:21]} <= ids)
        self.assertEqual(engine.status()["current"]["page"], 6)

    def test_date_seek_known_id_does_not_stop_rollback(self):
        old = source_rows()
        site = MovingSite(old, page_size=3)
        engine = self.started(site, page=2)
        # A genuine earlier observation outside sequential page2.
        with engine.db:
            engine.db.execute("INSERT INTO observations(job,request_id,stock,page,post_id,source_row) VALUES(?,?,?,?,?,?)",
                              (1, 1, "601012", 4, old[10]["post_id"], json.dumps(old[10])))
        site.rows = old[3:]
        self.resume(engine)
        self.tick(engine)
        stats = engine.requests()[0]["analysis"]["list_observation"]
        self.assertEqual(stats["overlap"], 1)
        self.assertEqual(self.rec(engine)["phase"], "rollback_frontier")
        self.tick(engine)
        self.tick(engine)
        self.assertEqual(self.rec(engine)["phase"], "complete")
        self.assertTrue({r["post_id"] for r in old[6:9]} <= {r["post_id"] for r in engine.raw_posts()})

    def test_restart_preserves_candidate_and_recursive_checkpoint(self):
        old = source_rows(24)
        site = MovingSite(old, page_size=3)
        engine = self.started(site, page=4)
        site.rows = old[6:]
        self.resume(engine)
        self.tick(engine)
        self.tick(engine)
        saved = self.rec(engine)
        engine.pause()
        engine.close()
        reopened = self.engine(site)
        reopened.start()
        self.assertEqual(self.rec(reopened)["generation"], saved["generation"])
        self.assertEqual(reopened.status()["current"]["page"], 3)
        self.tick(reopened)
        self.tick(reopened)
        self.assertEqual(self.rec(reopened)["phase"], "complete")
        self.assertEqual(site.calls.count("https://guba.eastmoney.com/list,601012,f_5.html"), 1)

    def test_count_change_during_rollback_keeps_candidate_and_falls_back(self):
        old = source_rows()
        site = MovingSite(old, page_size=3)
        engine = self.started(site, page=2)
        self.resume(engine)
        self.tick(engine)
        site.rows = old[1:]
        self.tick(engine)
        rec = self.rec(engine)
        self.assertEqual(rec["strategy"], "two_pass")
        self.assertEqual(rec["validated_requests"], 2)
        self.assertEqual(rec["next_candidate"]["page"], 3)
        self.assertIn("计数", rec["fallback_reason"])
        for _ in range(16):
            if self.rec(engine)["phase"] == "complete":
                break
            self.tick(engine)
        self.assertEqual(self.rec(engine)["phase"], "complete")
        self.assertTrue({r["post_id"] for r in old[6:9]} <= {r["post_id"] for r in engine.raw_posts()})

    def test_long_pause_keeps_current_page_check(self):
        site = MovingSite(source_rows(), page_size=3)
        engine = self.started(site, page=2)
        engine.pause()
        self.clock.advance(21*3600)
        engine.start()
        self.assertEqual(self.rec(engine)["strategy"], "stable_frontier")
        self.assertEqual(engine.status()["current"]["page"], 2)

    def test_challenge_on_next_candidate_stops_node_without_rollback_request(self):
        site = MovingSite(source_rows(), page_size=3)
        engine = self.started(site, page=2)
        self.resume(engine)
        site.override = ok(CHALLENGE.encode())
        self.tick(engine)
        self.assertIsNotNone(engine.status()["node_source_auth_barrier"])
        before = len(site.calls)
        self.tick(engine)
        self.assertEqual(len(site.calls), before)

    def test_timeout_keeps_exact_candidate_target(self):
        site = MovingSite(source_rows(), page_size=3)
        engine = self.started(site, page=2)
        self.resume(engine)
        site.override = Response(None, b"", {}, error="curl: (28) Operation timed out", transient_error="network_timeout")
        self.tick(engine)
        task = engine.status()["current"]
        self.tick(engine)
        self.assertEqual([r["page"] for r in engine.requests()[:2]], [3, 3])
        self.assertEqual(task["page"], 3)

    def test_waiting_candidate_checkpoint_survives_restart(self):
        site = MovingSite(source_rows(), page_size=3)
        engine = self.started(site, page=2)
        self.resume(engine)
        saved = self.rec(engine)
        target = engine.db.execute("SELECT id FROM tasks WHERE status='pending' AND recovery_id=?", (saved["generation"],)).fetchone()[0]
        engine.pause()
        engine.close()
        reopened = self.engine(site)
        reopened.start()
        self.assertEqual(self.rec(reopened)["phase"], "await_next")
        self.assertEqual(self.rec(reopened)["generation"], saved["generation"])
        self.assertEqual(reopened.db.execute("SELECT status FROM tasks WHERE id=?", (target,)).fetchone()[0], "pending")
        self.tick(reopened)
        self.assertEqual(reopened.requests()[0]["page"], 3)

    def test_gap_growing_while_candidate_waits_reverts_to_current_check(self):
        site = MovingSite(source_rows(), page_size=3)
        engine = self.started(site, page=2)
        self.resume(engine)
        self.clock.advance(3600)
        self.tick(engine)
        self.assertEqual(engine.requests()[0]["page"], 2)
        self.assertEqual(self.rec(engine)["strategy"], "stable_frontier")

    def test_rate_uses_actual_small_page_capacity_and_escalates(self):
        old = source_rows()
        site = MovingSite(old, page_size=3)
        engine = self.started(site)
        site.rows = [row("3000", published="2025-01-10 12:01:00", post_title="短标题")] + old
        self.tick(engine)  # source count rose by one in a60sec list interval.
        engine.pause()
        self.clock.advance(240)
        engine.start()
        rec = self.rec(engine)
        self.assertEqual(rec["strategy"], "stable_frontier")
        growth = engine._pagination_growth(1, "601012", engine._frontier(1, "601012"))
        self.assertEqual(growth["page_capacity"], 3)
        self.assertGreaterEqual(growth["projected_net_additions"], 3)

    def test_missing_candidate_count_retains_rows_and_uses_bounded_recovery(self):
        old = source_rows()
        site = MovingSite(old, page_size=3)
        engine = self.started(site, page=2)
        self.resume(engine)
        site.override = ok(list_html(old[6:9]))
        self.tick(engine)
        rec = self.rec(engine)
        self.assertEqual(rec["strategy"], "two_pass")
        self.assertEqual(rec["validated_requests"], 1)
        self.assertTrue({r["post_id"] for r in old[6:9]} <= {r["post_id"] for r in engine.raw_posts()})

    def test_recursive_budget_stops_without_losing_acquired_response(self):
        old = source_rows(48)
        site = MovingSite(old, page_size=3)
        engine = self.started(site, page=5)
        site.rows = old[9:]
        with patch("core.MAX_RECOVERY_REQUESTS", 3):
            self.resume(engine)
            for _ in range(3):
                self.tick(engine)
            self.assertEqual(self.rec(engine)["validated_requests"], 3)
            self.assertEqual(self.rec(engine)["phase"], "error")
            self.assertEqual(engine.requests()[0]["outcome"], "real_data")
            self.assertEqual(engine.status()["coverage"][0]["runtime"]["active_halt"], "calibration_limit")
            before = len(site.calls)
            self.tick(engine)
            self.assertEqual(len(site.calls), before)

    def test_two_old_pages_still_finish_without_extra_candidate_calibration(self):
        old = source_rows(9)
        for r in old:
            r["post_publish_time"] = r["post_publish_time"].replace("2025-01-10", "2024-12-30")
        site = MovingSite(old, page_size=3)
        engine = self.started(site)
        self.resume(engine)
        self.tick(engine)
        self.assertEqual([r["page"] for r in reversed(engine.requests())], [1, 2])
        self.assertTrue(engine.status()["coverage"][0]["list_complete"])
        self.assertEqual(engine.status()["coverage"][0]["pages"], 2)
