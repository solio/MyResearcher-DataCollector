"""Hub plus actual authenticated HTTP collectors, using offline source fixtures."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from test_core import Engine, Wire, Clock, CONFIG, row, list_html, detail_html, ok
from server import ConsoleServer
from fleet import FleetManager
from federation import export_page


class FleetHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.servers, self.engines, self.wires = [], [], []
        self.token = "offline-hub-credential-at-least-32-characters"
        self.node_token = "offline-node-credential-at-least-32-characters"
        self.a_row = row("9101")
        self.short = row("9102", post_title="保留短标题")
        self.empty_row = row("9103")
        self.a = self.collector("a", [self.a_row, self.short], ["共同正文"])
        self.b = self.collector("b", [self.a_row, self.short, self.empty_row], ["共同正文", ""])
        self.hub = self.collector("hub", [], [])
        self.fleet = FleetManager(self.root / "hub", self.hub, auto_sync=False)
        self.a_url = self.serve(self.a, self.node_token)
        self.b_url = self.serve(self.b, self.node_token)
        self.base = self.serve(self.hub, self.token, self.fleet)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.fleet.close()
        for server, thread in reversed(self.servers):
            server.shutdown()
            thread.join()
            server.server_close()
        for engine in self.engines:
            engine.close()
        self.temp.cleanup()

    def collector(self, name, rows, bodies):
        clock = Clock()
        required = [r for r in rows if len(r["post_title"].strip()) >= 40]
        responses = [ok(list_html(rows))] + [ok(detail_html(r, body)) for r, body in zip(required, bodies)] if rows else []
        wire = Wire(*responses)
        engine = Engine(self.root / name, transport=wire, clock=clock)
        self.engines.append(engine)
        self.wires.append(wire)
        if rows:
            engine.create_job(CONFIG)
            engine.start()
            for _ in responses:
                clock.now = max(clock.now, engine.status()["next_request_epoch"])
                engine.tick()
            engine.pause()
        return engine

    def serve(self, engine, token, fleet=None):
        server = ConsoleServer(("127.0.0.1", 0), engine, token, fleet=fleet)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.servers.append((server, thread))
        return "http://127.0.0.1:" + str(server.server_port)

    def request(self, path, body=None, method=None, *, base=None, token=None, auth=True, origin=None):
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = "Bearer " + (token or self.token)
        if origin:
            headers["Origin"] = origin
        req = urllib.request.Request((base or self.base) + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method, headers=headers)
        try:
            response = self.opener.open(req, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def register(self, alias, base):
        code, public = self.request("/api/fleet/nodes", {
            "id": alias, "name": "离线实例 " + alias, "base_url": base, "token": self.node_token})
        self.assertEqual(code, 200, public)
        self.assertNotIn(self.node_token, json.dumps(public))
        return public

    def synchronize(self, alias="all", targets=None):
        code, result = self.request("/api/fleet/sync", {"node_id": alias})
        self.assertEqual(code, 200, result)
        targets = targets or [self.a, self.b, self.hub]
        snapshots = [(engine.status()["instance_id"], export_page(engine)["snapshot"]) for engine in targets]
        until = time.monotonic() + 6
        while time.monotonic() < until:
            self.fleet.tick()
            if all(self.fleet.merge_store.cursor(instance) == sequence for instance, sequence in snapshots):
                return
            time.sleep(0.01)
        self.fail("同步未完成: " + json.dumps(self.fleet.overview(), ensure_ascii=False))

    def merged_rows(self):
        with closing(sqlite3.connect(self.root / "hub" / "fleet" / "collector.db")) as db:
            db.row_factory = sqlite3.Row
            return {r["source_item_id"]: dict(r) for r in db.execute("SELECT * FROM posts")}

    def test_authenticated_central_registration_control_and_paged_history(self):
        self.assertEqual(self.request("/api/fleet", auth=False)[0], 401)
        self.assertEqual(self.request("/api/federation/export", base=self.a_url, auth=False)[0], 401)
        self.assertEqual(self.request("/api/nodes/a/status", auth=False)[0], 401)
        self.register("a", self.a_url)
        before = [len(w.calls) for w in self.wires]
        code, status = self.request("/api/nodes/a/status")
        self.assertEqual(code, 200)
        self.assertEqual(status["instance_id"], self.a.status()["instance_id"])
        self.assertEqual(status["version"], "http-backfill.v4")
        code, edited = self.request("/api/nodes/a/jobs/current", {**CONFIG, "interval_seconds": 90}, "PATCH")
        self.assertEqual(code, 200, edited)
        self.assertEqual(edited["config"]["interval_seconds"], 90)
        self.assertEqual(self.b.status()["config"]["interval_seconds"], 60)
        code, started = self.request("/api/nodes/a/control", {"action": "start"})
        self.assertEqual((code, started["state"]), (200, "running"))
        self.assertEqual(self.request("/api/nodes/a/control", {"action": "pause"})[1]["state"], "paused")
        code, page = self.request("/api/nodes/a/requests?paged=1&limit=1")
        self.assertEqual(code, 200)
        self.assertTrue(page["has_more"])
        code, second = self.request("/api/nodes/a/requests?paged=1&limit=1&before_id=" + str(page["next_cursor"]) + "&snapshot_id=" + str(page["snapshot_id"]))
        self.assertEqual(code, 200)
        self.assertLess(second["items"][0]["id"], page["items"][0]["id"])
        self.assertEqual([len(w.calls) for w in self.wires], before)
        for method, path, body in [("POST", "/api/fleet/sync", {}), ("PATCH", "/api/fleet/nodes/a", {"name": "改名"}), ("DELETE", "/api/fleet/nodes/a", None)]:
            self.assertEqual(self.request(path, body, method, origin="https://unrelated.invalid")[0], 403)

    def test_two_instances_merge_overlap_empty_and_missing_body_with_retained_raw(self):
        self.register("a", self.a_url)
        self.register("b", self.b_url)
        before = [len(w.calls) for w in self.wires]
        self.synchronize()
        posts = self.merged_rows()
        self.assertEqual(set(posts), {"9101", "9102", "9103"})
        self.assertEqual(posts["9101"]["content"], "共同正文")
        self.assertIsNone(posts["9102"]["content"])
        self.assertEqual(posts["9103"]["content"], "")
        raw = list((self.root / "hub" / "fleet" / "raw").rglob("*.body"))
        known = {r["sha256"] for e in (self.a, self.b) for r in e.requests()}
        self.assertEqual({hashlib.sha256(p.read_bytes()).hexdigest() for p in raw}, known)
        versions = self.fleet.merge_store.versions("eastmoney_guba", "9101")
        self.assertEqual({v["instance_id"] for v in versions}, {self.a.status()["instance_id"], self.b.status()["instance_id"]})
        self.assertEqual([len(w.calls) for w in self.wires], before)
        self.synchronize()
        self.assertEqual(self.merged_rows(), posts)
        for engine in (self.a, self.b):
            self.assertEqual(self.fleet.merge_store.cursor(engine.status()["instance_id"]), export_page(engine)["snapshot"])
        self.assertEqual(self.request("/api/fleet/nodes/b", method="DELETE")[0], 200)
        self.assertEqual(self.merged_rows(), posts)
        self.assertNotIn(self.node_token, json.dumps(self.request("/api/fleet")[1]))

    def test_node_secret_omission_preserves_credentials_and_refuses_identity_switch(self):
        self.register("a", self.a_url)
        code, updated = self.request("/api/fleet/nodes/a", {"name": "保留密钥"}, "PATCH")
        self.assertEqual(code, 200, updated)
        self.assertEqual(self.request("/api/nodes/a/status")[0], 200)
        code, failure = self.request("/api/fleet/nodes/a", {"base_url": self.b_url}, "PATCH")
        self.assertNotEqual(code, 200)
        self.assertNotIn(self.node_token, json.dumps(failure))
        self.assertEqual(self.request("/api/nodes/a/status")[1]["instance_id"], self.a.status()["instance_id"])

    def test_remote_unauthorized_does_not_expire_hub_and_cursor_errors_are_explicit(self):
        self.register("a", self.a_url)
        node_server = self.servers[0][0]
        node_server.token_digest = hashlib.sha256(b"rotated-remote-token").digest()
        code, failure = self.request("/api/nodes/a/status")
        self.assertEqual(code, 502, failure)
        self.assertEqual(self.request("/api/status")[0], 200)
        self.assertNotIn(self.node_token, json.dumps(failure))
        for query in ("after=-1", "after=1&after=2", "limit=101", "snapshot=unknown"):
            self.assertEqual(self.request("/api/federation/export?" + query)[0], 400)
        self.assertEqual(self.request("/api/federation/raw?request_id=../../etc/passwd")[0], 400)
        with patch("federation.export_page", side_effect=sqlite3.OperationalError("offline export journal unavailable")):
            code, failure = self.request("/api/federation/export")
            self.assertEqual(code, 409)
            self.assertIn("offline export journal", failure["error"])


if __name__ == "__main__":
    unittest.main()
