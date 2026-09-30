import http.cookiejar
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import threading
import unittest
import urllib.error
import urllib.request

APP = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("console_server", APP / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class StubEngine:
    def __init__(self):
        self.state = "idle"
        self.calls = []
        self._mutex = threading.RLock()
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE requests(id INTEGER PRIMARY KEY,started REAL,finished REAL,headers TEXT,analysis TEXT);
        CREATE TABLE events(id INTEGER PRIMARY KEY,created REAL,kind TEXT,message TEXT,evidence TEXT);
        """)

    def _get(self, key):
        return "server-test-instance" if key == "instance_id" else None

    def status(self):
        return {"state": self.state, "attempts": 0}

    def create_job(self, config):
        if not config.get("stocks"):
            raise ValueError("stocks required")
        self.calls.append(("create", config))
        self.state = "paused"

    def start(self):
        self.calls.append("start")
        self.state = "running"

    def pause(self):
        self.calls.append("pause")
        self.state = "paused"

    def retry(self):
        raise RuntimeError("retry requires blocked or error")

    def requests(self, limit):
        return []

    def events(self, limit):
        return []

    def raw_posts(self, limit, offset):
        return []

    def jobs(self):
        return [{"id": 1, "status": "active"}]

    def update_job(self, config):
        if self.state == "running":
            raise RuntimeError("请先暂停任务")
        if not config.get("stocks"):
            raise ValueError("stocks required")
        self.calls.append(("update", config))

    def remove_stock(self, stock):
        if not re.fullmatch(r"[0-9]{6}", stock):
            raise ValueError("六位股票代码")
        self.calls.append(("remove", stock))

    def delete_job(self):
        self.calls.append("archive")
        self.state = "paused"


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.engine = StubEngine()
        self.token = "test-console-token-is-at-least-32-characters"
        self.server = server.ConsoleServer(("127.0.0.1", 0), self.engine, self.token)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.jar))

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.engine.db.close()

    def request(self, path, obj=None, method=None, headers=None):
        req = urllib.request.Request(self.base + path,
            data=json.dumps(obj).encode() if obj is not None else None,
            method=method, headers={"Content-Type": "application/json", **(headers or {})})
        try:
            response = self.opener.open(req)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, response.headers, response.read()

    def login(self):
        status, headers, _ = self.request("/api/session", {"token": self.token})
        self.assertEqual(status, 200)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])

    def test_control_requires_auth_and_reads_never_start_source(self):
        self.assertEqual(self.request("/api/control", {"action": "start"})[0], 401)
        self.assertEqual(self.request("/api/status")[0], 401)
        self.assertEqual(self.engine.calls, [])
        self.login()
        for _ in range(3):
            self.assertEqual(self.request("/api/status")[0], 200)
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(self.request("/api/jobs", {"stocks": ["601012"]})[0], 200)
        self.assertEqual(self.engine.state, "paused")
        self.assertEqual(self.request("/api/control", {"action": "start"})[0], 200)
        self.assertEqual(self.engine.state, "running")
        self.assertEqual(self.request("/api/control", {"action": "retry"})[0], 409)

    def test_origin_and_logout(self):
        self.login()
        status = self.request("/api/control", {"action": "pause"},
            headers={"Origin": "https://another.invalid"})[0]
        self.assertEqual(status, 403)
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(self.request("/api/session", method="DELETE")[0], 200)
        self.assertEqual(self.request("/api/events")[0], 401)

    def test_bearer_and_input_limits(self):
        self.assertEqual(self.request("/api/status", headers={"Authorization": "Bearer " + self.token})[0], 200)
        self.login()
        self.assertEqual(self.request("/api/posts?limit=100000")[0], 400)
        self.assertEqual(self.request("/api/posts?offset=-1")[0], 400)
        self.assertEqual(self.request("/api/jobs", {})[0], 400)
        self.assertEqual(self.request("/api/control", {"action": "reset_block"})[0], 400)
        self.assertEqual(self.request("/../../server.py")[0], 404)

    def test_real_html_assets_are_served_without_auth(self):
        status, _, body = self.request("/")
        self.assertEqual(status, 200)
        assets = re.findall(r'(?:src|href)="([^"#]+\.(?:css|js))"', body.decode())
        self.assertGreaterEqual(len(assets), 2)
        for asset in assets:
            with self.subTest(asset=asset):
                status, _, content = self.request("/" + asset)
                self.assertEqual(status, 200)
                self.assertTrue(content)

    def test_job_edit_remove_archive_are_authenticated_and_origin_checked(self):
        config = {"stocks": ["601012"]}
        self.assertEqual(self.request("/api/jobs/current", config, "PATCH")[0], 401)
        self.assertEqual(self.request("/api/jobs/current", method="DELETE")[0], 401)
        self.assertEqual(self.request("/api/jobs/current/stocks/601012", method="DELETE")[0], 401)
        self.login()
        for path, method, obj in (("/api/jobs/current", "PATCH", config),
                                  ("/api/jobs/current", "DELETE", None),
                                  ("/api/jobs/current/stocks/601012", "DELETE", None)):
            self.assertEqual(self.request(path, obj, method, {"Origin": "https://another.invalid"})[0], 403)
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(self.request("/api/jobs/current", config, "PATCH")[0], 200)
        self.assertEqual(self.request("/api/jobs/current/stocks/601012", method="DELETE")[0], 200)
        self.assertEqual(self.request("/api/jobs/current/stocks/invalid", method="DELETE")[0], 400)
        self.assertEqual(self.request("/api/jobs/current", method="DELETE")[0], 200)
        self.assertEqual(self.engine.calls, [("update", config), ("remove", "601012"), "archive"])
        status, _, body = self.request("/api/jobs")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)[0]["status"], "active")

    def test_edit_running_job_returns_conflict(self):
        self.login()
        self.engine.state = "running"
        self.assertEqual(self.request("/api/jobs/current", {"stocks": ["601012"]}, "PATCH")[0], 409)
        self.assertEqual(self.engine.calls, [])

    def test_paged_activity_auth_snapshot_and_legacy_arrays(self):
        self.assertEqual(self.request("/api/requests?paged=1")[0], 401)
        self.login()
        with self.engine.db:
            for row_id in range(1, 5):
                self.engine.db.execute("INSERT INTO requests VALUES(?,?,?,NULL,NULL)", (row_id, 1000, 1001))
                self.engine.db.execute("INSERT INTO events VALUES(?,?,?,'暂停',NULL)", (row_id, 1000, "paused"))
        for kind in ("requests", "events"):
            with self.subTest(kind=kind):
                self.assertIsInstance(json.loads(self.request(f"/api/{kind}")[2]), list)
                status, headers, body = self.request(f"/api/{kind}?paged=1&limit=2")
                self.assertEqual(status, 200)
                self.assertEqual(headers["Cache-Control"], "no-store")
                page = json.loads(body)
                self.assertEqual([r["id"] for r in page["items"]], [4, 3])
                self.assertEqual(page["snapshot_id"], 4)
                if kind == "requests":
                    self.engine.db.execute("INSERT INTO requests VALUES(5,1000,1001,NULL,NULL)")
                else:
                    self.engine.db.execute("INSERT INTO events VALUES(5,1000,'paused','暂停',NULL)")
                _, _, body = self.request(f"/api/{kind}?paged=1&limit=2&before_id=3&snapshot_id=4")
                page = json.loads(body)
                self.assertEqual([r["id"] for r in page["items"]], [2, 1])
                self.assertEqual(page["total"], 4)
                self.assertFalse(page["has_more"])
                self.assertIsNone(page["next_cursor"])
                self.assertEqual(page["items"][0]["instance_id"], "server-test-instance")
        self.assertEqual(self.engine.calls, [])

    def test_paged_activity_rejects_bad_cursors_and_limit(self):
        self.login()
        for query in ("paged=1&limit=201", "paged=1&limit=0", "paged=1&before_id=0",
                      "paged=1&snapshot_id=-1", "paged=1&before_id=x", "paged=1&before_id=",
                      "paged=1&before_id=1&before_id=2", "paged=1&snapshot_id=9223372036854775808", "paged=wrong"):
            with self.subTest(query=query):
                status, _, body = self.request("/api/requests?" + query)
                self.assertEqual(status, 400)
                self.assertIn("error", json.loads(body))
        self.assertEqual(self.engine.calls, [])


if __name__ == "__main__":
    unittest.main()
