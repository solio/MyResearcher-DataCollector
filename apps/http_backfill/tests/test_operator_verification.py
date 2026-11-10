"""Offline controls/lifecycle checks; no CAPTCHA or real source requests."""
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/ops/verify_http_backfill.py"
spec = importlib.util.spec_from_file_location("operator_verification", SCRIPT)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def worker():
    scope = {"__name__": "test_worker"}
    exec(helper.REMOTE_WORKER, scope)
    return scope


def status():
    return {"instance_id": "node-a", "job": {"id": 3, "revision": 8}, "state": "blocked",
            "stock_runtimes": [{"job": 3, "stock": "002648", "state": "blocked",
                "active_halt": "challenge", "halt_task_id": 7,
                "block_evidence": {"id": 10, "url": "https://guba.eastmoney.com/list,002648,f_47.html"}},
                {"job": 3, "stock": "600031", "state": "running", "active_halt": None,
                 "block_evidence": {"id": 9, "url": "https://guba.eastmoney.com/list,600031,f.html"}}],
            "proxy": {"settings": {"mode": "http", "endpoint": "http://127.0.0.1:7891"}, "has_auth": False},
            "coverage": [], "node_source_auth_barrier": {
                "request_id": 10, "job": 3, "stock": "002648", "task_id": 7}}


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.w = worker()
        self.s = status()
        self.route = {"host": "127.0.0.1", "port": 7891, "endpoint": "http://127.0.0.1:7891", "signature": "fixed"}
        self.w["route"] = lambda *args: dict(self.route)
        self.view = self.w["snapshot"](self.s)
        self.target = self.view["targets"][0]

    def test_full_ip_and_short_alias_resolve_same_private_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            directory.joinpath("nodes.json").write_text(json.dumps({
                "36.138.125.17": {"user": "root", "port": 22},
                "8.148.251.114": {"user": "root", "port": 22},
                "8.148.252.117": {"user": "root", "port": 22}}))
            with patch.object(helper, "PRIVATE", directory):
                self.assertEqual(helper.node_config("36"), helper.node_config("36.138.125.17"))
                self.assertEqual(helper.node_config("251"), helper.node_config("8.148.251.114"))
                self.assertEqual(helper.node_config("252"), helper.node_config("8.148.252.117"))
                choices = helper.configured_node_choices()
                labels = [label for _, label in choices]
                self.assertIn("251 (8.148.251.114)", labels)
                self.assertIn("252 (8.148.252.117)", labels)
                self.assertIn("8.148.251.114", labels)
                self.assertIn("8.148.252.117", labels)

    def test_read_only_audit_reports_halt_when_effective_route_is_missing(self):
        w = worker()
        def missing_route(*_args):
            raise RuntimeError("当前任务没有 HTTP 代理")
        w["route"] = missing_route
        with self.assertRaisesRegex(RuntimeError, "没有 HTTP 代理"):
            w["snapshot"](status())
        view = w["snapshot"](status(), allow_route_errors=True)
        self.assertEqual(view["targets"][0]["stock"], "002648")
        self.assertIsNone(view["targets"][0]["route"])
        self.assertIn("没有 HTTP 代理", view["targets"][0]["route_error"])

    def test_native_ssh_command_passes_json_stdin_without_interpolation(self):
        # Execute only a harmless stand-in, never the real worker/SSH/source.
        payload = {"action": "audit", "text": "中文 ' \" `echo wrong` $(echo wrong)\nnext", "flag": True, "empty": None}
        source = 'print(json.dumps({"ok": True, "value": PAYLOAD}, ensure_ascii=False))'
        with patch.object(helper, "REMOTE_WORKER", source):
            argv = shlex.split(helper.ssh_worker_command())
        result = subprocess.run([sys.executable, *argv[1:]], input=json.dumps(payload),
                                capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), {"ok": True, "value": payload})

    def test_native_ssh_command_uses_exact_desktop_worker(self):
        import base64
        import re
        encoded = re.search(r'base64.b64decode\("([A-Za-z0-9+/=]+)"\)', helper.ssh_worker_command())[1]
        source = base64.b64decode(encoded).decode("utf-8")
        self.assertEqual(source, "import json, sys\nPAYLOAD = json.load(sys.stdin)\n" + helper.REMOTE_WORKER)

    def test_historical_evidence_without_halt_does_not_open_source(self):
        self.assertEqual(len(self.view["targets"]), 1)
        self.s["stock_runtimes"][0]["active_halt"] = None
        self.assertEqual(self.w["snapshot"](self.s)["targets"], [])
        fake = Mock(return_value=self.w["snapshot"](self.s))
        with patch.object(helper, "remote", fake), patch.object(helper, "browser_session") as browser:
            self.assertEqual(helper.run({"host": "node"}, SimpleNamespace(check=False, check_exit=False, stock=None)), 0)
            browser.assert_not_called()
            self.assertEqual(fake.call_count, 1)

    def test_target_changed_aborts_before_any_control(self):
        altered = copy.deepcopy(self.view)
        altered["targets"][0]["request_id"] = 11
        with self.assertRaisesRegex(RuntimeError, "原阻断"):
            self.w["unchanged"](altered, self.target)
        altered = copy.deepcopy(self.view)
        altered["revision"] += 1
        with self.assertRaisesRegex(RuntimeError, "配置已变更"):
            self.w["unchanged"](altered, self.target)

    def test_exit_selection_change_aborts(self):
        altered = copy.deepcopy(self.view)
        altered["targets"][0]["route"]["signature"] = "new-proxy"
        with self.assertRaisesRegex(RuntimeError, "代理出口"):
            self.w["unchanged"](altered, self.target)

    def test_source_url_rejects_foreign_hosts_queries_and_userinfo(self):
        source_url = self.w["source_url"]
        self.assertTrue(source_url("https://guba.eastmoney.com/news,002648,12345.html"))
        for url in ("https://example.org/list,002648,f.html", "http://guba.eastmoney.com/list,002648,f.html",
                    "https://user@guba.eastmoney.com/list,002648,f.html", "https://guba.eastmoney.com/list,002648,f.html?x=1"):
            self.assertFalse(source_url(url))

    def test_node_barrier_selects_its_original_halt(self):
        other = dict(self.target, stock="600031", request_id=13)
        view = dict(self.view, targets=[other, self.target])
        self.assertEqual(helper.choose_target(view), self.target)
        view["barrier_request_id"] = None
        view["barrier_target"] = None
        self.assertEqual(helper.choose_target(view), other)

    def test_failed_probe_updates_evidence_but_keeps_barrier_task_owner(self):
        self.s["stock_runtimes"][0]["block_evidence"]["id"] = 11
        self.s["stock_runtimes"][1].update(active_halt="challenge", state="blocked", halt_task_id=9)
        self.s["stock_runtimes"][1]["block_evidence"]["id"] = 13
        view = self.w["snapshot"](self.s)
        target = helper.choose_target(view)
        self.assertEqual((target["stock"], target["task"], target["request_id"]), ("002648", 7, 11))
        self.assertEqual(helper.choose_target(view, "600031")["request_id"], 13)

    def test_no_barrier_prefers_current_stock_over_retained_detached_halt(self):
        archived = dict(self.target, job=1, stock="603039", request_id=100, detached=True)
        view = dict(self.view, targets=[archived, self.target], barrier_request_id=None, barrier_target=None)
        self.assertEqual(helper.choose_target(view), self.target)
        view["targets"] = [archived]
        self.assertEqual(helper.choose_target(view), archived)

    def test_prepare_pauses_running_sibling_only_and_preserves_halt(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            self.w["locate"] = lambda: (Path(tmp), "http://local")
            def api(base, data, path="status", body=None):
                if path == "status": return self.s
                calls.append((path, body))
                return {}
            self.w["api"] = api
            result = self.w["execute"]({"action": "prepare", "target": self.target})
            self.assertEqual(result["paused_stocks"], ["600031"])
            self.assertEqual(calls, [("stocks/600031/control", {"action": "pause", "job_id": 3})])
            self.assertEqual(self.s["stock_runtimes"][0]["active_halt"], "challenge")

    def test_probe_result_matches_task_and_one_shot(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            db = sqlite3.connect(data / "collector.db")
            with db:
                db.execute("CREATE TABLE requests(id,job,stock,task,probe,outcome,http_status,error,finished)")
                db.executemany("INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?)", [
                    (11,3,"002648",99,1,"real_data",200,None,1),
                    (12,3,"002648",7,0,"real_data",200,None,1),
                    (13,3,"002648",7,1,"access_block",200,"身份核实",1)])
            db.close()
            self.w["locate"] = lambda: (data, "http://local")
            self.w["api"] = lambda *args: self.s
            result = self.w["execute"]({"action": "result", "target": self.target, "before_request_id": 10})
            self.assertTrue(result["done"])
            self.assertEqual(result["request"]["id"], 13)

    def test_prepare_can_pause_an_inflight_sibling_before_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.s["request_inflight"] = True
            calls = []
            self.w["locate"] = lambda: (Path(tmp), "http://local")
            def api(base, data, path="status", body=None):
                if path == "status": return self.s
                calls.append(body)
                self.s["request_inflight"] = False
                self.s["stock_runtimes"][1]["state"] = "paused"
                return self.s
            self.w["api"] = api
            self.assertEqual(self.w["execute"]({"action": "prepare", "target": self.target})["paused_stocks"], ["600031"])
            self.assertEqual(calls, [{"action": "pause", "job_id": 3}])

    def test_cancel_and_close_never_schedule_probe(self):
        fake = Mock(side_effect=[self.view, {"paused_stocks": []}])
        args = SimpleNamespace(check=False, check_exit=False, stock=None)
        with patch.object(helper, "remote", fake), patch.object(helper, "browser_session", return_value=False):
            self.assertEqual(helper.run({"host": "node"}, args), 0)
        self.assertEqual([c.args[1] for c in fake.call_args_list], ["audit", "prepare"])

    def test_one_failed_probe_never_resumes_or_retries(self):
        fake = Mock(side_effect=[self.view, {}, {"before_request_id": 10},
            {"done": True, "request": {"id": 11, "outcome": "access_block", "error": "身份核实"}}])
        args = SimpleNamespace(check=False, check_exit=False, stock=None, probe_wait=5, resume=True)
        with patch.object(helper, "remote", fake), patch.object(helper, "browser_session", return_value=True):
            self.assertEqual(helper.run({"host": "node"}, args), 1)
        self.assertEqual([c.args[1] for c in fake.call_args_list], ["audit", "prepare", "probe", "result"])

    def test_resume_refuses_changed_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.w["locate"] = lambda: (Path(tmp), "http://local")
            self.s["stock_runtimes"][0].update(state="paused", active_halt=None)
            self.s["node_source_auth_barrier"] = None
            self.s["job"]["revision"] += 1
            api = Mock(return_value=self.s)
            self.w["api"] = api
            with self.assertRaisesRegex(RuntimeError, "未自动继续"):
                self.w["execute"]({"action": "resume", "target": self.target})
            self.assertEqual(api.call_count, 1)

    def test_resume_starts_only_the_verified_stock(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.w["locate"] = lambda: (Path(tmp), "http://local")
            self.s["stock_runtimes"][0].update(state="paused", active_halt=None)
            self.s["stock_runtimes"][1]["state"] = "paused"
            self.s["node_source_auth_barrier"] = None
            api = Mock(return_value=self.s)
            self.w["api"] = api
            self.assertTrue(self.w["execute"]({"action": "resume", "target": self.target})["started"])
            self.assertEqual(api.call_args.args[2:], ("stocks/002648/control", {"action": "start", "job_id": 3}))
            self.assertEqual(api.call_count, 2)

    def test_successful_probe_stays_paused_without_resume_option(self):
        fake = Mock(side_effect=[self.view, {}, {"before_request_id": 10},
            {"done": True, "request": {"id": 11, "outcome": "real_data", "http_status": 200}}])
        args = SimpleNamespace(check=False, check_exit=False, stock=None, probe_wait=5, resume=False)
        with patch.object(helper, "remote", fake), patch.object(helper, "browser_session", return_value=True):
            self.assertEqual(helper.run({"host": "node"}, args), 0)
        self.assertEqual([c.args[1] for c in fake.call_args_list], ["audit", "prepare", "probe", "result"])

    def test_cleanup_closes_stdin_guardian_and_child(self):
        tunnel = helper.Tunnel({}, {})
        tunnel.process = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
                                          stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
        tunnel.errors = tempfile.TemporaryFile()
        tunnel.close()
        self.assertEqual(tunnel.process.returncode, 0)
        self.assertTrue(tunnel.errors.closed)

    def test_detached_stock_route_does_not_use_new_job_override(self):
        scope = worker()
        s = status()
        s["coverage"] = [{"stock": "002648", "task_proxy": {"settings": {"mode": "http", "endpoint": "http://127.0.0.1:7991"}}}]
        s["detached_stock_runtimes"] = [{"job": 1, "stock": "002648", "task_proxy": {"settings": {"mode": "http", "endpoint": "http://127.0.0.1:7992"}}}]
        with patch.object(Path, "exists", return_value=False):
            self.assertEqual(scope["route"](s,"002648",1)["port"],7992)
            self.assertEqual(scope["route"](s,"002648",3)["port"],7991)
            s["coverage"] = []
            s["detached_stock_runtimes"][0]["job"] = 3
            self.assertEqual(scope["route"](s,"002648",3)["port"],7992)


    def test_direct_effective_route_returns_verifiable_descriptor(self):
        w = worker()
        s = status()
        s["proxy"] = {"settings": {"mode": "direct", "endpoint": ""}, "has_auth": False}
        target = w["snapshot"](s)["targets"][0]
        self.assertIsNone(target.get("route_error"))
        self.assertEqual(target["route"]["mode"], "direct")
        self.assertEqual(target["route"]["outbound"], "DIRECT")
        self.assertTrue(target["route"]["signature"])
        s2 = status()
        s2["coverage"] = [{"stock": "002648",
                           "task_proxy": {"settings": {"mode": "direct", "endpoint": ""}}}]
        self.assertEqual(w["snapshot"](s2)["targets"][0]["route"]["mode"], "direct")

    def test_exit_action_direct_egress_uses_no_proxy(self):
        w = worker()
        s = status()
        s["proxy"] = {"settings": {"mode": "direct", "endpoint": ""}, "has_auth": False}
        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def read(self, _n): return b"203.0.113.7"
        opener = Mock(); opener.open.return_value = _Resp()
        w["locate"] = lambda: (Path("/tmp"), "http://local")
        w["api"] = lambda *args: s
        with patch("urllib.request.build_opener", return_value=opener) as build:
            result = w["execute"]({"action": "exit", "stock": "002648", "job": 3})
        self.assertEqual(build.call_args[0][0].proxies, {})
        self.assertEqual(result["exit_ip"], "203.0.113.7")
        self.assertEqual(result["route"]["mode"], "direct")

    def test_probe_direct_recheck_compares_worker_egress_without_proxy(self):
        w = worker()
        s = status()
        s["proxy"] = {"settings": {"mode": "direct", "endpoint": ""}, "has_auth": False}
        target = w["snapshot"](s)["targets"][0]
        target["exit_ip"] = "203.0.113.7"
        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def read(self, _n): return b"203.0.113.7"
        opener = Mock(); opener.open.return_value = _Resp()
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            db = sqlite3.connect(data / "collector.db")
            with db:
                db.execute("CREATE TABLE requests(id,job,stock,task,probe,outcome,http_status,error,finished)")
            db.close()
            w["locate"] = lambda: (data, "http://local")
            calls = []
            def api(base, data_, path="status", body=None):
                calls.append((path, body)); return s
            w["api"] = api
            with patch("urllib.request.build_opener", return_value=opener) as build:
                result = w["execute"]({"action": "probe", "target": target})
        self.assertEqual(build.call_args[0][0].proxies, {})
        self.assertEqual(result["before_request_id"], 0)
        self.assertEqual(calls[-1], ("stocks/002648/control",
                                     {"action": "retry", "job_id": 3, "retry_request_id": 10}))

    def test_chromium_socks5_sends_hostname_to_proxy_not_local_dns(self):
        """实测 Chromium socks5:// 是否把域名交给代理远端解析（ATYP=domain）。

        不访问任何真实来源：目标是 .invalid 保留 TLD，SOCKS5 mock 用固定 200 应答。
        mock 带缓冲精确解析（问候与 CONNECT 可能合段到达）、接受多条连接
        （Chromium 会并发开多条）——固定 recv 单连接的 mock 会假报代理连接失败。
        """
        if importlib.util.find_spec("playwright") is None:
            self.skipTest("本机未安装 playwright")
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        port = srv.getsockname()[1]
        handshakes = []
        stop = threading.Event()

        def recv_exact(conn, count, buffer):
            while len(buffer) < count:
                chunk = conn.recv(count - len(buffer))
                if not chunk:
                    raise OSError("对端提前关闭")
                buffer += chunk
            return buffer[:count], buffer[count:]

        def handle(conn):
            with conn:
                try:
                    conn.settimeout(5)
                    buffer = b""
                    greeting, buffer = recv_exact(conn, 2, buffer)
                    methods, buffer = recv_exact(conn, greeting[1], buffer)
                    conn.sendall(b"\x05\x00")
                    head, buffer = recv_exact(conn, 4, buffer)
                    entry = {"atyp": head[3]}
                    if entry["atyp"] == 3:
                        raw, buffer = recv_exact(conn, 1, buffer)
                        host_raw, buffer = recv_exact(conn, raw[0], buffer)
                        entry["host"] = host_raw.decode()
                    elif entry["atyp"] == 1:
                        host_raw, buffer = recv_exact(conn, 4, buffer)
                        entry["host"] = socket.inet_ntoa(host_raw)
                    elif entry["atyp"] == 4:
                        host_raw, buffer = recv_exact(conn, 16, buffer)
                        entry["host"] = socket.inet_ntop(socket.AF_INET6, host_raw)
                    else:
                        entry["host"] = None
                        with lock:
                            handshakes.append(entry)
                        return
                    port_raw, buffer = recv_exact(conn, 2, buffer)
                    entry["port"] = (port_raw[0] << 8) | port_raw[1]
                    conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
                    while b"\r\n\r\n" not in buffer:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        buffer += chunk
                    entry["request_head"] = buffer[:32]
                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
                    with lock:
                        handshakes.append(entry)
                except OSError:
                    return

        def serve():
            srv.settimeout(0.3)
            while not stop.is_set():
                try:
                    conn, _ = srv.accept()
                except (socket.timeout, OSError):
                    if stop.is_set():
                        break
                    continue
                threading.Thread(target=handle, args=(conn,), daemon=True).start()

        lock = threading.Lock()
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        from playwright.sync_api import sync_playwright, Error
        try:
            with tempfile.TemporaryDirectory() as td, sync_playwright() as p:
                browser = p.chromium.launch(headless=True, channel="chrome",
                                            proxy={"server": f"socks5://127.0.0.1:{port}"},
                                            args=["--disable-quic"])
                try:
                    page = browser.new_page()
                    page.goto("http://socks-dns-probe.invalid/", wait_until="commit", timeout=20000)
                finally:
                    browser.close()
        except Error as exc:
            srv.close()
            self.fail(f"经 SOCKS5 mock 加载页面失败（handshakes={handshakes}）：{str(exc)[:200]}")
        stop.set()
        srv.close()
        main = [h for h in handshakes if h.get("host") == "socks-dns-probe.invalid"]
        self.assertTrue(main, f"未捕获目标域名的握手：{handshakes}")
        self.assertEqual(main[0]["atyp"], 3,
                         f"Chromium 未把域名交给 SOCKS5 远端解析：{main}")
        self.assertEqual(main[0]["port"], 80)


    def test_direct_route_signature_matches_worker_descriptor(self):
        w = worker()
        s = status()
        s["proxy"] = {"settings": {"mode": "direct", "endpoint": ""}, "has_auth": False}
        self.assertEqual(helper.direct_route()["signature"], w["route"](s, "002648", 3)["signature"])

    def test_exit_direct_flag_bypasses_task_route_and_returns_bare_ip(self):
        w = worker()
        s = status()
        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def read(self, _n): return b"203.0.113.7"
        opener = Mock(); opener.open.return_value = _Resp()
        w["locate"] = lambda: (Path("/tmp"), "http://local")
        w["api"] = lambda *args: s
        with patch("urllib.request.build_opener", return_value=opener) as build:
            result = w["execute"]({"action": "exit", "stock": "002648", "job": 3, "direct": True})
        self.assertEqual(build.call_args[0][0].proxies, {})
        self.assertEqual(result["route"]["mode"], "direct")
        self.assertEqual(result["exit_ip"], "203.0.113.7")

    def test_unchanged_direct_expected_skips_route_signature_but_pins_request(self):
        altered = copy.deepcopy(self.view)
        altered["targets"][0]["route"]["signature"] = "new-proxy"
        with self.assertRaisesRegex(RuntimeError, "代理出口"):
            self.w["unchanged"](altered, self.target)
        direct_expected = dict(self.target, route=helper.direct_route())
        self.assertEqual(self.w["unchanged"](altered, direct_expected)["stock"], "002648")
        drifted = copy.deepcopy(altered)
        drifted["targets"][0]["request_id"] += 1
        with self.assertRaisesRegex(RuntimeError, "原阻断"):
            self.w["unchanged"](drifted, direct_expected)

    def test_resume_direct_expected_skips_route_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.w["locate"] = lambda: (Path(tmp), "http://local")
            self.s["stock_runtimes"][0].update(state="paused", active_halt=None)
            self.s["node_source_auth_barrier"] = None
            self.w["api"] = Mock(return_value=self.s)
            direct_target = dict(self.target, route=helper.direct_route())
            self.assertTrue(self.w["execute"]({"action": "resume", "target": direct_target})["started"])

    def test_run_direct_override_passes_direct_target_to_prepare(self):
        fake = Mock(side_effect=[self.view, {"paused_stocks": []}])
        args = SimpleNamespace(check=False, check_exit=False, stock=None, direct=False)
        direct_args = SimpleNamespace(check=False, check_exit=False, stock=None, direct=True)
        with patch.object(helper, "remote", fake), \
             patch.object(helper, "browser_session", return_value=False) as browser:
            self.assertEqual(helper.run({"host": "node"}, direct_args), 0)
            self.assertEqual(browser.call_args.kwargs["direct"], True)
            prepared = fake.call_args_list[1].kwargs["target"]
            self.assertEqual(prepared["route"]["mode"], "direct")
            self.assertNotIn("route_error", prepared)
            args.direct = False
            fake2 = Mock(side_effect=[self.view, {"paused_stocks": []}])
            with patch.object(helper, "remote", fake2):
                helper.run({"host": "node"}, args)
            self.assertEqual(fake2.call_args_list[1].kwargs["target"]["route"].get("mode"), None)
            self.assertEqual(fake2.call_args_list[1].kwargs["target"]["route"]["signature"], "fixed")


if __name__ == "__main__":
    unittest.main()
