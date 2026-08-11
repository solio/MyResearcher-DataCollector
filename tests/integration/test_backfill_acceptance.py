"""Independent offline acceptance for Eastmoney Backfill v0.1.

These tests use the public Collector -> integration -> SQLite boundary with
deterministic transports.  They never use a live source and never modify
production code.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from myresearcher_collector.backfill import BackfillConfigError, resolve_backfill_range
from myresearcher_collector.cli.main import main
from myresearcher_collector.integration import execute_and_persist_backfill_collection
from myresearcher_collector.models import CollectionStatus
from myresearcher_collector.sources.eastmoney_guba import CollectorConfig, HttpResponse, EastmoneyGubaCollector
from myresearcher_collector.storage import RawEvidenceStore, SafeFrontier, SQLitePersistence
from tests.unit.test_backfill import synthetic_detail, synthetic_page


UTC = timezone.utc
SHANGHAI = timezone(timedelta(hours=8))
STOCK = "600001"
T0 = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)


class MappingTransport:
    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, *, timeout: float) -> HttpResponse:
        del timeout
        self.calls.append(url)
        value = self.routes[url]
        if isinstance(value, list):
            value = value.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def page_url(page: int) -> str:
    return EastmoneyGubaCollector.list_url(STOCK, page)


def detail_url(item_id: str) -> str:
    return f"https://guba.eastmoney.com/news,{STOCK},{item_id}.html"


def config(max_pages: int = 5) -> CollectorConfig:
    return CollectorConfig(
        max_pages=max_pages,
        min_interval_seconds=2.5,
        base_backoff_seconds=0,
    )


def run_backfill(
    tmp_path: Path,
    routes: dict[str, object],
    *,
    run_id: str,
    from_time: datetime,
    to_time: datetime,
    max_pages: int = 5,
):
    return execute_and_persist_backfill_collection(
        db_path=tmp_path / "collector.db",
        raw_data_dir=tmp_path / "data",
        stock_code=STOCK,
        from_time=from_time,
        to_time=to_time,
        transport=MappingTransport(routes),
        run_id=run_id,
        collector_config=config(max_pages),
        clock=lambda: datetime(2026, 8, 11, tzinfo=UTC),
        sleep_fn=lambda _: None,
        max_pages=max_pages,
    )


def reopen(tmp_path: Path) -> SQLitePersistence:
    return SQLitePersistence(
        tmp_path / "collector.db",
        RawEvidenceStore(tmp_path / "data", source="eastmoney_guba"),
    )


def seed_checkpoint(tmp_path: Path, value: datetime = T0) -> tuple[str, str]:
    raw = RawEvidenceStore(tmp_path / "data", source="eastmoney_guba")
    store = SQLitePersistence(tmp_path / "collector.db", raw)
    try:
        store.start_run(
            "forward-seed",
            "eastmoney_guba",
            f"stock:{STOCK}",
            started_at=datetime(2026, 8, 11, tzinfo=UTC),
            collector_version="collector.test",
            parser_version="parser.test",
            schema_version="eastmoney_guba.raw.v1",
        )
        assert store.finish_run(
            "forward-seed",
            status="SUCCESS",
            finished_at=datetime(2026, 8, 11, tzinfo=UTC),
            safe_frontier=SafeFrontier(value),
        )
    finally:
        store.close()
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z"), "forward-seed"


def in_range() -> tuple[datetime, datetime]:
    return (
        datetime(2026, 8, 9, 0, 0, tzinfo=SHANGHAI),
        datetime(2026, 8, 10, 23, 59, 59, 999999, tzinfo=SHANGHAI),
    )


def test_bf_a01_range_traversal_and_a19_normal_evidence(tmp_path: Path) -> None:
    from_time, to_time = in_range()
    routes = {
        page_url(1): synthetic_page(
            ("9001", "2026-08-11 10:00:00"),  # newer than to: list only
            ("9002", "2026-08-10 10:00:00"),  # in range
        ),
        page_url(2): synthetic_page(("9003", "2026-08-09 10:00:00")),
        page_url(3): synthetic_page(("9004", "2026-08-08 10:00:00")),
        detail_url("9002"): synthetic_detail("9002", "2026-08-10 10:00:00"),
        detail_url("9003"): synthetic_detail("9003", "2026-08-09 10:00:00"),
    }
    execution = run_backfill(
        tmp_path, routes, run_id="bf-a01", from_time=from_time, to_time=to_time, max_pages=3
    )
    result = execution.execution.result
    assert result.status is CollectionStatus.SUCCESS
    assert result.stop_reason == "backfill_range_complete"
    assert execution.execution.range_complete is True
    assert [item.source_item_id for item in result.items] == ["9002", "9003"]
    store = reopen(tmp_path)
    try:
        assert store.conn.execute(
            "SELECT source_item_id FROM source_item_observations ORDER BY source_item_id"
        ).fetchall() == [("9002",), ("9003",)]
        assert store.conn.execute("SELECT count(*) FROM raw_evidence WHERE run_id='bf-a01'").fetchone()[0] == 5
        assert store.conn.execute("SELECT count(*) FROM observation_evidence").fetchone()[0] == 4
        assert store.conn.execute(
            "SELECT count(*) FROM raw_body_state WHERE source='eastmoney_guba' AND body_state='PRESENT'"
        ).fetchone()[0] == 5
        assert store.conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%backfill%'"
        ).fetchall() == []
    finally:
        store.close()


def test_bf_a02_checkpoint_isolation_and_a03_fresh_forward_pending(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = seed_checkpoint(tmp_path)
    from_time, to_time = in_range()
    routes = {
        page_url(1): synthetic_page(("9010", "2026-05-15 10:00:00")),
        page_url(2): synthetic_page(("9011", "2026-04-30 10:00:00")),
        detail_url("9010"): synthetic_detail("9010", "2026-05-15 10:00:00"),
    }
    execution = run_backfill(
        tmp_path, routes, run_id="bf-a02", from_time=datetime(2026, 5, 1, tzinfo=UTC),
        to_time=datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC), max_pages=2,
    )
    assert execution.checkpoint_before == before[0]
    assert execution.checkpoint_after == before[0]
    store = reopen(tmp_path)
    try:
        assert store.checkpoint("eastmoney_guba", f"stock:{STOCK}") == before
    finally:
        store.close()

    fresh = tmp_path / "fresh"
    fresh_raw = RawEvidenceStore(fresh / "data", source="eastmoney_guba")
    fresh_store = SQLitePersistence(fresh / "collector.db", fresh_raw)
    try:
        assert fresh_store.checkpoint("eastmoney_guba", f"stock:{STOCK}") is None
        assert fresh_store.conn.execute(
            "SELECT count(*) FROM collector_checkpoints WHERE source=? AND scope_key=?",
            ("eastmoney_guba", f"stock:{STOCK}"),
        ).fetchone()[0] == 0
    finally:
        fresh_store.close()

    fresh_from, fresh_to = in_range()
    successful = run_backfill(
        fresh,
        {
            page_url(1): synthetic_page(("9015", "2026-08-10 10:00:00")),
            page_url(2): synthetic_page(("9016", "2026-08-08 10:00:00")),
            detail_url("9015"): synthetic_detail("9015", "2026-08-10 10:00:00"),
        },
        run_id="bf-a03-fresh-success",
        from_time=fresh_from,
        to_time=fresh_to,
        max_pages=2,
    )
    assert successful.execution.result.status is CollectionStatus.SUCCESS
    assert successful.execution.result.stop_reason == "backfill_range_complete"
    fresh_store = reopen(fresh)
    try:
        assert fresh_store.conn.execute(
            "SELECT count(*) FROM collection_runs WHERE run_id='bf-a03-fresh-success'"
        ).fetchone()[0] == 1
        assert fresh_store.conn.execute(
            "SELECT count(*) FROM raw_evidence WHERE run_id='bf-a03-fresh-success'"
        ).fetchone()[0] >= 3
        assert fresh_store.conn.execute(
            "SELECT count(*) FROM source_item_observations WHERE source_item_id='9015'"
        ).fetchone()[0] == 1
        assert fresh_store.conn.execute(
            "SELECT count(*) FROM collector_checkpoints WHERE source=? AND scope_key=?",
            ("eastmoney_guba", f"stock:{STOCK}"),
        ).fetchone()[0] == 0
        assert fresh_store.checkpoint("eastmoney_guba", f"stock:{STOCK}") is None
    finally:
        fresh_store.close()

    assert main([
        "eastmoney-guba-persistent", STOCK, "--data-dir", str(fresh), "--plan-only",
    ]) == 0
    forward_plan = json.loads(capsys.readouterr().out)
    assert forward_plan["collection_mode"] == "BOOTSTRAP_PENDING"


def test_bf_a04_known_ids_do_not_stop_traversal(tmp_path: Path) -> None:
    from_time, to_time = in_range()
    first_routes = {
        page_url(1): synthetic_page(("9020", "2026-08-10 10:00:00")),
        page_url(2): synthetic_page(("9021", "2026-08-08 10:00:00")),
        detail_url("9020"): synthetic_detail("9020", "2026-08-10 10:00:00"),
        detail_url("9021"): synthetic_detail("9021", "2026-08-08 10:00:00"),
    }
    first = run_backfill(tmp_path, first_routes, run_id="bf-known-seed", from_time=from_time, to_time=to_time, max_pages=2)
    assert first.records_new == 1
    second_transport = MappingTransport({
        page_url(1): synthetic_page(("9020", "2026-08-10 10:00:00")),
        page_url(2): synthetic_page(("9022", "2026-08-09 10:00:00")),
        detail_url("9020"): synthetic_detail("9020", "2026-08-10 10:00:00"),
        detail_url("9022"): synthetic_detail("9022", "2026-08-09 10:00:00"),
    })
    second = execute_and_persist_backfill_collection(
        db_path=tmp_path / "collector.db", raw_data_dir=tmp_path / "data", stock_code=STOCK,
        from_time=from_time, to_time=to_time, transport=second_transport, run_id="bf-known-next",
        collector_config=config(2), clock=lambda: datetime(2026, 8, 11, tzinfo=UTC),
        sleep_fn=lambda _: None, max_pages=2,
    )
    assert page_url(2) in second_transport.calls
    assert second.records_new == 1
    assert second.execution.result.status is CollectionStatus.PARTIAL_COLLECTION


def test_bf_a05_overlap_detail_once_and_a14_idempotent_repeat(tmp_path: Path) -> None:
    from_time, to_time = in_range()
    rows = {
        "9030": "2026-08-10 10:00:00", "9031": "2026-08-10 09:00:00",
        "9032": "2026-08-10 08:00:00", "9033": "2026-08-09 10:00:00",
        "9034": "2026-08-09 09:00:00", "9035": "2026-08-08 09:00:00",
    }
    routes: dict[str, object] = {
        page_url(1): synthetic_page(*rows.items()),
    }
    # Replace the first page with A/B/C and use C/D/E on page two.
    routes[page_url(1)] = synthetic_page(("9030", rows["9030"]), ("9031", rows["9031"]), ("9032", rows["9032"]))
    routes[page_url(2)] = synthetic_page(("9032", rows["9032"]), ("9033", rows["9033"]), ("9034", rows["9034"]))
    routes[page_url(3)] = synthetic_page(("9035", rows["9035"]))
    for item_id, published in rows.items():
        routes[detail_url(item_id)] = synthetic_detail(item_id, published)
    transport = MappingTransport(routes)
    first = execute_and_persist_backfill_collection(
        db_path=tmp_path / "collector.db", raw_data_dir=tmp_path / "data", stock_code=STOCK,
        from_time=from_time, to_time=to_time, transport=transport, run_id="bf-overlap-1",
        collector_config=config(3), clock=lambda: datetime(2026, 8, 11, tzinfo=UTC),
        sleep_fn=lambda _: None, max_pages=3,
    )
    assert first.execution.result.status is CollectionStatus.SUCCESS
    assert transport.calls.count(detail_url("9032")) == 1
    second = execute_and_persist_backfill_collection(
        db_path=tmp_path / "collector.db", raw_data_dir=tmp_path / "data", stock_code=STOCK,
        from_time=from_time, to_time=to_time, transport=MappingTransport(routes), run_id="bf-overlap-2",
        collector_config=config(3), clock=lambda: datetime(2026, 8, 11, tzinfo=UTC),
        sleep_fn=lambda _: None, max_pages=3,
    )
    assert second.records_new == 0
    assert second.records_existing == 5
    store = reopen(tmp_path)
    try:
        assert store.conn.execute("SELECT count(*) FROM source_item_observations").fetchone()[0] == 5
        assert store.conn.execute("SELECT max(observation_version) FROM source_item_observations").fetchone()[0] == 1
    finally:
        store.close()


def test_bf_a06_schema_mismatch_is_persisted_and_checkpoint_isolated(tmp_path: Path) -> None:
    from_time, to_time = in_range()
    execution = run_backfill(
        tmp_path,
        {
            page_url(1): synthetic_page(("9040", "2026-08-10 10:00:00")),
            detail_url("9040"): HttpResponse(200, b"<script>var post_article={};</script>", {}),
        },
        run_id="bf-schema",
        from_time=from_time,
        to_time=to_time,
        max_pages=1,
    )
    assert execution.execution.result.status is CollectionStatus.SPEC_MISMATCH
    assert execution.execution.result.stop_reason == "detail_schema_mismatch"
    store = reopen(tmp_path)
    try:
        assert store.conn.execute("SELECT count(*) FROM raw_evidence WHERE run_id='bf-schema'").fetchone()[0] == 2
        assert store.conn.execute("SELECT count(*) FROM collection_failures WHERE run_id='bf-schema'").fetchone()[0] == 1
        assert store.checkpoint("eastmoney_guba", f"stock:{STOCK}") is None
    finally:
        store.close()


def test_bf_a07_a08_timezone_and_days_are_host_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    expected_from = datetime(2026, 4, 30, 16, tzinfo=UTC)
    expected_to = datetime(2026, 5, 2, 15, 59, 59, 999999, tzinfo=UTC)
    values = []
    for tz_name in ("UTC", "America/New_York", "Asia/Shanghai"):
        monkeypatch.setenv("TZ", tz_name)
        if hasattr(__import__("time"), "tzset"):
            __import__("time").tzset()
        value = resolve_backfill_range(
            source="eastmoney_guba", stock_code=STOCK,
            from_value="2026-05-01", to_value="2026-05-02",
        )
        values.append((value.from_time, value.to_time))
    assert all(pair == (expected_from, expected_to) for pair in values)
    days = resolve_backfill_range(
        source="eastmoney_guba", stock_code=STOCK, days=2,
        now=datetime(2026, 8, 11, 7, 0, tzinfo=UTC),
    )
    assert days.from_time == datetime(2026, 8, 9, 16, tzinfo=UTC)
    assert days.to_time == datetime(2026, 8, 11, 15, 59, 59, 999999, tzinfo=UTC)


def test_bf_a09_a11_mixed_detail_failure_reconciles_and_is_partial(tmp_path: Path) -> None:
    from_time, to_time = in_range()
    failed = detail_url("9052")
    execution = run_backfill(
        tmp_path,
        {
            page_url(1): synthetic_page(
                ("9050", "2026-08-10 10:00:00"),
                ("9051", "2026-08-10 09:00:00"),
                ("9052", "2026-08-10 08:00:00"),
            ),
            page_url(2): synthetic_page(("9053", "2026-08-08 08:00:00")),
            detail_url("9050"): synthetic_detail("9050", "2026-08-10 10:00:00"),
            detail_url("9051"): synthetic_detail("9051", "2026-08-10 09:00:00"),
            failed: [HttpResponse(503, b"failure", {})] * 3,
        },
        run_id="bf-mixed",
        from_time=from_time,
        to_time=to_time,
        max_pages=2,
    )
    counters = execution.execution.result.counters
    assert (counters.details_requested, counters.details_success, counters.details_failed) == (3, 2, 1)
    assert counters.details_requested == counters.details_success + counters.details_failed
    assert execution.execution.result.status is CollectionStatus.PARTIAL_COLLECTION
    assert execution.checkpoint_after is None


def test_bf_a10_all_details_failed_is_collection_failed_with_evidence(tmp_path: Path) -> None:
    from_time, to_time = in_range()
    routes: dict[str, object] = {
        page_url(1): synthetic_page(
            ("9060", "2026-08-10 10:00:00"),
            ("9061", "2026-08-10 09:00:00"),
            ("9062", "2026-08-10 08:00:00"),
        ),
        page_url(2): synthetic_page(("9063", "2026-08-08 08:00:00")),
    }
    for item_id in ("9060", "9061", "9062"):
        routes[detail_url(item_id)] = [HttpResponse(503, b"retry exhausted", {})] * 3
    execution = run_backfill(tmp_path, routes, run_id="bf-all-failed", from_time=from_time, to_time=to_time, max_pages=2)
    result = execution.execution.result
    assert (result.counters.details_requested, result.counters.details_success, result.counters.details_failed) == (3, 0, 3)
    assert result.status is CollectionStatus.COLLECTION_FAILED
    assert result.stop_reason == "all_candidate_details_failed"
    store = reopen(tmp_path)
    try:
        assert store.conn.execute("SELECT count(*) FROM collection_failures WHERE run_id='bf-all-failed'").fetchone()[0] == 3
        assert store.conn.execute("SELECT count(*) FROM raw_evidence WHERE run_id='bf-all-failed'").fetchone()[0] >= 4
        assert store.checkpoint("eastmoney_guba", f"stock:{STOCK}") is None
    finally:
        store.close()


def test_bf_a12_empty_range_is_success_and_a13_cap_is_partial(tmp_path: Path) -> None:
    empty_from = datetime(2026, 8, 2, tzinfo=SHANGHAI)
    empty_to = datetime(2026, 8, 5, 23, 59, 59, tzinfo=SHANGHAI)
    empty = run_backfill(
        tmp_path / "empty",
        {
            page_url(1): synthetic_page(("9070", "2026-08-10 10:00:00")),
            page_url(2): synthetic_page(("9071", "2026-08-01 10:00:00")),
        },
        run_id="bf-empty",
        from_time=empty_from,
        to_time=empty_to,
        max_pages=2,
    )
    assert empty.execution.result.status is CollectionStatus.SUCCESS
    assert empty.execution.result.stop_reason == "backfill_range_complete"
    assert empty.execution.result.counters.details_requested == 0
    assert empty.execution.range_complete is True

    capped = run_backfill(
        tmp_path / "capped",
        {
            page_url(1): synthetic_page(("9072", "2026-08-10 10:00:00")),
            detail_url("9072"): synthetic_detail("9072", "2026-08-10 10:00:00"),
        },
        run_id="bf-capped",
        from_time=datetime(2026, 8, 1, tzinfo=SHANGHAI),
        to_time=datetime(2026, 8, 11, tzinfo=SHANGHAI),
        max_pages=1,
    )
    assert capped.execution.result.status is CollectionStatus.PARTIAL_COLLECTION
    assert capped.execution.result.stop_reason == "max_pages_reached"
    assert capped.execution.range_complete is False
    assert capped.checkpoint_after is None


def test_bf_a15_drift_versioning_preserves_history_without_version3(tmp_path: Path) -> None:
    from_time, to_time = in_range()
    def run(version: str, run_id: str):
        return run_backfill(
            tmp_path,
            {
                page_url(1): synthetic_page(("9080", "2026-08-10 10:00:00")),
                page_url(2): synthetic_page(("9081", "2026-08-08 08:00:00")),
                detail_url("9080"): synthetic_detail("9080", "2026-08-10 10:00:00", content=version),
            },
            run_id=run_id,
            from_time=from_time,
            to_time=to_time,
            max_pages=2,
        )
    assert run("body-v1", "bf-drift-1").records_new == 1
    assert run("body-v2", "bf-drift-2").records_versioned == 1
    assert run("body-v2", "bf-drift-3").records_versioned == 0
    store = reopen(tmp_path)
    try:
        rows = store.conn.execute(
            "SELECT source_item_id, observation_version, content FROM source_item_observations ORDER BY observation_version"
        ).fetchall()
        assert rows == [("9080", 1, "body-v1"), ("9080", 2, "body-v2")]
    finally:
        store.close()


def test_bf_a16_invalid_range_fails_before_transport_and_a17_plan_only_is_read_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(BackfillConfigError):
        resolve_backfill_range(
            source="eastmoney_guba", stock_code=STOCK,
            from_value="2026-08-10", to_value="2026-08-01",
        )
    data_dir = tmp_path / "plan-only"
    assert main([
        "backfill", "--source", "eastmoney_guba", "--stock", STOCK,
        "--from", "2026-05-01", "--to", "2026-05-02",
        "--data-dir", str(data_dir), "--plan-only",
    ]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["network_execution"] is False
    assert plan["checkpoint_mutation"] is False
    assert not data_dir.exists()


@pytest.mark.parametrize("case", ["partial", "failed", "spec"])
def test_bf_a18_all_terminal_states_preserve_forward_checkpoint(tmp_path: Path, case: str) -> None:
    before = seed_checkpoint(tmp_path)
    from_time, to_time = in_range()
    if case == "partial":
        routes = {
            page_url(1): synthetic_page(("9090", "2026-08-10 10:00:00")),
            page_url(2): HttpResponse(503, b"page failed", {}),
            detail_url("9090"): synthetic_detail("9090", "2026-08-10 10:00:00"),
        }
        max_pages = 2
    elif case == "failed":
        routes = {
            page_url(1): synthetic_page(("9091", "2026-08-10 10:00:00")),
            detail_url("9091"): [HttpResponse(503, b"failed", {})] * 3,
        }
        max_pages = 1
    else:
        routes = {
            page_url(1): synthetic_page(("9092", "2026-08-10 10:00:00")),
            detail_url("9092"): HttpResponse(200, b"<script>var post_article={};</script>", {}),
        }
        max_pages = 1
    execution = run_backfill(tmp_path, routes, run_id=f"bf-terminal-{case}", from_time=from_time, to_time=to_time, max_pages=max_pages)
    assert execution.checkpoint_before == before[0]
    assert execution.checkpoint_after == before[0]
    store = reopen(tmp_path)
    try:
        assert store.checkpoint("eastmoney_guba", f"stock:{STOCK}") == before
    finally:
        store.close()
