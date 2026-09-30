"""A moving fake source proves repair behavior without any network traffic."""
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest

from test_core import Clock, CONFIG, Engine, Response, CHALLENGE, row, list_html, detail_html, ok


class MovingSite:
    def __init__(self, rows, page_size=2):
        self.rows = list(rows)
        self.original = {str(r["post_id"]): r for r in rows}
        self.page_size = page_size
        self.calls = []
        self.override = None
        self.on_call = None

    def __call__(self, url, client):
        self.calls.append(url)
        if self.on_call:
            self.on_call(url)
        if self.override:
            response, self.override = self.override, None
            return response
        if "/list," in url:
            match = re.search(r",f(?:_(\d+))?\.html", url)
            page = int(match[1] or 1)
            return ok(list_html(self.rows[(page - 1) * self.page_size:page * self.page_size], count=len(self.rows)))
        pid = re.search(r",([0-9]+)\.html", url)[1]
        source_row = self.original.get(pid) or next(r for r in self.rows if str(r["post_id"]) == pid)
        return ok(detail_html(source_row))


def rows():
    return [row(str(1006 - i), published=f"2025-01-10 10:0{5-i}:00") for i in range(6)]


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.engines = []

    def tearDown(self):
        for e in self.engines:
            e.close()
        self.tmp.cleanup()

    def engine(self, site):
        e = Engine(self.tmp.name, transport=site, clock=self.clock)
        self.engines.append(e)
        return e

    def started(self, site):
        e = self.engine(site)
        e.create_job(CONFIG)
        e.start()
        return e

    def tick(self, e):
        self.clock.now = max(self.clock.now, e.status()["next_request_epoch"])
        return e.tick()

    def until(self, e, condition, max_ticks=80):
        for _ in range(max_ticks):
            if condition():
                return
            if e.status()["state"] != "running":
                self.fail(f"Stopped before expected invariant: {e.status()['reason']}")
            self.tick(e)
        self.fail("Fake source did not reach expected state")

    def test_pinned_only_anchor_error_does_not_prevent_restart_or_erase_halt(self):
        source = rows()[:2]
        for r in source:
            r["post_top_status"] = 1
        site = MovingSite(source)
        e = self.started(site)
        self.tick(e)
        before = e.status()
        self.assertEqual(before["state"], "error")
        self.assertEqual(before["active_halt"], "schema_error")
        halted = before["block_evidence"]["id"]
        due = before["next_request_epoch"]
        e.close()
        reopened = self.engine(site)
        after = reopened.status()
        self.assertEqual(after["active_halt"], "schema_error")
        self.assertEqual(after["block_evidence"]["id"], halted)
        self.assertEqual(after["next_request_epoch"], due)
        self.assertEqual(len(site.calls), 1)
        self.assertTrue(any(g["kind"] == "recovery_anchor_unavailable" for g in after["coverage"][0]["gaps"]))
        self.assertFalse(after["coverage_complete"])
        with self.assertRaises(RuntimeError):
            reopened.start()
        self.assertEqual(len(site.calls), 1)
        reopened.retry()
        self.tick(reopened)
        self.assertEqual(len(site.calls), 2)
        self.assertEqual(reopened.status()["state"], "error")

    def test_resume_deletion_recovers_item_that_old_page_number_would_skip(self):
        source = rows()
        site = MovingSite(source)
        e = self.started(site)
        self.tick(e)  # page 1: A, B
        self.tick(e)  # A body
        self.tick(e)  # B body
        e.pause()
        site.rows = source[1:]  # deleting A moves C onto page 1
        e.start()
        self.tick(e)
        self.assertEqual(e.requests()[0]["purpose"], "recovery")
        self.assertEqual(e.requests()[0]["page"], 1)
        skipped_id = source[2]["post_id"]
        self.until(e, lambda: any(p["post_id"] == skipped_id and p["body_complete"] for p in e.raw_posts()))
        self.assertNotIn("f_2.html", site.calls[:site.calls.index(f"https://guba.eastmoney.com/news,601012,{skipped_id}.html")])
        self.assertFalse(e.status()["coverage_complete"])

    def test_resume_insertion_relocates_frontier_to_new_physical_page(self):
        source = rows()
        site = MovingSite(source)
        e = self.started(site)
        self.tick(e)
        self.tick(e)
        self.tick(e)
        e.pause()
        newer = [row("2002", published="2026-09-30 18:01:00"), row("2001", published="2026-09-30 18:00:00")]
        site.rows = newer + source
        e.start()
        self.until(e, lambda: e.status()["recovery"][0]["phase"] == "complete")
        self.assertEqual(e.status()["current"]["page"], 3)  # old page 2 would repeat A,B
        self.assertEqual(e.status()["current"]["purpose"], "forward")
        self.assertGreater(e.status()["recovery"][0]["drift_count"], 0)
        self.assertTrue(e.status()["reconciliation_complete"])
        self.assertEqual(e.status()["aggregate"]["unique_posts"], 2)
        self.assertEqual(e.status()["aggregate"]["list_pages"], 1)
        self.assertFalse(e.status()["coverage_complete"])

    def test_mixed_type20_placements_do_not_send_recent_frontier_to_weeks_ago(self):
        ordinary = rows()
        ad1 = row("9001", published="2024-12-01 18:07:14", post_type=20)
        ad2 = row("9002", published="2025-01-01 15:03:19", post_type=20)
        site = MovingSite([ad1, ad2] + ordinary, page_size=4)
        e = self.started(site)
        self.tick(e)
        self.until(e, lambda: e.status()["recovery"][0]["phase"] == "complete")
        rec = e.status()["recovery"][0]
        self.assertEqual(rec["target_time"], ordinary[1]["post_publish_time"])
        self.assertTrue(rec["time_order_verified"])
        self.assertEqual(e.status()["current"]["page"], 2)
        self.assertFalse(any("f_2.html" in url for url in site.calls))
        self.assertEqual(e.status()["aggregate"]["unique_posts"], 2)
        self.assertEqual(e.status()["aggregate"]["list_pages"], 1)
        self.assertFalse(e.status()["coverage_complete"])

    def test_details_stage_deletion_triggers_recheck_and_new_details_first(self):
        source = rows()
        site = MovingSite(source)
        changed = False
        def during_body(url):
            nonlocal changed
            if "/news," in url and not changed:
                site.rows = source[1:]
                changed = True
        site.on_call = during_body
        e = self.started(site)
        self.tick(e)
        missing = source[2]["post_id"]
        self.until(e, lambda: any(p["post_id"] == missing and p["body_complete"] for p in e.raw_posts()))
        body_index = next(i for i, url in enumerate(site.calls) if f",{missing}.html" in url)
        self.assertIn("list,601012,f.html", site.calls[body_index - 1])
        self.assertFalse(any("f_2.html" in url for url in site.calls[:body_index]))
        self.assertGreater(e.status()["aggregate"]["calibration_requests"], 0)

    def test_previous_page_rescan_repairs_gap_already_created_before_forward_page(self):
        source = rows()
        site = MovingSite(source)
        e = self.started(site)
        for _ in range(5):
            self.tick(e)  # page1 + A/B details + its two verification passes
        self.assertEqual(e.status()["current"]["purpose"], "forward")
        self.assertEqual(e.status()["current"]["page"], 2)
        site.rows = source[1:]  # page2 now D,E; C would already be skipped
        self.tick(e)
        skipped = source[2]["post_id"]
        self.assertFalse(any(p["post_id"] == skipped for p in e.raw_posts()))
        self.until(e, lambda: any(p["post_id"] == skipped and p["body_complete"] for p in e.raw_posts()))
        self.assertEqual(e.status()["aggregate"]["list_pages"], 2)
        self.assertEqual(e.status()["coverage"][0]["pages"], 2)
        self.assertTrue(any(r["purpose"] == "recovery" and r["page"] == 1 for r in e.requests()))

    def test_shift_between_two_passes_is_observed_and_repaired(self):
        source = rows()
        site = MovingSite(source)
        e = self.started(site)
        self.tick(e)
        self.tick(e)
        self.tick(e)
        self.tick(e)  # first calibration snapshot A,B
        site.rows = source[1:]
        self.tick(e)  # second snapshot B,C disagrees; C is queued
        self.assertEqual(e.status()["current"]["kind"], "detail")
        self.assertEqual(e.status()["current"]["post_id"], source[2]["post_id"])
        self.assertGreater(e.status()["recovery"][0]["drift_count"], 0)
        self.until(e, lambda: e.status()["recovery"][0]["phase"] == "complete")
        self.assertTrue(any(p["post_id"] == source[2]["post_id"] and p["body_complete"] for p in e.raw_posts()))

    def test_calibration_challenge_stops_and_probe_has_same_target(self):
        site = MovingSite(rows())
        e = self.started(site)
        self.tick(e)
        self.tick(e)
        self.tick(e)
        site.override = ok(CHALLENGE.encode())
        self.tick(e)
        self.assertEqual(e.status()["state"], "blocked")
        failed = e.requests()[0]
        self.assertEqual(failed["purpose"], "recovery")
        self.clock.advance(600)
        self.assertFalse(e.tick()["attempted"])
        e.retry()
        self.tick(e)
        self.assertEqual(e.requests()[0]["url"], failed["url"])
        self.assertEqual(e.status()["state"], "paused")
        self.assertEqual(e.status()["block_evidence"]["id"], failed["id"])

    def test_restart_rechecks_before_old_pending_details_and_keeps_id_and_due(self):
        source = rows()
        site = MovingSite(source)
        e = self.started(site)
        self.tick(e)
        due, identity = e.status()["next_request_epoch"], e.status()["instance_id"]
        e.close()
        site.rows = source[1:]
        new = self.engine(site)
        self.assertEqual(new.status()["state"], "paused")
        self.assertEqual(new.status()["instance_id"], identity)
        new.start()
        self.assertFalse(new.tick()["attempted"])
        self.assertEqual(new.status()["next_request_epoch"], due)
        self.tick(new)
        self.assertEqual(new.requests()[0]["purpose"], "recovery")
        self.assertEqual(new.status()["aggregate"]["pending"], 3)  # A,B plus recovered C

    def test_all_anchor_ids_disappear_time_fallback_remains_an_explicit_gap(self):
        source = rows()
        site = MovingSite(source)
        e = self.started(site)
        self.tick(e)
        self.tick(e)
        self.tick(e)
        e.pause()
        site.rows = source[2:]
        e.start()
        self.until(e, lambda: e.status()["recovery"][0]["phase"] == "complete")
        status = e.status()
        self.assertTrue(status["recovery"][0]["time_fallback"])
        self.assertTrue(any(g["kind"] == "anchor_ids_missing_time_fallback" for g in status["coverage"][0]["gaps"]))
        self.assertFalse(status["reconciliation_complete"])
        self.assertFalse(status["coverage_complete"])

    def test_anchor_source_empty_pauses_instead_of_guessing_next_page(self):
        site = MovingSite(rows())
        e = self.started(site)
        self.tick(e)
        self.tick(e)
        self.tick(e)
        site.rows = []
        self.tick(e)
        self.assertEqual(e.status()["state"], "error")
        self.assertTrue(any(g["kind"] == "anchor_not_located" for g in e.status()["coverage"][0]["gaps"]))
        self.assertFalse(e.status()["coverage"][0]["list_complete"])
        self.assertEqual(e.status()["current"]["purpose"], "recovery")

    def test_all_nonstandard_rows_can_anchor_without_body_fetch(self):
        source = [row("3002", published="2025-01-10 10:00:00", post_type=20), row("3001", published="2025-01-10 09:00:00", post_type=3)]
        site = MovingSite(source)
        e = self.started(site)
        self.tick(e)
        self.until(e, lambda: e.status()["recovery"][0]["phase"] == "complete")
        self.assertEqual(e.status()["aggregate"]["unique_posts"], 0)
        self.assertFalse(any("/news," in url for url in site.calls))
        self.assertFalse(e.status()["reconciliation_complete"])
        self.assertTrue(any(g["kind"] == "time_order_unverified" for g in e.status()["coverage"][0]["gaps"]))

    def test_all_requests_including_recovery_are_paced_and_raw_hash_replayable(self):
        import hashlib
        site = MovingSite(rows())
        e = self.started(site)
        self.tick(e)
        self.until(e, lambda: e.status()["recovery"][0]["phase"] == "complete")
        ledger = list(reversed(e.requests(100)))
        for prev, following in zip(ledger, ledger[1:]):
            self.assertGreaterEqual(following["started"] - prev["finished"], 60)
        for observation in ledger:
            raw = Path(self.tmp.name) / observation["raw_ref"]
            self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(), observation["sha256"])
            self.assertEqual(observation["instance_id"], e.status()["instance_id"])

    def test_v1_observations_are_additively_migrated_and_not_accepted_as_verified(self):
        site = MovingSite(rows())
        e = self.started(site)
        self.tick(e)
        old_requests = e.status()["aggregate"]["attempts"]
        with e.db:
            e.db.execute("DELETE FROM frontiers")
            e.db.execute("DELETE FROM recoveries")
        e.close()
        new = self.engine(site)
        self.assertEqual(new.status()["aggregate"]["attempts"], old_requests)
        self.assertTrue(new._frontier(new._get("job_id"), "601012")["migrated_v1"])
        self.assertFalse(new.status()["reconciliation_complete"])
        self.assertEqual(new.status()["current"]["purpose"], "recovery")

    def test_exact_challenge_title_without_assets_still_blocks_payload(self):
        html = list_html(rows()[:2]).replace(b"<title>\xe8\x82\xa1\xe5\x90\xa7</title>", "<title>身份核实</title>".encode())
        e = self.started(lambda url, client: ok(html))
        self.tick(e)
        self.assertEqual(e.status()["state"], "blocked")
        self.assertEqual(e.status()["aggregate"]["unique_posts"], 0)

    def test_geetest_static_container_and_instructions_block_valid_payload(self):
        html = list_html(rows()[:2], extra='<div class="geetest_panel"><span>请完成安全验证</span></div>')
        e = self.started(lambda url, client: ok(html))
        self.tick(e)
        self.assertEqual(e.status()["state"], "blocked")
        self.assertEqual(e.status()["aggregate"]["unique_posts"], 0)


if __name__ == "__main__":
    unittest.main()
