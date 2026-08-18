from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from myresearcher_collector.sources.xueqiu.dom_parser import (
    XueqiuDomParseError,
    parse_detail_status,
    parse_dom_item,
    parse_time_text,
)
from myresearcher_collector.sources.xueqiu import symbol_for
from myresearcher_collector.sources.xueqiu.dom_transport import (
    XueqiuDomTransport,
    XueqiuDomTransportError,
)


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


class _NavigatingPage:
    def __init__(self) -> None:
        self.url = "about:blank"
        self.main_frame = SimpleNamespace(url=self.url)
        self.handlers: dict[str, object] = {}
        self.goto_urls: list[str] = []

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler

    def goto(self, url: str, *, wait_until: str) -> None:
        assert wait_until == "domcontentloaded"
        self.goto_urls.append(url)
        self.url = url
        self.main_frame.url = url
        self.handlers["framenavigated"](self.main_frame)

    def wait_posts_loaded(self, timeout_ms: int) -> None:
        assert timeout_ms > 0

    def active_page(self) -> int:
        return 1


def test_xueqiu_records_main_navigation_and_dom_ready() -> None:
    page = _NavigatingPage()
    transport = XueqiuDomTransport(page, timeout_ms=500)

    transport.open_stock("601012")

    assert transport.main_goto_count == 1
    assert transport.frame_navigation_urls == [
        "https://xueqiu.com/S/SH601012"
    ]
    assert transport.post_dom_loaded is True


def test_xueqiu_navigation_diagnostics_do_not_retain_challenge_values() -> None:
    page = _NavigatingPage()
    transport = XueqiuDomTransport(page, timeout_ms=500)
    page.main_frame.url = (
        "https://xueqiu.com/S/SH601012?alichlgref=secret&md5__1038=signature"
    )

    page.handlers["framenavigated"](page.main_frame)

    assert transport.frame_navigation_urls == ["https://xueqiu.com/S/SH601012"]


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


class _EmptyTransitionPage(_AsyncPage):
    def active_page(self) -> int:
        return 2

    def read_dom_page(self):
        self.reads += 1
        if self.reads == 1:
            return [_item("old")]
        if self.reads == 2:
            return []
        return [_item("new")]


def test_dom_transport_does_not_accept_transient_empty_target_page() -> None:
    page = _EmptyTransitionPage()
    transport = XueqiuDomTransport(page, timeout_ms=500)
    transport.open_stock("601012")
    first = transport.read_current_page()
    second = transport.goto_page(2, previous_ids=first["active_ids"])
    assert second["active_ids"] == ("new",)
    assert page.reads >= 3


class _RestorePage:
    def __init__(self) -> None:
        self.page_no = 1
        self.pending_page: int | None = None
        self.reads_after_click = 0

    def open_stock(self, stock_code: str) -> None:
        assert stock_code == "601012"
        self.page_no = 1

    def wait_posts_loaded(self, timeout_ms: int) -> None:
        assert timeout_ms > 0

    def active_page(self) -> int:
        return self.page_no

    def read_dom_page(self):
        if self.pending_page is not None:
            self.reads_after_click += 1
            if self.reads_after_click >= 2:
                self.page_no = self.pending_page
                self.pending_page = None
        ids = ("A", "B", "C")
        return [_item(item_id) for item_id in ids]

    def goto_page(self, page_no: int) -> None:
        self.pending_page = page_no
        self.reads_after_click = 0


def test_restore_target_page_allows_same_ids() -> None:
    page = _RestorePage()
    transport = XueqiuDomTransport(page, timeout_ms=300)
    transport.open_stock("601012")
    restored = transport.restore_page(5, expected_ids=("A", "B", "C"))
    assert restored["page_no"] == 5
    assert tuple(item["status_id"] for item in restored["items"]) == ("A", "B", "C")


def test_normal_next_page_with_same_ids_still_fails() -> None:
    page = _RestorePage()
    transport = XueqiuDomTransport(page, timeout_ms=60)
    transport.open_stock("601012")
    with pytest.raises(XueqiuDomTransportError, match="pagination did not progress"):
        transport.goto_page(2, previous_ids=("A", "B", "C"))


class _DetailPage:
    def __init__(self, value=None, error: Exception | None = None) -> None:
        self.value = value
        self.error = error
        self.goto_urls: list[str] = []
        self.closed = 0

    def goto(self, url: str, *, wait_until: str) -> None:
        assert wait_until == "domcontentloaded"
        self.goto_urls.append(url)
        if self.error is not None:
            raise self.error

    def wait_for_function(self, expression: str, *, timeout: int) -> None:
        assert "SNOWMAN_STATUS" in expression
        assert timeout > 0
        if self.error is not None:
            raise self.error

    def evaluate(self, expression: str):
        assert "SNOWMAN_STATUS" in expression
        return self.value

    def close(self) -> None:
        self.closed += 1


class _DetailContext:
    def __init__(self, pages: list[_DetailPage]) -> None:
        self.pages = pages
        self.created: list[_DetailPage] = []

    def new_page(self) -> _DetailPage:
        page = self.pages[len(self.created)]
        self.created.append(page)
        return page


def test_modified_detail_uses_temporary_page_and_leaves_main_page_unchanged() -> None:
    main = _RestorePage()
    main.page_no = 5
    detail = _DetailPage({"id": 101, "created_at": 1786607804000})
    transport = XueqiuDomTransport(main, runtime=SimpleNamespace(context=_DetailContext([detail])))
    transport.stock_code = "601012"
    transport.current_page = 5
    value = transport.read_detail_created_at("https://xueqiu.com/u/101")
    assert value["id"] == 101
    assert detail.goto_urls == ["https://xueqiu.com/u/101"]
    assert detail.closed == 1
    assert main.page_no == 5
    assert transport.assert_current_page(5, expected_ids=("A", "B", "C"))["page_no"] == 5


def test_three_modified_details_are_serial_and_each_detail_page_closes() -> None:
    main = _RestorePage()
    main.page_no = 5
    details = [
        _DetailPage({"id": index, "created_at": 1786607804000 + index})
        for index in (101, 102, 103)
    ]
    context = _DetailContext(details)
    transport = XueqiuDomTransport(main, runtime=SimpleNamespace(context=context))
    transport.stock_code = "601012"
    transport.current_page = 5
    for index in (101, 102, 103):
        assert transport.read_detail_created_at(f"https://xueqiu.com/u/{index}")["id"] == index
        assert main.page_no == 5
    assert context.created == details
    assert [page.closed for page in details] == [1, 1, 1]


def test_missing_detail_status_closes_temporary_page_and_preserves_main_page() -> None:
    main = _RestorePage()
    main.page_no = 5
    detail = _DetailPage(error=TimeoutError("missing status"))
    transport = XueqiuDomTransport(main, runtime=SimpleNamespace(context=_DetailContext([detail])))
    transport.stock_code = "601012"
    transport.current_page = 5
    with pytest.raises(XueqiuDomTransportError):
        transport.read_detail_created_at("https://xueqiu.com/u/404")
    assert detail.closed == 1
    assert main.page_no == 5


def test_detail_goto_exception_still_closes_temporary_page() -> None:
    main = _RestorePage()
    main.page_no = 5
    detail = _DetailPage(error=RuntimeError("navigation failed"))
    transport = XueqiuDomTransport(main, runtime=SimpleNamespace(context=_DetailContext([detail])))
    transport.stock_code = "601012"
    transport.current_page = 5
    with pytest.raises(XueqiuDomTransportError):
        transport.read_detail_created_at("https://xueqiu.com/u/500")
    assert detail.closed == 1
    assert main.page_no == 5
