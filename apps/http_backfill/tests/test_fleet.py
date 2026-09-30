"""Fleet contracts with fake collectors and actual authenticated loopback HTTP."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import stat
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import test_core  # Makes the app/source import paths available.
from fleet import FleetError, FleetManager, NodeClient, normalize_base_url

LOCAL_ID = "11111111-1111-4111-8111-111111111111"
REMOTE_ID = "22222222-2222-4222-8222-222222222222"
OTHER_ID = "33333333-3333-4333-8333-333333333333"
TOKEN = "X" * 32


class FakeEngine:
    def __init__(self):
        self.id, self.state, self.controls = LOCAL_ID, "paused", []
        self.source_requests = 0
        self.exports = []

    def status(self):
        return {"version": "http-backfill.v4", "instance_id": self.id, "state": self.state,
                "aggregate": {"attempts": self.source_requests}, "active_halt": None}

    def jobs(self):
        return [{"id": 1}]

    def pause(self):
        self.state = "paused"
        self.controls.append("pause")

    def start(self):
        self.state = "running"
        self.controls.append("start")

    def retry(self):
        self.controls.append("retry")


class FakeMergeStore:
    """Only the FleetManager/MergeStore boundary; federation has full merge tests."""
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "fleet" / "fixture-cursors.json"
        self.cursors = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.merged = []

    def cursor(self, instance_id):
        return self.cursors.get(instance_id, 0)

    def merge_page(self, instance_id, page, raw_loader):
        if page["after"] != self.cursor(instance_id):
            raise ValueError("cursor mismatch")
        for item in page["items"]:
            for request in item.get("requests", []):
                raw = raw_loader(request["id"])
                if raw.get("instance_id") != instance_id:
                    raise ValueError("raw instance mismatch")
        self.merged.extend(page["items"])
        self.cursors[instance_id] = page["next_after"]
        self.path.write_text(json.dumps(self.cursors))
        return {"merged": len(page["items"])}

    def status(self):
        return {"cursors": dict(self.cursors), "collector_db": str(self.path.parent / "collector.db"), "model_database_eligible": False}

    def close(self):
        pass


def export_page(engine, after=0, limit=20, snapshot=None):
    items = [i for i in engine.exports if i["seq"] > after][:limit]
    next_after = items[-1]["seq"] if items else after
    return {"instance_id": engine.id, "schema_version": "fixture", "after": after, "snapshot": len(engine.exports),
            "next_after": next_after, "has_more": next_after < len(engine.exports), "items": items}


def export_raw(engine, request_id):
    return {"instance_id": engine.id, "request_id": request_id, "body_base64": base64.b64encode(b"raw").decode()}


class RemoteFixture:
    def __init__(self):
        self.identity, self.version, self.state = REMOTE_ID, "http-backfill.v4", "paused"
        self.calls, self.items = [], []
        self.redirect, self.echo_error, self.raw_wrong_identity = None, False, False
        self.force_timeout = False
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, code, payload):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self.serve()

            def do_POST(self):
                self.serve()

            def do_PATCH(self):
                self.serve()

            def do_DELETE(self):
                self.serve()

            def serve(self):
                parsed = urlparse(self.path)
                fixture.calls.append((self.command, parsed.path, self.headers.get("Authorization")))
                if self.headers.get("Authorization") != "Bearer " + TOKEN:
                    return self.send(401, {"error": "unauthorized"})
                if fixture.redirect:
                    self.send_response(302)
                    self.send_header("Location", fixture.redirect)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if fixture.force_timeout:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                if fixture.echo_error:
                    return self.send(409, {"error": "remote echo: " + TOKEN})
                if parsed.path == "/collector/api/status" or self.command != "GET":
                    return self.send(200, {"instance_id": fixture.identity, "version": fixture.version, "state": fixture.state})
                if parsed.path == "/collector/api/federation/export":
                    query = parse_qs(parsed.query)
                    after, limit = int(query["after"][0]), int(query["limit"][0])
                    items = [i for i in fixture.items if i["seq"] > after][:limit]
                    next_after = items[-1]["seq"] if items else after
                    return self.send(200, {"instance_id": fixture.identity, "schema_version": "fixture", "after": after,
                                          "snapshot": len(fixture.items), "next_after": next_after,
                                          "has_more": next_after < len(fixture.items), "items": items})
                if parsed.path == "/collector/api/federation/raw":
                    return self.send(200, {"instance_id": OTHER_ID if fixture.raw_wrong_identity else fixture.identity,
                                          "request_id": int(parse_qs(parsed.query)["request_id"][0]), "body_base64": "cmF3"})
                if parsed.path == "/collector/api/events":
                    return self.send(200, {"items": [{"id": 3}], "next_cursor": 3, "snapshot_id": 4, "has_more": True, "total": 4})
                return self.send(200, [{"id": 1}])
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/collector"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


class FleetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine, self.managers, self.remotes = FakeEngine(), [], []
        self.now = 1000.0
        module = types.ModuleType("federation")
        module.MergeStore, module.export_page, module.export_raw = FakeMergeStore, export_page, export_raw
        self.patch = patch.dict(sys.modules, {"federation": module})
        self.patch.start()

    def tearDown(self):
        for manager in self.managers:
            manager.close()
        for remote in self.remotes:
            remote.close()
        self.patch.stop()
        self.tmp.cleanup()

    def manager(self, **kwargs):
        manager = FleetManager(self.tmp.name, self.engine, clock=lambda: self.now, **kwargs)
        self.managers.append(manager)
        return manager

    def remote(self):
        remote = RemoteFixture()
        self.remotes.append(remote)
        return remote

    def wait(self, manager):
        for future in list(manager._futures.values()):
            future.result(timeout=3)

    def test_url_validation_allows_explicit_private_network_and_rejects_ambiguous_destinations(self):
        self.assertEqual(normalize_base_url("HTTP://192.168.1.2:8790/collector/"), "http://192.168.1.2:8790/collector")
        self.assertEqual(normalize_base_url("http://[::1]:8790/collector"), "http://[::1]:8790/collector")
        for bad in ("file:///tmp/node", "http://user:pass@localhost", "http://localhost/?", "http://localhost/#", "http://localhost/?token=x",
                    "http://localhost/../collector", "http://localhost/%2fcollector", "http://localhost//collector", " http://localhost", "http://localhost:invalid"):
            with self.subTest(url=bad), self.assertRaises(ValueError):
                normalize_base_url(bad)

    def test_local_is_immediately_usable_without_registration_or_source_request(self):
        manager = self.manager(auto_sync=False)
        self.assertEqual(manager.list_nodes()[0]["id"], "local")
        self.assertEqual(manager.overview()["nodes"][0]["connection"], "online")
        self.assertEqual(manager.proxy("local", "GET", "api/jobs"), [{"id": 1}])
        manager.proxy("local", "POST", "api/control", {"action": "pause"})
        self.assertEqual(self.engine.controls, ["pause"])
        self.assertEqual(self.engine.source_requests, 0)
        with self.assertRaises(ValueError):
            manager.remove("local")

    def test_registration_pins_uuid_private_permissions_and_token_omission_update_survives_restart(self):
        remote, manager = self.remote(), self.manager(auto_sync=False)
        node = manager.register({"id": "node-a", "name": "远程一", "base_url": remote.url + "/", "token": TOKEN})
        self.assertEqual(node["instance_id"], REMOTE_ID)
        self.assertNotIn(TOKEN, json.dumps(node))
        self.assertEqual(stat.S_IMODE(manager.registry_path.stat().st_mode), 0o600)
        manager.register({"id": "node-a", "name": "新名称"})
        self.assertEqual(manager.proxy("node-a", "GET", "api/jobs"), [{"id": 1}])
        manager.close()
        reopened = self.manager(auto_sync=False)
        self.assertEqual(reopened.list_nodes()[1]["name"], "新名称")
        self.assertNotIn(TOKEN, json.dumps(reopened.overview()))
        self.assertEqual(reopened.proxy("node-a", "GET", "api/status")["instance_id"], REMOTE_ID)
        self.assertTrue(all(auth == "Bearer " + TOKEN for _, _, auth in remote.calls))

    def test_duplicate_clone_uuid_and_changed_identity_are_rejected_before_mutation(self):
        remote, manager = self.remote(), self.manager()
        manager.register({"id": "node-a", "base_url": remote.url, "token": TOKEN})
        with self.assertRaises(FleetError):
            manager.register({"id": "node-b", "base_url": remote.url, "token": TOKEN})
        remote.identity = OTHER_ID
        with self.assertRaises(FleetError):
            manager.proxy("node-a", "POST", "api/control", {"action": "start"})
        self.assertFalse(any(method == "POST" for method, _, _ in remote.calls))
        with self.assertRaises(FleetError):
            manager.register({"id": "node-a", "name": "错误新身份"})
        self.assertEqual(manager.list_nodes()[1]["instance_id"], REMOTE_ID)
        remote.identity = LOCAL_ID
        with self.assertRaises(FleetError):
            manager.register({"id": "cloned-local", "base_url": remote.url, "token": TOKEN})

    def test_old_version_unknown_alias_and_nonwhitelisted_proxy_are_refused(self):
        remote, manager = self.remote(), self.manager()
        remote.version = "http-backfill.v3"
        with self.assertRaisesRegex(FleetError, "升级"):
            manager.register({"id": "old", "base_url": remote.url, "token": TOKEN})
        for path in ("api/session", "api/fleet", "api/status?token=x", "api/jobs/current/stocks/../../x"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                manager.proxy("local", "GET", path)
        with self.assertRaises(FleetError):
            manager.proxy("missing", "GET", "api/status")

    def test_auth_redirect_never_reaches_second_server_and_errors_redact_tokens(self):
        remote, other, manager = self.remote(), self.remote(), self.manager()
        manager.register({"id": "node-a", "base_url": remote.url, "token": TOKEN})
        remote.redirect = other.url + "/api/jobs"
        with self.assertRaises(FleetError):
            manager.proxy("node-a", "GET", "api/jobs")
        self.assertEqual(other.calls, [])
        remote.redirect, remote.echo_error = None, True
        with self.assertRaises(FleetError) as caught:
            manager.proxy("node-a", "GET", "api/jobs")
        self.assertNotIn(TOKEN, caught.exception.error)
        self.assertNotIn(TOKEN, json.dumps(manager.overview()))

    def test_ambiguous_mutation_is_sent_once_with_no_automatic_retry(self):
        remote, manager = self.remote(), self.manager(auto_sync=False)
        manager.register({"id": "node-a", "base_url": remote.url, "token": TOKEN})
        original = manager.client.request
        def disconnect(method, url, token, body=None):
            if method == "POST":
                original(method, url, token, body)
                raise FleetError(504, "response lost " + token, ambiguous=True)
            return original(method, url, token, body)
        with patch.object(manager.client, "request", side_effect=disconnect), self.assertRaises(FleetError) as caught:
            manager.proxy("node-a", "POST", "api/control", {"action": "retry"})
        self.assertTrue(caught.exception.ambiguous)
        self.assertNotIn(TOKEN, caught.exception.error)
        self.assertEqual(sum(method == "POST" for method, _, _ in remote.calls), 1)

    def test_paged_history_payload_and_queries_pass_through(self):
        remote, manager = self.remote(), self.manager()
        manager.register({"id": "node-a", "base_url": remote.url, "token": TOKEN})
        result = manager.proxy("node-a", "GET", "api/events", query={"paged": ["1"], "before_id": ["4"], "snapshot_id": ["4"], "limit": ["2"]})
        self.assertEqual(result["next_cursor"], 3)
        self.assertTrue(result["has_more"])

    def test_async_sync_transfers_collector_evidence_incrementally_and_remove_keeps_merge(self):
        remote, manager = self.remote(), self.manager(auto_sync=False)
        remote.items = [{"seq": i, "requests": [{"id": i}]} for i in range(1, 23)]
        manager.register({"id": "node-a", "base_url": remote.url, "token": TOKEN})
        manager.request_sync("node-a")
        self.wait(manager)
        self.assertEqual(manager.merge_store.cursor(REMOTE_ID), 20)
        self.assertTrue(manager.overview()["nodes"][1]["sync"]["has_more"])
        manager.tick()
        self.wait(manager)
        self.assertEqual(manager.merge_store.cursor(REMOTE_ID), 22)
        self.assertEqual(manager.overview()["nodes"][1]["sync"]["state"], "ready")
        manager.remove("node-a")
        self.assertEqual(manager.merge_store.cursor(REMOTE_ID), 22)
        self.assertEqual(len(manager.list_nodes()), 1)
        manager.close()
        reopened = self.manager(auto_sync=False)
        self.assertEqual(reopened.merge_store.cursor(REMOTE_ID), 22)
        self.assertEqual(self.engine.source_requests, 0)

    def test_failed_raw_transfer_retains_cursor_and_is_a_sync_error_not_zero_data(self):
        remote, manager = self.remote(), self.manager(auto_sync=False)
        remote.items = [{"seq": 1, "requests": [{"id": 1}]}]
        remote.raw_wrong_identity = True
        manager.register({"id": "node-a", "base_url": remote.url, "token": TOKEN})
        manager.request_sync("node-a")
        self.wait(manager)
        node = manager.overview()["nodes"][1]
        self.assertEqual(manager.merge_store.cursor(REMOTE_ID), 0)
        self.assertEqual(node["sync"]["state"], "error")
        self.assertIn("raw instance mismatch", node["sync"]["error"])
        self.assertEqual(node["connection"], "online")

    def test_restart_exposes_durable_cursor_and_continues_requested_pages(self):
        remote, manager = self.remote(), self.manager(auto_sync=False)
        remote.items = [{"seq": i, "requests": []} for i in range(1, 23)]
        manager.register({"id": "node-a", "base_url": remote.url, "token": TOKEN})
        manager.request_sync("node-a")
        self.wait(manager)
        self.assertEqual(manager.merge_store.cursor(REMOTE_ID), 20)
        manager.close()
        reopened = self.manager(auto_sync=False)
        node = reopened.overview()["nodes"][1]
        self.assertEqual(node["connection"], "unknown")
        self.assertEqual(node["sync"]["cursor"], 20)
        self.assertIn("node-a", reopened._requested)
        reopened.tick()
        self.wait(reopened)
        self.assertEqual(reopened.merge_store.cursor(REMOTE_ID), 22)

    def test_overview_is_cache_only_and_background_status_timeout_is_distinct(self):
        remote, manager = self.remote(), self.manager(auto_sync=False)
        manager.register({"id": "node-a", "base_url": remote.url, "token": TOKEN})
        count = len(remote.calls)
        manager.overview()
        manager.overview()
        self.assertEqual(len(remote.calls), count)
        with patch.object(manager.client, "request", side_effect=FleetError(504, "节点连接超时")):
            manager.tick()
            self.wait(manager)
        node = manager.overview()["nodes"][1]
        self.assertEqual(node["connection"], "offline")
        self.assertIn("超时", node["last_error"])
        self.assertEqual(node["status"]["state"], "paused")

    def test_automatic_sync_interval_and_explicit_sync_when_auto_disabled(self):
        manager = self.manager(auto_sync=False)
        manager.tick()
        self.wait(manager)
        self.assertEqual(manager.merge_store.cursor(LOCAL_ID), 0)
        self.engine.exports = [{"seq": 1, "requests": []}]
        manager.request_sync("local")
        self.wait(manager)
        self.assertEqual(manager.merge_store.cursor(LOCAL_ID), 1)
        self.assertEqual(self.engine.source_requests, 0)

    def test_automatic_sync_waits_sixty_seconds_while_status_polls_continue(self):
        manager = self.manager()
        self.engine.exports = [{"seq": 1, "requests": []}]
        manager.tick()
        self.wait(manager)
        self.assertEqual(manager.merge_store.cursor(LOCAL_ID), 1)
        self.engine.exports.append({"seq": 2, "requests": []})
        self.now += 59
        manager.tick()
        self.wait(manager)
        self.assertEqual(manager.merge_store.cursor(LOCAL_ID), 1)
        self.now += 1
        manager.tick()
        self.wait(manager)
        self.assertEqual(manager.merge_store.cursor(LOCAL_ID), 2)

    def test_explicit_sync_queued_during_inflight_snapshot_is_not_lost(self):
        manager = self.manager(auto_sync=False)
        self.engine.exports = [{"seq": 1, "requests": []}]
        entered, release = threading.Event(), threading.Event()
        original = manager.merge_store.merge_page
        def slow_merge(instance_id, page, loader):
            entered.set()
            self.assertTrue(release.wait(2))
            return original(instance_id, page, loader)
        with patch.object(manager.merge_store, "merge_page", side_effect=slow_merge):
            manager.request_sync("local")
            self.assertTrue(entered.wait(2))
            self.engine.exports.append({"seq": 2, "requests": []})
            manager.request_sync("local")
            release.set()
            self.wait(manager)
        self.assertIn("local", manager._requested)
        manager.tick()
        self.wait(manager)
        self.assertEqual(manager.merge_store.cursor(LOCAL_ID), 2)


if __name__ == "__main__":
    unittest.main()
