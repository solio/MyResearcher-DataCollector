from datetime import datetime, timezone

import pytest

from myresearcher_collector.integration import plan_backfill
from myresearcher_collector.page_anchor import (
    PageAnchor,
    PageProbe,
    SeekFailure,
    predict_page,
    seek_historical_page,
)
from myresearcher_collector.simple_store import SimplePostStore
from myresearcher_collector.integration import execute_and_persist_simple_backfill_collection
from myresearcher_collector.sources.eastmoney_guba import CollectorConfig, EastmoneyGubaCollector
from tests.unit.test_backfill import MappingTransport, synthetic_page


def t(day: int) -> datetime:
    return datetime(2026, 7, day, tzinfo=timezone.utc)


def anchor(page: int = 50, *, count: int | None = 100_000) -> PageAnchor:
    return PageAnchor("eastmoney_guba", "600001", t(20), page, t(19), t(21), count, 80)


def seek(target: datetime, anchors, pages):
    calls = []
    def probe(page: int) -> PageProbe:
        calls.append(page)
        return pages[page]
    proof = seek_historical_page(target_to=target, anchors=anchors, probe=probe)
    return proof, calls


def test_case1_source_count_drift_predicts_without_page1():
    pages = {
        50: PageProbe(50, t(19), t(21), 102_400, 80),
        80: PageProbe(80, t(19), t(20), 102_400, 80),
    }
    proof, calls = seek(t(20), [anchor()], pages)
    assert calls == [50, 80]
    assert proof.verified_page == 80
    assert predict_page(anchor(), 102_400) == 80


def test_case2_predicted_page_too_new_moves_older():
    pages = {
        50: PageProbe(50, t(22), t(23), 100_000, 80),
        52: PageProbe(52, t(21), t(22), 100_000, 80),
        56: PageProbe(56, t(20), t(21), 100_000, 80),
    }
    proof, calls = seek(t(20), [anchor()], pages)
    assert proof.verified_page == 56
    assert calls[0] == 50


def test_case3_predicted_page_too_old_moves_newer():
    pages = {
        50: PageProbe(50, t(17), t(18), 100_000, 80),
        48: PageProbe(48, t(19), t(20), 100_000, 80),
    }
    proof, calls = seek(t(20), [anchor()], pages)
    assert proof.verified_page == 48
    assert calls[0] == 50


def test_case4_missing_source_count_uses_local_anchor_seek():
    pages = {
        50: PageProbe(50, t(22), t(23), None, 80),
        52: PageProbe(52, t(20), t(21), None, 80),
    }
    proof, calls = seek(t(20), [anchor(count=None)], pages)
    assert proof.verified_page == 52
    assert calls[0] == 50


def test_case5_stale_anchor_requires_live_verification():
    pages = {50: PageProbe(50, t(19), t(20), 100_000, 80)}
    proof, calls = seek(t(20), [anchor(page=50)], pages)
    assert calls == [50]
    assert proof.verified_page == 50


def test_case6_no_anchor_uses_bounded_nonsequential_fallback():
    pages = {
        1: PageProbe(1, t(30), t(31), 100_000, 80),
        3: PageProbe(3, t(20), t(21), 100_000, 80),
    }
    proof, calls = seek(t(20), [], pages)
    assert proof.verified_page == 3
    assert calls == [1, 3]


def test_case7_probe_limit_is_clean_failure():
    def probe(page: int) -> PageProbe:
        return PageProbe(page, t(30), t(31), None, 80)
    with pytest.raises(SeekFailure):
        seek_historical_page(target_to=t(20), anchors=[], probe=probe, max_probes=3)


def test_case8_seek_proof_is_usable_for_traversal_start():
    pages = {10: PageProbe(10, t(19), t(20), None, 80)}
    proof, _ = seek(t(20), [anchor(page=10, count=None)], pages)
    assert proof.start_page == 9
    assert proof.probe_count == 1


def test_case8b_time_seek_then_traversal_can_establish_coverage(tmp_path):
    stock = "600001"
    store = SimplePostStore(tmp_path / "collector.db")
    try:
        store.save_page_anchor(anchor(page=5, count=1))
    finally:
        store.close()
    routes = {
        EastmoneyGubaCollector.list_url(stock, 4): synthetic_page(("4001", "2026-07-22 12:00:00")),
        EastmoneyGubaCollector.list_url(stock, 5): synthetic_page(
            ("5001", "2026-07-19 12:00:00"), ("5002", "2026-07-20 12:00:00")
        ),
        EastmoneyGubaCollector.list_url(stock, 6): synthetic_page(("6001", "2026-07-01 12:00:00")),
        EastmoneyGubaCollector.list_url(stock, 7): synthetic_page(),
    }
    transport = MappingTransport(routes)
    result = execute_and_persist_simple_backfill_collection(
        db_path=tmp_path / "collector.db", stock_code=stock,
        from_time=t(1), to_time=t(20), transport=transport,
        collector_config=CollectorConfig(min_interval_seconds=2.5),
        sleep_fn=lambda _: None, clock=lambda: t(30), enable_time_seek=True,
    )
    assert transport.calls[0] == EastmoneyGubaCollector.list_url(stock, 5)
    assert transport.calls[1] == EastmoneyGubaCollector.list_url(stock, 4)
    assert result.execution.range_complete is True
    store = SimplePostStore(tmp_path / "collector.db")
    try:
        assert store.coverage_ranges("eastmoney_guba", stock) == [(t(1), t(20))]
    finally:
        store.close()


def test_case9_manual_start_is_not_time_seek_eligible(tmp_path):
    store = SimplePostStore(tmp_path / "collector.db")
    try:
        plan = plan_backfill(store, source="eastmoney_guba", stock_code="600001",
                             from_time=t(1), to_time=t(20), explicit_start_page=50,
                             started_at=t(30))
        assert plan.start_page == 50
        assert plan.time_seek_eligible is False
    finally:
        store.close()


def test_case10_resume_and_coverage_remain_higher_priority(tmp_path):
    store = SimplePostStore(tmp_path / "collector.db")
    try:
        store.save_backfill_resume("eastmoney_guba", "600001", t(1), t(20), 7)
        plan = plan_backfill(store, source="eastmoney_guba", stock_code="600001",
                             from_time=t(1), to_time=t(20), started_at=t(30))
        assert plan.start_page == 8
        assert plan.time_seek_eligible is False
        store.clear_backfill_resume("eastmoney_guba", "600001", t(1), t(20))
        store.add_coverage("eastmoney_guba", "600001", t(1), t(20))
        covered = plan_backfill(store, source="eastmoney_guba", stock_code="600001",
                                from_time=t(5), to_time=t(10), started_at=t(30))
        assert covered.already_covered is True
    finally:
        store.close()
