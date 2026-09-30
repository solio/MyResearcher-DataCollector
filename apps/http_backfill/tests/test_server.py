import http.cookiejar
import importlib.util
import json
from pathlib import Path
import re
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


if __name__ == "__main__":
    unittest.main()
