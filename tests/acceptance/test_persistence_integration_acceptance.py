"""Independent final Collector -> persistence acceptance cases."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from myresearcher_collector.integration import execute_and_persist_collection
from myresearcher_collector.sources.eastmoney_guba import (
    CollectorConfig,
    EastmoneyGubaCollector,
    HttpResponse,
)
from myresearcher_collector.storage import RawEvidenceStore, SafeFrontier, SQLitePersistence


FIXTURES = Path(__file__).parents[1] / "fixtures" / "eastmoney_guba"
UTC = timezone.utc
NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
T0 = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
TMID = datetime(2026, 8, 10, 1, 0, tzinfo=UTC)
T1 = datetime(2026, 8, 10, 2, 0, tzinfo=UTC)


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
    links = "".join(
        f'<a data-postid="{row["post_id"]}" href="/news,600001,{row["post_id"]}.html">'
        f'{row["post_title"]}</a>'
        for row in rows
    )
    payload = {"rc": 1, "re": rows, "count": len(rows), "time": "synthetic"}
    html = (
        "<!doctype html><html><body>"
        f"{links}<script>var article_list={json.dumps(payload)};</script>"
        "</body></html>"
    )
    return HttpResponse(200, html.encode(), {"content-type": "text/html"})


def synthetic_detail(item_id: str, published_at: str, status: int = 200) -> HttpResponse:
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
    html = (
        "<!doctype html><html><body>"
        f"<script>var post_article={json.dumps(payload)};</script>"
        "</body></html>"
    )
    return HttpResponse(status, html.encode(), {"content-type": "text/html"})


def run_integration(
    tmp_path: Path,
    run_id: str,
    transport: MappingTransport,
    *,
    max_pages: int = 2,
):
    return execute_and_persist_collection(
        db_path=tmp_path / "collector.db",
        raw_data_dir=tmp_path / "data",
        stock_code="600001",
        transport=transport,
        run_id=run_id,
        collector_config=CollectorConfig(
            max_pages=max_pages,
            min_interval_seconds=2.5,
            base_backoff_seconds=0,
        ),
        clock=lambda: NOW,
        sleep_fn=lambda _: None,
        max_pages=max_pages,
    )


def seed_checkpoint(tmp_path: Path, watermark: datetime) -> None:
    raw = RawEvidenceStore(tmp_path / "data", source="eastmoney_guba")
    store = SQLitePersistence(tmp_path / "collector.db", raw)
    try:
        store.start_run(
            "seed-run",
            "eastmoney_guba",
            "stock:600001",
            started_at=NOW,
            collector_version="collector.v1",
            parser_version="parser.v1",
            schema_version="eastmoney_guba.raw.v1",
        )
        assert store.finish_run(
            "seed-run",
            status="SUCCESS",
            finished_at=NOW,
            safe_frontier=SafeFrontier(watermark),
        ) is True
    finally:
        store.close()


def reopen(tmp_path: Path) -> SQLitePersistence:
    return SQLitePersistence(
        tmp_path / "collector.db",
        RawEvidenceStore(tmp_path / "data", source="eastmoney_guba"),
    )


def test_pst021_unknown_old_id_is_not_suppressed_by_checkpoint_in_real_integration(tmp_path: Path) -> None:
    seed_checkpoint(tmp_path, T0)
    page1 = EastmoneyGubaCollector.list_url("600001", 1)
    page2 = EastmoneyGubaCollector.list_url("600001", 2)
    detail = "https://guba.eastmoney.com/news,600001,3001.html"
    transport = MappingTransport({
        page1: synthetic_list_page([synthetic_row("3001", "2026-08-10 08:00:00")]),
        page2: response("empty_page.html"),
        detail: synthetic_detail("3001", "2026-08-10 08:00:00"),
    })

    execution = run_integration(tmp_path, "unknown-old-integration", transport)
    assert detail in transport.calls
    assert execution.result.items
    assert execution.result.items[0].source_item_id == "3001"

    store = reopen(tmp_path)
    try:
        assert store.conn.execute(
            "SELECT source_item_id FROM source_item_observations"
        ).fetchall() == [("3001",)]
        assert store.conn.execute(
            "SELECT status FROM collection_runs WHERE run_id='unknown-old-integration'"
        ).fetchone()[0] == "SUCCESS"
        checkpoint = store.checkpoint("eastmoney_guba", "stock:600001")
        assert checkpoint == ("2026-08-10T00:00:00.000000Z", "unknown-old-integration")
    finally:
        store.close()


def test_pst020_partial_detail_failure_cannot_cross_unresolved_mid_page_item(tmp_path: Path) -> None:
    seed_checkpoint(tmp_path, T0)
    page1 = EastmoneyGubaCollector.list_url("600001", 1)
    page2 = EastmoneyGubaCollector.list_url("600001", 2)
    detail_a = "https://guba.eastmoney.com/news,600001,3101.html"
    detail_b = "https://guba.eastmoney.com/news,600001,3102.html"
    transport = MappingTransport({
        page1: synthetic_list_page([synthetic_row("3101", "2026-08-10 10:00:00")]),
        page2: synthetic_list_page([synthetic_row("3102", "2026-08-10 09:00:00")]),
        detail_a: synthetic_detail("3101", "2026-08-10 10:00:00"),
        detail_b: [synthetic_detail("3102", "2026-08-10 09:00:00", status=503)] * 3,
    })

    execution = run_integration(tmp_path, "cross-gap-integration", transport)
    assert execution.result.status.value == "PARTIAL_COLLECTION"
    assert execution.result.failures
    assert detail_b in transport.calls

    store = reopen(tmp_path)
    try:
        checkpoint = store.checkpoint("eastmoney_guba", "stock:600001")
        run_state = store.conn.execute(
            "SELECT status, watermark_after_utc FROM collection_runs WHERE run_id='cross-gap-integration'"
        ).fetchone()
        expected_checkpoint = ("2026-08-10T00:00:00.000000Z", "seed-run")
        runtime_frontier_ok = (
            execution.result.safe_frontier is None
            or execution.result.safe_frontier <= TMID
        )
        checkpoint_ok = checkpoint == expected_checkpoint and run_state == (
            "PARTIAL_COLLECTION", None
        )
        assert runtime_frontier_ok and checkpoint_ok, (
            f"runtime_safe_frontier={execution.result.safe_frontier!r}; "
            f"persisted_checkpoint={checkpoint!r}; run_state={run_state!r}"
        )
    finally:
        store.close()
