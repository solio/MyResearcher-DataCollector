"""Deterministic Collector → SQLite/raw-files integration executions."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path

import pytest

from myresearcher_collector.integration import execute_and_persist_collection
from myresearcher_collector.models import CollectionStatus
from myresearcher_collector.sources.eastmoney_guba import CollectorConfig, HttpResponse, EastmoneyGubaCollector
from myresearcher_collector.storage import RawEvidenceStore, SQLitePersistence


FIXTURES = Path(__file__).parents[1] / "fixtures" / "eastmoney_guba"
NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


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


def response(name: str, status: int = 200) -> HttpResponse:
    return HttpResponse(
        status,
        (FIXTURES / name).read_bytes(),
        {"content-type": "text/html"},
    )


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


def synthetic_list_page(rows: list[dict[str, object]]) -> HttpResponse:
    import json

    links = "".join(
        f'<a data-postid="{row["post_id"]}" href="/news,600001,{row["post_id"]}.html">{row["post_title"]}</a>'
        for row in rows
    )
    payload = {"rc": 1, "re": rows, "count": len(rows), "time": "synthetic"}
    html = f"<!doctype html><html><body>{links}<script>var article_list={json.dumps(payload)};</script></body></html>"
    return HttpResponse(200, html.encode(), {"content-type": "text/html"})


def synthetic_detail(item_id: str, published_at: str, status: int = 200) -> HttpResponse:
    import json

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
    return HttpResponse(status, html.encode(), {"content-type": "text/html"})


def urls() -> tuple[str, str, str, str, str]:
    return (
        EastmoneyGubaCollector.list_url("600001", 1),
        EastmoneyGubaCollector.list_url("600001", 2),
        EastmoneyGubaCollector.list_url("600001", 3),
        "https://guba.eastmoney.com/news,600001,1001.html",
        "https://guba.eastmoney.com/news,600001,1002.html",
    )


def config(max_pages: int = 3) -> CollectorConfig:
    return CollectorConfig(max_pages=max_pages, min_interval_seconds=2.5, base_backoff_seconds=0)


def run_one(tmp_path: Path, run_id: str, transport: MappingTransport, *, max_pages: int = 3):
    return execute_and_persist_collection(
        db_path=tmp_path / "collector.db",
        raw_data_dir=tmp_path / "data",
        stock_code="600001",
        transport=transport,
        run_id=run_id,
        collector_config=config(max_pages),
        clock=lambda: NOW,
        sleep_fn=lambda _: None,
        max_pages=max_pages,
    )


def reopen(tmp_path: Path) -> SQLitePersistence:
    return SQLitePersistence(
        tmp_path / "collector.db",
        RawEvidenceStore(tmp_path / "data", source="eastmoney_guba"),
    )


def test_happy_path_persists_actual_execution_and_reopens(tmp_path: Path) -> None:
    page1, page2, page3, detail1, detail2 = urls()
    transport = MappingTransport({
        page1: response("list_page_1.html"),
        page2: response("list_page_2.html"),
        page3: response("empty_page.html"),
        detail1: [response("detail_1001.html"), response("detail_1001.html")],
        detail2: response("detail_1002.html"),
    })

    execution = run_one(tmp_path, "run-happy", transport)
    assert execution.result.status is CollectionStatus.SUCCESS
    assert execution.result.stop_reason == "bootstrap_complete"
    assert execution.result.safe_frontier == datetime(
        2026, 8, 10, 2, 0, tzinfo=timezone.utc
    )
    assert execution.attempt_count == 6
    assert execution.evidence_count == 6
    assert execution.failure_count == 0

    store = reopen(tmp_path)
    try:
        assert store.conn.execute("SELECT status FROM collection_runs WHERE run_id='run-happy'").fetchone()[0] == "SUCCESS"
        assert store.conn.execute("SELECT count(*) FROM collection_attempts WHERE run_id='run-happy'").fetchone()[0] == 6
        assert store.conn.execute("SELECT count(*) FROM raw_evidence WHERE run_id='run-happy'").fetchone()[0] == 6
        assert store.conn.execute("SELECT count(*) FROM source_item_observations WHERE source='eastmoney_guba'").fetchone()[0] == 2
        assert store.conn.execute("SELECT count(*) FROM observation_evidence").fetchone()[0] == 6
        assert store.conn.execute("SELECT count(*) FROM observation_scopes WHERE scope_key='stock:600001'").fetchone()[0] == 2
        evidence_ids = [row[0] for row in store.conn.execute("SELECT evidence_id FROM raw_evidence WHERE run_id='run-happy'")]
        assert all(store.verify_evidence(evidence_id).exists() for evidence_id in evidence_ids)
        first_raw = store.conn.execute(
            "SELECT content_sha256, byte_size FROM raw_evidence WHERE evidence_id='run-happy-evidence-0'"
        ).fetchone()
        list_bytes = (FIXTURES / "list_page_1.html").read_bytes()
        assert first_raw == (hashlib.sha256(list_bytes).hexdigest(), len(list_bytes))
        assert store.checkpoint("eastmoney_guba", "stock:600001") == (
            "2026-08-10T02:00:00.000000Z",
            "run-happy",
        )
    finally:
        store.close()
    assert (tmp_path / "data" / "raw" / "eastmoney_guba").is_dir()


def test_bootstrap_retry_ignores_prior_observations_until_checkpoint_exists(
    tmp_path: Path,
) -> None:
    page1, page2, page3, detail1, _ = urls()
    first_transport = MappingTransport({
        page1: response("list_page_1.html"),
        page2: [response("empty_page.html", status=503)] * 3,
        detail1: response("detail_1001.html"),
    })

    first = run_one(tmp_path, "bootstrap-partial", first_transport)

    assert first.result.status is CollectionStatus.PARTIAL_COLLECTION
    assert first.result.safe_frontier is None
    store = reopen(tmp_path)
    try:
        assert store.checkpoint("eastmoney_guba", "stock:600001") is None
        assert store.conn.execute(
            "SELECT count(*) FROM source_item_observations"
        ).fetchone()[0] == 1
    finally:
        store.close()

    retry_transport = MappingTransport({
        page1: response("list_page_1.html"),
        page2: response("empty_page.html"),
        page3: response("empty_page.html"),
        detail1: response("detail_1001.html"),
    })
    retry = run_one(tmp_path, "bootstrap-retry", retry_transport)

    assert retry.result.status is CollectionStatus.SUCCESS
    assert retry.result.stop_reason == "bootstrap_complete"
    assert retry.result.items == []
    assert retry.result.safe_frontier == datetime(
        2026, 8, 10, 2, 0, tzinfo=timezone.utc
    )
    assert retry_transport.calls[0] == page1
    assert retry_transport.calls == [page1, detail1, page2, page3]
    store = reopen(tmp_path)
    try:
        assert store.checkpoint("eastmoney_guba", "stock:600001") == (
            "2026-08-10T02:00:00.000000Z",
            "bootstrap-retry",
        )
        assert store.conn.execute(
            "SELECT count(*) FROM source_item_observations"
        ).fetchone()[0] == 1
    finally:
        store.close()


def test_short_bootstrap_configuration_fails_before_network_or_run(
    tmp_path: Path,
) -> None:
    transport = MappingTransport({})

    with pytest.raises(ValueError, match="bootstrap requires max_pages >= 3"):
        run_one(tmp_path, "bootstrap-too-short", transport, max_pages=2)

    assert transport.calls == []
    store = reopen(tmp_path)
    try:
        assert store.checkpoint("eastmoney_guba", "stock:600001") is None
        assert store.conn.execute(
            "SELECT count(*) FROM collection_runs"
        ).fetchone()[0] == 0
    finally:
        store.close()


def test_incremental_execution_reads_ids_and_checkpoint_and_returns_no_new_data(tmp_path: Path) -> None:
    page1, page2, page3, detail1, detail2 = urls()
    first = run_one(
        tmp_path,
        "run-first",
        MappingTransport({
            page1: response("list_page_1.html"),
            page2: response("list_page_2.html"),
            page3: response("empty_page.html"),
            detail1: [response("detail_1001.html"), response("detail_1001.html")],
            detail2: response("detail_1002.html"),
        }),
    )
    assert first.result.status is CollectionStatus.SUCCESS
    before = reopen(tmp_path)
    try:
        checkpoint = before.checkpoint("eastmoney_guba", "stock:600001")
    finally:
        before.close()
    assert checkpoint is not None

    transport = MappingTransport({page1: response("list_page_1.html"), page2: response("list_page_2.html")})
    second = run_one(tmp_path, "run-second", transport, max_pages=2)
    assert second.result.status is CollectionStatus.NO_NEW_DATA
    assert second.result.items == []
    assert detail1 not in transport.calls and detail2 not in transport.calls

    store = reopen(tmp_path)
    try:
        assert store.conn.execute("SELECT status FROM collection_runs WHERE run_id='run-second'").fetchone()[0] == "NO_NEW_DATA"
        assert store.checkpoint("eastmoney_guba", "stock:600001") == (checkpoint[0], "run-second")
    finally:
        store.close()


def test_partial_runtime_safe_prefix_advances_checkpoint_exactly_to_prefix(tmp_path: Path) -> None:
    page1, page2, page3, detail1, _ = urls()
    seed = run_one(
        tmp_path,
        "run-seed",
        MappingTransport({
            page1: response("list_page_1.html"),
            page2: response("empty_page.html"),
            page3: response("empty_page.html"),
            detail1: response("detail_1001.html"),
        }),
        max_pages=3,
    )
    assert seed.result.status is CollectionStatus.SUCCESS
    before = reopen(tmp_path)
    try:
        checkpoint_before = before.checkpoint("eastmoney_guba", "stock:600001")
    finally:
        before.close()
    assert checkpoint_before is not None

    new_page = synthetic_list_page([synthetic_row("1003", "2026-08-10 12:00:00")])
    new_detail = synthetic_detail("1003", "2026-08-10 12:00:00")
    later_page = synthetic_list_page([synthetic_row("1004", "2026-08-10 13:00:00")])
    later_detail = synthetic_detail("1004", "2026-08-10 13:00:00", status=503)
    new_page_url = EastmoneyGubaCollector.list_url("600001", 1)
    second_page_url = EastmoneyGubaCollector.list_url("600001", 2)
    detail_url = "https://guba.eastmoney.com/news,600001,1003.html"
    transport = MappingTransport({
        new_page_url: new_page,
        second_page_url: later_page,
        detail_url: [new_detail, synthetic_detail("1003", "2026-08-10 12:00:00", status=503)]
        + [synthetic_detail("1003", "2026-08-10 12:00:00", status=503)] * 2,
        "https://guba.eastmoney.com/news,600001,1004.html": [later_detail] * 3,
    })
    partial = run_one(tmp_path, "run-partial-safe", transport, max_pages=2)
    assert partial.result.status is CollectionStatus.PARTIAL_COLLECTION
    assert partial.result.safe_frontier is not None
    assert partial.result.safe_frontier > datetime(2026, 8, 10, 2, 0, tzinfo=timezone.utc)
    assert partial.failure_count == 1

    store = reopen(tmp_path)
    try:
        checkpoint_after = store.checkpoint("eastmoney_guba", "stock:600001")
        expected_frontier = partial.result.safe_frontier.astimezone(timezone.utc).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z")
        assert checkpoint_after == (
            expected_frontier,
            "run-partial-safe",
        )
        assert store.conn.execute(
            "SELECT status, watermark_after_utc FROM collection_runs WHERE run_id='run-partial-safe'"
        ).fetchone() == ("PARTIAL_COLLECTION", checkpoint_after[0])
        failure = store.conn.execute(
            "SELECT evidence_id FROM collection_failures WHERE run_id='run-partial-safe'"
        ).fetchone()
        assert failure[0] is not None and store.verify_evidence(failure[0]).exists()
    finally:
        store.close()


def test_partial_execution_persists_failure_and_does_not_cross_checkpoint(tmp_path: Path) -> None:
    page1, page2, _, detail1, _ = urls()
    first = run_one(
        tmp_path,
        "run-first",
        MappingTransport({
            page1: response("list_page_1.html"),
            page2: response("list_page_2.html"),
            EastmoneyGubaCollector.list_url("600001", 3): response("empty_page.html"),
            detail1: [response("detail_1001.html"), response("detail_1001.html")],
            "https://guba.eastmoney.com/news,600001,1002.html": response("detail_1002.html"),
        }),
    )
    assert first.result.status is CollectionStatus.SUCCESS
    before = reopen(tmp_path)
    try:
        checkpoint = before.checkpoint("eastmoney_guba", "stock:600001")
    finally:
        before.close()
    assert checkpoint is not None

    transport = MappingTransport({
        page1: response("list_page_1.html"),
        page2: [response("empty_page.html", status=503)] * 3,
    })
    partial = run_one(tmp_path, "run-partial", transport, max_pages=2)
    assert partial.result.status is CollectionStatus.PARTIAL_COLLECTION
    assert partial.failure_count == 1
    assert partial.evidence_count == 4

    store = reopen(tmp_path)
    try:
        assert store.conn.execute("SELECT status FROM collection_runs WHERE run_id='run-partial'").fetchone()[0] == "PARTIAL_COLLECTION"
        failure = store.conn.execute(
            "SELECT attempt_id, evidence_id, failure_class FROM collection_failures WHERE run_id='run-partial'"
        ).fetchone()
        assert failure[0] is not None and failure[1] is not None and failure[2] == "http_503"
        assert store.verify_evidence(failure[1]).exists()
        assert store.checkpoint("eastmoney_guba", "stock:600001") == checkpoint
    finally:
        store.close()
