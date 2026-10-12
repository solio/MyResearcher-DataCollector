"""Browser lifetime across real backfill runs, without launching a browser."""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading

import pytest

from myresearcher_collector.sources.eastmoney_guba.browser_runtime import ManagedChromiumTransport
from tests.unit.test_backfill import MappingTransport, synthetic_page

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/ops/backfill_session.py"
spec = importlib.util.spec_from_file_location("backfill_session", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def request(tmp_path, stock):
    return {"argv": ["backfill", "--source", "eastmoney_guba", "--stock", stock,
                     "--from", "2026-08-09", "--to", "2026-08-11", "--start-page", "1",
                     "--data-dir", str(tmp_path / "data"), "--list-only", "--confirm-live",
                     "--acquisition-mode", "managed-chromium"],
            "report": str(tmp_path / f"{stock}.json"), "error": str(tmp_path / f"{stock}.err")}


def test_two_real_stock_runs_borrow_one_browser_and_persist_individual_reports(tmp_path, monkeypatch):
    import myresearcher_collector.integration as integration
    import importlib
    cli = importlib.import_module("myresearcher_collector.cli.main")
    real = integration.execute_and_persist_simple_backfill_collection
    monkeypatch.setattr(cli, "execute_and_persist_simple_backfill_collection",
                        lambda **kwargs: real(**kwargs, sleep_fn=lambda _: None))
    delays = []
    monkeypatch.setattr(module.time, "sleep", delays.append)
    class Wire(MappingTransport):
        closed = 0
        def close(self):
            self.closed += 1
    wire = Wire({f"https://guba.eastmoney.com/list,{stock},f{suffix}.html": body
                 for stock, post in [("600001", "1001"), ("600002", "2001")]
                 for suffix, body in [("", synthetic_page((post, "2026-08-10 10:00:00"))), ("_2", synthetic_page())]})
    factories = []
    session = module.BrowserSession(factory=lambda args: (factories.append(args.stock) or wire))
    try:
        for stock in ["600001", "600002"]:
            assert session.run(request(tmp_path, stock)) == {"exit_code": 0}
            report = json.loads((tmp_path / f"{stock}.json").read_text())
            assert report["status"] == "SUCCESS"
            assert report["browser_session"] == {"shared": True, "reused": stock == "600002"}
            assert wire.closed == 0
        assert factories == ["600001"]
        assert len(wire.calls) == 4
        assert delays and all(0 < delay <= 3 for delay in delays)
        with closing(sqlite3.connect(tmp_path / "data/collector.db")) as db:
            assert db.execute("SELECT stock_code FROM posts ORDER BY stock_code").fetchall() == [("600001",), ("600002",)]
    finally:
        session.close()
    assert wire.closed == 1


def test_a_failed_stock_stops_session_without_trying_the_next_stock(tmp_path):
    calls = []
    class Browser:
        closed = False
        def close(self):
            self.closed = True
    browser = Browser()
    def execute(args, transport):
        calls.append(args.stock)
        transport.close()
        return {"status": "COLLECTION_FAILED", "stop_reason": "access_block"}
    session = module.BrowserSession(factory=lambda args: browser, execute=execute)
    try:
        assert session.run(request(tmp_path, "600001")) == {"exit_code": 1}
        with pytest.raises(RuntimeError, match="上一只股票未成功"):
            session.run(request(tmp_path, "600002"))
        assert calls == ["600001"]
        assert browser.closed is False
    finally:
        session.close()
    assert browser.closed is True


def test_reset_observations_keeps_context_and_page(tmp_path, monkeypatch):
    runtime = ManagedChromiumTransport(profile_dir=tmp_path)
    runtime.context, runtime.page, runtime.delegate = object(), object(), object()
    context, page, old_delegate = runtime.context, runtime.page, runtime.delegate
    new_delegate = object()
    monkeypatch.setattr(runtime, "_new_delegate", lambda: new_delegate)
    runtime.reset_run_observations()
    assert runtime.context is context and runtime.page is page
    assert runtime.delegate is new_delegate and runtime.delegate is not old_delegate


def test_session_client_argument_boundary_and_acknowledgement(tmp_path):
    # A real client process talks only to this owned local fake server.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="backfill-client-", dir="/tmp") as owned:
        path = str(Path(owned) / "browser.sock")
        received = []
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(path)
            server.listen(1)
            def respond():
                connection, _ = server.accept()
                with connection:
                    received.append(module.read_message(connection))
                    connection.sendall(b'{"exit_code":0}\n')
            worker = threading.Thread(target=respond, daemon=True)
            worker.start()
            data = request(tmp_path, "600001")
            completed = subprocess.run([sys.executable, str(SCRIPT), "call", "--socket", path,
                                        "--report", data["report"], "--error", data["error"], "--", *data["argv"]],
                                       capture_output=True, text=True, timeout=10)
            worker.join(timeout=2)
        assert completed.returncode == 0, completed.stderr
        assert received == [data]
