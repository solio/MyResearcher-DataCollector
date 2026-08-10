"""Offline acquisition tests using local fixture responses only."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from myresearcher_collector.models import CollectionStatus
from myresearcher_collector.sources.eastmoney_guba import (
    CollectorConfig,
    EastmoneyGubaCollector,
    HttpResponse,
    InMemoryRawEvidenceStore,
)


FIXTURES = Path(__file__).parents[1] / "fixtures" / "eastmoney_guba"


def body(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class MappingTransport:
    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, *, timeout: float) -> object:
        self.calls.append(url)
        value = self.routes[url]
        if isinstance(value, list):
            current = value.pop(0)
        else:
            current = value
        if isinstance(current, BaseException):
            raise current
        return current


def response(name: str, status: int = 200) -> HttpResponse:
    return HttpResponse(status, body(name), {})


def collector(routes: dict[str, object], store: InMemoryRawEvidenceStore | None = None) -> tuple[EastmoneyGubaCollector, MappingTransport]:
    transport = MappingTransport(routes)
    result = EastmoneyGubaCollector(
        transport,
        evidence_store=store or InMemoryRawEvidenceStore(),
        config=CollectorConfig(max_pages=3, base_backoff_seconds=0),
        sleep_fn=lambda _: None,
        clock=lambda: datetime(2026, 8, 10, 3, 0, tzinfo=timezone.utc),
    )
    return result, transport


def urls() -> tuple[str, str, str, str]:
    return (
        EastmoneyGubaCollector.list_url("600001", 1),
        EastmoneyGubaCollector.list_url("600001", 2),
        EastmoneyGubaCollector.list_url("600001", 3),
        "https://guba.eastmoney.com/news,600001,1001.html",
    )


def test_collects_pages_details_and_overlap_idempotently() -> None:
    page1, page2, page3, detail1 = urls()
    detail2 = "https://guba.eastmoney.com/news,600001,1002.html"
    store = InMemoryRawEvidenceStore()
    run, transport = collector(
        {
            page1: response("list_page_1.html"),
            page2: response("list_page_2.html"),
            page3: response("empty_page.html"),
            detail1: response("detail_1001.html"),
            detail2: response("detail_1002.html"),
        },
        store,
    )

    result = run.collect("600001")

    assert result.status is CollectionStatus.SUCCESS
    assert [item.source_item_id for item in result.items] == ["1001", "1002"]
    assert result.counters.duplicate_records == 1
    assert result.counters.records_out_of_scope == 1
    assert result.counters.pages_success == 3
    assert result.counters.details_success == 2
    assert len(store.snapshots) == 5  # three list pages plus two details
    assert all(item.raw_ref["list"].startswith("memory://list/") for item in result.items)
    assert detail1 in transport.calls and detail2 in transport.calls


def test_watermark_confirmation_returns_no_new_data_without_detail_requests() -> None:
    page1, page2, _, detail1 = urls()
    run, transport = collector(
        {
            page1: response("list_page_1.html"),
            page2: response("list_page_2.html"),
            detail1: response("detail_1001.html"),
        }
    )

    result = run.collect(
        "600001",
        existing_ids={"1001"},
        watermark=datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc).replace(
            tzinfo=timezone.utc
        ),
        max_pages=2,
    )

    assert result.status is CollectionStatus.NO_NEW_DATA
    assert result.items == []
    assert result.counters.details_requested == 0
    assert detail1 not in transport.calls
    assert result.stop_reason == "watermark_confirmed"


def test_later_page_failure_is_partial_not_no_data() -> None:
    page1, page2, _, detail1 = urls()
    run, _ = collector(
        {
            page1: response("list_page_1.html"),
            page2: response("empty_page.html", status=503),
            detail1: response("detail_1001.html"),
        }
    )

    result = run.collect("600001", max_pages=2)

    assert result.status is CollectionStatus.PARTIAL_COLLECTION
    assert result.counters.pages_success == 1
    assert result.counters.pages_failed == 1
    assert result.failures == ["page 2: http_503"]


def test_first_page_schema_failure_is_spec_mismatch() -> None:
    page1, _, _, _ = urls()
    run, _ = collector({page1: response("malformed_page.html")})

    result = run.collect("600001", max_pages=1)

    assert result.status is CollectionStatus.SPEC_MISMATCH
    assert result.items == []
    assert result.counters.pages_failed == 1


def test_valid_empty_page_is_no_new_data() -> None:
    page1, _, _, _ = urls()
    run, _ = collector({page1: response("empty_page.html")})

    result = run.collect("600001", max_pages=1)

    assert result.status is CollectionStatus.NO_NEW_DATA
    assert result.stop_reason == "empty_page"
    assert result.counters.pages_success == 1


def test_detail_failure_is_partial_and_does_not_emit_title_as_body() -> None:
    page1, _, _, detail1 = urls()
    run, _ = collector(
        {
            page1: response("list_page_1.html"),
            detail1: response("detail_1001.html", status=403),
        }
    )

    result = run.collect("600001", max_pages=1)

    assert result.status is CollectionStatus.PARTIAL_COLLECTION
    assert result.items == []
    assert result.counters.details_failed == 1
    assert result.counters.records_failed == 1


def test_timeout_retries_then_succeeds() -> None:
    page1, _, _, detail1 = urls()
    run, transport = collector(
        {
            page1: [TimeoutError(), TimeoutError(), response("list_page_1.html")],
            detail1: response("detail_1001.html"),
        }
    )

    result = run.collect("600001", max_pages=1)

    assert result.status is CollectionStatus.PARTIAL_COLLECTION  # max-page cap is not a complete boundary
    assert result.counters.requests_total == 4
    assert result.counters.requests_failed == 2
    assert result.counters.requests_success == 2
    assert transport.calls.count(page1) == 3


def test_rate_limit_retries_without_becoming_no_data() -> None:
    page1, _, _, _ = urls()
    run, transport = collector(
        {page1: [response("empty_page.html", status=429), response("empty_page.html")]}
    )

    result = run.collect("600001", max_pages=1)

    assert result.status is CollectionStatus.NO_NEW_DATA
    assert result.counters.requests_failed == 1
    assert result.counters.requests_success == 1
    assert len(transport.calls) == 2
