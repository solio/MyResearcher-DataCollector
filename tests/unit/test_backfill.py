"""Deterministic Eastmoney Backfill v0.1 coverage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from myresearcher_collector.backfill import BackfillConfigError, resolve_backfill_range
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
    assert result.result.stop_reason == "backfill_range_complete" or result.result.stop_reason == "max_pages_reached"
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
    assert value.from_time.isoformat() == "2026-08-09T23:59:59.999999+00:00"
    assert value.to_time.isoformat() == "2026-08-11T23:59:59.999999+00:00"
