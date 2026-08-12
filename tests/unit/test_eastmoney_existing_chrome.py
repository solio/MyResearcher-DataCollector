"""Deterministic existing-user Chrome DOM acquisition tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from myresearcher_collector.sources.eastmoney_guba import (
    BROWSER_DOM_SNAPSHOT,
    EastmoneyExistingChromeDomTransport,
    ExistingChromeAcquisitionError,
)
from myresearcher_collector.sources.eastmoney_guba.existing_chrome import (
    CREATE_TAB,
    DOM_SNAPSHOT_JS,
    EXECUTE_JS,
    TAB_LOADING,
)
from myresearcher_collector.sources.eastmoney_guba.parser import parse_list_page


NOW = datetime(2026, 8, 12, 4, 0, tzinfo=timezone.utc)


class FakeAppleEvents:
    def __init__(self, snapshot: object) -> None:
        self.snapshot = snapshot
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def __call__(self, script: str, *values: object) -> str:
        self.calls.append((script, values))
        if script == CREATE_TAB:
            return "10|20"
        if script == TAB_LOADING:
            return "false"
        if script == EXECUTE_JS:
            assert values[-1] == DOM_SNAPSHOT_JS
            return json.dumps(self.snapshot)
        return ""


def test_existing_chrome_returns_truthful_dom_snapshot_without_fake_http_metadata() -> None:
    url = "https://guba.eastmoney.com/list,601012,f.html"
    html = '<html><script>var article_list={"rc":1,"re":[]};</script></html>'
    runner = FakeAppleEvents({
        "html": html,
        "observedUrl": url,
        "title": "601012股吧",
        "readyState": "complete",
    })
    transport = EastmoneyExistingChromeDomTransport(
        script_runner=runner,
        settle_seconds=0,
        clock=lambda: NOW,
        sleep_fn=lambda _seconds: None,
    )

    acquired = transport.get(url, timeout=2)

    assert acquired.payload == html.encode("utf-8")
    assert acquired.capture_method == BROWSER_DOM_SNAPSHOT
    assert acquired.http_status is None
    assert acquired.content_type is None
    assert acquired.headers == {}
    assert acquired.observed_url == url
    assert acquired.fetched_at == NOW
    assert acquired.metadata["serialization"].endswith(":utf-8")


def test_existing_chrome_rejects_invalid_dom_snapshot() -> None:
    url = "https://guba.eastmoney.com/list,601012,f.html"
    transport = EastmoneyExistingChromeDomTransport(
        script_runner=FakeAppleEvents({"html": None, "observedUrl": url}),
        settle_seconds=0,
        sleep_fn=lambda _seconds: None,
    )
    with pytest.raises(ExistingChromeAcquisitionError) as caught:
        transport.get(url, timeout=2)
    assert caught.value.kind == "invalid_document"


def test_existing_chrome_rejects_off_source_observed_url() -> None:
    url = "https://guba.eastmoney.com/list,601012,f.html"
    transport = EastmoneyExistingChromeDomTransport(
        script_runner=FakeAppleEvents({
            "html": "<html></html>",
            "observedUrl": "https://example.com/challenge",
        }),
        settle_seconds=0,
        sleep_fn=lambda _seconds: None,
    )
    with pytest.raises(RuntimeError):
        transport.get(url, timeout=2)


def test_production_parser_accepts_dom_anchor_without_data_postid() -> None:
    html = """<html><body>
      <a href="/news,601012,12345.html">post</a>
      <script>var article_list={"rc":1,"re":[{
        "post_id":12345,"post_title":"post","stockbar_code":"601012",
        "stockbar_name":"bar","post_publish_time":"2026-08-12 10:00:00",
        "post_type":0
      }]};</script>
    </body></html>"""
    page = parse_list_page(html, "601012")
    assert page.rows[0].source_item_id == "12345"
    assert page.rows[0].url == "https://guba.eastmoney.com/news,601012,12345.html"
