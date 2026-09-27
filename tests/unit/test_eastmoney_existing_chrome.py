"""Deterministic existing-user Chrome DOM acquisition tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

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

# The journal the production caller writes to. Every focus row goes here unless a
# test injects its own path, so it must stay byte-identical across a test run.
LIVE_FOCUS_JOURNAL = Path("runtime/logs/eastmoney-focus.jsonl")


def _transport(tmp_path, **kwargs):
    """Construct the transport with a test-private focus journal.

    `focus_log_path` defaults to the live production journal. That default is
    correct for the production caller in `browser_runtime`, but it means a test
    that omits the argument appends `CREATE_TAB` / `TAB_LOADING` / `EXECUTE_JS`
    rows to the real journal -- three operations per `get()`, i.e. nine rows per
    run of this file, and rows that read like a collection run in progress.
    Measured 2026-09-23 before the fix: +9 rows per run of this module.
    """
    kwargs.setdefault("focus_log_path", tmp_path / "focus.jsonl")
    return EastmoneyExistingChromeDomTransport(**kwargs)


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


def test_existing_chrome_returns_truthful_dom_snapshot_without_fake_http_metadata(tmp_path) -> None:
    url = "https://guba.eastmoney.com/list,601012,f.html"
    html = '<html><script>var article_list={"rc":1,"re":[]};</script></html>'
    runner = FakeAppleEvents({
        "html": html,
        "observedUrl": url,
        "title": "601012股吧",
        "readyState": "complete",
    })
    transport = _transport(
        tmp_path,
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


def test_existing_chrome_rejects_invalid_dom_snapshot(tmp_path) -> None:
    url = "https://guba.eastmoney.com/list,601012,f.html"
    transport = _transport(
        tmp_path,
        script_runner=FakeAppleEvents({"html": None, "observedUrl": url}),
        settle_seconds=0,
        sleep_fn=lambda _seconds: None,
    )
    with pytest.raises(ExistingChromeAcquisitionError) as caught:
        transport.get(url, timeout=2)
    assert caught.value.kind == "invalid_document"


def test_existing_chrome_rejects_off_source_observed_url(tmp_path) -> None:
    url = "https://guba.eastmoney.com/list,601012,f.html"
    transport = _transport(
        tmp_path,
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


def test_journaling_honours_an_injected_path_and_never_the_live_journal(tmp_path) -> None:
    """The focus journal is a production artefact; a test must not append to it.

    Negative control for the leak fixed above: before the injection every test in
    this module wrote to `runtime/logs/eastmoney-focus.jsonl` (+9 rows per run),
    which is indistinguishable from a collection run when read as a log. The
    positive half matters as much as the negative one -- if journaling silently
    stopped happening, the "live journal unchanged" assertion would pass for the
    wrong reason, so the injected file is checked to have content first.
    """
    url = "https://guba.eastmoney.com/list,601012,f.html"
    html = '<html><script>var article_list={"rc":1,"re":[]};</script></html>'
    live_before = (
        LIVE_FOCUS_JOURNAL.stat().st_size if LIVE_FOCUS_JOURNAL.exists() else 0
    )

    transport = _transport(
        tmp_path,
        script_runner=FakeAppleEvents({
            "html": html, "observedUrl": url, "title": "601012股吧",
            "readyState": "complete",
        }),
        settle_seconds=0,
        clock=lambda: NOW,
        sleep_fn=lambda _seconds: None,
    )
    transport.get(url, timeout=2)

    # Positive: the three operations really were journaled, just not to the live file.
    # `operation` is the label passed alongside the script, not the script source --
    # the CREATE_TAB / TAB_LOADING / EXECUTE_JS constants are the AppleScript bodies.
    rows = [
        json.loads(line)
        for line in (tmp_path / "focus.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [row["operation"] for row in rows] == [
        "CREATE_TAB", "TAB_LOADING", "EXECUTE_JS"
    ]

    # Negative: the production journal did not grow by a single byte.
    live_after = (
        LIVE_FOCUS_JOURNAL.stat().st_size if LIVE_FOCUS_JOURNAL.exists() else 0
    )
    assert live_after == live_before
