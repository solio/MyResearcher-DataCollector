from __future__ import annotations

from datetime import datetime, timezone

import pytest

from myresearcher_collector.sources.xueqiu.dom_parser import (
    XueqiuDomParseError,
    parse_detail_status,
    parse_dom_item,
    parse_time_text,
)
from myresearcher_collector.sources.xueqiu import symbol_for
from myresearcher_collector.sources.xueqiu.dom_transport import XueqiuDomTransport


NOW = datetime(2026, 8, 16, 4, 0, tzinfo=timezone.utc)


def _item(status_id: str, time_text: str = "08/15 10:00", **extra):
    return {
        "status_id": status_id,
        "author_id": "u-1",
        "author_name": "author",
        "url": f"/u-1/{status_id}",
        "content": f"body-{status_id}",
        "title": None,
        "time_text_observed": time_text,
        **extra,
    }


def test_symbol_mapping_and_dom_fields() -> None:
    assert XueqiuDomTransport.symbol_for("601012") == "SH601012"
    assert XueqiuDomTransport.symbol_for("002594") == "SZ002594"
    assert symbol_for("002028") == "SZ002028"
    item = parse_dom_item(_item("100"), now=NOW)
    assert item.url == "https://xueqiu.com/u-1/100"
    assert item.content == "body-100"
    assert item.published_at is not None
    assert item.published_at.tzinfo == timezone.utc


def test_modified_list_time_is_not_published_time() -> None:
    item = parse_dom_item(_item("101", "修改于 08-14 16:57"), now=NOW)
    assert item.published_at is None
    assert item.requires_detail_timestamp


def test_detail_timestamp_overrides_modified_list_time() -> None:
    status_id, created, edited = parse_detail_status(
        {"id": 101, "created_at": 1786607804000},
    )
    assert status_id == "101"
    assert created.tzinfo == timezone.utc
    assert edited is None


def test_invalid_detail_shape_is_rejected() -> None:
    with pytest.raises(XueqiuDomParseError):
        parse_detail_status({"id": "101", "created_at": "not-a-millis"})


class _AsyncPage:
    def __init__(self) -> None:
        self.url = "https://xueqiu.com/S/SH601012"
        self.opened: list[str] = []
        self.requested_pages: list[int] = []
        self.reads = 0

    def open_stock(self, stock_code: str) -> None:
        self.opened.append(stock_code)

    def wait_posts_loaded(self, timeout_ms: int) -> None:
        assert timeout_ms > 0

    def active_page(self) -> int:
        return 1 if self.reads < 2 else 2

    def read_dom_page(self):
        self.reads += 1
        # The first read after clicking is stale DOM; the second is the new
        # active page and has a different ID sequence.
        if self.reads < 3:
            return [_item("old")]
        return [_item("new")]

    def goto_page(self, page_no: int) -> None:
        self.requested_pages.append(page_no)


def test_dom_transport_waits_for_active_page_and_id_progression() -> None:
    page = _AsyncPage()
    transport = XueqiuDomTransport(page, timeout_ms=500)
    transport.open_stock("601012")
    first = transport.read_current_page()
    assert first["page_no"] == 1
    second = transport.goto_page(2, previous_ids=("old",))
    assert second["page_no"] == 2
    assert [row["status_id"] for row in second["items"]] == ["new"]
    assert page.opened == ["601012"]
    assert page.requested_pages == [2]
    assert page.url == "https://xueqiu.com/S/SH601012"
