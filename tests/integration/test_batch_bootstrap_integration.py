"""Combined Batch check for fresh bootstrap and established checkpoints."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import myresearcher_collector.batch as batch_module
from myresearcher_collector.batch import execute_batch_collection, validate_targets
from myresearcher_collector.integration import execute_and_persist_collection
from myresearcher_collector.sources.eastmoney_guba import (
    BOOTSTRAP_MIN_PAGES,
    CollectorConfig,
    EastmoneyGubaCollector,
    HttpResponse,
)
from myresearcher_collector.storage import RawEvidenceStore, SQLitePersistence


UTC = timezone.utc
SOURCE_TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 8, 11, 6, 0, tzinfo=UTC)
FRESH_A = "600519"
FRESH_B = "300750"
EXISTING_C = "002594"


class MappingTransport:
    def __init__(
        self,
        stock_code: str,
        routes: dict[str, HttpResponse],
        trace: list[tuple[str, str]],
    ) -> None:
        self.stock_code = stock_code
        self.routes = routes
        self.trace = trace
        self.calls: list[str] = []

    def get(self, url: str, *, timeout: float) -> HttpResponse:
        del timeout
        self.calls.append(url)
        self.trace.append((self.stock_code, url))
        return self.routes[url]


def source_time(value: str) -> str:
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=SOURCE_TZ
    )
    return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def list_url(stock_code: str, page: int) -> str:
    return EastmoneyGubaCollector.list_url(stock_code, page)


def detail_url(stock_code: str, item_id: str) -> str:
    return f"https://guba.eastmoney.com/news,{stock_code},{item_id}.html"


def row(stock_code: str, item_id: str, published_at: str) -> dict[str, object]:
    return {
        "post_id": int(item_id),
        "post_title": f"post {item_id}",
        "stockbar_code": stock_code,
        "stockbar_name": f"Synthetic {stock_code}",
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


def list_page(stock_code: str, item: dict[str, object]) -> HttpResponse:
    item_id = item["post_id"]
    link = (
        f'<a data-postid="{item_id}" '
        f'href="/news,{stock_code},{item_id}.html">post {item_id}</a>'
    )
    payload = {"rc": 1, "re": [item], "count": 1, "time": "synthetic"}
    html = (
        "<!doctype html><html><body>"
        f"{link}<script>var article_list={json.dumps(payload)};</script>"
        "</body></html>"
    )
    return HttpResponse(200, html.encode(), {"content-type": "text/html"})


def detail_page(
    stock_code: str, item_id: str, published_at: str
) -> HttpResponse:
    payload = {
        "post_id": int(item_id),
        "post_user": {
            "user_id": f"u-{item_id}",
            "user_nickname": f"author-{item_id}",
        },
        "post_guba": {
            "stockbar_code": stock_code,
            "stockbar_name": f"Synthetic {stock_code}",
        },
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
    return HttpResponse(200, html.encode(), {"content-type": "text/html"})


def items(stock_code: str) -> list[tuple[str, str]]:
    return [
        (f"9{stock_code}1", "2026-08-11 12:00:00"),
        (f"9{stock_code}2", "2026-08-11 11:00:00"),
        (f"9{stock_code}3", "2026-08-11 10:00:00"),
    ]


def bootstrap_routes(stock_code: str) -> dict[str, HttpResponse]:
    routes: dict[str, HttpResponse] = {}
    for page, (item_id, published_at) in enumerate(items(stock_code), start=1):
        routes[list_url(stock_code, page)] = list_page(
            stock_code, row(stock_code, item_id, published_at)
        )
        routes[detail_url(stock_code, item_id)] = detail_page(
            stock_code, item_id, published_at
        )
    return routes


def incremental_routes(stock_code: str) -> dict[str, HttpResponse]:
    known = items(stock_code)
    return {
        list_url(stock_code, page): list_page(
            stock_code, row(stock_code, item_id, published_at)
        )
        for page, (item_id, published_at) in enumerate(known[:2], start=1)
    }


def test_batch_bootstraps_fresh_scopes_and_keeps_existing_scope_incremental(
    tmp_path: Path, monkeypatch
) -> None:
    data_root = tmp_path / "combined-data"
    config = CollectorConfig(
        max_pages=BOOTSTRAP_MIN_PAGES,
        min_interval_seconds=2.5,
        base_backoff_seconds=0,
    )

    seed_trace: list[tuple[str, str]] = []
    seed_transport = MappingTransport(
        EXISTING_C, bootstrap_routes(EXISTING_C), seed_trace
    )
    seed = execute_and_persist_collection(
        db_path=data_root / "collector.db",
        raw_data_dir=data_root,
        stock_code=EXISTING_C,
        transport=seed_transport,
        run_id="seed-existing-c",
        collector_config=config,
        clock=lambda: NOW,
        sleep_fn=lambda _: None,
        max_pages=BOOTSTRAP_MIN_PAGES,
    )
    assert seed.result.status.value == "SUCCESS", seed.result.as_dict()

    real_execute = execute_and_persist_collection

    def fast_execute(**kwargs):
        return real_execute(
            **kwargs, clock=lambda: NOW, sleep_fn=lambda _: None
        )

    monkeypatch.setattr(batch_module, "execute_and_persist_collection", fast_execute)
    trace: list[tuple[str, str]] = []
    transports: dict[str, MappingTransport] = {}

    def transport_factory(stock_code: str) -> MappingTransport:
        routes = (
            incremental_routes(stock_code)
            if stock_code == EXISTING_C
            else bootstrap_routes(stock_code)
        )
        transport = MappingTransport(stock_code, routes, trace)
        transports[stock_code] = transport
        return transport

    targets = validate_targets({
        "source": "eastmoney_guba",
        "stocks": [FRESH_A, FRESH_B, EXISTING_C],
    })
    summary = execute_batch_collection(
        targets,
        data_root=data_root,
        collector_config=config,
        transport_factory=transport_factory,
        batch_id="batch-bootstrap-combined",
    )

    assert summary.targets_total == summary.targets_completed == 3
    assert summary.targets_success == 3
    assert summary.targets_partial == summary.targets_failed == 0
    assert [result.stock_code for result in summary.per_stock] == [
        FRESH_A, FRESH_B, EXISTING_C
    ]
    assert [result.status for result in summary.per_stock] == [
        "SUCCESS", "SUCCESS", "NO_NEW_DATA"
    ]
    assert [stock for stock, _ in trace] == (
        [FRESH_A] * 6 + [FRESH_B] * 6 + [EXISTING_C] * 2
    )
    assert len(transports[FRESH_A].calls) == BOOTSTRAP_MIN_PAGES * 2
    assert len(transports[FRESH_B].calls) == BOOTSTRAP_MIN_PAGES * 2
    assert transports[EXISTING_C].calls == [
        list_url(EXISTING_C, 1), list_url(EXISTING_C, 2)
    ]

    store = SQLitePersistence(
        data_root / "collector.db",
        RawEvidenceStore(data_root, source="eastmoney_guba"),
    )
    try:
        run_rows = store.conn.execute(
            """SELECT run_id, scope_key, status, watermark_before_utc
               FROM collection_runs WHERE run_id LIKE 'batch-bootstrap-combined-%'
               ORDER BY rowid"""
        ).fetchall()
        assert run_rows == [
            (f"batch-bootstrap-combined-{FRESH_A}", f"stock:{FRESH_A}", "SUCCESS", None),
            (f"batch-bootstrap-combined-{FRESH_B}", f"stock:{FRESH_B}", "SUCCESS", None),
            (
                f"batch-bootstrap-combined-{EXISTING_C}",
                f"stock:{EXISTING_C}",
                "NO_NEW_DATA",
                source_time("2026-08-11 12:00:00"),
            ),
        ]
        checkpoints = dict(store.conn.execute(
            "SELECT scope_key, watermark_utc FROM collector_checkpoints"
        ).fetchall())
        assert checkpoints == {
            f"stock:{stock_code}": source_time("2026-08-11 12:00:00")
            for stock_code in (FRESH_A, FRESH_B, EXISTING_C)
        }
        scope_counts = dict(store.conn.execute(
            """SELECT scope_key, count(*) FROM observation_scopes
               GROUP BY scope_key"""
        ).fetchall())
        assert scope_counts == {
            f"stock:{FRESH_A}": 3,
            f"stock:{FRESH_B}": 3,
            f"stock:{EXISTING_C}": 3,
        }
        evidence_counts = dict(store.conn.execute(
            """SELECT run_id, count(*) FROM raw_evidence
               WHERE run_id LIKE 'batch-bootstrap-combined-%'
               GROUP BY run_id"""
        ).fetchall())
        assert evidence_counts == {
            f"batch-bootstrap-combined-{FRESH_A}": 6,
            f"batch-bootstrap-combined-{FRESH_B}": 6,
            f"batch-bootstrap-combined-{EXISTING_C}": 2,
        }
    finally:
        store.close()
