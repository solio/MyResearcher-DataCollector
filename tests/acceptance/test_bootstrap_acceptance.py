"""Independent deterministic acceptance for frozen Eastmoney bootstrap semantics.

These cases intentionally exercise the public Collector -> persistence boundary.
They use no live network and do not derive expectations from implementation
details.  Case identifiers map directly to the frozen BST-001..BST-008 contract.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from myresearcher_collector.integration import execute_and_persist_collection
from myresearcher_collector.models import CollectionStatus, GubaSourceItem
from myresearcher_collector.sources.eastmoney_guba import (
    CollectorConfig,
    EastmoneyGubaCollector,
    HttpResponse,
)
from myresearcher_collector.storage import RawEvidenceStore, SQLitePersistence


UTC = timezone.utc
SOURCE_TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
STOCK_CODE = "600001"
SCOPE_KEY = f"stock:{STOCK_CODE}"


class ScriptedTransport:
    """Synchronous fake that records order and rejects threaded fan-out."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.calls: list[str] = []
        self.owner_thread = threading.get_ident()

    def get(self, url: str, *, timeout: float) -> HttpResponse:
        del timeout
        assert threading.get_ident() == self.owner_thread, (
            "bootstrap transport was invoked from a concurrent worker"
        )
        self.calls.append(url)
        assert url in self.routes, f"unexpected request: {url}"
        value = self.routes[url]
        if isinstance(value, list):
            assert value, f"exhausted scripted responses for {url}"
            value = value.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, HttpResponse)
        return value


def source_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SOURCE_TZ)


def utc_checkpoint(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def list_url(page: int) -> str:
    return EastmoneyGubaCollector.list_url(STOCK_CODE, page)


def detail_url(item_id: str) -> str:
    return f"https://guba.eastmoney.com/news,{STOCK_CODE},{item_id}.html"


def synthetic_row(item_id: str, published_at: str) -> dict[str, object]:
    return {
        "post_id": int(item_id),
        "post_title": f"post {item_id}",
        "stockbar_code": STOCK_CODE,
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


def list_page(*rows: dict[str, object], status: int = 200) -> HttpResponse:
    links = "".join(
        f'<a data-postid="{row["post_id"]}" '
        f'href="/news,{STOCK_CODE},{row["post_id"]}.html">'
        f'{row["post_title"]}</a>'
        for row in rows
    )
    payload = {"rc": 1, "re": list(rows), "count": len(rows), "time": "synthetic"}
    html = (
        "<!doctype html><html><body>"
        f"{links}<script>var article_list={json.dumps(payload)};</script>"
        "</body></html>"
    )
    return HttpResponse(status, html.encode(), {"content-type": "text/html"})


def detail_page(item_id: str, published_at: str, *, status: int = 200) -> HttpResponse:
    payload = {
        "post_id": int(item_id),
        "post_user": {
            "user_id": f"u-{item_id}",
            "user_nickname": f"author-{item_id}",
        },
        "post_guba": {
            "stockbar_code": STOCK_CODE,
            "stockbar_name": "Synthetic Bar",
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
    return HttpResponse(status, html.encode(), {"content-type": "text/html"})


def routes_for_items(items: list[tuple[str, str]]) -> dict[str, object]:
    routes: dict[str, object] = {}
    for page, (item_id, published_at) in enumerate(items, start=1):
        routes[list_url(page)] = list_page(synthetic_row(item_id, published_at))
        routes[detail_url(item_id)] = detail_page(item_id, published_at)
    return routes


def expected_bootstrap_calls(items: list[tuple[str, str]]) -> list[str]:
    calls: list[str] = []
    for page, (item_id, _) in enumerate(items, start=1):
        calls.extend((list_url(page), detail_url(item_id)))
    return calls


def run_collection(
    tmp_path: Path,
    run_id: str,
    transport: ScriptedTransport,
    *,
    max_pages: int = 3,
):
    return execute_and_persist_collection(
        db_path=tmp_path / "collector.db",
        raw_data_dir=tmp_path / "data",
        stock_code=STOCK_CODE,
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


def reopen(tmp_path: Path) -> SQLitePersistence:
    return SQLitePersistence(
        tmp_path / "collector.db",
        RawEvidenceStore(tmp_path / "data", source="eastmoney_guba"),
    )


def persisted_run_state(tmp_path: Path, run_id: str) -> tuple[object, ...]:
    store = reopen(tmp_path)
    try:
        row = store.conn.execute(
            """SELECT status, watermark_before_utc, watermark_after_utc,
                      safe_frontier_utc
               FROM collection_runs WHERE run_id=?""",
            (run_id,),
        ).fetchone()
        assert row is not None
        return tuple(row)
    finally:
        store.close()


def persisted_checkpoint(tmp_path: Path) -> tuple[str | None, str | None] | None:
    store = reopen(tmp_path)
    try:
        return store.checkpoint("eastmoney_guba", SCOPE_KEY)
    finally:
        store.close()


def make_seed_item(item_id: str, published_at: str) -> GubaSourceItem:
    published = source_time(published_at)
    return GubaSourceItem(
        source="eastmoney_guba",
        schema_version="eastmoney_guba.raw.v1",
        source_item_id=item_id,
        requested_bar_code=STOCK_CODE,
        canonical_bar_code=STOCK_CODE,
        canonical_bar_name="Synthetic Bar",
        author_id=f"u-{item_id}",
        author_name=f"author-{item_id}",
        title=f"post {item_id}",
        content=f"body {item_id}",
        published_at=published,
        last_updated_at=published,
        display_time=published,
        url=detail_url(item_id),
        post_type=0,
        post_state=0,
        post_top_status=0,
        read_count=0,
        reply_count=0,
        like_count=0,
        forward_count=0,
        source_post_id="",
        collected_at=NOW,
        source_times_raw={
            "post_publish_time": published_at,
            "post_last_time": published_at,
            "post_display_time": published_at,
        },
        source_metadata={"extra": {}},
        raw_ref={},
    )


def seed_observation_without_checkpoint(
    tmp_path: Path,
    *,
    item_id: str,
    published_at: str,
) -> None:
    raw_store = RawEvidenceStore(tmp_path / "data", source="eastmoney_guba")
    store = SQLitePersistence(tmp_path / "collector.db", raw_store)
    run_id = "prior-partial"
    try:
        store.start_run(
            run_id,
            "eastmoney_guba",
            SCOPE_KEY,
            started_at=NOW,
            collector_version="seed.collector.v1",
            parser_version="seed.parser.v1",
            schema_version="eastmoney_guba.raw.v1",
        )
        evidence_links: list[tuple[str, str]] = []
        for ordinal, role in enumerate(("list", "detail")):
            attempt_id = f"{run_id}-attempt-{ordinal}"
            evidence_id = f"{run_id}-evidence-{ordinal}"
            request_url = list_url(1) if role == "list" else detail_url(item_id)
            store.record_attempt(
                run_id,
                attempt_id,
                ordinal=ordinal,
                request_kind=role,
                request_url=request_url,
                started_at=NOW,
                finished_at=NOW,
                outcome="success",
                retry_number=1,
                retry_budget=3,
                http_status=200,
            )
            published = raw_store.publish(
                run_id, ordinal, f"prior {role} evidence".encode()
            )
            store.record_raw_evidence(
                run_id,
                attempt_id,
                evidence_id,
                published,
                evidence_kind=role,
                request_url=request_url,
                final_url=request_url,
                fetched_at=NOW,
                http_status=200,
                content_type="text/html",
            )
            evidence_links.append((evidence_id, role))
        store.persist_result(
            run_id,
            [(make_seed_item(item_id, published_at), SCOPE_KEY, evidence_links)],
            status="PARTIAL_COLLECTION",
            finished_at=NOW,
            safe_frontier=None,
        )
        assert store.known_item_ids("eastmoney_guba", SCOPE_KEY) == {item_id}
        assert store.checkpoint("eastmoney_guba", SCOPE_KEY) is None
    finally:
        store.close()


def test_bst001_clean_bootstrap_success_commits_initial_checkpoint(
    tmp_path: Path,
) -> None:
    items = [
        ("4101", "2026-08-10 09:00:00"),
        ("4102", "2026-08-10 11:00:00"),
        ("4103", "2026-08-10 10:00:00"),
    ]
    transport = ScriptedTransport(routes_for_items(items))

    execution = run_collection(tmp_path, "bst-001", transport)

    frontier = source_time("2026-08-10 11:00:00")
    assert execution.result.status is CollectionStatus.SUCCESS
    assert execution.result.stop_reason == "bootstrap_complete"
    assert execution.result.safe_frontier == frontier
    assert execution.result.counters.pages_success == 3
    assert execution.result.counters.details_success == 3
    assert transport.calls == expected_bootstrap_calls(items)
    assert persisted_checkpoint(tmp_path) == (utc_checkpoint(frontier), "bst-001")
    assert persisted_run_state(tmp_path, "bst-001") == (
        "SUCCESS",
        None,
        utc_checkpoint(frontier),
        utc_checkpoint(frontier),
    )


def test_bst002_bootstrap_detail_failure_keeps_checkpoint_null(
    tmp_path: Path,
) -> None:
    items = [
        ("4201", "2026-08-10 12:00:00"),
        ("4202", "2026-08-10 11:00:00"),
        ("4203", "2026-08-10 10:00:00"),
    ]
    routes = routes_for_items(items)
    routes[detail_url("4203")] = [
        detail_page("4203", items[2][1], status=503) for _ in range(3)
    ]
    transport = ScriptedTransport(routes)

    execution = run_collection(tmp_path, "bst-002", transport)

    assert execution.result.status is not CollectionStatus.SUCCESS
    assert execution.result.status is not CollectionStatus.NO_NEW_DATA
    assert execution.result.counters.details_failed == 1
    assert transport.calls == [
        list_url(1),
        detail_url("4201"),
        list_url(2),
        detail_url("4202"),
        list_url(3),
        detail_url("4203"),
        detail_url("4203"),
        detail_url("4203"),
    ]
    assert persisted_checkpoint(tmp_path) is None
    assert persisted_run_state(tmp_path, "bst-002")[1:3] == (None, None)


def test_bst003_bootstrap_page_failure_keeps_checkpoint_null(tmp_path: Path) -> None:
    items = [
        ("4301", "2026-08-10 12:00:00"),
        ("4302", "2026-08-10 11:00:00"),
    ]
    routes = routes_for_items(items)
    routes[list_url(3)] = [list_page(status=503) for _ in range(3)]
    transport = ScriptedTransport(routes)

    execution = run_collection(tmp_path, "bst-003", transport)

    assert execution.result.status is not CollectionStatus.SUCCESS
    assert execution.result.status is not CollectionStatus.NO_NEW_DATA
    assert execution.result.counters.pages_failed == 1
    assert transport.calls == [
        list_url(1),
        detail_url("4301"),
        list_url(2),
        detail_url("4302"),
        list_url(3),
        list_url(3),
        list_url(3),
    ]
    assert persisted_checkpoint(tmp_path) is None
    assert persisted_run_state(tmp_path, "bst-003")[1:3] == (None, None)


def test_bst004_prior_observations_without_checkpoint_restart_full_bootstrap(
    tmp_path: Path,
) -> None:
    prior = ("4401", "2026-08-10 15:00:00")
    seed_observation_without_checkpoint(
        tmp_path, item_id=prior[0], published_at=prior[1]
    )
    items = [
        prior,
        ("4402", "2026-08-10 11:00:00"),
        ("4403", "2026-08-10 10:00:00"),
    ]
    transport = ScriptedTransport(routes_for_items(items))

    execution = run_collection(tmp_path, "bst-004", transport)

    frontier = source_time(prior[1])
    assert execution.result.status is CollectionStatus.SUCCESS
    assert execution.result.stop_reason == "bootstrap_complete"
    assert execution.result.safe_frontier == frontier
    assert transport.calls == expected_bootstrap_calls(items)
    assert transport.calls[0] == list_url(1)
    assert persisted_checkpoint(tmp_path) == (utc_checkpoint(frontier), "bst-004")


def test_bst005_insufficient_bootstrap_window_never_establishes_checkpoint(
    tmp_path: Path,
) -> None:
    items = [
        ("4501", "2026-08-10 12:00:00"),
        ("4502", "2026-08-10 11:00:00"),
    ]
    transport = ScriptedTransport(routes_for_items(items))

    try:
        execution = run_collection(
            tmp_path, "bst-005", transport, max_pages=2
        )
    except ValueError as exc:
        message = str(exc).lower()
        assert "bootstrap" in message and "max_pages" in message and "3" in message
        assert transport.calls == []
    else:
        assert execution.result.status is not CollectionStatus.SUCCESS
        assert execution.result.status is not CollectionStatus.NO_NEW_DATA

    assert persisted_checkpoint(tmp_path) is None


def test_bst006_initial_frontier_is_newest_resolved_publication_time(
    tmp_path: Path,
) -> None:
    items = [
        ("4601", "2026-08-10 09:30:00"),
        ("4602", "2026-08-10 16:45:00"),
        ("4603", "2026-08-10 08:15:00"),
    ]
    transport = ScriptedTransport(routes_for_items(items))

    execution = run_collection(tmp_path, "bst-006", transport)

    expected = source_time("2026-08-10 16:45:00")
    assert execution.result.status is CollectionStatus.SUCCESS
    assert execution.result.safe_frontier == expected
    assert execution.result.safe_frontier != source_time(items[2][1])
    assert execution.result.safe_frontier != NOW
    assert persisted_checkpoint(tmp_path) == (utc_checkpoint(expected), "bst-006")


def test_bst007_unknown_old_id_remains_eligible_after_bootstrap(
    tmp_path: Path,
) -> None:
    bootstrap_items = [
        ("4701", "2026-08-10 12:00:00"),
        ("4702", "2026-08-10 11:00:00"),
        ("4703", "2026-08-10 10:00:00"),
    ]
    first = ScriptedTransport(routes_for_items(bootstrap_items))
    initial = run_collection(tmp_path, "bst-007-bootstrap", first)
    checkpoint_before = persisted_checkpoint(tmp_path)
    assert initial.result.status is CollectionStatus.SUCCESS
    assert checkpoint_before == (
        utc_checkpoint(source_time(bootstrap_items[0][1])),
        "bst-007-bootstrap",
    )

    unknown = ("4799", "2026-08-10 09:00:00")
    routes = {
        list_url(1): list_page(synthetic_row(*unknown)),
        detail_url(unknown[0]): detail_page(*unknown),
        list_url(2): list_page(synthetic_row(*bootstrap_items[1])),
        list_url(3): list_page(synthetic_row(*bootstrap_items[2])),
    }
    second_transport = ScriptedTransport(routes)

    incremental = run_collection(
        tmp_path, "bst-007-incremental", second_transport
    )

    assert incremental.result.status is CollectionStatus.SUCCESS
    assert incremental.result.stop_reason == "watermark_confirmed"
    assert [item.source_item_id for item in incremental.result.items] == [unknown[0]]
    assert second_transport.calls == [
        list_url(1),
        detail_url(unknown[0]),
        list_url(2),
        list_url(3),
    ]
    store = reopen(tmp_path)
    try:
        assert store.conn.execute(
            """SELECT count(*) FROM source_item_observations
               WHERE source='eastmoney_guba' AND source_item_id=?""",
            (unknown[0],),
        ).fetchone()[0] == 1
        checkpoint_after = store.checkpoint("eastmoney_guba", SCOPE_KEY)
        assert checkpoint_after is not None
        assert checkpoint_after[0] == checkpoint_before[0]
    finally:
        store.close()


def test_bst008_successful_bootstrap_transitions_to_ordinary_incremental(
    tmp_path: Path,
) -> None:
    bootstrap_items = [
        ("4801", "2026-08-10 12:00:00"),
        ("4802", "2026-08-10 11:00:00"),
        ("4803", "2026-08-10 10:00:00"),
    ]
    first = ScriptedTransport(routes_for_items(bootstrap_items))
    initial = run_collection(tmp_path, "bst-008-bootstrap", first)
    checkpoint_before = persisted_checkpoint(tmp_path)
    assert initial.result.status is CollectionStatus.SUCCESS
    assert checkpoint_before is not None

    second_transport = ScriptedTransport({
        list_url(1): list_page(synthetic_row(*bootstrap_items[0])),
        list_url(2): list_page(synthetic_row(*bootstrap_items[1])),
    })
    incremental = run_collection(
        tmp_path, "bst-008-incremental", second_transport
    )

    assert incremental.result.status is CollectionStatus.NO_NEW_DATA
    assert incremental.result.stop_reason == "watermark_confirmed"
    assert incremental.result.items == []
    assert second_transport.calls == [list_url(1), list_url(2)]
    assert persisted_checkpoint(tmp_path) == (
        checkpoint_before[0],
        "bst-008-incremental",
    )
