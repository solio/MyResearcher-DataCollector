"""Sequential multi-target orchestration over the approved single-stock boundary."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping

from .integration import PersistentCollection, execute_and_persist_collection
from .models import CollectionStatus
from .run_report import summarize_run
from .sources.eastmoney_guba.collector import CollectorConfig, Transport
from .storage import PersistenceError, RawStoreError, SchemaError


SOURCE = "eastmoney_guba"
_SUCCESS_STATUSES = {CollectionStatus.SUCCESS.value, CollectionStatus.NO_NEW_DATA.value}
_GLOBAL_STOP_STATUSES = {CollectionStatus.SPEC_MISMATCH.value, CollectionStatus.CANCELLED.value}


class BatchConfigError(ValueError):
    """Target configuration is invalid and no execution may start."""


@dataclass(frozen=True)
class BatchTargets:
    source: str
    stocks: tuple[str, ...]


@dataclass(frozen=True)
class SingleStockOutcome:
    """The small result contract consumed by the batch orchestrator."""

    stock_code: str
    run_id: str | None
    status: str
    records_accepted: int = 0
    checkpoint_before: str | None = None
    checkpoint_after: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class BatchPlan:
    source: str
    stocks: tuple[str, ...]
    stock_count: int
    execution: str
    data_root: str
    network_execution: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self) | {"stocks": list(self.stocks)}


@dataclass
class BatchSummary:
    batch_id: str
    source: str
    targets_total: int
    targets_completed: int = 0
    targets_success: int = 0
    targets_partial: int = 0
    targets_failed: int = 0
    per_stock: list[SingleStockOutcome] = field(default_factory=list)
    stop_reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["per_stock"] = [asdict(item) for item in self.per_stock]
        return value


def validate_targets(value: Mapping[str, object]) -> BatchTargets:
    """Validate and deduplicate a static target mapping before any runner call."""
    if not isinstance(value, Mapping):
        raise BatchConfigError("target config must be an object")
    source = value.get("source")
    if source != SOURCE:
        raise BatchConfigError(f"source must be {SOURCE}")
    stocks = value.get("stocks")
    if not isinstance(stocks, list):
        raise BatchConfigError("stocks must be a list")
    unique: list[str] = []
    seen: set[str] = set()
    for stock in stocks:
        if not isinstance(stock, str) or len(stock) != 6 or not stock.isdigit():
            raise BatchConfigError("every stock must be a six-digit string")
        if stock not in seen:
            seen.add(stock)
            unique.append(stock)
    if not unique:
        raise BatchConfigError("stocks must contain at least one target")
    return BatchTargets(source=source, stocks=tuple(unique))


def load_targets(path: str | Path) -> BatchTargets:
    """Load the intentionally small JSON target format using only stdlib."""
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BatchConfigError(f"cannot read target config: {path}") from exc
    return validate_targets(payload)


def make_batch_plan(targets: BatchTargets, data_root: str | Path) -> BatchPlan:
    return BatchPlan(
        source=targets.source,
        stocks=targets.stocks,
        stock_count=len(targets.stocks),
        execution="sequential",
        data_root=str(Path(data_root).expanduser().resolve()),
        network_execution=False,
    )


def _is_global_persistence_error(exc: Exception) -> bool:
    return isinstance(exc, (PersistenceError, SchemaError, RawStoreError, sqlite3.Error))


class BatchRunner:
    """Execute one injected single-stock runner at a time, in target order."""

    def __init__(self, single_stock_runner: Callable[[str], SingleStockOutcome]) -> None:
        self.single_stock_runner = single_stock_runner

    def run(self, targets: BatchTargets, *, batch_id: str | None = None) -> BatchSummary:
        summary = BatchSummary(
            batch_id=batch_id or uuid.uuid4().hex,
            source=targets.source,
            targets_total=len(targets.stocks),
        )
        for stock_code in targets.stocks:
            try:
                outcome = self.single_stock_runner(stock_code)
                if not isinstance(outcome, SingleStockOutcome) or outcome.stock_code != stock_code:
                    raise RuntimeError("single-stock runner returned a mismatched target")
            except Exception as exc:
                if _is_global_persistence_error(exc):
                    summary.per_stock.append(SingleStockOutcome(
                        stock_code=stock_code,
                        run_id=None,
                        status=CollectionStatus.COLLECTION_FAILED.value,
                        error=f"global_persistence_error: {type(exc).__name__}",
                    ))
                    summary.targets_completed += 1
                    summary.targets_failed += 1
                    summary.stop_reason = f"global_persistence_error:{stock_code}"
                    break
                outcome = SingleStockOutcome(
                    stock_code=stock_code,
                    run_id=None,
                    status=CollectionStatus.COLLECTION_FAILED.value,
                    error=f"runner_error: {type(exc).__name__}",
                )

            summary.per_stock.append(outcome)
            summary.targets_completed += 1
            if outcome.status in _SUCCESS_STATUSES:
                summary.targets_success += 1
            elif outcome.status == CollectionStatus.PARTIAL_COLLECTION.value:
                summary.targets_partial += 1
            else:
                summary.targets_failed += 1

            if outcome.status in _GLOBAL_STOP_STATUSES:
                summary.stop_reason = f"{outcome.status.lower()}:{stock_code}"
                break
        return summary


def _outcome_from_execution(
    stock_code: str,
    execution: PersistentCollection,
) -> SingleStockOutcome:
    report = summarize_run(
        db_path=execution.db_path,
        raw_data_dir=execution.raw_data_dir,
        run_id=execution.run_id,
    )
    return SingleStockOutcome(
        stock_code=stock_code,
        run_id=execution.run_id,
        status=str(report["status"]),
        records_accepted=int(report["records_accepted"]),
        checkpoint_before=report["checkpoint_before"],
        checkpoint_after=report["checkpoint_after"],
    )


def execute_batch_collection(
    targets: BatchTargets,
    *,
    data_root: str | Path,
    collector_config: CollectorConfig | None = None,
    transport_factory: Callable[[str], Transport] | None = None,
    batch_id: str | None = None,
) -> BatchSummary:
    """Run the approved persistent single-stock boundary sequentially.

    A transport factory is injectable for deterministic tests. The CLI supplies
    a real transport only after explicit live confirmation.
    """
    if transport_factory is None:
        raise RuntimeError(
            "browser-managed Eastmoney transport factory must be supplied by the host"
        )
    data_root = Path(data_root)
    resolved_batch_id = batch_id or uuid.uuid4().hex

    def run_one(stock_code: str) -> SingleStockOutcome:
        execution = execute_and_persist_collection(
            db_path=data_root / "collector.db",
            raw_data_dir=data_root,
            stock_code=stock_code,
            transport=transport_factory(stock_code),
            run_id=f"{resolved_batch_id}-{stock_code}",
            collector_config=collector_config,
            max_pages=collector_config.max_pages if collector_config else None,
        )
        return _outcome_from_execution(stock_code, execution)

    return BatchRunner(run_one).run(targets, batch_id=resolved_batch_id)
