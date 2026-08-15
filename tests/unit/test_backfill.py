"""Deterministic Eastmoney Backfill v0.1 coverage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from myresearcher_collector.backfill import (
    BackfillConfigError,
    BackfillRange,
    resolve_backfill_range,
    resolve_effective_backfill_range,
)
from myresearcher_collector.models import CollectionStatus
from myresearcher_collector.sources.eastmoney_guba import (
    CollectorConfig,
    EastmoneyGubaCollector,
    HttpResponse,
    InMemoryRawEvidenceStore,
)


FIXTURES = Path(__file__).parents[1] / "fixtures" / "eastmoney_guba"


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


def synthetic_page(*rows: tuple[str, str]) -> HttpResponse:
    values = []
    links = []
    for item_id, published in rows:
        values.append({
            "post_id": int(item_id), "post_title": f"post {item_id}",
            "stockbar_code": "600001", "stockbar_name": "Synthetic",
            "user_id": f"u-{item_id}", "user_nickname": f"author-{item_id}",
            "post_click_count": 0, "post_forward_count": 0,
            "post_comment_count": 0, "post_publish_time": published,
            "post_last_time": published, "post_display_time": published,
            "post_type": 0, "post_state": 0, "post_top_status": 0,
            "post_source_id": "",
        })
        links.append(f'<a data-postid="{item_id}" href="/news,600001,{item_id}.html">x</a>')
    payload = {"rc": 1, "re": values, "count": len(values), "time": "synthetic"}
    html = f"{''.join(links)}<script>var article_list={json.dumps(payload)};</script>"
    return HttpResponse(200, html.encode(), {})


def synthetic_detail(item_id: str, published: str, content: str | None = None) -> HttpResponse:
    payload = {
        "post_id": int(item_id),
        "post_user": {"user_id": f"u-{item_id}", "user_nickname": f"author-{item_id}"},
        "post_guba": {"stockbar_code": "600001", "stockbar_name": "Synthetic"},
        "post_title": f"post {item_id}", "post_content": content or f"body-{item_id}",
        "post_publish_time": published, "post_last_time": published,
        "post_display_time": published, "post_click_count": 0,
        "post_forward_count": 0, "post_comment_count": 0, "post_like_count": 0,
        "post_type": 0, "post_state": 0, "post_top_status": 0, "post_source_id": "",
    }
    html = f"<script>var post_article={json.dumps(payload)};</script>"
    return HttpResponse(200, html.encode(), {})


def make_collector(routes: dict[str, object]) -> tuple[EastmoneyGubaCollector, MappingTransport]:
    transport = MappingTransport(routes)
    collector = EastmoneyGubaCollector(
        transport, evidence_store=InMemoryRawEvidenceStore(),
        config=CollectorConfig(max_pages=5, min_interval_seconds=2.5, base_backoff_seconds=0),
        sleep_fn=lambda _: None,
        clock=lambda: datetime(2026, 8, 11, tzinfo=timezone.utc),
    )
    return collector, transport


def test_bf_001_range_success_and_bf_003_in_range_details() -> None:
    p1 = EastmoneyGubaCollector.list_url("600001", 1)
    p2 = EastmoneyGubaCollector.list_url("600001", 2)
    p3 = EastmoneyGubaCollector.list_url("600001", 3)
    d1 = "https://guba.eastmoney.com/news,600001,1001.html"
    d2 = "https://guba.eastmoney.com/news,600001,1002.html"
    routes = {
        p1: synthetic_page(("1001", "2026-08-10 10:00:00")),
        p2: synthetic_page(("1002", "2026-08-09 10:00:00")),
        "https://guba.eastmoney.com/news,600001,1001.html": synthetic_detail("1001", "2026-08-10 10:00:00"),
        "https://guba.eastmoney.com/news,600001,1002.html": synthetic_detail("1002", "2026-08-09 10:00:00"),
        p3: synthetic_page(("1003", "2026-08-07 10:00:00")),
        d1: synthetic_detail("1001", "2026-08-10 10:00:00"),
        d2: synthetic_detail("1002", "2026-08-09 10:00:00"),
    }
    collector, transport = make_collector(routes)
    result = collector.collect_backfill(
        "600001", from_time=datetime(2026, 8, 8, tzinfo=timezone.utc),
        to_time=datetime(2026, 8, 11, tzinfo=timezone.utc), max_pages=3,
    )
    assert result.result.status is CollectionStatus.SUCCESS
    assert result.result.stop_reason == "backfill_range_complete"
    assert result.range_complete is True
    assert result.records_in_range == 2
    assert len(result.result.items) == 2
    assert all("detail" in item.raw_ref for item in result.result.items)
    assert transport.calls[:2] == [p1, d1]


def test_bf_002_newer_than_to_is_list_only_and_bf_004_crosses_old_page() -> None:
    p1 = EastmoneyGubaCollector.list_url("600001", 1)
    p2 = EastmoneyGubaCollector.list_url("600001", 2)
    d1 = "https://guba.eastmoney.com/news,600001,1001.html"
    routes = {
        p1: synthetic_page(("1001", "2026-08-10 10:00:00")),
        p2: synthetic_page(("1002", "2026-08-01 10:00:00")),
        d1: synthetic_detail("1001", "2026-08-10 10:00:00"),
    }
    collector, transport = make_collector(routes)
    result = collector.collect_backfill(
        "600001", from_time=datetime(2026, 8, 2, tzinfo=timezone.utc),
        to_time=datetime(2026, 8, 5, tzinfo=timezone.utc), max_pages=2,
    )
    assert result.result.status is CollectionStatus.SUCCESS
    assert result.records_in_range == 0
    assert d1 not in transport.calls
    assert result.range_complete is True


def test_bf_005_known_ids_do_not_stop_and_bf_009_cap_is_partial() -> None:
    p1 = EastmoneyGubaCollector.list_url("600001", 1)
    p2 = EastmoneyGubaCollector.list_url("600001", 2)
    routes = {
        p1: synthetic_page(("1001", "2026-08-10 10:00:00")),
        p2: synthetic_page(("1002", "2026-08-09 10:00:00")),
        "https://guba.eastmoney.com/news,600001,1001.html": synthetic_detail("1001", "2026-08-10 10:00:00"),
        "https://guba.eastmoney.com/news,600001,1002.html": synthetic_detail("1002", "2026-08-09 10:00:00"),
    }
    collector, transport = make_collector(routes)
    result = collector.collect_backfill(
        "600001", from_time=datetime(2026, 8, 1, tzinfo=timezone.utc),
        to_time=datetime(2026, 8, 11, tzinfo=timezone.utc), max_pages=2,
    )
    assert result.result.status is CollectionStatus.PARTIAL_COLLECTION
    assert result.result.stop_reason == "max_pages_reached"
    assert transport.calls.count(p2) == 1


def test_bf_015_invalid_range_fails_before_transport() -> None:
    with pytest.raises(BackfillConfigError):
        resolve_backfill_range(
            source="eastmoney_guba", stock_code="600001",
            from_value="2026-08-10", to_value="2026-08-01",
        )
    with pytest.raises(BackfillConfigError):
        resolve_backfill_range(
            source="eastmoney_guba", stock_code="600001",
            from_value="2026-08-01", days=2,
        )


def test_days_resolves_explicit_inclusive_utc_boundaries() -> None:
    value = resolve_backfill_range(
        source="eastmoney_guba", stock_code="600001", days=2,
        now=datetime(2026, 8, 11, 13, 0, tzinfo=timezone.utc),
    )
    assert value.from_time.isoformat() == "2026-08-09T16:00:00+00:00"
    assert value.to_time.isoformat() == "2026-08-11T15:59:59.999999+00:00"


def test_effective_range_is_clamped_once_to_run_start() -> None:
    requested = BackfillRange(
        "eastmoney_guba", "600001",
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 14, 23, 59, 59, tzinfo=timezone.utc),
    )
    effective = resolve_effective_backfill_range(
        requested, datetime(2026, 8, 13, 4, tzinfo=timezone.utc)
    )
    assert effective.from_time == requested.from_time
    assert effective.to_time == datetime(2026, 8, 13, 4, tzinfo=timezone.utc)


def test_effective_range_keeps_historical_requested_end() -> None:
    requested = BackfillRange(
        "eastmoney_guba", "600001",
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 10, tzinfo=timezone.utc),
    )
    assert resolve_effective_backfill_range(
        requested, datetime(2026, 8, 13, tzinfo=timezone.utc)
    ) == requested


def test_effective_range_rejects_interval_entirely_in_the_future() -> None:
    requested = BackfillRange(
        "eastmoney_guba", "600001",
        datetime(2026, 8, 14, tzinfo=timezone.utc),
        datetime(2026, 8, 14, 23, 59, 59, tzinfo=timezone.utc),
    )
    with pytest.raises(BackfillConfigError, match="begins after run start"):
        resolve_effective_backfill_range(
            requested, datetime(2026, 8, 13, 4, tzinfo=timezone.utc)
        )


def test_bf_016_overlap_detail_and_emission_are_deduplicated_per_run() -> None:
    pages = [EastmoneyGubaCollector.list_url("600001", page) for page in (1, 2, 3)]
    rows = {
        "1001": "2026-08-10 10:00:00", "1002": "2026-08-10 09:00:00",
        "1003": "2026-08-10 08:00:00", "1004": "2026-08-09 10:00:00",
        "1005": "2026-08-09 09:00:00", "1006": "2026-08-07 09:00:00",
    }
    routes: dict[str, object] = {
        pages[0]: synthetic_page(("1001", rows["1001"]), ("1002", rows["1002"]), ("1003", rows["1003"])),
        pages[1]: synthetic_page(("1003", rows["1003"]), ("1004", rows["1004"]), ("1005", rows["1005"])),
        pages[2]: synthetic_page(("1006", rows["1006"])),
    }
    for item_id in rows:
        routes[f"https://guba.eastmoney.com/news,600001,{item_id}.html"] = synthetic_detail(item_id, rows[item_id])
    collector, transport = make_collector(routes)
    result = collector.collect_backfill(
        "600001", from_time=datetime(2026, 8, 8, tzinfo=timezone.utc),
        to_time=datetime(2026, 8, 11, tzinfo=timezone.utc), max_pages=3,
    )
    assert result.result.status is CollectionStatus.SUCCESS
    assert [item.source_item_id for item in result.result.items] == ["1001", "1002", "1003", "1004", "1005"]
    assert transport.calls.count("https://guba.eastmoney.com/news,600001,1003.html") == 1


def test_bf_017_detail_schema_mismatch_is_spec_mismatch() -> None:
    page = EastmoneyGubaCollector.list_url("600001", 1)
    detail_url = "https://guba.eastmoney.com/news,600001,1001.html"
    collector, _transport = make_collector({
        page: synthetic_page(("1001", "2026-08-10 10:00:00")),
        detail_url: HttpResponse(200, b"<script>var post_article={};</script>", {}),
    })
    result = collector.collect_backfill(
        "600001", from_time=datetime(2026, 8, 8, tzinfo=timezone.utc),
        to_time=datetime(2026, 8, 11, tzinfo=timezone.utc), max_pages=1,
    )
    assert result.result.status is CollectionStatus.SPEC_MISMATCH
    assert result.result.stop_reason == "detail_schema_mismatch"
    assert result.result.counters.details_requested == 1
    assert result.result.counters.details_success == 0
    assert result.result.counters.details_failed == 1


def test_bf_018_date_only_range_is_host_timezone_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    before = resolve_backfill_range(
        source="eastmoney_guba", stock_code="600001",
        from_value="2026-05-01", to_value="2026-05-02",
    )
    if hasattr(time, "tzset"):
        monkeypatch.setenv("TZ", "UTC")
        time.tzset()
    after = resolve_backfill_range(
        source="eastmoney_guba", stock_code="600001",
        from_value="2026-05-01", to_value="2026-05-02",
    )
    assert before.from_time == after.from_time == datetime(2026, 4, 30, 16, tzinfo=timezone.utc)
    assert before.to_time == after.to_time == datetime(2026, 5, 2, 15, 59, 59, 999999, tzinfo=timezone.utc)
def test_bf_019_detail_counters_reconcile_success_and_failure() -> None:
    p1 = EastmoneyGubaCollector.list_url("600001", 1)
    p2 = EastmoneyGubaCollector.list_url("600001", 2)
    failed = "https://guba.eastmoney.com/news,600001,1003.html"
    routes: dict[str, object] = {
        p1: synthetic_page(("1001", "2026-08-10 10:00:00"), ("1002", "2026-08-10 09:00:00"), ("1003", "2026-08-10 08:00:00")),
        p2: synthetic_page(("1004", "2026-08-07 08:00:00")),
        "https://guba.eastmoney.com/news,600001,1001.html": synthetic_detail("1001", "2026-08-10 10:00:00"),
        "https://guba.eastmoney.com/news,600001,1002.html": synthetic_detail("1002", "2026-08-10 09:00:00"),
        failed: [HttpResponse(503, b"failure", {})] * 3,
    }
    collector, _transport = make_collector(routes)
    result = collector.collect_backfill(
        "600001", from_time=datetime(2026, 8, 8, tzinfo=timezone.utc),
        to_time=datetime(2026, 8, 11, tzinfo=timezone.utc), max_pages=2,
    )
    counters = result.result.counters
    assert counters.details_requested == 3
    assert counters.details_success == 2
    assert counters.details_failed == 1
    assert counters.details_requested == counters.details_success + counters.details_failed


def test_bf_020_all_candidate_details_failed_is_collection_failed() -> None:
    p1 = EastmoneyGubaCollector.list_url("600001", 1)
    p2 = EastmoneyGubaCollector.list_url("600001", 2)
    routes: dict[str, object] = {
        p1: synthetic_page(
            ("1001", "2026-08-10 10:00:00"),
            ("1002", "2026-08-10 09:00:00"),
            ("1003", "2026-08-10 08:00:00"),
        ),
        p2: synthetic_page(("1004", "2026-08-07 08:00:00")),
    }
    for item_id in ("1001", "1002", "1003"):
        routes[f"https://guba.eastmoney.com/news,600001,{item_id}.html"] = [
            HttpResponse(503, b"retry exhausted", {})
        ] * 3
    collector, _transport = make_collector(routes)
    result = collector.collect_backfill(
        "600001", from_time=datetime(2026, 8, 8, tzinfo=timezone.utc),
        to_time=datetime(2026, 8, 11, tzinfo=timezone.utc), max_pages=2,
    )
    counters = result.result.counters
    assert counters.details_requested == 3
    assert counters.details_success == 0
    assert counters.details_failed == 3
    assert result.result.status is CollectionStatus.COLLECTION_FAILED
    assert result.result.stop_reason == "all_candidate_details_failed"
    assert result.range_complete is False
