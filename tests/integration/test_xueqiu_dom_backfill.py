from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from myresearcher_collector.backfill import BackfillRange
from myresearcher_collector.cli.main import build_parser, execute_backfill_cli
from myresearcher_collector.simple_store import SimplePostStore
from myresearcher_collector.sources.xueqiu.dom_backfill import execute_xueqiu_dom_backfill
from myresearcher_collector.sources.xueqiu.dom_transport import XueqiuDomTransportError


UTC = timezone.utc
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
FROM = datetime(2026, 8, 14, 0, 0, tzinfo=UTC)
TO = datetime(2026, 8, 16, 4, 0, tzinfo=UTC)


def _raw(status_id: str, time_text: str, *, content: str | None = None) -> dict[str, object]:
    return {
        "status_id": status_id,
        "author_id": f"author-{status_id}",
        "author_name": "synthetic author",
        "url": f"https://xueqiu.com/author/{status_id}",
        "content": content or f"content-{status_id}",
        "title": None,
        "time_text_observed": time_text,
    }


class FakeDomTransport:
    def __init__(self, pages: dict[int, list[dict[str, object]]], details=None) -> None:
        self.pages = pages
        self.details = details or {}
        self.current = 1
        self.opened: list[str] = []
        self.goto_calls: list[int] = []
        self.restored: list[int] = []
        self.closed = False

    def open_stock(self, stock_code: str) -> None:
        self.opened.append(stock_code)
        self.current = 1

    def read_current_page(self) -> dict[str, object]:
        return {"page_no": self.current, "items": self.pages[self.current]}

    def goto_page(self, page_no: int, *, previous_ids=()):
        self.goto_calls.append(page_no)
        self.current = page_no
        return self.read_current_page()

    def read_detail_created_at(self, url: str):
        value = self.details[url]
        if isinstance(value, BaseException):
            raise value
        return value

    def restore_page(self, page_no: int, *, expected_ids=(), previous_ids=()):
        self.restored.append(page_no)
        self.current = page_no
        return self.read_current_page()

    def close(self) -> None:
        self.closed = True


def _request() -> BackfillRange:
    return BackfillRange("xueqiu", "601012", FROM, TO)


def test_rows_under_ten_continue_and_modified_detail_is_resolved(tmp_path: Path) -> None:
    modified_url = "https://xueqiu.com/author/modified"
    pages = {
        1: [
            _raw("new-1", "08/15 10:00"),
            _raw("modified", "修改于 08-14 16:57", content="edited body"),
        ],
        2: [_raw("old-1", "08/13 10:00")],
    }
    pages[1][1]["url"] = modified_url
    transport = FakeDomTransport(
        pages,
        {modified_url: {"id": "modified", "created_at": 1786607804000, "edited_at": 1786697834000}},
    )
    pacing: list[float] = []
    result = execute_xueqiu_dom_backfill(
        db_path=tmp_path / "collector.db", stock_code="601012", requested=_request(),
        transport=transport, sleep_fn=pacing.append, clock=lambda: NOW,
    )
    assert result.status == "SUCCESS"
    assert result.range_complete is True
    assert result.records_in_range == 1
    assert result.modified_posts_resolved == 1
    assert transport.goto_calls == [2]
    assert len(pacing) == 3  # detail, restore, and next-page navigation
    assert all(3.0 <= delay <= 10.0 for delay in pacing)
    store = SimplePostStore(tmp_path / "collector.db", read_only=True)
    try:
        rows = store.rows("xueqiu", "601012")
        assert [row["source_item_id"] for row in rows] == ["new-1"]
        assert store.coverage_ranges("xueqiu", "601012")
    finally:
        store.close()


def test_page_is_durable_before_later_pagination_failure(tmp_path: Path) -> None:
    class FailingTransport(FakeDomTransport):
        def goto_page(self, page_no: int, *, previous_ids=()):
            raise XueqiuDomTransportError("synthetic page failure")

    transport = FailingTransport({1: [_raw("one", "08/15 10:00")]})
    result = execute_xueqiu_dom_backfill(
        db_path=tmp_path / "collector.db", stock_code="601012", requested=_request(),
        transport=transport, sleep_fn=lambda _: None, clock=lambda: NOW,
    )
    assert result.status == "PARTIAL_COLLECTION"
    store = SimplePostStore(tmp_path / "collector.db", read_only=True)
    try:
        assert store.count("xueqiu", "601012") == 1
        assert store.backfill_resume_page("xueqiu", "601012", FROM, TO) == 1
    finally:
        store.close()


def test_duplicate_page_is_a_pagination_failure(tmp_path: Path) -> None:
    same = [_raw("same", "08/15 10:00")]
    transport = FakeDomTransport({1: same, 2: same})
    result = execute_xueqiu_dom_backfill(
        db_path=tmp_path / "collector.db", stock_code="601012", requested=_request(),
        transport=transport, sleep_fn=lambda _: None, clock=lambda: NOW,
        max_pages=2,
    )
    assert result.status == "COLLECTION_FAILED"
    assert result.stop_reason == "pagination_failure"


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    pages = {1: [_raw("one", "08/15 10:00")], 2: [_raw("old", "08/13 10:00")]}
    first = execute_xueqiu_dom_backfill(
        db_path=tmp_path / "collector.db", stock_code="601012", requested=_request(),
        transport=FakeDomTransport(pages), sleep_fn=lambda _: None, clock=lambda: NOW,
    )
    assert first.status == "SUCCESS"
    second = execute_xueqiu_dom_backfill(
        db_path=tmp_path / "collector.db", stock_code="601012", requested=_request(),
        transport=FakeDomTransport(pages), sleep_fn=lambda _: None, clock=lambda: NOW,
    )
    assert second.status == "SUCCESS"
    assert second.stop_reason == "already_covered"
    assert second.records_new == 0


def test_unified_backfill_cli_dispatches_xueqiu_dom_path(tmp_path: Path) -> None:
    args = build_parser().parse_args([
        "backfill", "--source", "xueqiu", "--stock", "601012",
        "--from", "2026-08-14T00:00:00Z", "--to", "2026-08-16T04:00:00Z",
        "--data-dir", str(tmp_path), "--min-interval", "3", "--max-interval", "3",
        "--confirm-live",
    ])
    transport = FakeDomTransport({1: [_raw("one", "08/15 10:00")], 2: [_raw("old", "08/13 10:00")]})
    summary = execute_backfill_cli(args, transport=transport, sleep_fn=lambda _: None)
    assert summary["source"] == "xueqiu"
    assert summary["status"] == "SUCCESS"
    assert transport.closed is True


def test_manual_unproven_start_page_does_not_create_reusable_resume(tmp_path: Path) -> None:
    first_transport = FakeDomTransport({
        51: [_raw("manual-51", "08/15 10:00")],
        52: [_raw("manual-old", "08/13 10:00")],
    })
    first = execute_xueqiu_dom_backfill(
        db_path=tmp_path / "collector.db", stock_code="601012", requested=_request(),
        transport=first_transport, start_page=51, sleep_fn=lambda _: None, clock=lambda: NOW,
    )
    assert first.status == "PARTIAL_COLLECTION"
    store = SimplePostStore(tmp_path / "collector.db", read_only=True)
    try:
        assert store.backfill_resume_page("xueqiu", "601012", FROM, TO) is None
    finally:
        store.close()

    second_transport = FakeDomTransport({
        1: [_raw("normal-1", "08/15 10:00")],
        2: [_raw("normal-old", "08/13 10:00")],
    })
    second = execute_xueqiu_dom_backfill(
        db_path=tmp_path / "collector.db", stock_code="601012", requested=_request(),
        transport=second_transport, sleep_fn=lambda _: None, clock=lambda: NOW,
    )
    assert second.start_page == 1
    assert second_transport.goto_calls == [2]


def test_exact_historical_resume_remains_usable(tmp_path: Path) -> None:
    store = SimplePostStore(tmp_path / "collector.db")
    try:
        store.save_backfill_resume("xueqiu", "601012", FROM, TO, 50)
    finally:
        store.close()
    transport = FakeDomTransport({
        51: [_raw("resume-51", "08/15 10:00")],
        52: [_raw("resume-old", "08/13 10:00")],
    })
    result = execute_xueqiu_dom_backfill(
        db_path=tmp_path / "collector.db", stock_code="601012", requested=_request(),
        transport=transport, sleep_fn=lambda _: None, clock=lambda: NOW,
    )
    assert result.status == "SUCCESS"
    assert result.start_page == 51
    assert transport.goto_calls == [51, 52]
    store = SimplePostStore(tmp_path / "collector.db", read_only=True)
    try:
        assert store.coverage_ranges("xueqiu", "601012") == [(FROM, TO)]
        assert store.backfill_resume_page("xueqiu", "601012", FROM, TO) is None
    finally:
        store.close()
