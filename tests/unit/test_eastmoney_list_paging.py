"""Deterministic tests for the pager-click helpers."""

from __future__ import annotations

import pytest

from myresearcher_collector.sources.eastmoney_guba.collector import (
    EastmoneyGubaCollector,
)
from myresearcher_collector.sources.eastmoney_guba.list_paging import (
    CLICK_NEXT_LIST_PAGE_JS,
    list_page_parts,
    list_page_url,
    navigation_report,
)


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://guba.eastmoney.com/list,601012,f.html", ("601012", 1)),
        ("https://guba.eastmoney.com/list,601012,f_2.html", ("601012", 2)),
        ("https://guba.eastmoney.com/list,601012,f_8293.html", ("601012", 8293)),
        ("https://guba.eastmoney.com/list,300054,f_99.html", ("300054", 99)),
        # Read from the path, so a tracking query a pager href might carry does
        # not make the page unrecognisable (only the path is matched).
        ("https://guba.eastmoney.com/list,601012,f_2.html?from=list", ("601012", 2)),
    ],
)
def test_list_page_parts_reads_the_bar_and_the_page(url, expected) -> None:
    assert list_page_parts(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        # a detail page: the enrich surface, not the backfill surface
        "https://guba.eastmoney.com/news,601012,1776571734.html",
        "https://caifuhao.eastmoney.com/news/202608111234",
        "https://example.com/list,601012,f_2.html",
        "https://guba.eastmoney.com/list,601012.html",
        "https://guba.eastmoney.com/list,601012,f_2.htm",
        # A shape we do not recognise must degrade to the URL navigation, which is
        # what the collector asked for anyway -- never to a guess at a page number.
        "https://guba.eastmoney.com/List,601012,f_2.html",
        "not a url at all",
    ],
)
def test_list_page_parts_returns_none_for_anything_but_a_list_page(url) -> None:
    """None means "leave this URL on the direct path", never "page 1"."""
    assert list_page_parts(url) is None


def test_list_page_url_matches_the_collector_definition() -> None:
    """Pin the URL shape to the one the walk actually requests.

    There are two copies of this shape -- `collector.list_url` builds the URLs the
    walk asks for, and this module builds the ones a click is *verified* against.
    If they drift, every click is skipped as a mis-target and the scheme quietly
    degrades to `goto`; this test makes that drift impossible to land.
    """
    for page in (1, 2, 3, 99, 8293):
        assert list_page_url("601012", page) == EastmoneyGubaCollector.list_url(
            "601012", page
        )


def test_the_click_script_reports_what_it_did() -> None:
    """The click has no return value, so its contract is the instrument.

    Asserted on the script text because the script only runs in a real browser:
    `clicked` must be reachable both ways, and the href must be echoed so a
    fallback can be explained afterwards rather than guessed at.
    """
    assert "anchor.click()" in CLICK_NEXT_LIST_PAGE_JS
    assert "clicked: true" in CLICK_NEXT_LIST_PAGE_JS
    assert "clicked: false" in CLICK_NEXT_LIST_PAGE_JS
    assert "href: href" in CLICK_NEXT_LIST_PAGE_JS
    # It must verify the anchor's own target before clicking it, so a pager that
    # points somewhere else cannot navigate the walk off its 1:1 sequence.
    assert "page !== args.page" in CLICK_NEXT_LIST_PAGE_JS


def test_navigation_report_always_states_every_counter() -> None:
    """Defaults to 0 rather than omitting: "measured, did not happen" != "unmeasured"."""
    assert navigation_report(None) == {
        "click": 0, "goto": 0, "click_no_anchor": 0, "click_not_taken": 0,
        "samples": {},
    }
    assert navigation_report({"click": 3}, {"click": {"sec-fetch-site": "same-origin"}}) == {
        "click": 3, "goto": 0, "click_no_anchor": 0, "click_not_taken": 0,
        "samples": {"click": {"sec-fetch-site": "same-origin"}},
    }


def test_navigation_report_ignores_a_transport_that_is_not_a_counter_dict() -> None:
    """A transport that does not implement paging must not break the report."""
    assert navigation_report("not a dict", ["nor this"])["click"] == 0
    assert navigation_report(None, ["not a dict"])["samples"] == {}
