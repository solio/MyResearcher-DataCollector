"""Native batch boundaries, using temporary databases and offline transports."""
from contextlib import closing, nullcontext
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from myresearcher_collector.simple_store import SimplePostStore
from tests.unit.test_backfill import MappingTransport, synthetic_page

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/ops/browser_jobs.py"
spec = importlib.util.spec_from_file_location("browser_jobs", SCRIPT)
jobs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jobs)


def arguments(command, data_dir, *extra):
    return jobs.parser().parse_args([command, "--data-dir", str(data_dir), *extra])


def seed(data_dir, rows):
    store = SimplePostStore(data_dir / "collector.db")
    try:
        for post, stock, title, content in rows:
            store.upsert_post(source="eastmoney_guba", source_item_id=post, stock_code=stock,
                              title=title, content=content, author_id="a", author_name="作者",
                              published_at="2026-08-10T02:00:00Z",
                              url=f"https://guba.eastmoney.com/news,{stock},{post}.html",
                              read_count=0, reply_count=0, like_count=0, forward_count=0)
    finally:
        store.close()


def test_common_cli_import_and_plan_without_windows_missing_unix_features(tmp_path):
    # Run in a fresh interpreter so existing imports cannot conceal fcntl use.
    code = """
import builtins, importlib.util, socket, sys
real_import = builtins.__import__
def windows_import(name, *args, **kwargs):
    if name == 'fcntl':
        raise ImportError('not available on Windows')
    return real_import(name, *args, **kwargs)
builtins.__import__ = windows_import
if hasattr(socket, 'AF_UNIX'):
    del socket.AF_UNIX
spec = importlib.util.spec_from_file_location('browser_jobs', sys.argv[1])
jobs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jobs)
raise SystemExit(jobs.main(['backfill', '--stocks', '600001', '--days', '14',
                           '--plan-only', '--data-dir', sys.argv[2]]))
"""
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", code, str(SCRIPT), str(tmp_path / "data")],
                            capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)[0]["acquisition_method"] == "managed-chromium"
    assert not (tmp_path / "data").exists()


def test_planning_uses_selected_database_and_skip_ledger_with_uri_characters(tmp_path):
    data = tmp_path / "中文 space '#%&"
    seed(data, [("1", "600001", "长" * 40, None), ("2", "600001", "长" * 41, None),
                ("3", "600001", "短" * 39, None), ("4", "600001", "长" * 40, "已有正文"),
                ("5", "600002", "长" * 40, None)])
    ledger = data / "collector.detail_enrichment_skips.db"
    with closing(sqlite3.connect(ledger)) as db:
        db.execute("CREATE TABLE detail_enrichment_skips (source TEXT, source_item_id TEXT)")
        db.execute("INSERT INTO detail_enrichment_skips VALUES ('eastmoney_guba', '2')")
        db.commit()
    before = {path: path.read_bytes() for path in (data / "collector.db", ledger)}
    assert jobs.enrich_plan.pending_for(["600001"], data / "collector.db") == [("600001", 1)]
    assert dict(jobs.enrich_plan.backlog(data / "collector.db")) == {"600001": 1, "600002": 1}
    assert all(path.read_bytes() == content for path, content in before.items())


def test_from_only_extends_coverage_but_explicit_window_is_respected(tmp_path):
    store = SimplePostStore(tmp_path / "collector.db")
    try:
        store.add_coverage("eastmoney_guba", "600001", datetime(2026, 8, 10, tzinfo=timezone.utc),
                           datetime(2026, 8, 20, tzinfo=timezone.utc))
    finally:
        store.close()
    args = arguments("backfill", tmp_path, "--from", "2026-08-01")
    parsed = jobs.build_parser().parse_args(jobs.backfill_arguments(args, "600001"))
    assert parsed.to_value == "2026-08-10T00:00:00Z"
    assert parsed.start_page is None  # Preserve the existing date seek.
    assert jobs.backfill_arguments(arguments("backfill", tmp_path, "--from", "2026-08-11"), "600001") is None
    explicit = arguments("backfill", tmp_path, "--from", "2026-08-01", "--to", "2026-08-15", "--start-page", "47")
    parsed = jobs.build_parser().parse_args(jobs.backfill_arguments(explicit, "600001"))
    assert parsed.to_value == "2026-08-15" and parsed.start_page == 47


def test_native_backfill_runs_two_stocks_in_one_session_and_database(tmp_path, monkeypatch):
    import importlib
    import myresearcher_collector.integration as integration
    cli = importlib.import_module("myresearcher_collector.cli.main")
    real = integration.execute_and_persist_simple_backfill_collection
    monkeypatch.setattr(cli, "execute_and_persist_simple_backfill_collection",
                        lambda **kwargs: real(**kwargs, sleep_fn=lambda _: None))
    monkeypatch.setattr(jobs, "REPO", tmp_path)
    monkeypatch.setattr(jobs.time, "sleep", lambda _: None)

    class Wire(MappingTransport):
        closed = 0
        def close(self):
            self.closed += 1

    wire = Wire({f"https://guba.eastmoney.com/list,{stock},f{suffix}.html": body
                 for stock, post in [("600001", "1001"), ("600002", "2001")]
                 for suffix, body in [("", synthetic_page((post, "2026-08-10 10:00:00"))), ("_2", synthetic_page())]})
    factories = []
    Session = jobs.BrowserSession
    monkeypatch.setattr(jobs, "BrowserSession", lambda: Session(factory=lambda args: factories.append(args.stock) or wire))
    args = arguments("backfill", tmp_path / "data", "--stocks", "600001", "600002",
                     "--from", "2026-08-09", "--to", "2026-08-11", "--start-page", "1")
    assert jobs.run_backfill(args) == 0
    assert factories == ["600001"] and wire.closed == 1 and len(wire.calls) == 4
    reports = sorted((tmp_path / "runtime/logs").glob("*.json"))
    assert [json.loads(path.read_text())["browser_session"]["reused"] for path in reports] == [False, True]
    with closing(sqlite3.connect(tmp_path / "data/collector.db")) as db:
        assert db.execute("SELECT stock_code FROM posts ORDER BY stock_code").fetchall() == [("600001",), ("600002",)]


@pytest.mark.parametrize("first_report,code,expected_calls", [
    ({"stopped": True, "failed": 1}, 1, 1),
    (None, 2, 1),
    ({"stopped": False, "failed": 1, "skipped_not_found_added": 1}, 1, 2),
])
def test_cdp_enrich_stops_on_block_or_bad_report_but_continues_deleted_posts(
        tmp_path, monkeypatch, first_report, code, expected_calls):
    data = tmp_path / "data"
    seed(data, [("1", "600001", "长" * 40, None), ("2", "600002", "长" * 40, None)])
    monkeypatch.setattr(jobs, "REPO", tmp_path)
    monkeypatch.setattr(jobs, "cdp_version", lambda _: {"webSocketDebuggerUrl": "ws://local"})
    monkeypatch.setattr(jobs.time, "sleep", lambda _: None)
    monkeypatch.setenv("MYRESEARCHER_CDP_ENDPOINT", "http://previous:9222")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert command[:5] == [sys.executable, "-X", "utf8", "-m", "myresearcher_collector.cli.main"]
        assert command[command.index("--acquisition-mode") + 1] == "chrome-cdp"
        assert kwargs["env"]["MYRESEARCHER_CDP_ENDPOINT"] == "http://127.0.0.1:9333"
        assert kwargs["env"]["PYTHONPATH"].split(jobs.os.pathsep)[0] == str(tmp_path / "src")
        report = first_report if len(calls) == 1 else {"stopped": False, "failed": 0, "success": 1}
        kwargs["stdout"].write(json.dumps(report) if report else "")
        return SimpleNamespace(returncode=code if len(calls) == 1 else 0)

    monkeypatch.setattr(jobs.subprocess, "run", run)
    args = arguments("enrich", data, "--stocks", "600001", "600002", "--cdp-endpoint", "http://127.0.0.1:9333")
    result = jobs.run_enrich(args)
    assert len(calls) == expected_calls
    assert (result != 0) == (expected_calls == 1)
    assert jobs.os.environ["MYRESEARCHER_CDP_ENDPOINT"] == "http://previous:9222"


def test_enrich_plan_never_connects_or_creates_logs(tmp_path, monkeypatch):
    seed(tmp_path, [("1", "600001", "长" * 40, None)])
    monkeypatch.setattr(jobs, "REPO", tmp_path)
    def unexpected(*args, **kwargs):
        pytest.fail("a plan must not contact CDP or start a process")
    monkeypatch.setattr(jobs, "cdp_version", unexpected)
    monkeypatch.setattr(jobs.subprocess, "run", unexpected)
    assert jobs.run_enrich(arguments("enrich", tmp_path, "--plan-only")) == 0
    assert not (tmp_path / "runtime").exists()


def test_windows_chrome_launcher_uses_dedicated_profile_and_loopback(tmp_path, monkeypatch):
    chrome = tmp_path / "Program Files/Google/Chrome/Application/chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.touch()
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "Program Files"))
    assert jobs.find_chrome() == chrome
    def free_port(*args, **kwargs):
        raise ConnectionRefusedError
    monkeypatch.setattr(jobs.socket, "create_connection", free_port)
    monkeypatch.setattr(jobs, "cdp_version", lambda _: {"webSocketDebuggerUrl": "ws://local"})
    launched = []
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda command, **kwargs: launched.append(command))
    profile = tmp_path / "中文 profile"
    args = jobs.parser().parse_args(["open-cdp", "--profile-dir", str(profile)])
    assert jobs.open_cdp(args) == 0
    assert launched == [[str(chrome), f"--user-data-dir={profile}", "--remote-debugging-address=127.0.0.1",
                         "--remote-debugging-port=9222", "--no-first-run", "--no-default-browser-check", "about:blank"]]


def test_occupied_cdp_port_does_not_launch_another_browser(tmp_path, monkeypatch):
    chrome = tmp_path / "chrome.exe"
    chrome.touch()
    monkeypatch.setattr(jobs.socket, "create_connection", lambda *args, **kwargs: nullcontext())
    def unexpected(*args, **kwargs):
        pytest.fail("occupied port must not launch Chrome")
    monkeypatch.setattr(jobs.subprocess, "Popen", unexpected)
    args = jobs.parser().parse_args(["open-cdp", "--chrome", str(chrome), "--profile-dir", str(tmp_path / "profile")])
    with pytest.raises(ValueError, match="已被占用"):
        jobs.open_cdp(args)
    assert not (tmp_path / "profile").exists()
