from __future__ import annotations

import json

import pytest

from myresearcher_collector.sources.eastmoney_guba.existing_chrome import (
    CLOSE_TAB,
    CREATE_TAB,
    EXECUTE_JS,
)
from myresearcher_collector.sources.xueqiu.dom_transport import (
    XueqiuDomTransport,
    create_xueqiu_dom_transport,
)
from myresearcher_collector.sources.xueqiu.existing_chrome import (
    ACTIVE_PAGE_JS,
    CREATE_ACTIVE_TAB,
    DETAIL_STATE_JS,
    PAGE_STATE_JS,
    READ_PAGE_JS,
    XueqiuExistingChromePage,
)


def _item(status_id: str) -> dict[str, object]:
    return {
        "status_id": status_id,
        "author_id": f"author-{status_id}",
        "author_name": "author",
        "url": f"https://xueqiu.com/author/{status_id}",
        "content": f"content-{status_id}",
        "title": None,
        "time_text_observed": "今天 10:00",
        "read_count": None,
        "reply_count": 1,
        "like_count": 2,
        "forward_count": 3,
    }


class FakeChromeAppleEvents:
    def __init__(self) -> None:
        self.current_page = 1
        self.created_tabs = 0
        self.closed_tabs: list[tuple[str, str]] = []

    def __call__(self, script: str, *values: object) -> str:
        if script in {CREATE_TAB, CREATE_ACTIVE_TAB}:
            self.created_tabs += 1
            return f"10|{20 + self.created_tabs}"
        if script == CLOSE_TAB:
            self.closed_tabs.append((str(values[0]), str(values[1])))
            return ""
        if script != EXECUTE_JS:
            return ""
        javascript = str(values[-1])
        if javascript == PAGE_STATE_JS:
            return json.dumps({
                "url": "https://xueqiu.com/S/SH601012",
                "title": "隆基绿能",
                "readyState": "complete",
                "posts": 2,
                "challenge": [],
            })
        if javascript == ACTIVE_PAGE_JS:
            return str(self.current_page)
        if javascript == READ_PAGE_JS:
            ids = ("p1-a", "p1-b") if self.current_page == 1 else ("p2-a", "p2-b")
            return json.dumps([_item(value) for value in ids])
        if "const wanted = String(2)" in javascript:
            self.current_page = 2
            return json.dumps({"clicked": True})
        if javascript == DETAIL_STATE_JS:
            return json.dumps({
                "url": "https://xueqiu.com/author/modified",
                "challenge": [],
                "status": None,
                "embeddedStatusJson": json.dumps({
                    "id": "modified", "created_at": 1786607804000,
                }),
            })
        raise AssertionError(f"unexpected JavaScript: {javascript[:80]}")


def test_existing_chrome_supports_list_pagination_and_temporary_detail_tab() -> None:
    runner = FakeChromeAppleEvents()
    page = XueqiuExistingChromePage(
        script_runner=runner,
        poll_interval_seconds=0,
        sleep_fn=lambda _seconds: None,
    )
    transport = XueqiuDomTransport(page)

    transport.open_stock("601012")
    first = transport.read_current_page()
    second = transport.goto_page(2, previous_ids=first["active_ids"])
    detail = transport.read_detail_created_at("https://xueqiu.com/author/modified")

    assert transport.acquisition_mode == "existing-chrome"
    assert transport.main_goto_count == 1
    assert transport.post_dom_loaded is True
    assert first["active_ids"] == ("p1-a", "p1-b")
    assert second["active_ids"] == ("p2-a", "p2-b")
    assert detail == {"id": "modified", "created_at": 1786607804000}
    assert transport.assert_current_page(2, expected_ids=second["active_ids"])["page_no"] == 2
    assert runner.created_tabs == 2
    assert runner.closed_tabs == [("10", "22")]

    transport.close()
    assert runner.closed_tabs == [("10", "22"), ("10", "21")]


def test_existing_chrome_factory_does_not_accept_a_profile_copy() -> None:
    with pytest.raises(ValueError, match="running user profile"):
        create_xueqiu_dom_transport("existing-chrome", profile_dir="copied-profile")
