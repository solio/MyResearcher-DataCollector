from __future__ import annotations

import json
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from myresearcher_collector.cli.main import (
    _browser_socket_transport,
    build_parser,
    execute_backfill_cli,
    main,
)
from myresearcher_collector.backfill import resolve_backfill_range
from myresearcher_collector.simple_store import SimplePostStore
from myresearcher_collector.sources.eastmoney_guba import EastmoneyExistingChromeDomTransport


cli_main = importlib.import_module("myresearcher_collector.cli.main")


def test_backfill_plan_only_is_network_and_persistence_free(tmp_path: Path, capsys) -> None:
    data_dir = tmp_path / "data"
    code = main([
        "backfill", "--source", "eastmoney_guba", "--stock", "600001",
            "--days", "7", "--data-dir", str(data_dir),
        "--plan-only",
    ])
    assert code == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["estimated_mode"] == "BACKFILL"
    assert plan["network_execution"] is False
    assert plan["checkpoint_mutation"] is False
    assert plan["acquisition_method"] == "browser-socket"
    assert plan["resume_from_page"] == 1
    assert plan["already_covered"] is False
    assert not data_dir.exists()


def test_backfill_plan_only_is_range_aware_and_non_mutating(tmp_path: Path, capsys) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "collector.db"
    store = SimplePostStore(db_path)
    try:
        resolved = resolve_backfill_range(
            source="eastmoney_guba", stock_code="600001",
            from_value="2026-08-01", to_value="2026-08-13",
        )
        store.save_backfill_resume(
            "eastmoney_guba", "600001", resolved.from_time, resolved.to_time, 20
        )
    finally:
        store.close()
    before = db_path.stat().st_mtime_ns

    code = main([
        "backfill", "--source", "eastmoney_guba", "--stock", "600001",
        "--from", "2026-08-01", "--to", "2026-08-13",
        "--data-dir", str(data_dir), "--plan-only",
    ])
    assert code == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["resume_from_page"] == 21
    assert plan["already_covered"] is False

    # A different range must not inherit the persisted page checkpoint.
    code = main([
        "backfill", "--source", "eastmoney_guba", "--stock", "600001",
        "--from", "2026-08-01", "--to", "2026-08-20",
        "--data-dir", str(data_dir), "--plan-only",
    ])
    assert code == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["resume_from_page"] == 1
    assert plan["already_covered"] is False
    assert db_path.stat().st_mtime_ns == before


def test_backfill_requires_complete_explicit_range() -> None:
    parser = build_parser()
    args = parser.parse_args([
        "backfill", "--source", "eastmoney_guba", "--stock", "600001",
        "--from", "2026-08-01", "--to", "2026-08-02",
        "--data-dir", "data", "--plan-only",
    ])
    assert args.from_value == "2026-08-01"
    assert args.to_value == "2026-08-02"


def test_backfill_host_can_inject_browser_owned_transport(
    tmp_path: Path, monkeypatch
) -> None:
    args = build_parser().parse_args([
        "backfill", "--source", "eastmoney_guba", "--stock", "600001",
        "--from", "2026-08-01", "--to", "2026-08-02",
            "--data-dir", str(tmp_path / "data"),
        "--confirm-live",
    ])
    injected = object()
    captured: dict[str, object] = {}

    def fake_execute(**kwargs):
        captured.update(kwargs)
        result = SimpleNamespace(status=SimpleNamespace(value="SUCCESS"), stop_reason="done")
        stats = SimpleNamespace(
            result=result,
            pages_scanned=1,
            records_received=1,
            records_in_range=1,
            records_failed=0,
            earliest_observed_at=None,
            latest_observed_at=None,
            range_complete=True,
        )
        return SimpleNamespace(
            execution=stats,
            run_id="browser-run",
            records_new=1,
            records_existing=0,
            records_versioned=0,
            checkpoint_before=None,
            checkpoint_after=None,
            start_page=1,
        )

    monkeypatch.setattr(
        cli_main, "execute_and_persist_simple_backfill_collection", fake_execute
    )

    report = execute_backfill_cli(args, transport=injected)

    assert captured["transport"] is injected
    assert report["status"] == "SUCCESS"
    assert report["records_new"] == 1


def test_backfill_live_fails_closed_without_browser_host(tmp_path: Path) -> None:
    args = build_parser().parse_args([
        "backfill", "--source", "eastmoney_guba", "--stock", "600001",
        "--from", "2026-08-01", "--to", "2026-08-02",
            "--data-dir", str(tmp_path / "data"),
        "--confirm-live",
    ])
    with pytest.raises(RuntimeError, match="browser-managed Eastmoney transport"):
        execute_backfill_cli(args)


def test_backfill_cli_selects_existing_chrome_dom_acquisition(tmp_path: Path) -> None:
    args = build_parser().parse_args([
        "backfill", "--source", "eastmoney_guba", "--stock", "600001",
        "--days", "1", "--data-dir", str(tmp_path / "data"),
        "--acquisition-method", "existing-chrome-dom", "--confirm-live",
    ])
    assert isinstance(_browser_socket_transport(args), EastmoneyExistingChromeDomTransport)
