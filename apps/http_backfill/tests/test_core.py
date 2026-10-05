"""Offline invariants: injected transport/clock, no source traffic or sleeps."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

APP = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(APP))
from core import Engine, Response, challenge_evidence


class Clock:
    def __init__(self):
        self.now = 1_790_759_252.0  # 2026-09-30, both source test windows are in the past.

    def __call__(self):
        return self.now

    def advance(self, seconds=60):
        self.now += seconds


class Wire:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, client):
        self.calls.append((url, client))
        if not self.responses:
            raise AssertionError("Unexpected request")
        r = self.responses.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


def row(pid="1001", stock="601012", published="2025-01-10 10:00:00", **extra):
    # Most existing tests exercise detail transport; use an eligible title.
    # Policy-boundary tests explicitly provide short/exact/overflow titles.
    return {"post_id": pid, "post_type": 0, "post_title": "源" * 40, "stockbar_code": stock,
            "post_publish_time": published, "post_last_time": "2026-01-01 00:00:00",
            "user_id": "12345", "user_nickname": "源作者", "post_top_status": 0, **extra}


def list_html(rows, count=None, extra="", rc=1):
    links = "".join(f'<a href="/news,{r.get("stockbar_code")},{r.get("post_id")}.html">帖子</a>' for r in rows)
    payload = {"rc": rc, "re": rows}
    if count is not None:
        payload["count"] = count
    return (f'<html><head><title>股吧</title></head><body>{links}<script>var article_list={json.dumps(payload, ensure_ascii=False)};</script>{extra}</body></html>').encode()


def detail_html(r, content="完整正文", **extra):
    p = {"post_id": r["post_id"], "post_type": r["post_type"], "post_title": r.get("post_title"),
         "post_publish_time": r["post_publish_time"], "post_content": content,
         "post_guba": {"stockbar_code": r.get("stockbar_code")},
         "post_user": {"user_id": r.get("user_id"), "user_nickname": r.get("user_nickname")}, **extra}
    return f'<html><head><title>帖子</title></head><body><script>var post_article={json.dumps(p, ensure_ascii=False)};</script></body></html>'.encode()


def ok(body, **kw):
    return Response(200, body, {}, **kw)


CONFIG = {"stocks": ["601012"], "from_date": "2025-01-01", "to_date": "2025-01-31"}
CHALLENGE = '<title>身份核实</title><script src="/em_capt.js"></script><div id="emcaptcha">拖动滑块</div>'


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.engines = []

    def tearDown(self):
        for e in self.engines:
            e.close()
        self.tmp.cleanup()

    def engine(self, wire):
        e = Engine(self.tmp.name, transport=wire, clock=self.clock)
        self.engines.append(e)
        return e

    def started(self, wire, config=None):
        e = self.engine(wire)
        e.create_job(config or CONFIG)
        e.start()
        return e

    def tick_due(self, e):
        self.clock.now = max(self.clock.now, e.status()["next_request_epoch"])
        return e.tick()

    def test_default_paused_no_source_request(self):
        wire = Wire()
        e = self.engine(wire)
        self.assertEqual(e.status()["state"], "paused")
        self.assertFalse(e.tick()["attempted"])
        e.create_job(CONFIG)
        self.assertFalse(e.tick()["attempted"])
        self.assertEqual(wire.calls, [])

    def test_validation_and_no_job_overwrite(self):
        e = self.engine(Wire())
        for config in ({**CONFIG, "interval_seconds": -1}, {**CONFIG, "interval_seconds": True},
                       {**CONFIG, "stocks": ["123"]}, {**CONFIG, "from_date": "2025-02-31"},
                       {**CONFIG, "stocks": ["601012", "601012"]}, {**CONFIG, "client": "foo"}):
            with self.assertRaises(ValueError):
                e.create_job(config)
        e.create_job(CONFIG)
        with self.assertRaises(RuntimeError):
            e.create_job(CONFIG)

    def test_pacing_and_list_all_details_before_next_list(self):
        a, b = row(), row("1002")
        wire = Wire(ok(list_html([a, b])), ok(detail_html(a)), ok(detail_html(b, "")),
                    ok(list_html([a, b])), ok(list_html([a, b])), ok(list_html([])),
                    ok(list_html([a, b])), ok(list_html([a, b])))
        e = self.started(wire)
        e.tick()
        self.assertEqual(e.status()["current"]["kind"], "detail")
        for seconds in (0, 30, 29):
            self.clock.advance(seconds)
            self.assertFalse(e.tick()["attempted"])
        self.clock.advance(1)
        e.tick()
        self.assertIn("1001", wire.calls[1][0])
        self.tick_due(e)
        self.assertIn("1002", wire.calls[2][0])
        self.assertEqual(e.status()["current"]["kind"], "list")
        for _ in range(5):
            self.tick_due(e)
        counts = e.status()["aggregate"]
        self.assertEqual(counts["attempts"], 8)
        self.assertEqual(counts["list_pages"], 2)
        self.assertEqual(counts["calibration_requests"], 4)
        self.assertEqual(counts["body_complete"], 2)
        self.assertEqual(counts["nonempty_body"], 1)
        self.assertEqual(counts["source_empty_body"], 1)
        self.assertEqual(counts["pending"], 0)
        self.assertEqual(e.status()["state"], "completed")
        self.assertFalse(e.status()["coverage_complete"])

    def test_restart_requires_start_and_keeps_global_due(self):
        a = row()
        e = self.started(Wire(ok(list_html([a]))))
        e.tick()
        due = e.status()["next_request_epoch"]
        e.close()
        wire = Wire(ok(list_html([a])), ok(detail_html(a)))
        new = self.engine(wire)
        self.assertEqual(new.status()["state"], "paused")
        new.start()
        self.assertFalse(new.tick()["attempted"])
        self.assertEqual(new.status()["next_request_epoch"], due)
        self.clock.now = due
        new.tick()
        self.assertEqual(len(wire.calls), 1)
        self.assertIn("list,", wire.calls[0][0])
        self.assertEqual(new.requests()[0]["purpose"], "recovery")
        self.tick_due(new)
        self.assertEqual(new.status()["aggregate"]["body_complete"], 1)

    def test_single_process_lock(self):
        self.engine(Wire())
        with self.assertRaisesRegex(RuntimeError, "worker"):
            Engine(self.tmp.name, transport=Wire(), clock=self.clock)

    def test_payload_cannot_mask_challenge_retry_is_one_probe(self):
        a = row()
        wire = Wire(ok(list_html([a], extra=CHALLENGE)), ok(list_html([a])), ok(detail_html(a)))
        e = self.started(wire)
        e.tick()
        self.assertEqual(e.status()["state"], "blocked")
        self.assertEqual(e.status()["aggregate"]["unique_posts"], 0)
        evidence = e.status()["block_evidence"]
        with self.assertRaises(RuntimeError):
            e.start()
        with self.assertRaises(RuntimeError):
            e.create_job(CONFIG)
        e.retry()
        self.assertFalse(e.tick()["attempted"])
        self.tick_due(e)
        self.assertEqual(e.status()["state"], "paused")
        self.assertEqual(e.status()["block_evidence"], evidence)
        self.assertIsNone(e.status()["active_halt"])
        self.clock.advance(60)
        self.assertFalse(e.tick()["attempted"])
        self.assertEqual(len(wire.calls), 2)
        e.start()
        e.tick()
        self.assertEqual(len(wire.calls), 3)

    def test_captcha_asset_alone_and_hidden_overlay_are_not_blocks(self):
        html = list_html([row()], extra='<script src="/em_capt.js"></script><div id="emcaptcha" style="display:none">拖动滑块</div>')
        e = self.started(Wire(ok(html)))
        e.tick()
        self.assertEqual(e.status()["state"], "running")
        self.assertEqual(e.requests()[0]["outcome"], "real_data")
        self.assertFalse(challenge_evidence(html.decode())["challenge"])

    def test_static_overlay_challenge_even_with_normal_title(self):
        e = self.started(Wire(ok(list_html([row()], extra='<div style="position:fixed">请完成验证</div>'))))
        e.tick()
        self.assertEqual(e.status()["state"], "blocked")

    def test_429_retry_after_and_block_persist_restart(self):
        wire = Wire(Response(429, b"limited", {"Retry-After": "600"}))
        e = self.started(wire)
        start = self.clock.now
        e.tick()
        self.assertEqual(e.status()["next_request_epoch"], start + 600)
        e.close()
        new_wire = Wire(ok(list_html([row()])))
        new = self.engine(new_wire)
        self.assertEqual(new.status()["state"], "blocked")
        self.assertEqual(new.status()["block_evidence"]["http_status"], 429)
        new.retry()
        self.clock.now = start + 599
        self.assertFalse(new.tick()["attempted"])
        self.clock.advance(1)
        new.tick()
        self.assertEqual(new.status()["state"], "paused")

    def test_403_not_treated_as_data(self):
        e = self.started(Wire(Response(403, list_html([row()]), {})))
        e.tick()
        self.assertEqual(e.status()["aggregate"]["unique_posts"], 0)
        self.assertEqual(e.status()["state"], "blocked")

    def test_redirect_hops_each_get_one_rate_slot(self):
        url = "https://guba.eastmoney.com/list,601012,f.html?source=redirect"
        wire = Wire(Response(302, b"", {"location": url}), ok(list_html([row()])))
        e = self.started(wire)
        e.tick()
        self.assertEqual(e.requests()[0]["outcome"], "redirect")
        self.assertFalse(e.tick()["attempted"])
        self.tick_due(e)
        self.assertEqual(wire.calls[1][0], url)
        self.assertEqual(e.status()["aggregate"]["attempts"], 2)
        self.assertEqual(e.status()["aggregate"]["list_pages"], 1)

    def test_redirect_probe_does_not_clear_block_or_loop(self):
        wire = Wire(ok(CHALLENGE.encode()), Response(302, b"", {"location": "/list,601012,f.html?probe=1"}), ok(list_html([row()])))
        e = self.started(wire)
        e.tick()
        e.retry()
        self.tick_due(e)
        self.assertEqual(e.status()["state"], "blocked")
        with self.assertRaises(RuntimeError):
            e.start()
        self.clock.advance(60)
        self.assertFalse(e.tick()["attempted"])
        e.retry()
        e.tick()
        self.assertEqual(e.status()["state"], "paused")

    def test_offsite_redirect_is_error_and_not_requested(self):
        wire = Wire(Response(302, b"", {"location": "https://example.com"}))
        e = self.started(wire)
        e.tick()
        self.assertEqual(e.status()["state"], "error")
        self.clock.advance(1000)
        self.assertFalse(e.tick()["attempted"])
        self.assertEqual(len(wire.calls), 1)

    def test_partial_utf8_and_unknown_schema_fail_closed(self):
        samples = [ok(b"\xff"), ok(b"<html>unknown</html>"),
                   Response(200, list_html([row()]), {"content-length": "1"}),
                   ok(list_html([row()], rc=True)), ok(list_html([row(post_id="wrong")])),
                   ok(list_html([row(post_publish_time="2025-1-10 10:00:00")]))]
        for i, response in enumerate(samples):
            with self.subTest(i=i), tempfile.TemporaryDirectory() as tmp:
                e = Engine(tmp, transport=Wire(response), clock=self.clock)
                try:
                    e.create_job(CONFIG)
                    e.start()
                    e.tick()
                    self.assertEqual(e.status()["state"], "error")
                    self.assertEqual(e.status()["aggregate"]["unique_posts"], 0)
                    self.assertEqual(e.status()["aggregate"]["failures"], 1)
                finally:
                    e.close()

    def test_transport_failure_is_logged_and_no_loop(self):
        e = self.started(Wire(OSError("offline")))
        e.tick()
        r = e.requests()[0]
        self.assertEqual(r["outcome"], "transport_error")
        self.assertIn("offline", r["error"])
        self.assertEqual(r["sha256"], hashlib.sha256(b"").hexdigest())
        self.assertTrue((Path(self.tmp.name) / r["raw_ref"]).exists())
        self.clock.advance(60)
        self.assertFalse(e.tick()["attempted"])

    def test_strict_detail_identity_retains_pending(self):
        a = row()
        e = self.started(Wire(ok(list_html([a])), ok(detail_html(a, post_id="999"))))
        e.tick()
        self.tick_due(e)
        self.assertEqual(e.status()["state"], "error")
        self.assertEqual(e.status()["aggregate"]["body_complete"], 0)
        self.assertEqual(e.status()["aggregate"]["pending"], 1)
        self.assertEqual(e.status()["current"]["post_id"], a["post_id"])

    def test_source_missing_detail_distinct_from_schema_failure(self):
        a = row()
        missing = b'<html><div class="error404_page">\xe6\x82\xa8\xe8\xae\xbf\xe9\x97\xae\xe7\x9a\x84\xe5\xb8\x96\xe5\xad\x90\xe4\xb8\x8d\xe5\xad\x98\xe5\x9c\xa8</div></html>'
        e = self.started(Wire(ok(list_html([a])), ok(missing)))
        e.tick()
        self.tick_due(e)
        self.assertEqual(e.status()["aggregate"]["removed"], 1)
        self.assertEqual(e.status()["aggregate"]["body_complete"], 0)
        self.assertEqual(e.requests()[0]["outcome"], "detail_unavailable")
        self.assertEqual(e.raw_posts()[0]["status"], "removed")
        self.assertFalse(e.status()["coverage"][0]["details_complete"])

    def test_truncated_not_found_shell_is_error_not_terminal_missing(self):
        a = row()
        e = self.started(Wire(ok(list_html([a])), Response(200, b'<div class="error404_page">', {"content-length": "1000"})))
        e.tick()
        self.tick_due(e)
        self.assertEqual(e.status()["state"], "error")
        self.assertEqual(e.status()["aggregate"]["removed"], 0)
        self.assertEqual(e.status()["aggregate"]["pending"], 1)

    def test_server_error_not_found_markup_is_not_terminal_missing(self):
        a = row()
        e = self.started(Wire(ok(list_html([a])), Response(500, b'<div class="error404_page">server problem</div>', {})))
        e.tick()
        self.tick_due(e)
        self.assertEqual(e.status()["state"], "error")
        self.assertEqual(e.status()["aggregate"]["removed"], 0)
        self.assertEqual(e.status()["aggregate"]["pending"], 1)

    def test_empty_source_count_zero_and_missing_preserved(self):
        e = self.started(Wire(ok(list_html([], count=0))))
        e.tick()
        status = e.status()
        self.assertEqual(status["state"], "completed")
        self.assertEqual(status["coverage"][0]["source_count"], 0)
        self.assertTrue(status["coverage"][0]["gaps"])
        self.assertFalse(status["coverage_complete"])
        e.create_job(CONFIG)
        self.assertIsNone(e.status()["coverage"][0]["source_count"])

    def test_two_old_pages_confirm_boundary_without_body_fetch(self):
        a = row("1001", published="2024-12-31 23:59:59")
        b = row("1002", published="2024-12-30 00:00:00")
        e = self.started(Wire(*[ok(list_html(rows)) for rows in ([a], [a], [a], [b], [a], [b], [a], [b])]))
        e.tick()
        self.assertEqual(e.status()["current"]["kind"], "list")
        for _ in range(7):
            self.tick_due(e)
        status = e.status()
        self.assertEqual(status["state"], "completed")
        self.assertTrue(status["coverage"][0]["date_boundary_reached"])
        self.assertFalse(status["coverage_complete"])
        self.assertTrue(status["observed_work_complete"])
        self.assertEqual(status["aggregate"]["body_complete"], 0)

    def test_duplicate_forward_repositions_then_pauses_if_no_progress(self):
        a = row(published="2025-02-01 00:00:00")
        e = self.started(Wire(*[ok(list_html([a])) for _ in range(6)]))
        e.tick()
        for _ in range(5):
            self.tick_due(e)
        self.assertEqual(e.status()["state"], "error")
        self.assertEqual(e.status()["coverage"][0]["pages"], 1)
        self.assertEqual(e.status()["current"]["purpose"], "recovery")
        self.assertIn("分页无进展", e.status()["reason"])

    def test_global_post_dedupe_with_both_bar_associations(self):
        a = row(stock="600519")
        wire = Wire(ok(list_html([a])), ok(detail_html(a)), ok(list_html([a])), ok(list_html([a])), ok(list_html([a])))
        e = self.started(wire, {**CONFIG, "stocks": ["601012", "600519"]})
        e.tick()
        self.tick_due(e)
        self.tick_due(e)
        self.tick_due(e)
        self.tick_due(e)
        self.assertEqual(e.status()["aggregate"]["unique_posts"], 1)
        self.assertEqual(e.status()["aggregate"]["body_complete"], 1)
        self.assertEqual(len(e.raw_posts()[0]["associations"]), 2)
        self.assertEqual(sum("news," in url for url, _ in wire.calls), 1)

    def test_raw_hash_export_and_original_publish_not_update(self):
        a = row()
        body = list_html([a])
        e = self.started(Wire(ok(body), ok(detail_html(a))))
        e.tick()
        self.tick_due(e)
        record = e.raw_posts()[0]
        self.assertEqual(record["source_row"]["post_publish_time"], "2025-01-10 10:00:00")
        self.assertEqual(record["item"]["published_at"], "2025-01-10T10:00:00+08:00")
        r = e.requests()[1]
        self.assertEqual(r["sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual((Path(self.tmp.name) / r["raw_ref"]).read_bytes(), body)
        self.assertTrue(record["research_only"])
        self.assertFalse(record["model_database_eligible"])

    def test_pause_during_inflight_is_responsive_and_no_next_request(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def wire(url, client):
            calls.append(url)
            entered.set()
            self.assertTrue(release.wait(2))
            return ok(list_html([row()]))
        e = self.started(wire)
        t = threading.Thread(target=e.tick)
        t.start()
        self.assertTrue(entered.wait(2))
        self.assertTrue(e.status()["request_inflight"])
        e.pause()
        self.assertEqual(e.status()["state"], "paused")
        self.assertFalse(e.tick()["attempted"])
        release.set()
        t.join(2)
        self.assertFalse(t.is_alive())
        self.clock.advance(60)
        self.assertFalse(e.tick()["attempted"])
        self.assertEqual(e.status()["aggregate"]["list_pages"], 1)
        self.assertEqual(len(calls), 1)

    def test_crashed_reserved_request_remains_ambiguous_and_paused(self):
        e = self.started(Wire())
        with e.db:
            task = e._target()
            e.db.execute("INSERT INTO requests(job,task,kind,stock,page,url,started) VALUES(?,?,'list',?,1,?,?)",
                         (task["job"], task["id"], task["stock"], task["url"], self.clock.now))
            e.db.execute("UPDATE tasks SET status='inflight' WHERE id=?", (task["id"],))
            e._set("next_due", self.clock.now + 60)
        e.close()
        new = self.engine(Wire())
        self.assertEqual(new.status()["state"], "error")
        self.assertEqual(new.requests()[0]["outcome"], "interrupted_unknown")
        self.assertIsNone(new.requests()[0]["network_attempted"])
        self.assertEqual(new.status()["current"]["page"], 1)
        self.assertFalse(new.tick()["attempted"])
        with self.assertRaises(RuntimeError):
            new.start()

    def test_crash_orphan_raw_bytes_relinked_without_claiming_complete(self):
        e = self.started(Wire())
        with e.db:
            task = e._target()
            rid = e.db.execute("INSERT INTO requests(job,task,kind,stock,page,url,started) VALUES(?,?,'list',?,1,?,?)",
                               (task["job"], task["id"], task["stock"], task["url"], self.clock.now)).lastrowid
            e.db.execute("UPDATE tasks SET status='inflight' WHERE id=?", (task["id"],))
        body = b"partially saved source bytes"
        (Path(self.tmp.name) / "raw" / f"{rid:09d}.tmp").write_bytes(body)
        e.close()
        new = self.engine(Wire())
        r = new.requests()[0]
        self.assertEqual(r["outcome"], "interrupted_unknown")
        self.assertEqual(r["sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual((Path(self.tmp.name) / r["raw_ref"]).read_bytes(), body)
        self.assertEqual(new.status()["aggregate"]["list_pages"], 0)

    def test_future_upper_bound_is_frozen_at_job_creation(self):
        now = self.clock.now
        a = row(published="2026-09-30 23:59:59")
        e = self.started(Wire(ok(list_html([a]))), {**CONFIG, "to_date": "2027-01-01"})
        self.assertEqual(e._get("effective_to_epoch"), now)
        e.tick()
        self.assertEqual(e.status()["aggregate"]["unique_posts"], 0)
        self.assertFalse(e.status()["coverage_complete"])
        self.assertEqual(e.status()["coverage_proof"], "observed_pages_only")

    def test_future_start_rejected(self):
        e = self.engine(Wire())
        with self.assertRaises(ValueError):
            e.create_job({**CONFIG, "from_date": "2027-01-01", "to_date": "2027-01-31"})

    def test_continuous_segment_stops_clock_when_paused(self):
        e = self.started(Wire(ok(list_html([row()]))))
        self.clock.advance(120)
        e.tick()
        e.pause()
        self.assertEqual(e.status()["continuous_run_seconds"], 120)
        self.clock.advance(600)
        self.assertEqual(e.status()["continuous_run_seconds"], 120)
        self.assertEqual(e.status()["job_age_seconds"], 720)
        e.start()
        self.assertEqual(e.status()["continuous_run_seconds"], 0)
        self.clock.advance(30)
        self.assertEqual(e.status()["continuous_run_seconds"], 30)

    def test_list_page_snapshot_retains_count_and_overlap(self):
        a = row(published="2024-12-31 00:00:00")
        b = row("1002", published="2024-12-30 00:00:00")
        c = row("1003", published="2024-12-29 00:00:00")
        e = self.started(Wire(ok(list_html([a, b], count=100)), ok(list_html([b, c], count=101))))
        e.tick()
        self.tick_due(e)
        stats = e.requests()[0]["analysis"]["list_observation"]
        self.assertEqual(stats["source_count"], 101)
        self.assertEqual(stats["overlap"], 1)
        self.assertEqual(stats["new_ids"], 1)
        self.assertEqual(e.status()["coverage"][0]["pagination_overlap"], 1)
        self.assertFalse(e.status()["coverage_complete"])


if __name__ == "__main__":
    unittest.main()
