"""Offline controls/lifecycle checks; no CAPTCHA or real source requests."""
import copy
import importlib.util
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
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


if __name__ == "__main__":
    unittest.main()
