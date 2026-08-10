"""Offline acquisition tests using local fixture responses only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


def response(name: str, status: int = 200, headers: dict[str, str] | None = None, final_url: str | None = None) -> HttpResponse:
    return HttpResponse(status, body(name), headers or {}, final_url)


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
            detail1: response("detail_1001.html", final_url=detail1 + "?redirected=1"),
            detail2: response("detail_1002.html"),
        },
        store,
    )

    result = run.collect("600001")

    assert result.status is CollectionStatus.SUCCESS
    assert [item.source_item_id for item in result.items] == ["1001", "1001", "1002"]
    assert [item.observation_version for item in result.items] == [1, 2, 1]
    assert result.counters.duplicate_records == 1
    assert result.counters.identity_content_drifts == 1
    assert result.counters.records_out_of_scope == 1
    assert result.counters.pages_success == 3
    assert result.counters.details_success == 3
    assert len(store.snapshots) == 5  # three list pages plus two unique detail payloads
    assert all(item.raw_ref["list"].startswith("memory://list/") for item in result.items)
    assert result.items[0].schema_version == "eastmoney_guba.raw.v1"
    assert result.items[0].final_url == detail1 + "?redirected=1"
    assert result.items[0].source_metadata["final_urls"]["detail"] == detail1 + "?redirected=1"
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
    store = InMemoryRawEvidenceStore()
    run, _ = collector({page1: response("malformed_page.html")}, store)

    result = run.collect("600001", max_pages=1)

    assert result.status is CollectionStatus.SPEC_MISMATCH
    assert result.items == []
    assert result.counters.pages_failed == 1
    assert len(store.snapshots) == 1


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


def test_429_uses_three_attempt_retry_budget() -> None:
    page1, _, _, _ = urls()
    run, transport = collector({
        page1: [response("empty_page.html", status=429)] * 3,
    })

    result = run.collect("600001", max_pages=1)

    assert result.status is CollectionStatus.COLLECTION_FAILED
    assert result.counters.requests_total == 3
    assert result.counters.requests_failed == 3
    assert len(transport.calls) == 3


def test_5xx_uses_three_attempt_retry_budget() -> None:
    page1, _, _, _ = urls()
    run, transport = collector({
        page1: [response("empty_page.html", status=503)] * 3,
    })

    result = run.collect("600001", max_pages=1)

    assert result.status is CollectionStatus.COLLECTION_FAILED
    assert result.counters.requests_total == 3
    assert result.counters.requests_failed == 3
    assert len(transport.calls) == 3


def test_403_keeps_two_attempt_access_block_budget() -> None:
    page1, _, _, _ = urls()
    run, transport = collector({
        page1: [response("empty_page.html", status=403)] * 3,
    })

    result = run.collect("600001", max_pages=1)

    assert result.status is CollectionStatus.COLLECTION_FAILED
    assert result.counters.requests_total == 2
    assert len(transport.calls) == 2


def test_detail_content_drift_creates_new_observation_version() -> None:
    page1, page2, _, detail1 = urls()
    changed = body("detail_1001.html").replace(b"synthetic body one", b"changed detail body")
    run, transport = collector({
        page1: response("list_page_1.html"),
        page2: response("list_page_1.html"),
        detail1: [response("detail_1001.html"), HttpResponse(200, changed, {})],
    })

    result = run.collect("600001", max_pages=2)

    assert result.status is CollectionStatus.PARTIAL_COLLECTION
    assert result.counters.identity_content_drifts == 1
    assert result.counters.details_requested == 2
    assert [item.observation_version for item in result.items] == [1, 2]
    assert [item.content for item in result.items] == ["synthetic body one", "changed detail body"]
    assert transport.calls.count(detail1) == 2


def test_seen_id_newer_than_watermark_is_eligible() -> None:
    page1, page2, _, detail1 = urls()
    run, transport = collector({
        page1: response("list_page_1.html"),
        page2: response("empty_page.html"),
        detail1: response("detail_1001.html"),
    })

    result = run.collect(
        "600001",
        existing_ids={"1001"},
        watermark=datetime(2026, 8, 10, 9, 0, tzinfo=timezone(timedelta(hours=8))),
        max_pages=2,
    )

    assert result.status is CollectionStatus.SUCCESS
    assert len(result.items) == 1
    assert result.counters.details_requested == 1
    assert detail1 in transport.calls


def test_retry_exhaustion_is_collection_failed() -> None:
    page1, _, _, _ = urls()
    run, transport = collector({page1: [TimeoutError(), TimeoutError(), TimeoutError()]})

    result = run.collect("600001", max_pages=1)

    assert result.status is CollectionStatus.COLLECTION_FAILED
    assert result.counters.requests_failed == 3
    assert transport.calls.count(page1) == 3


def test_max_pages_is_hard_partial_boundary() -> None:
    page1, _, _, detail1 = urls()
    run, _ = collector({
        page1: response("list_page_1.html"),
        detail1: response("detail_1001.html"),
    })

    result = run.collect("600001", max_pages=1)

    assert result.status is CollectionStatus.PARTIAL_COLLECTION
    assert result.stop_reason == "max_pages"
    assert result.counters.pages_requested == 1
    assert result.counters.details_success == 1


def test_retry_after_and_bounded_backoff_are_observable() -> None:
    page1, _, _, _ = urls()
    sleeps: list[float] = []
    transport = MappingTransport({
        page1: [response("empty_page.html", status=429, headers={"Retry-After": "2"}), response("empty_page.html")]
    })
    run = EastmoneyGubaCollector(
        transport,
        config=CollectorConfig(max_pages=1, base_backoff_seconds=0.5, min_interval_seconds=2.5),
        sleep_fn=sleeps.append,
        monotonic_fn=lambda: 0.0,
        jitter_fn=lambda _low, _high: 0.0,
    )

    result = run.collect("600001", max_pages=1)

    assert result.status is CollectionStatus.NO_NEW_DATA
    assert 2.0 in sleeps
    assert 2.5 in sleeps
    assert max(sleeps) <= 30.0


def test_minimum_interval_cannot_be_disabled() -> None:
    page1, _, _, _ = urls()
    transport = MappingTransport({page1: response("empty_page.html")})
    try:
        EastmoneyGubaCollector(transport, config=CollectorConfig(min_interval_seconds=2.4))
    except ValueError as exc:
        assert "at least 2.5" in str(exc)
    else:
        raise AssertionError("interval below policy minimum was accepted")


def test_redirect_final_url_outside_source_boundary_fails_closed() -> None:
    page1, _, _, _ = urls()
    run, transport = collector({page1: response("empty_page.html", final_url="https://example.invalid/redirect")})

    result = run.collect("600001", max_pages=1)

    assert result.status is CollectionStatus.COLLECTION_FAILED
    assert result.failures == ["page 1: redirect"]
    assert transport.calls == [page1]


def test_cancellation_is_explicit_and_does_not_request() -> None:
    page1, _, _, _ = urls()
    run, transport = collector({page1: response("empty_page.html")})
    run.cancel_check = lambda: True

    result = run.collect("600001", max_pages=1)

    assert result.status is CollectionStatus.CANCELLED
    assert result.stop_reason == "cancelled"
    assert transport.calls == []


def test_partial_run_does_not_advance_watermark() -> None:
    page1, page2, _, detail1 = urls()
    original = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
    run, _ = collector({
        page1: response("list_page_1.html"),
        page2: response("empty_page.html", status=503),
        detail1: response("detail_1001.html"),
    })

    result = run.collect("600001", watermark=original, max_pages=2)

    assert result.status is CollectionStatus.PARTIAL_COLLECTION
    assert result.watermark == original
