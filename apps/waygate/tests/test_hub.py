"""No source traffic, SSH, real accounts or vendor push. Real HTTP/WSS loopback."""
import asyncio
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

from apps.waygate.hub.collector import allowed_connect, check_target
from apps.waygate.hub.service import HUB_KEY, Waygate, create_app
from apps.waygate.hub.store import Store, notification_payload


def fleet():
    row = {"job": 1, "stock": "002648", "state": "blocked", "active_halt": "challenge",
           "halt_task_id": 12, "block_evidence": {"id": 20}}
    return [{"id": "node36", "name": "测试节点", "base_url": "http://example.test:8790",
             "connection": "online", "last_seen_at": datetime.now(timezone.utc).isoformat(),
             "status": {"instance_id": "instance-a", "job": {"id": 1, "revision": 3},
                        "state": "blocked", "stock_runtimes": [row], "node_source_auth_barrier": {
                            "job": 1, "stock": "002648", "task_id": 12, "request_id": 19}}}]


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.nodes = fleet()
        self.store.device("test-device", "fake-push-token")

    def tearDown(self):
        self.store.db.close()

    def test_poll_and_failed_probe_deduplicate_by_barrier_owner(self):
        self.store.reconcile(self.nodes)
        first = self.store.inbox(self.nodes)[0]["id"]
        row = self.nodes[0]["status"]["stock_runtimes"][0]
        row["block_evidence"]["id"] = 21
        self.nodes[0]["status"]["stock_runtimes"].append(dict(row, stock="600031", halt_task_id=13))
        for _ in range(4):
            self.store.reconcile(self.nodes)
        self.assertEqual([i["id"] for i in self.store.inbox(self.nodes)], [first])
        self.assertEqual(self.store.incident(first)["target"]["request_id"], 21)
        self.assertEqual(len(self.store.notifications()), 1)

    def test_offline_stale_and_historical_evidence(self):
        self.store.reconcile(self.nodes)
        self.nodes[0]["status"]["stock_runtimes"][0]["active_halt"] = None
        self.nodes[0]["connection"] = "offline"
        self.store.reconcile(self.nodes)
        self.assertEqual(len(self.store.inbox(self.nodes)), 1)
        self.nodes[0]["connection"] = "online"
        self.nodes[0]["last_seen_at"] = "2020-01-01T00:00:00+00:00"
        self.store.reconcile(self.nodes)
        self.assertFalse(self.store.inbox(self.nodes)[0]["can_verify"])
        self.nodes[0]["last_seen_at"] = datetime.now(timezone.utc).isoformat()
        self.store.reconcile(self.nodes)
        self.assertEqual(self.store.inbox(self.nodes), [])
        self.assertEqual(len(self.store.notifications()), 0)

    def test_transport_is_not_captcha_and_proxy_auth_is_diagnostic(self):
        row = self.nodes[0]["status"]["stock_runtimes"][0]
        for kind in (None, "transport_error", "tls_error"):
            row["active_halt"] = kind
            self.store.reconcile(self.nodes)
            self.assertEqual(self.store.inbox(self.nodes), [])
        row["active_halt"] = "http_407"
        self.store.reconcile(self.nodes)
        item = self.store.inbox(self.nodes)[0]
        self.assertEqual(item["title"], "代理认证失败")
        self.assertFalse(item["can_verify"])

    def test_notification_acceptance_no_repeat_and_explicit_reminder_new_id(self):
        self.store.reconcile(self.nodes)
        item = self.store.notifications()[0]
        first = notification_payload(item, {})["payload"]["notification"]["appMessageId"]
        self.store.notified(item, "temporary")
        self.store.db.execute("UPDATE outbox SET due=0")
        retry = self.store.notifications()[0]
        self.assertEqual(notification_payload(retry, {})["payload"]["notification"]["appMessageId"], first)
        self.store.notified(retry)
        self.store.reconcile(self.nodes)
        self.assertEqual(self.store.notifications(), [])
        self.store.snooze(item["incident"])
        self.store.db.execute("UPDATE incidents SET snoozed=0")
        self.store.db.execute("UPDATE outbox SET due=0")
        later = self.store.notifications()[0]
        self.assertNotEqual(notification_payload(later, {})["payload"]["notification"]["appMessageId"], first)

    def test_restart_preserves_inbox_and_marks_dispatched_probe_uncertain(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inbox.db"
            original = Store(path)
            original.reconcile(self.nodes)
            ident = original.inbox(self.nodes)[0]["id"]
            original.session("one", ident, "dispatching")
            original.db.close()
            reopened = Store(path)
            self.assertEqual(reopened.inbox(self.nodes)[0]["id"], ident)
            self.assertEqual(reopened.session("one")["phase"], "uncertain")
            reopened.db.close()

    def test_new_block_after_clear_creates_new_incident(self):
        self.store.reconcile(self.nodes)
        first = self.store.inbox(self.nodes)[0]["id"]
        row = self.nodes[0]["status"]["stock_runtimes"][0]
        row["active_halt"] = None
        self.store.reconcile(self.nodes)
        row["active_halt"] = "challenge"
        self.store.reconcile(self.nodes)
        self.assertNotEqual(self.store.inbox(self.nodes)[0]["id"], first)


class Tunnel:
    port = 1
    closed = False

    def close(self):
        self.closed = True


class FakeCollector:
    def __init__(self):
        self.calls = []
        self.hold = None
        self.result = "real_data"
        self.failure = None
        self.tunnel_instance = Tunnel()
        self.target = {"instance_id": "instance-a", "job": 1, "revision": 3, "stock": "002648",
                       "task": 12, "request_id": 20, "url": "https://guba.eastmoney.com/list,002648,f_47.html",
                       "route": {"signature": "route-a", "host": "127.0.0.1", "port": 7891}}

    async def call(self, node, action, **payload):
        self.calls.append((action, copy.deepcopy(payload)))
        if action == self.failure:
            raise RuntimeError("注入的连接失败")
        if action == "audit":
            if self.hold:
                await self.hold.wait()
            return {"instance_id": self.target["instance_id"], "revision": self.target["revision"],
                    "targets": [copy.deepcopy(self.target)]}
        if action == "exit":
            return {"route": self.target["route"], "exit_ip": "203.0.113.9"}
        if action == "probe":
            self.assert_closed = self.tunnel_instance.closed
            return {"before_request_id": 20}
        if action == "result":
            return {"done": True, "request": {"id": 21, "outcome": self.result}}
        return {}

    async def tunnel(self, node, route):
        self.tunnel_instance.closed = False
        return self.tunnel_instance


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.token = "local-test-token-not-a-secret"
        token_path = Path(self.tmp.name) / "console.token"
        token_path.write_text(self.token)
        config = {"collector_token_file": str(token_path), "collector_url": "http://127.0.0.1:1",
                  "public_url": "https://hub.example/waygate", "data_dir": self.tmp.name,
                  "source_domains": ["eastmoney.com"], "ssh": {"example.test": {"user": "root"}}}
        self.fake = FakeCollector()
        self.app = create_app(config, Store(":memory:"), self.fake, background=False)
        self.hub = self.app[HUB_KEY]
        self.hub.nodes = fleet()

        async def read_fleet():
            self.hub.store.reconcile(self.hub.nodes)
        self.hub.fleet = read_fleet
        await read_fleet()
        self.incident = self.hub.store.inbox(self.hub.nodes)[0]["id"]
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.headers = {"Authorization": "Bearer " + self.token}

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def post(self, path, body=None):
        response = await self.client.post(path, json=body or {}, headers=self.headers)
        return response.status, await response.json()

    async def start(self):
        status, created = await self.post("/v1/incidents/" + self.incident + "/sessions")
        self.assertEqual(status, 202, created)
        await asyncio.gather(*list(self.hub.jobs))
        return created["id"]

    async def ready(self, sid):
        code, value = await self.post("/v1/sessions/" + sid + "/ready", {"browser_ip": "203.0.113.9"})
        self.assertEqual(code, 200, value)

    async def test_fleet_reads_entire_response_before_parsing(self):
        expected = fleet()
        expected[0]["status"]["padding"] = "分批到达的状态" * 30000
        body = json.dumps({"nodes": expected}, ensure_ascii=False).encode()
        has_length = True

        async def serve(request):
            self.assertEqual(request.headers.get("Authorization"), self.headers["Authorization"])
            headers = {"Content-Length": str(len(body))} if has_length else {}
            response = web.StreamResponse(headers=headers)
            await response.prepare(request)
            await response.write(body[:1024])
            await asyncio.sleep(.03)
            await response.write(body[1024:])
            await response.write_eof()
            return response

        source = web.Application()
        source.router.add_get('/api/fleet', serve)
        async with TestServer(source) as server:
            self.hub.config['collector_url'] = str(server.make_url('/')).rstrip('/')
            for has_length in (True, False):
                with self.subTest(content_length=has_length):
                    await Waygate.fleet(self.hub)
                    self.assertEqual(self.hub.nodes, expected)
                    self.assertGreater(self.hub.last_poll, 0)
                    self.assertEqual(self.hub.poll_error, '')
        self.assertEqual(self.fake.calls, [])

    async def test_fleet_failures_preserve_snapshot_and_report_reason(self):
        case = {}
        async def serve(request):
            if case.get('truncated'):
                response = web.StreamResponse(headers={'Content-Length': '1000'})
                await response.prepare(request)
                await response.write(b'{"nodes":[')
                request.transport.close()
                return response
            return web.Response(status=case.get('status', 200), body=case['body'])

        source = web.Application()
        source.router.add_get('/api/fleet', serve)
        previous = copy.deepcopy(self.hub.nodes)
        self.hub.last_poll = 123
        cases = [
            {'status': 401, 'body': b'private-response-secret', 'error': 'HTTP 401'},
            {'body': b'private-response-secret', 'error': '无效 JSON'},
            {'body': b'{"nodes":{}}', 'error': '缺少 nodes'},
            {'truncated': True, 'error': '响应传输中断'},
            {'body': b'x' * 129, 'limit': 128, 'error': '超过 16 MiB'},
        ]
        async with TestServer(source) as server:
            self.hub.config['collector_url'] = str(server.make_url('/')).rstrip('/')
            for case in cases:
                with self.subTest(error=case['error']), \
                        patch('apps.waygate.hub.service.MAX_FLEET_BYTES', case.get('limit', 16 * 1024 * 1024)):
                    with self.assertRaisesRegex(ValueError, case['error']) as failure:
                        await Waygate.fleet(self.hub)
                    self.assertNotIn('private-response-secret', str(failure.exception))
                    self.assertEqual(self.hub.nodes, previous)
                    self.assertEqual(self.hub.last_poll, 123)
                    self.assertEqual(len(self.hub.store.inbox(self.hub.nodes)), 1)

    async def test_auth_poll_does_not_contact_source_or_ssh(self):
        self.assertEqual((await self.client.get("/v1/inbox")).status, 401)
        response = await self.client.get("/v1/inbox", headers=self.headers)
        body = await response.json()
        self.assertEqual(len(body["items"]), 1)
        self.assertNotIn("route", body["items"][0])
        self.assertEqual(self.fake.calls, [])

    async def test_duplicate_start_and_finish_only_one_probe_and_scoped_resume(self):
        sid = await self.start()
        code, _ = await self.post("/v1/incidents/" + self.incident + "/sessions")
        self.assertEqual(code, 409)
        await self.ready(sid)
        path = "/v1/sessions/" + sid + "/finish"
        answers = await asyncio.gather(self.post(path, {"human_confirmed": True}), self.post(path, {"human_confirmed": True}))
        self.assertEqual(sorted(a[0] for a in answers), [202, 409])
        await asyncio.gather(*list(self.hub.jobs))
        self.assertEqual([a for a, _ in self.fake.calls].count("probe"), 1)
        resumed = [payload for action, payload in self.fake.calls if action == "resume"]
        self.assertEqual([p["target"]["stock"] for p in resumed], ["002648"])
        self.assertTrue(self.fake.assert_closed)
        self.assertEqual(self.hub.store.session(sid)["phase"], "resumed")

    async def test_wrong_exit_cancels_without_source_navigation_or_probe(self):
        sid = await self.start()
        code, _ = await self.post("/v1/sessions/" + sid + "/ready", {"browser_ip": "203.0.113.10"})
        self.assertEqual(code, 409)
        self.assertNotIn(sid, self.hub.sessions)
        self.assertTrue(self.fake.tunnel_instance.closed)
        self.assertNotIn("probe", [a for a, _ in self.fake.calls])

    async def test_changed_revision_prevents_prepare(self):
        self.fake.target["revision"] = 4
        sid = await self.start()
        self.assertEqual(self.hub.store.session(sid)["phase"], "failed")
        self.assertEqual([a for a, _ in self.fake.calls], ["audit"])

    async def test_changed_route_prevents_browser_source_url(self):
        sid = await self.start()
        self.fake.target["route"]["signature"] = "new"
        code, _ = await self.post("/v1/sessions/" + sid + "/ready", {"browser_ip": "203.0.113.9"})
        self.assertEqual(code, 409)
        self.assertFalse(self.hub.sessions[sid]["ready"])

    async def test_cancel_during_preparation_keeps_lock_until_old_work_finishes(self):
        self.fake.hold = asyncio.Event()
        _, created = await self.post("/v1/incidents/" + self.incident + "/sessions")
        sid = created["id"]
        await self.post("/v1/sessions/" + sid + "/cancel")
        await self.post("/v1/sessions/" + sid + "/cancel")
        code, _ = await self.post("/v1/incidents/" + self.incident + "/sessions")
        self.assertEqual(code, 409)
        self.fake.hold.set()
        await asyncio.gather(*list(self.hub.jobs))
        self.assertNotIn(sid, self.hub.sessions)
        self.assertNotIn("prepare", [a for a, _ in self.fake.calls])

    async def test_failed_probe_does_not_resume_and_timeout_is_not_retried(self):
        for outcome in ("challenge", "transport_error"):
            self.fake.result = outcome
            sid = await self.start()
            await self.ready(sid)
            await self.post("/v1/sessions/" + sid + "/finish", {"human_confirmed": True})
            await asyncio.gather(*list(self.hub.jobs))
            self.assertEqual(self.hub.store.session(sid)["phase"], "failed")
        self.fake.failure = "probe"
        sid = await self.start()
        await self.ready(sid)
        await self.post("/v1/sessions/" + sid + "/finish", {"human_confirmed": True})
        await asyncio.gather(*list(self.hub.jobs))
        self.assertEqual(self.hub.store.session(sid)["phase"], "uncertain")
        self.assertEqual([a for a, _ in self.fake.calls].count("probe"), 3)
        self.assertNotIn("resume", [a for a, _ in self.fake.calls])

    async def test_confirmation_and_neutral_check_required(self):
        sid = await self.start()
        code, _ = await self.post("/v1/sessions/" + sid + "/finish", {"human_confirmed": True})
        self.assertEqual(code, 409)
        await self.ready(sid)
        code, _ = await self.post("/v1/sessions/" + sid + "/finish")
        self.assertEqual(code, 409)
        self.assertNotIn("probe", [a for a, _ in self.fake.calls])

    async def test_real_binary_ws_connect_relay_and_cleanup(self):
        accepted = []

        async def proxy(reader, writer):
            header = await reader.readuntil(b"\r\n\r\n")
            accepted.append(header)
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            while data := await reader.read(1024):
                writer.write(data)
                await writer.drain()
            writer.close()
            await writer.wait_closed()

        tcp = await asyncio.start_server(proxy, "127.0.0.1", 0)
        self.fake.tunnel_instance.port = tcp.sockets[0].getsockname()[1]
        try:
            sid = await self.start()
            ws = await self.client.ws_connect("/v1/sessions/" + sid + "/relay", headers=self.headers)
            await ws.send_bytes(b"CONNECT api.ipify.org:443 HTTP/1.1\r\n")
            await ws.send_bytes(b"Proxy-Authorization: must-not-forward\r\n\r\n")
            reply = await ws.receive(timeout=2)
            self.assertIn(b"200 Connection established", reply.data)
            payload = b"\x16\x03\x03opaque-encrypted-browser-stream"
            await ws.send_bytes(payload)
            self.assertEqual((await ws.receive(timeout=2)).data, payload)
            self.assertNotIn(b"Proxy-Authorization", accepted[0])
            await self.post("/v1/sessions/" + sid + "/cancel")
            self.assertIn((await ws.receive(timeout=2)).type, {WSMsgType.CLOSE, WSMsgType.CLOSED})
        finally:
            tcp.close()
            await tcp.wait_closed()

    async def test_console_ticket_single_use_and_cross_origin_rejected(self):
        _, value = await self.post("/v1/console-ticket")
        query = value["url"].split("?", 1)[1]
        first = await self.client.get("/console/?" + query, allow_redirects=False)
        self.assertEqual(first.status, 302)
        cookie = first.cookies["waygate_console"]
        self.assertTrue(cookie["secure"])
        self.assertTrue(cookie["httponly"])
        self.assertEqual((await self.client.get("/console/?" + query, allow_redirects=False)).status, 401)
        bad = await self.client.post("/console/api/control", headers={
            "Cookie": "waygate_console=" + cookie.value, "Origin": "https://evil.example"}, json={})
        self.assertEqual(bad.status, 403)


class RelayPolicyTests(unittest.TestCase):
    def test_source_traffic_only_after_exit_check(self):
        header = b"CONNECT guba.eastmoney.com:443 HTTP/1.1"
        with self.assertRaises(ValueError):
            allowed_connect(header, ["eastmoney.com"], False)
        self.assertEqual(allowed_connect(header, ["eastmoney.com"], True), "guba.eastmoney.com")

    def test_no_arbitrary_proxy_or_private_ip_targets(self):
        for request in (
            "CONNECT 127.0.0.1:443 HTTP/1.1", "CONNECT 169.254.169.254:443 HTTP/1.1",
            "CONNECT eastmoney.com.evil.test:443 HTTP/1.1", "CONNECT guba.eastmoney.com:22 HTTP/1.1",
            "GET https://guba.eastmoney.com/ HTTP/1.1", "CONNECT localhost:443 HTTP/1.1",
            "CONNECT user@guba.eastmoney.com:443 HTTP/1.1",
        ):
            with self.assertRaises(ValueError, msg=request):
                allowed_connect(request.encode(), ["eastmoney.com"], True)


if __name__ == "__main__":
    unittest.main()
