"""Offline tests for static sequential batch orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from myresearcher_collector.batch import (
    BatchConfigError,
    BatchRunner,
    SingleStockOutcome,
    load_targets,
    make_batch_plan,
    validate_targets,
)
from myresearcher_collector.cli.main import main
from myresearcher_collector.storage import PersistenceError


def outcome(stock: str, status: str = "SUCCESS") -> SingleStockOutcome:
    return SingleStockOutcome(
        stock_code=stock,
        run_id=f"run-{stock}",
        status=status,
        records_accepted=1,
        checkpoint_before=f"before-{stock}",
        checkpoint_after=f"after-{stock}",
    )


def test_target_validation_deduplicates_in_order_and_rejects_invalid() -> None:
    targets = validate_targets({
        "source": "eastmoney_guba",
        "stocks": ["600519", "300750", "600519", "002594"],
    })
    assert targets.stocks == ("600519", "300750", "002594")
    with pytest.raises(BatchConfigError):
        validate_targets({"source": "eastmoney_guba", "stocks": ["60051"]})
    with pytest.raises(BatchConfigError):
        validate_targets({"source": "eastmoney_guba", "stocks": ["600519", 300750]})


def test_json_targets_and_plan_are_explicit_and_network_free(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "targets.json"
    config.write_text(json.dumps({"source": "eastmoney_guba", "stocks": ["600519", "300750"]}))
    targets = load_targets(config)
    plan = make_batch_plan(targets, tmp_path / "data")
    assert plan.as_dict() == {
        "source": "eastmoney_guba",
        "stocks": ["600519", "300750"],
        "stock_count": 2,
        "execution": "sequential",
        "data_root": str((tmp_path / "data").resolve()),
        "network_execution": False,
    }
    assert main(["collect-batch", "--targets", str(config), "--data-dir", str(tmp_path / "data"), "--plan-only"]) == 0
    assert json.loads(capsys.readouterr().out)["network_execution"] is False
    assert not (tmp_path / "data").exists()


def test_batch_runs_in_order_without_concurrency_and_summarizes_isolated_scopes() -> None:
    order: list[str] = []
    active = 0
    max_active = 0

    def runner(stock: str) -> SingleStockOutcome:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        order.append(stock)
        result = outcome(stock, "NO_NEW_DATA" if stock == "300750" else "SUCCESS")
        active -= 1
        return result

    summary = BatchRunner(runner).run(
        validate_targets({"source": "eastmoney_guba", "stocks": ["600519", "300750", "002594"]}),
        batch_id="batch-1",
    )
    assert order == ["600519", "300750", "002594"]
    assert max_active == 1
    assert summary.targets_total == 3
    assert summary.targets_completed == 3
    assert summary.targets_success == 3
    assert summary.targets_partial == 0
    assert summary.targets_failed == 0
    assert [item.checkpoint_before for item in summary.per_stock] == [
        "before-600519", "before-300750", "before-002594"
    ]


def test_stock_failure_is_recorded_and_later_targets_execute() -> None:
    order: list[str] = []

    def runner(stock: str) -> SingleStockOutcome:
        order.append(stock)
        if stock == "300750":
            raise RuntimeError("synthetic transport failure")
        return outcome(stock)

    summary = BatchRunner(runner).run(
        validate_targets({"source": "eastmoney_guba", "stocks": ["600519", "300750", "002594"]}),
        batch_id="batch-failure",
    )
    assert order == ["600519", "300750", "002594"]
    assert summary.targets_completed == 3
    assert summary.targets_success == 2
    assert summary.targets_failed == 1
    assert summary.per_stock[1].status == "COLLECTION_FAILED"
    assert summary.per_stock[1].error == "runner_error: RuntimeError"
    assert summary.stop_reason is None


def test_partial_is_counted_and_spec_mismatch_stops_following_targets() -> None:
    order: list[str] = []

    def runner(stock: str) -> SingleStockOutcome:
        order.append(stock)
        if stock == "300750":
            return outcome(stock, "PARTIAL_COLLECTION")
        if stock == "002594":
            return outcome(stock, "SPEC_MISMATCH")
        return outcome(stock)

    summary = BatchRunner(runner).run(
        validate_targets({"source": "eastmoney_guba", "stocks": ["600519", "300750", "002594", "000001"]}),
        batch_id="batch-stop",
    )
    assert order == ["600519", "300750", "002594"]
    assert summary.targets_completed == 3
    assert summary.targets_success == 1
    assert summary.targets_partial == 1
    assert summary.targets_failed == 1
    assert summary.stop_reason == "spec_mismatch:002594"


def test_global_persistence_error_records_target_and_stops() -> None:
    order: list[str] = []

    def runner(stock: str) -> SingleStockOutcome:
        order.append(stock)
        if stock == "300750":
            raise PersistenceError("synthetic schema failure")
        return outcome(stock)

    summary = BatchRunner(runner).run(
        validate_targets({"source": "eastmoney_guba", "stocks": ["600519", "300750", "002594"]}),
        batch_id="batch-global-stop",
    )
    assert order == ["600519", "300750"]
    assert summary.targets_completed == 2
    assert summary.targets_success == 1
    assert summary.targets_failed == 1
    assert summary.per_stock[-1].error == "global_persistence_error: PersistenceError"
    assert summary.stop_reason == "global_persistence_error:300750"
