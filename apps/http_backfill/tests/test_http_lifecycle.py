"""Real Engine behind the HTTP API; all source responses are offline fixtures."""
import hashlib
import http.cookiejar
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from test_core import Engine, Clock, Wire, CONFIG, CHALLENGE, row, list_html, ok
from test_server import server


class JobHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock, self.wire = Clock(), Wire()
        self.engine = Engine(self.tmp.name, transport=self.wire, clock=self.clock)
        self.token = "offline-http-fixture-token-at-least-32-characters"
        self.server = server.ConsoleServer(("127.0.0.1", 0), self.engine, self.token)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}/api/"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.assertEqual(self.request("session", {"token": self.token})[0], 200)

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.engine.close()
        self.tmp.cleanup()

    def request(self, path, obj=None, method=None):
        request = urllib.request.Request(self.base + path,
            data=json.dumps(obj).encode() if obj is not None else None,
            method=method, headers={"Content-Type": "application/json"})
        try:
            response = self.opener.open(request, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_edit_remove_last_stock_archive_and_create_without_source_calls(self):
        config = {**CONFIG, "stocks": ["601012", "600519"]}
        code, state = self.request("jobs", config)
        self.assertEqual(code, 200)
        job_id = state["job"]["id"]
        code, state = self.request("jobs/current", {**config, "client": "urllib", "interval_seconds": 90}, "PATCH")
        self.assertEqual(code, 200)
        self.assertEqual(state["job"]["id"], job_id)
        self.assertEqual(state["job"]["revision"], 2)
        self.assertEqual(state["state"], "paused")
        self.assertEqual(state["config"]["client"], "urllib")
        code, state = self.request("jobs/current/stocks/600519", method="DELETE")
        self.assertEqual(code, 200)
        self.assertEqual(state["config"]["stocks"], ["601012"])
        code, state = self.request("jobs/current/stocks/601012", method="DELETE")
        self.assertEqual(code, 200)
        self.assertIsNone(state["job"])
        code, history = self.request("jobs")
        self.assertEqual(code, 200)
        self.assertTrue(history[0]["archived"])
        self.assertFalse(history[0]["current"])
        code, state = self.request("jobs", CONFIG)
        self.assertEqual(code, 200)
        self.assertNotEqual(state["job"]["id"], job_id)
        self.assertEqual(self.wire.calls, [])

    def test_date_edit_keeps_raw_and_post_history(self):
        self.assertEqual(self.request("jobs", CONFIG)[0], 200)
        self.wire.responses.append(ok(list_html([row()])))
        self.engine.start()
        self.engine.tick()
        self.engine.pause()
        raw_before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.engine.raw_dir.iterdir()}
        code, state = self.request("jobs/current", {**CONFIG, "from_date": "2024-01-01", "to_date": "2024-12-31"}, "PATCH")
        self.assertEqual(code, 200)
        self.assertEqual(state["aggregate"]["unique_posts"], 0)
        self.assertEqual(self.engine.db.execute("SELECT COUNT(*) FROM http_posts").fetchone()[0], 1)
        self.assertEqual(self.engine.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 1)
        self.assertEqual(self.request("jobs/current", method="DELETE")[0], 200)
        self.assertEqual(raw_before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.engine.raw_dir.iterdir()})
        self.assertEqual(len(self.wire.calls), 1)

    def test_archive_blocked_job_keeps_probe_entry_and_no_new_scope_writes(self):
        self.assertEqual(self.request("jobs", CONFIG)[0], 200)
        self.wire.responses.extend([ok(list_html([row()], extra=CHALLENGE)), ok(list_html([row()]))])
        self.engine.start()
        self.engine.tick()
        due = self.engine.status()["next_request_epoch"]
        code, state = self.request("jobs/current", method="DELETE")
        self.assertEqual(code, 200)
        self.assertIsNone(state["job"])
        self.assertEqual(state["state"], "blocked")
        self.assertIsNotNone(state["current"])
        self.assertEqual(self.request("jobs", CONFIG)[0], 409)
        self.assertEqual(self.request("control", {"action": "start"})[0], 409)
        self.assertEqual(self.request("control", {"action": "retry"})[0], 200)
        self.assertFalse(self.engine.tick()["attempted"])
        self.clock.now = due
        self.engine.tick()
        state = self.engine.status()
        self.assertEqual(state["state"], "paused")
        self.assertIsNone(state["job"])
        self.assertIsNone(state["active_halt"])
        self.assertEqual(self.engine.db.execute("SELECT COUNT(*) FROM http_posts").fetchone()[0], 0)
        self.assertEqual(self.engine.db.execute("SELECT probe_only FROM requests ORDER BY id DESC LIMIT 1").fetchone()[0], 1)
        self.assertEqual(len(self.wire.calls), 2)
        self.assertEqual(self.request("jobs", CONFIG)[0], 200)


if __name__ == "__main__":
    unittest.main()
