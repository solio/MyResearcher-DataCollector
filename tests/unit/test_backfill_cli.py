from __future__ import annotations

import json
from pathlib import Path

from myresearcher_collector.cli.main import build_parser, main


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
