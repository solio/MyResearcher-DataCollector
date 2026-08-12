"""Offline acquisition tests using local fixture responses only."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

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


def synthetic_list_page(rows: list[dict[str, object]]) -> HttpResponse:
    links = "".join(
        f'<a data-postid="{row["post_id"]}" href="/news,600001,{row["post_id"]}.html">{row["post_title"]}</a>'
        for row in rows
    )
    payload = {"rc": 1, "re": rows, "count": len(rows), "time": "synthetic"}
    html = f"<!doctype html><html><body>{links}<script>var article_list={json.dumps(payload)};</script></body></html>"
    return HttpResponse(200, html.encode(), {})


def synthetic_detail(item_id: str, published_at: str) -> HttpResponse:
    payload = {
        "post_id": int(item_id),
        "post_user": {"user_id": f"u-{item_id}", "user_nickname": f"author-{item_id}"},
        "post_guba": {"stockbar_code": "600001", "stockbar_name": "Synthetic Bar"},
        "post_title": f"post {item_id}",
        "post_content": f"body {item_id}",
        "post_publish_time": published_at,
        "post_last_time": published_at,
        "post_display_time": published_at,
        "post_click_count": 0,
        "post_forward_count": 0,
        "post_comment_count": 0,
        "post_like_count": 0,
        "post_type": 0,
        "post_state": 0,
        "post_top_status": 0,
        "post_source_id": "",
    }
    html = f"<!doctype html><html><body><script>var post_article={json.dumps(payload)};</script></body></html>"
    return HttpResponse(200, html.encode(), {})


def synthetic_row(item_id: str, published_at: str) -> dict[str, object]:
    return {
        "post_id": int(item_id),
        "post_title": f"post {item_id}",
        "stockbar_code": "600001",
        "stockbar_name": "Synthetic Bar",
        "user_id": f"u-{item_id}",
        "user_nickname": f"author-{item_id}",
        "post_click_count": 0,
        "post_forward_count": 0,
        "post_comment_count": 0,
        "post_publish_time": published_at,
        "post_last_time": published_at,
        "post_display_time": published_at,
        "post_type": 0,
        "post_state": 0,
        "post_top_status": 0,
        "post_source_id": "",
    }


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


def test_fresh_bootstrap_completes_exact_three_page_window_and_declares_frontier() -> None:
    page1, page2, page3, _ = urls()
    rows = [
        synthetic_row("3001", "2026-08-10 10:00:00"),
        synthetic_row("3002", "2026-08-10 12:00:00"),
        synthetic_row("3003", "2026-08-10 11:00:00"),
    ]
    detail_urls = [
        f"https://guba.eastmoney.com/news,600001,{row['post_id']}.html"
        for row in rows
    ]
    run, transport = collector({
        page1: synthetic_list_page([rows[0]]),
        page2: synthetic_list_page([rows[1]]),
        page3: synthetic_list_page([rows[2]]),
        detail_urls[0]: synthetic_detail("3001", "2026-08-10 10:00:00"),
        detail_urls[1]: synthetic_detail("3002", "2026-08-10 12:00:00"),
        detail_urls[2]: synthetic_detail("3003", "2026-08-10 11:00:00"),
    })

    result = run.collect("600001", max_pages=8)

    expected_frontier = datetime(
        2026, 8, 10, 12, 0, tzinfo=timezone(timedelta(hours=8))
    )
    assert result.status is CollectionStatus.SUCCESS
    assert result.stop_reason == "bootstrap_complete"
    assert result.safe_frontier == expected_frontier
    assert result.watermark == expected_frontier
    assert result.counters.pages_requested == 3
    assert transport.calls == [
        page1, detail_urls[0], page2, detail_urls[1], page3, detail_urls[2]
    ]


def test_bootstrap_detail_failure_never_declares_initial_frontier() -> None:
    page1, page2, page3, _ = urls()
    rows = [
        synthetic_row("3101", "2026-08-10 10:00:00"),
        synthetic_row("3102", "2026-08-10 11:00:00"),
        synthetic_row("3103", "2026-08-10 12:00:00"),
    ]
    routes: dict[str, object] = {
        page1: synthetic_list_page([rows[0]]),
        page2: synthetic_list_page([rows[1]]),
        page3: synthetic_list_page([rows[2]]),
    }
    for row in rows[:2]:
        item_id = str(row["post_id"])
        routes[f"https://guba.eastmoney.com/news,600001,{item_id}.html"] = (
            synthetic_detail(item_id, str(row["post_publish_time"]))
        )
    failed_detail = "https://guba.eastmoney.com/news,600001,3103.html"
    routes[failed_detail] = [HttpResponse(503, b"synthetic failure", {})] * 3
    run, _ = collector(routes)

    result = run.collect("600001", max_pages=3)

    assert result.status is CollectionStatus.PARTIAL_COLLECTION
    assert result.safe_frontier is None
    assert result.watermark is None
    assert result.counters.pages_success == 3
    assert result.counters.details_failed == 1


@pytest.mark.parametrize("max_pages", [1, 2])
def test_bootstrap_rejects_short_window_before_network(
    max_pages: int,
) -> None:
    run, transport = collector({})

    with pytest.raises(ValueError, match="bootstrap requires max_pages >= 3"):
        run.collect("600001", max_pages=max_pages)

    assert transport.calls == []


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
        existing_ids={"1001", "1002"},
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

    result = run.collect("600001", max_pages=2, bootstrap=False)

    assert result.status is CollectionStatus.PARTIAL_COLLECTION
    assert result.counters.pages_success == 1
    assert result.counters.pages_failed == 1
    assert result.failures == ["page 2: http_503"]


def test_first_page_schema_failure_is_spec_mismatch() -> None:
    page1, _, _, _ = urls()
    store = InMemoryRawEvidenceStore()
    run, _ = collector({page1: response("malformed_page.html")}, store)

    result = run.collect("600001", max_pages=1, bootstrap=False)

    assert result.status is CollectionStatus.SPEC_MISMATCH
    assert result.items == []
    assert result.counters.pages_failed == 1
    assert len(store.snapshots) == 1


def test_http200_verification_retries_then_normal_article_list_succeeds() -> None:
    page1, page2, _, detail1 = urls()
    run, transport = collector(
        {
            page1: [response("verification_page.html"), response("list_page_1.html")],
            page2: response("empty_page.html"),
            detail1: response("detail_1001.html"),
        }
    )

    result = run.collect("600001", max_pages=2, bootstrap=False)

    assert result.status is CollectionStatus.SUCCESS
    assert result.stop_reason == "empty_page"
    assert result.counters.requests_total == 4
    assert result.counters.requests_failed == 1
    assert result.counters.requests_success == 3
    assert transport.calls == [page1, page1, detail1, page2]


def test_http200_verification_twice_is_collection_failed_on_first_page() -> None:
    page1, _, _, _ = urls()
    run, transport = collector(
        {page1: [response("verification_page.html"), response("verification_page.html")]}
    )

    result = run.collect("600001", max_pages=1, bootstrap=False)

    assert result.status is CollectionStatus.COLLECTION_FAILED
    assert result.stop_reason == "page_failure"
    assert result.failures == ["page 1: access_block"]
    assert result.counters.requests_total == 2
    assert result.counters.requests_failed == 2
    assert result.counters.requests_success == 0
    assert transport.calls == [page1, page1]


def test_http200_ordinary_malformed_html_remains_spec_mismatch() -> None:
    page1, _, _, _ = urls()
    run, transport = collector({page1: response("malformed_page.html")})

    result = run.collect("600001", max_pages=1, bootstrap=False)

    assert result.status is CollectionStatus.SPEC_MISMATCH
    assert result.stop_reason == "schema_mismatch"
    assert result.counters.requests_total == 1
    assert result.counters.requests_failed == 0
    assert result.counters.requests_success == 1
    assert transport.calls == [page1]


def test_valid_empty_page_is_no_new_data() -> None:
    page1, _, _, _ = urls()
    run, _ = collector({page1: response("empty_page.html")})

    result = run.collect("600001", max_pages=1, bootstrap=False)

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

    result = run.collect("600001", max_pages=1, bootstrap=False)

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

    result = run.collect("600001", max_pages=1, bootstrap=False)

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

    result = run.collect("600001", max_pages=1, bootstrap=False)

    assert result.status is CollectionStatus.NO_NEW_DATA
    assert result.counters.requests_failed == 1
    assert result.counters.requests_success == 1
    assert len(transport.calls) == 2


def test_429_uses_three_attempt_retry_budget() -> None:
    page1, _, _, _ = urls()
    run, transport = collector({
        page1: [response("empty_page.html", status=429)] * 3,
    })

    result = run.collect("600001", max_pages=1, bootstrap=False)

    assert result.status is CollectionStatus.COLLECTION_FAILED
    assert result.counters.requests_total == 3
    assert result.counters.requests_failed == 3
    assert len(transport.calls) == 3


def test_5xx_uses_three_attempt_retry_budget() -> None:
    page1, _, _, _ = urls()
    run, transport = collector({
        page1: [response("empty_page.html", status=503)] * 3,
    })

    result = run.collect("600001", max_pages=1, bootstrap=False)

    assert result.status is CollectionStatus.COLLECTION_FAILED
    assert result.counters.requests_total == 3
    assert result.counters.requests_failed == 3
    assert len(transport.calls) == 3


def test_403_keeps_two_attempt_access_block_budget() -> None:
    page1, _, _, _ = urls()
    run, transport = collector({
        page1: [response("empty_page.html", status=403)] * 3,
    })

    result = run.collect("600001", max_pages=1, bootstrap=False)

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

    result = run.collect("600001", max_pages=2, bootstrap=False)

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


def test_unknown_id_at_or_before_watermark_is_still_eligible() -> None:
    page1, _, _, detail1 = urls()
    run, transport = collector({
        page1: response("list_page_1.html"),
        detail1: response("detail_1001.html"),
    })

    result = run.collect(
        "600001",
        existing_ids=set(),
        watermark=datetime(2026, 8, 10, 11, 0, tzinfo=timezone(timedelta(hours=8))),
        max_pages=1,
    )

    assert result.status is CollectionStatus.PARTIAL_COLLECTION
    assert [item.source_item_id for item in result.items] == ["1001"]
    assert result.counters.details_requested == 1
    assert detail1 in transport.calls


def test_mixed_incremental_page_handles_known_and_unknown_old_and_new_ids() -> None:
    page1 = EastmoneyGubaCollector.list_url("600001", 1)
    rows = [
        synthetic_row("1001", "2026-08-10 10:00:00"),  # known old
        synthetic_row("1002", "2026-08-10 10:05:00"),  # unknown old
        synthetic_row("1003", "2026-08-10 12:00:00"),  # known newer
        synthetic_row("1004", "2026-08-10 12:05:00"),  # unknown newer
    ]
    routes: dict[str, object] = {page1: synthetic_list_page(rows)}
    for item_id, published_at in (
        ("1002", "2026-08-10 10:05:00"),
        ("1003", "2026-08-10 12:00:00"),
        ("1004", "2026-08-10 12:05:00"),
    ):
        routes[f"https://guba.eastmoney.com/news,600001,{item_id}.html"] = synthetic_detail(item_id, published_at)
    run, transport = collector(routes)

    result = run.collect(
        "600001",
        existing_ids={"1001", "1003"},
        watermark=datetime(2026, 8, 10, 11, 0, tzinfo=timezone(timedelta(hours=8))),
        max_pages=1,
    )

    assert result.status is CollectionStatus.PARTIAL_COLLECTION
    assert [item.source_item_id for item in result.items] == ["1002", "1003", "1004"]
    assert result.counters.details_requested == 3
    assert "https://guba.eastmoney.com/news,600001,1001.html" not in transport.calls
    assert all(
        f"https://guba.eastmoney.com/news,600001,{item_id}.html" in transport.calls
        for item_id in ("1002", "1003", "1004")
    )


def test_historical_known_first_page_does_not_stop_before_unknown_second_page() -> None:
    page1 = EastmoneyGubaCollector.list_url("600001", 1)
    page2 = EastmoneyGubaCollector.list_url("600001", 2)
    page3 = EastmoneyGubaCollector.list_url("600001", 3)
    old_known = synthetic_row("1001", "2026-08-10 10:00:00")
    old_unknown = synthetic_row("1002", "2026-08-10 10:05:00")
    detail2 = "https://guba.eastmoney.com/news,600001,1002.html"
    run, transport = collector({
        page1: synthetic_list_page([old_known]),
        page2: synthetic_list_page([old_unknown]),
        page3: synthetic_list_page([]),
        detail2: synthetic_detail("1002", "2026-08-10 10:05:00"),
    })

    result = run.collect(
        "600001",
        existing_ids={"1001"},
        watermark=datetime(2026, 8, 10, 11, 0, tzinfo=timezone(timedelta(hours=8))),
        max_pages=3,
    )

    assert result.status is CollectionStatus.SUCCESS
    assert result.stop_reason == "empty_page"
    assert [item.source_item_id for item in result.items] == ["1002"]
    assert result.counters.details_requested == 1
    assert page2 in transport.calls
    assert detail2 in transport.calls


def test_retry_exhaustion_is_collection_failed() -> None:
    page1, _, _, _ = urls()
    run, transport = collector({page1: [TimeoutError(), TimeoutError(), TimeoutError()]})

    result = run.collect("600001", max_pages=1, bootstrap=False)

    assert result.status is CollectionStatus.COLLECTION_FAILED
    assert result.counters.requests_failed == 3
    assert transport.calls.count(page1) == 3


def test_max_pages_is_hard_partial_boundary() -> None:
    page1, _, _, detail1 = urls()
    run, _ = collector({
        page1: response("list_page_1.html"),
        detail1: response("detail_1001.html"),
    })

    result = run.collect("600001", max_pages=1, bootstrap=False)

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

    result = run.collect("600001", max_pages=1, bootstrap=False)

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


def test_collector_has_no_plain_http_fallback() -> None:
    with pytest.raises(RuntimeError, match="must be supplied explicitly"):
        EastmoneyGubaCollector()


def test_redirect_final_url_outside_source_boundary_fails_closed() -> None:
    page1, _, _, _ = urls()
    run, transport = collector({page1: response("empty_page.html", final_url="https://example.invalid/redirect")})

    result = run.collect("600001", max_pages=1, bootstrap=False)

    assert result.status is CollectionStatus.COLLECTION_FAILED
    assert result.failures == ["page 1: redirect"]
    assert transport.calls == [page1]


def test_cancellation_is_explicit_and_does_not_request() -> None:
    page1, _, _, _ = urls()
    run, transport = collector({page1: response("empty_page.html")})
    run.cancel_check = lambda: True

    result = run.collect("600001", max_pages=1, bootstrap=False)

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
