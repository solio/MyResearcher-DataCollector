"""Range-aware backfill resume and completed-coverage semantics.

Covers the seven regression cases for the cross-range page-checkpoint bug:

1. same-range resume
2. cross-range no-resume
3. fully covered (zero requests, already_covered)
4. overlapping covered tail (early stop, existing_coverage_reached)
5. coverage in the middle (no early stop, keep scanning into history)
6. partial is not coverage
7. false-success regression (stale page 82 checkpoint must not skip new data)

All synthetic pages are parsed as Asia/Shanghai instants; the helper ``utc``
uses midnight-UTC boundaries so day-level comparisons stay unambiguous.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from myresearcher_collector.backfill import (
    BackfillConfigError,
    coverage_boundary,
    coverage_covers,
    coverage_stop_predicate,
    merge_coverage_intervals,
    resolve_backfill_range,
)
from myresearcher_collector.integration import (
    execute_and_persist_simple_backfill_collection,
    plan_backfill,
)
from myresearcher_collector.models import CollectionStatus
from myresearcher_collector.simple_store import SimplePostStore
from myresearcher_collector.sources.eastmoney_guba import CollectorConfig
from myresearcher_collector.sources.eastmoney_guba.collector import EastmoneyGubaCollector
from tests.unit.test_backfill import MappingTransport, synthetic_page


SOURCE = "eastmoney_guba"
STOCK = "600001"


def utc(day: int, month: int = 8) -> datetime:
    return datetime(2026, month, day, tzinfo=timezone.utc)


def routes_for(stock: str, pages: dict[int, list[tuple[str, str]]]) -> dict[str, object]:
    return {
        EastmoneyGubaCollector.list_url(stock, page): synthetic_page(*rows)
        for page, rows in pages.items()
    }


def run_backfill(db_path, transport, from_time, to_time, *, max_pages=None, start_page=None, clock=None):
    # Default clock is pinned after every synthetic range so the effective-now
    # coverage cap never depends on the wall clock.
    return execute_and_persist_simple_backfill_collection(
        db_path=db_path, stock_code=STOCK, from_time=from_time, to_time=to_time,
        transport=transport,
        collector_config=CollectorConfig(max_pages=5, min_interval_seconds=2.5),
        sleep_fn=lambda _: None,
        max_pages=max_pages,
        start_page=start_page,
        clock=clock or (lambda: datetime(2026, 8, 25, tzinfo=timezone.utc)),
    )


def test_future_range_fails_closed_before_store_or_transport(tmp_path):
    db_path = tmp_path / "collector.db"
    transport = MappingTransport({})
    with pytest.raises(BackfillConfigError, match="begins after run start"):
        execute_and_persist_simple_backfill_collection(
            db_path=db_path,
            stock_code=STOCK,
            from_time=datetime(2026, 8, 14, tzinfo=timezone.utc),
            to_time=datetime(2026, 8, 14, 23, 59, 59, tzinfo=timezone.utc),
            transport=transport,
            run_started_at=datetime(2026, 8, 13, 4, tzinfo=timezone.utc),
            collector_config=CollectorConfig(max_pages=5, min_interval_seconds=2.5),
            sleep_fn=lambda _: None,
        )
    assert transport.calls == []
    assert not db_path.exists()


def test_page_one_is_durable_before_unexpected_page_two_failure(tmp_path):
    db_path = tmp_path / "collector.db"
    transport = MappingTransport(routes_for(STOCK, {
        1: [("1001", "2026-08-10 12:00:00")],
    }))
    # An absent page-2 route is normalized by the Collector into a partial
    # pagination failure after page 1 has already committed.
    result = run_backfill(db_path, transport, utc(1), utc(20))
    assert result.execution.result.status is CollectionStatus.PARTIAL_COLLECTION
    assert result.execution.result.stop_reason == "pagination_failure"

    store = SimplePostStore(db_path)
    try:
        assert store.count(SOURCE, STOCK) == 1
        assert store.backfill_resume_page(SOURCE, STOCK, utc(1), utc(20)) == 1
        assert len(store.page_anchors(SOURCE, STOCK)) == 1
        assert store.coverage_ranges(SOURCE, STOCK) == []
    finally:
        store.close()


def test_multiple_successful_pages_survive_unexpected_interruption(tmp_path):
    db_path = tmp_path / "collector.db"
    transport = MappingTransport(routes_for(STOCK, {
        1: [("1001", "2026-08-10 12:00:00")],
        2: [("1002", "2026-08-09 12:00:00")],
    }))
    result = run_backfill(db_path, transport, utc(1), utc(20))
    assert result.execution.result.status is CollectionStatus.PARTIAL_COLLECTION
    assert result.execution.result.stop_reason == "pagination_failure"

    store = SimplePostStore(db_path)
    try:
        assert store.count(SOURCE, STOCK) == 2
        assert store.backfill_resume_page(SOURCE, STOCK, utc(1), utc(20)) == 2
        assert len(store.page_anchors(SOURCE, STOCK)) == 2
        assert store.coverage_ranges(SOURCE, STOCK) == []
    finally:
        store.close()


def test_page_transaction_rolls_back_posts_anchor_and_resume(tmp_path, monkeypatch):
    db_path = tmp_path / "collector.db"
    original = SimplePostStore.upsert_source_item
    calls = 0

    def fail_on_second(self, item, *, stock_code, content=None, updated_at=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected page transaction failure")
        return original(
            self, item, stock_code=stock_code, content=content, updated_at=updated_at
        )

    monkeypatch.setattr(SimplePostStore, "upsert_source_item", fail_on_second)
    transport = MappingTransport(routes_for(STOCK, {
        1: [
            ("1001", "2026-08-10 12:00:00"),
            ("1002", "2026-08-10 11:00:00"),
        ],
    }))
    with pytest.raises(RuntimeError, match="injected page transaction failure"):
        run_backfill(db_path, transport, utc(1), utc(20))

    store = SimplePostStore(db_path)
    try:
        assert store.count(SOURCE, STOCK) == 0
        assert store.backfill_resume_page(SOURCE, STOCK, utc(1), utc(20)) is None
        assert store.page_anchors(SOURCE, STOCK) == []
        assert store.coverage_ranges(SOURCE, STOCK) == []
    finally:
        store.close()


def test_live_top_partial_persists_posts_without_unsafe_resume(tmp_path):
    db_path = tmp_path / "collector.db"
    noon = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)
    resolved = resolve_backfill_range(source=SOURCE, stock_code=STOCK, days=36, now=noon)
    result = run_backfill(
        db_path,
        MappingTransport(routes_for(STOCK, {
            1: [("1001", "2026-08-13 10:00:00")],
        })),
        resolved.from_time, resolved.to_time, max_pages=1, clock=lambda: noon,
    )
    assert result.execution.result.status is CollectionStatus.PARTIAL_COLLECTION

    store = SimplePostStore(db_path)
    try:
        assert store.count(SOURCE, STOCK) == 1
        assert store.backfill_resume_page(
            SOURCE, STOCK, resolved.from_time, resolved.to_time
        ) is None
        assert store.coverage_ranges(SOURCE, STOCK) == []
    finally:
        store.close()


# --- CASE 1: same-range resume ---------------------------------------------

def test_case1_same_range_partial_resumes_from_next_page(tmp_path):
    db_path = tmp_path / "collector.db"
    transport1 = MappingTransport(routes_for(STOCK, {
        1: [("1001", "2026-08-10 12:00:00")],
        2: [("1002", "2026-08-05 12:00:00")],
    }))
    first = run_backfill(db_path, transport1, utc(1), utc(13), max_pages=2)
    assert first.execution.result.status is CollectionStatus.PARTIAL_COLLECTION
    assert first.execution.result.stop_reason == "max_pages_reached"
    assert first.execution.range_complete is False

    store = SimplePostStore(db_path)
    try:
        assert store.backfill_resume_page(SOURCE, STOCK, utc(1), utc(13)) == 2
        assert store.coverage_ranges(SOURCE, STOCK) == []
    finally:
        store.close()

    transport2 = MappingTransport(routes_for(STOCK, {
        3: [("1003", "2026-08-03 12:00:00")],
        4: [("1004", "2026-07-25 12:00:00")],
    }))
    second = run_backfill(db_path, transport2, utc(1), utc(13))
    assert transport2.calls[0] == EastmoneyGubaCollector.list_url(STOCK, 3)
    assert second.execution.result.status is CollectionStatus.SUCCESS
    assert second.execution.range_complete is True

    store = SimplePostStore(db_path)
    try:
        assert store.backfill_resume_page(SOURCE, STOCK, utc(1), utc(13)) is None
        assert store.coverage_ranges(SOURCE, STOCK) == [(utc(1), utc(13))]
    finally:
        store.close()


# --- CASE 2: cross-range no-resume -----------------------------------------

def test_case2_different_range_never_inherits_page_checkpoint(tmp_path):
    db_path = tmp_path / "collector.db"
    transport1 = MappingTransport(routes_for(STOCK, {
        1: [("1001", "2026-08-10 12:00:00")],
        2: [("1002", "2026-08-05 12:00:00")],
    }))
    first = run_backfill(db_path, transport1, utc(1), utc(13), max_pages=2)
    assert first.execution.result.status is CollectionStatus.PARTIAL_COLLECTION

    # A to_time change makes this a new range; page 20 checkpoint must not apply.
    transport2 = MappingTransport(routes_for(STOCK, {
        1: [("2001", "2026-08-14 12:00:00")],
        2: [("2002", "2026-07-25 12:00:00")],
    }))
    second = run_backfill(db_path, transport2, utc(1), utc(20))
    assert transport2.calls[0] == EastmoneyGubaCollector.list_url(STOCK, 1)
    assert transport2.calls == [
        EastmoneyGubaCollector.list_url(STOCK, 1),
        EastmoneyGubaCollector.list_url(STOCK, 2),
    ]
    assert second.execution.result.status is CollectionStatus.SUCCESS
    assert second.execution.range_complete is True

    store = SimplePostStore(db_path)
    try:
        # The stale resume row of the old range is untouched by the new range.
        assert store.backfill_resume_page(SOURCE, STOCK, utc(1), utc(13)) == 2
        assert store.coverage_ranges(SOURCE, STOCK) == [(utc(1), utc(20))]
    finally:
        store.close()


# --- CASE 3: fully covered ---------------------------------------------------

def test_case3_fully_covered_request_is_zero_request_success(tmp_path):
    db_path = tmp_path / "collector.db"
    seed = MappingTransport(routes_for(STOCK, {
        1: [("1001", "2026-08-15 12:00:00")],
        2: [("1002", "2026-07-25 12:00:00")],
        3: [("1003", "2026-07-05 12:00:00")],
        4: [("1004", "2026-06-25 12:00:00")],
    }))
    first = run_backfill(db_path, seed, utc(1, 7), utc(20))
    assert first.execution.result.status is CollectionStatus.SUCCESS
    assert first.execution.range_complete is True

    transport2 = MappingTransport({})
    second = run_backfill(db_path, transport2, utc(15, 7), utc(10))
    assert transport2.calls == []
    assert second.execution.result.status is CollectionStatus.SUCCESS
    assert second.execution.result.stop_reason == "already_covered"
    assert second.execution.pages_scanned == 0
    assert second.execution.range_complete is True
    assert second.execution.result.items == []

    store = SimplePostStore(db_path)
    try:
        assert store.coverage_ranges(SOURCE, STOCK) == [(utc(1, 7), utc(20))]
    finally:
        store.close()


# --- CASE 4: overlapping covered tail ----------------------------------------

def test_case4_overlap_early_stops_when_entering_covered_tail(tmp_path):
    db_path = tmp_path / "collector.db"
    seed = MappingTransport(routes_for(STOCK, {
        1: [("1001", "2026-08-12 12:00:00"), ("1002", "2026-08-10 12:00:00")],
        2: [("1003", "2026-08-05 12:00:00")],
        3: [("1004", "2026-07-25 12:00:00")],
        4: [("1005", "2026-07-05 12:00:00")],
    }))
    first = run_backfill(db_path, seed, utc(9, 7), utc(13))
    assert first.execution.result.status is CollectionStatus.SUCCESS
    assert first.execution.range_complete is True

    transport2 = MappingTransport(routes_for(STOCK, {
        1: [("2001", "2026-08-18 12:00:00"), ("2002", "2026-08-14 12:00:00")],
        2: [("2003", "2026-08-10 12:00:00")],
    }))
    second = run_backfill(db_path, transport2, utc(1), utc(20))
    assert transport2.calls == [
        EastmoneyGubaCollector.list_url(STOCK, 1),
        EastmoneyGubaCollector.list_url(STOCK, 2),
    ]
    assert second.execution.result.status is CollectionStatus.SUCCESS
    assert second.execution.result.stop_reason == "existing_coverage_reached"
    assert second.execution.range_complete is True

    store = SimplePostStore(db_path)
    try:
        assert store.coverage_ranges(SOURCE, STOCK) == [(utc(9, 7), utc(20))]
        assert store.backfill_resume_page(SOURCE, STOCK, utc(1), utc(20)) is None
        assert store.count(SOURCE, STOCK) == 7
    finally:
        store.close()


# --- CASE 5: coverage in the middle ------------------------------------------

def test_case5_uncovered_history_below_coverage_prevents_early_stop(tmp_path):
    db_path = tmp_path / "collector.db"
    seed = MappingTransport(routes_for(STOCK, {
        1: [("1001", "2026-08-12 12:00:00")],
        2: [("1002", "2026-08-05 12:00:00")],
        3: [("1003", "2026-07-25 12:00:00")],
        4: [("1004", "2026-07-05 12:00:00")],
    }))
    first = run_backfill(db_path, seed, utc(9, 7), utc(13))
    assert first.execution.range_complete is True

    transport2 = MappingTransport(routes_for(STOCK, {
        1: [("2001", "2026-08-18 12:00:00"), ("2002", "2026-08-14 12:00:00")],
        2: [("2003", "2026-08-10 12:00:00")],
        3: [("2004", "2026-07-02 12:00:00")],
        4: [("2005", "2026-05-25 12:00:00")],
    }))
    second = run_backfill(db_path, transport2, utc(1, 6), utc(20))
    assert len(transport2.calls) == 4
    assert second.execution.result.status is CollectionStatus.SUCCESS
    assert second.execution.result.stop_reason == "backfill_range_complete"
    assert second.execution.range_complete is True

    store = SimplePostStore(db_path)
    try:
        assert store.coverage_ranges(SOURCE, STOCK) == [(utc(1, 6), utc(20))]
    finally:
        store.close()


# --- CASE 6: partial is not coverage ------------------------------------------

def test_case6_partial_range_never_becomes_coverage(tmp_path):
    db_path = tmp_path / "collector.db"
    transport1 = MappingTransport(routes_for(STOCK, {
        1: [("1001", "2026-08-10 12:00:00")],
        2: [("1002", "2026-08-05 12:00:00")],
    }))
    first = run_backfill(db_path, transport1, utc(9, 7), utc(13), max_pages=2)
    assert first.execution.result.status is CollectionStatus.PARTIAL_COLLECTION

    store = SimplePostStore(db_path)
    try:
        assert store.coverage_ranges(SOURCE, STOCK) == []
        assert store.backfill_resume_page(SOURCE, STOCK, utc(9, 7), utc(13)) == 2
    finally:
        store.close()

    # The partial range cannot enable any early stop for a new range.
    transport2 = MappingTransport(routes_for(STOCK, {
        1: [("2001", "2026-08-14 12:00:00")],
        2: [("2002", "2026-08-10 12:00:00")],
        3: [("2003", "2026-07-25 12:00:00")],
    }))
    second = run_backfill(db_path, transport2, utc(1), utc(20))
    assert len(transport2.calls) == 3
    assert second.execution.result.stop_reason == "backfill_range_complete"
    assert second.execution.result.status is CollectionStatus.SUCCESS

    store = SimplePostStore(db_path)
    try:
        assert store.coverage_ranges(SOURCE, STOCK) == [(utc(1), utc(20))]
    finally:
        store.close()


# --- CASE 7: false-success regression ----------------------------------------

def test_case7_stale_page_checkpoint_never_skips_new_range_data(tmp_path):
    db_path = tmp_path / "collector.db"
    store = SimplePostStore(db_path)
    try:
        store.save_backfill_resume(SOURCE, STOCK, utc(9, 7), utc(13), 82)
    finally:
        store.close()

    # The new range overlaps the old one but contains genuinely new data
    # (2026-08-14 .. 2026-08-20) that only exists on page 1.
    transport = MappingTransport(routes_for(STOCK, {
        1: [("2001", "2026-08-15 12:00:00")],
        2: [("2002", "2026-07-25 12:00:00")],
    }))
    result = run_backfill(db_path, transport, utc(1), utc(20))
    assert transport.calls == [
        EastmoneyGubaCollector.list_url(STOCK, 1),
        EastmoneyGubaCollector.list_url(STOCK, 2),
    ]
    assert result.execution.result.status is CollectionStatus.SUCCESS
    assert result.execution.range_complete is True
    assert result.execution.result.stop_reason == "backfill_range_complete"

    store = SimplePostStore(db_path)
    try:
        assert store.backfill_resume_page(SOURCE, STOCK, utc(9, 7), utc(13)) == 82
        assert store.coverage_ranges(SOURCE, STOCK) == [(utc(1), utc(20))]
        post_ids = {row["source_item_id"] for row in store.rows(SOURCE, STOCK)}
        assert "2001" in post_ids
    finally:
        store.close()


# --- Coverage evidence tightening ----------------------------------------------

def test_coverage_never_extends_beyond_effective_now(tmp_path):
    from myresearcher_collector.backfill import resolve_backfill_range

    db_path = tmp_path / "collector.db"
    noon = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)  # 12:00 Asia/Shanghai
    resolved = resolve_backfill_range(source=SOURCE, stock_code=STOCK, days=1, now=noon)
    transport = MappingTransport(routes_for(STOCK, {
        1: [("1001", "2026-08-13 10:00:00")],
        2: [("1002", "2026-08-12 12:00:00")],
    }))
    result = run_backfill(
        db_path, transport, resolved.from_time, resolved.to_time, clock=lambda: noon
    )
    assert result.execution.result.status is CollectionStatus.SUCCESS
    assert result.execution.range_complete is True

    store = SimplePostStore(db_path)
    try:
        coverage = store.coverage_ranges(SOURCE, STOCK)
        assert len(coverage) == 1
        covered_from, covered_to = coverage[0]
        assert covered_from == resolved.from_time
        assert covered_to == noon
        assert covered_to < resolved.to_time  # never the end-of-day bound
    finally:
        store.close()


def test_same_day_rerun_is_not_already_covered_and_refetches_page1(tmp_path):
    from myresearcher_collector.backfill import resolve_backfill_range

    db_path = tmp_path / "collector.db"
    noon = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)      # 12:00 Asia/Shanghai
    afternoon = datetime(2026, 8, 13, 7, 0, tzinfo=timezone.utc)  # 15:00 Asia/Shanghai
    resolved = resolve_backfill_range(source=SOURCE, stock_code=STOCK, days=1, now=noon)

    first = run_backfill(
        db_path,
        MappingTransport(routes_for(STOCK, {
            1: [("1001", "2026-08-13 10:00:00")],
            2: [("1002", "2026-08-12 12:00:00")],
        })),
        resolved.from_time, resolved.to_time, clock=lambda: noon,
    )
    assert first.execution.range_complete is True

    transport2 = MappingTransport(routes_for(STOCK, {
        1: [("2001", "2026-08-13 13:00:00")],  # published after the noon run
        2: [("2002", "2026-08-12 12:00:00")],
    }))
    second = run_backfill(
        db_path, transport2, resolved.from_time, resolved.to_time,
        clock=lambda: afternoon,
    )
    assert second.execution.result.stop_reason != "already_covered"
    assert transport2.calls[0] == EastmoneyGubaCollector.list_url(STOCK, 1)
    assert second.execution.result.status is CollectionStatus.SUCCESS

    store = SimplePostStore(db_path)
    try:
        post_ids = {row["source_item_id"] for row in store.rows(SOURCE, STOCK)}
        assert "2001" in post_ids
        assert store.coverage_ranges(SOURCE, STOCK) == [(resolved.from_time, afternoon)]
    finally:
        store.close()


def test_explicit_start_page_without_resume_never_claims_coverage(tmp_path):
    db_path = tmp_path / "collector.db"
    transport = MappingTransport(routes_for(STOCK, {
        51: [("5001", "2026-08-01 12:00:00")],
        52: [("5002", "2026-07-25 12:00:00")],
    }))
    result = run_backfill(db_path, transport, utc(1), utc(13), start_page=51)
    assert transport.calls == [
        EastmoneyGubaCollector.list_url(STOCK, 51),
        EastmoneyGubaCollector.list_url(STOCK, 52),
    ]
    # The downward traversal may finish, but pages 1..50 were never scanned,
    # so the external result must not report a complete requested range.
    assert result.execution.result.stop_reason == "backfill_range_complete"
    assert result.execution.range_complete is False
    assert result.execution.result.status is CollectionStatus.PARTIAL_COLLECTION

    store = SimplePostStore(db_path)
    try:
        assert store.coverage_ranges(SOURCE, STOCK) == []
        assert store.backfill_resume_page(SOURCE, STOCK, utc(1), utc(13)) is None
    finally:
        store.close()


def test_live_top_partial_never_resumes_and_refetches_new_posts(tmp_path):
    from myresearcher_collector.backfill import resolve_backfill_range

    db_path = tmp_path / "collector.db"
    noon = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)      # 12:00 Asia/Shanghai
    afternoon = datetime(2026, 8, 13, 7, 0, tzinfo=timezone.utc)  # 15:00 Asia/Shanghai
    resolved = resolve_backfill_range(source=SOURCE, stock_code=STOCK, days=36, now=noon)

    # 12:00 partial run over a live-top range: the range top (end of today)
    # is not frozen, so no resumable row may be saved.
    first = run_backfill(
        db_path,
        MappingTransport(routes_for(STOCK, {
            1: [("1001", "2026-08-13 10:00:00")],
        })),
        resolved.from_time, resolved.to_time, max_pages=1, clock=lambda: noon,
    )
    assert first.execution.result.status is CollectionStatus.PARTIAL_COLLECTION
    store = SimplePostStore(db_path)
    try:
        assert store.backfill_resume_page(
            SOURCE, STOCK, resolved.from_time, resolved.to_time
        ) is None
    finally:
        store.close()

    # Simulate a stale page-50 resume row from an older run; the 15:00 rerun
    # must still start at page 1 to see posts published after noon.
    store = SimplePostStore(db_path)
    try:
        store.save_backfill_resume(
            SOURCE, STOCK, resolved.from_time, resolved.to_time, 50
        )
    finally:
        store.close()

    transport2 = MappingTransport(routes_for(STOCK, {
        1: [("2001", "2026-08-13 13:00:00")],  # published after the noon run
        2: [("2002", "2026-07-08 12:00:00")],  # below the 36-day from_time
    }))
    second = run_backfill(
        db_path, transport2, resolved.from_time, resolved.to_time,
        clock=lambda: afternoon,
    )
    assert transport2.calls == [
        EastmoneyGubaCollector.list_url(STOCK, 1),
        EastmoneyGubaCollector.list_url(STOCK, 2),
    ]
    assert second.execution.result.status is CollectionStatus.SUCCESS
    assert second.execution.range_complete is True

    store = SimplePostStore(db_path)
    try:
        post_ids = {row["source_item_id"] for row in store.rows(SOURCE, STOCK)}
        assert "2001" in post_ids
        assert store.coverage_ranges(SOURCE, STOCK) == [(resolved.from_time, afternoon)]
        assert store.backfill_resume_page(
            SOURCE, STOCK, resolved.from_time, resolved.to_time
        ) is None
    finally:
        store.close()


def test_exact_range_resume_continuation_allows_coverage(tmp_path):
    db_path = tmp_path / "collector.db"
    store = SimplePostStore(db_path)
    try:
        store.save_backfill_resume(SOURCE, STOCK, utc(1), utc(10), 50)
    finally:
        store.close()

    transport = MappingTransport(routes_for(STOCK, {
        51: [("5101", "2026-08-03 12:00:00")],
        52: [("5102", "2026-07-25 12:00:00")],
    }))
    result = run_backfill(db_path, transport, utc(1), utc(10))
    assert transport.calls == [
        EastmoneyGubaCollector.list_url(STOCK, 51),
        EastmoneyGubaCollector.list_url(STOCK, 52),
    ]
    assert result.execution.result.status is CollectionStatus.SUCCESS
    assert result.execution.range_complete is True

    store = SimplePostStore(db_path)
    try:
        assert store.coverage_ranges(SOURCE, STOCK) == [(utc(1), utc(10))]
        assert store.backfill_resume_page(SOURCE, STOCK, utc(1), utc(10)) is None
    finally:
        store.close()


# --- Store-level semantics ----------------------------------------------------

def test_resume_row_is_exact_range_match(tmp_path):
    store = SimplePostStore(tmp_path / "collector.db")
    try:
        store.save_backfill_resume(SOURCE, STOCK, utc(9, 7), utc(13), 20)
        assert store.backfill_resume_page(SOURCE, STOCK, utc(9, 7), utc(13)) == 20
        assert store.backfill_resume_page(SOURCE, STOCK, utc(9, 7), utc(20)) is None
        assert store.backfill_resume_page(SOURCE, STOCK, utc(10, 7), utc(13)) is None
        assert store.backfill_resume_page(SOURCE, "600002", utc(9, 7), utc(13)) is None
        store.save_backfill_resume(SOURCE, STOCK, utc(9, 7), utc(13), 25)
        assert store.backfill_resume_page(SOURCE, STOCK, utc(9, 7), utc(13)) == 25
        store.clear_backfill_resume(SOURCE, STOCK, utc(9, 7), utc(13))
        assert store.backfill_resume_page(SOURCE, STOCK, utc(9, 7), utc(13)) is None
    finally:
        store.close()


def test_coverage_merges_overlapping_and_adjacent_intervals(tmp_path):
    store = SimplePostStore(tmp_path / "collector.db")
    try:
        store.add_coverage(SOURCE, STOCK, utc(9, 7), utc(13))
        store.add_coverage(SOURCE, STOCK, utc(10), utc(20))
        assert store.coverage_ranges(SOURCE, STOCK) == [(utc(9, 7), utc(20))]
        store.add_coverage(SOURCE, STOCK, utc(1, 6), utc(15, 6))
        assert store.coverage_ranges(SOURCE, STOCK) == [
            (utc(1, 6), utc(15, 6)),
            (utc(9, 7), utc(20)),
        ]
        assert store.coverage_ranges(SOURCE, "600002") == []
    finally:
        store.close()


def test_legacy_backfill_state_is_dropped_and_never_resumed(tmp_path):
    import sqlite3

    db_path = tmp_path / "collector.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """CREATE TABLE posts (
               source TEXT NOT NULL, source_item_id TEXT NOT NULL, stock_code TEXT NOT NULL,
               title TEXT, content TEXT, author_id TEXT, author_name TEXT,
               published_at TEXT NOT NULL, url TEXT NOT NULL, read_count INTEGER,
               reply_count INTEGER, like_count INTEGER, forward_count INTEGER,
               created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
               PRIMARY KEY(source, source_item_id));
               CREATE TABLE backfill_state (
               source TEXT NOT NULL, stock_code TEXT NOT NULL, last_successful_page INTEGER NOT NULL,
               PRIMARY KEY(source, stock_code));"""
        )
        conn.execute(
            "INSERT INTO backfill_state(source,stock_code,last_successful_page) VALUES(?,?,82)",
            (SOURCE, STOCK),
        )
        conn.commit()
    finally:
        conn.close()

    store = SimplePostStore(db_path)
    try:
        tables = {
            r[0] for r in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "backfill_state" not in tables
        assert "backfill_resume" in tables and "backfill_coverage" in tables
        plan = plan_backfill(
            store, source=SOURCE, stock_code=STOCK,
            from_time=utc(1), to_time=utc(20),
        )
        assert plan.start_page == 1
        assert plan.already_covered is False
        assert plan.coverage_ranges == ()
    finally:
        store.close()


def test_read_only_store_does_not_create_or_mutate(tmp_path):
    import sqlite3

    db_path = tmp_path / "collector.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE posts (source TEXT, source_item_id TEXT, stock_code TEXT,"
            " published_at TEXT, url TEXT, created_at TEXT, updated_at TEXT,"
            " PRIMARY KEY(source, source_item_id))"
        )
        conn.commit()
    finally:
        conn.close()
    before = db_path.stat().st_mtime_ns

    store = SimplePostStore(db_path, read_only=True)
    try:
        assert store.backfill_resume_page(SOURCE, STOCK, utc(1), utc(13)) is None
        assert store.coverage_ranges(SOURCE, STOCK) == []
    finally:
        store.close()
    assert db_path.stat().st_mtime_ns == before


# --- Pure coverage helper semantics -------------------------------------------

def test_merge_coverage_intervals_sorts_and_merges():
    assert merge_coverage_intervals([
        (utc(10), utc(20)),
        (utc(1), utc(15)),
        (utc(15), utc(18)),
    ]) == [(utc(1), utc(20))]


def test_coverage_covers_subset_and_gaps():
    covered = [(utc(9, 7), utc(13))]
    assert coverage_covers(covered, utc(10, 7), utc(12)) is True
    assert coverage_covers(covered, utc(1), utc(20)) is False
    assert coverage_covers(covered, utc(1, 6), utc(10, 7)) is False
    assert coverage_covers([], utc(1), utc(20)) is False


def test_coverage_boundary_stops_at_first_gap():
    covered = [(utc(1, 6), utc(15, 6)), (utc(9, 7), utc(13))]
    assert coverage_boundary(covered, utc(10, 6)) == utc(15, 6)
    assert coverage_boundary(covered, utc(1)) == utc(13)
    assert coverage_boundary(covered, utc(10, 7)) == utc(13)
    assert coverage_boundary([], utc(1)) is None


def test_coverage_stop_predicate_is_conservative():
    covered = [(utc(9, 7), utc(13))]
    stop = coverage_stop_predicate(covered, utc(1))
    assert stop(utc(10), utc(12)) is True
    assert stop(utc(2), utc(5)) is True
    assert stop(utc(10), utc(14)) is False      # page reaches above coverage
    assert stop(utc(28, 7), utc(10)) is False   # page dips below from_time
    # Uncovered from_time disables the predicate entirely.
    assert coverage_stop_predicate(covered, utc(1, 6))(utc(10), utc(12)) is False
    assert coverage_stop_predicate([], utc(1))(utc(10), utc(12)) is False
