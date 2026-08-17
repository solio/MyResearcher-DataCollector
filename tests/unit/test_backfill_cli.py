from __future__ import annotations

import json
import importlib
from datetime import datetime, timezone
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
    assert "requested_from_time" in plan
    assert "requested_to_time" in plan
    assert "run_started_at" in plan
    assert "effective_from_time" in plan
    assert "effective_to_time" in plan
    assert "requested_range_truncated_at_run_start" in plan
    assert not data_dir.exists()


def test_xueqiu_plan_reports_existing_user_chrome_default(tmp_path: Path, capsys) -> None:
    code = main([
        "backfill", "--source", "xueqiu", "--stock", "601012",
        "--days", "3", "--data-dir", str(tmp_path / "data"), "--plan-only",
    ])
    assert code == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["acquisition_method"] == "existing-chrome"
    assert plan["source_access"] == "EXISTING_USER_CHROME_APPLE_EVENTS"
    assert plan["unattended_production_ready"] is False


def test_backfill_plan_only_is_range_aware_and_non_mutating(tmp_path: Path, capsys) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "collector.db"
    # The seeded range is fully in the past so the resume validity check
    # (to_time < now) never depends on the wall clock.
    store = SimplePostStore(db_path)
    try:
        resolved = resolve_backfill_range(
            source="eastmoney_guba", stock_code="600001",
            from_value="2026-08-01", to_value="2026-08-10",
        )
        store.save_backfill_resume(
            "eastmoney_guba", "600001", resolved.from_time, resolved.to_time, 20
        )
    finally:
        store.close()
    before = db_path.stat().st_mtime_ns

    code = main([
        "backfill", "--source", "eastmoney_guba", "--stock", "600001",
        "--from", "2026-08-01", "--to", "2026-08-10",
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


def test_backfill_plan_only_future_range_is_configuration_error(tmp_path: Path, capsys) -> None:
    data_dir = tmp_path / "data"
    code = main([
        "backfill", "--source", "eastmoney_guba", "--stock", "600001",
        "--from", "2099-01-01", "--to", "2099-01-02",
        "--data-dir", str(data_dir), "--plan-only",
    ])
    assert code == 2
    assert "backfill range begins after run start" in capsys.readouterr().err
    assert not data_dir.exists()


def test_backfill_report_distinguishes_requested_and_effective_ranges(
    tmp_path: Path, monkeypatch
) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 13, 4, tzinfo=timezone.utc)

    monkeypatch.setattr(cli_main, "datetime", FrozenDateTime)
    args = build_parser().parse_args([
        "backfill", "--source", "eastmoney_guba", "--stock", "600001",
        "--from", "2026-08-01", "--to", "2026-08-20",
        "--data-dir", str(tmp_path / "data"), "--confirm-live",
    ])

    def fake_execute(**kwargs):
        result = SimpleNamespace(
            status=SimpleNamespace(value="SUCCESS"), stop_reason="backfill_range_complete"
        )
        stats = SimpleNamespace(
            result=result, pages_scanned=1, records_received=1, records_in_range=1,
            records_failed=0, earliest_observed_at=None, latest_observed_at=None,
            range_complete=True,
        )
        return SimpleNamespace(
            execution=stats, run_id="range-report", records_new=1,
            records_existing=0, records_versioned=0, checkpoint_before=None,
            checkpoint_after=None, start_page=1,
        )

    monkeypatch.setattr(
        cli_main, "execute_and_persist_simple_backfill_collection", fake_execute
    )
    report = execute_backfill_cli(args, transport=object())
    assert report["to_time"] == "2026-08-20T15:59:59.999999Z"
    assert report["effective_to_time"] == "2026-08-13T04:00:00Z"
    assert report["requested_range_truncated_at_run_start"] is True
    assert report["effective_range_complete"] is True
    assert report["requested_range_complete"] is False
    assert report["range_complete"] is True
    assert report["range_complete_scope"] == "effective"


def test_backfill_cli_selects_existing_chrome_dom_acquisition(tmp_path: Path) -> None:
    args = build_parser().parse_args([
        "backfill", "--source", "eastmoney_guba", "--stock", "600001",
        "--days", "1", "--data-dir", str(tmp_path / "data"),
        "--acquisition-method", "existing-chrome-dom", "--confirm-live",
    ])
    assert isinstance(_browser_socket_transport(args), EastmoneyExistingChromeDomTransport)
