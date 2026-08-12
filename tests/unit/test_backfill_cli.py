from __future__ import annotations

import json
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from myresearcher_collector.cli.main import build_parser, execute_backfill_cli, main


cli_main = importlib.import_module("myresearcher_collector.cli.main")


def test_backfill_plan_only_is_network_and_persistence_free(tmp_path: Path, capsys) -> None:
    data_dir = tmp_path / "data"
    code = main([
        "backfill", "--source", "eastmoney_guba", "--stock", "600001",
        "--days", "7", "--data-dir", str(data_dir), "--max-pages", "10",
        "--plan-only",
    ])
    assert code == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["estimated_mode"] == "BACKFILL"
    assert plan["network_execution"] is False
    assert plan["checkpoint_mutation"] is False
    assert not data_dir.exists()


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
        "--data-dir", str(tmp_path / "data"), "--max-pages", "10",
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
        )

    monkeypatch.setattr(
        cli_main, "execute_and_persist_backfill_collection", fake_execute
    )

    report = execute_backfill_cli(args, transport=injected)

    assert captured["transport"] is injected
    assert report["status"] == "SUCCESS"
    assert report["records_new"] == 1


def test_backfill_live_fails_closed_without_browser_host(tmp_path: Path) -> None:
    args = build_parser().parse_args([
        "backfill", "--source", "eastmoney_guba", "--stock", "600001",
        "--from", "2026-08-01", "--to", "2026-08-02",
        "--data-dir", str(tmp_path / "data"), "--max-pages", "10",
        "--confirm-live",
    ])
    with pytest.raises(RuntimeError, match="browser-managed Eastmoney transport"):
        execute_backfill_cli(args)
