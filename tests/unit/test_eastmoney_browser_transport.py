"""Deterministic tests for the browser-owned Eastmoney HTML transport."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

import myresearcher_collector.sources.eastmoney_guba.browser_transport as transport_module

from myresearcher_collector.sources.eastmoney_guba import (
    EastmoneyBrowserBoundaryError,
    EastmoneyBrowserSocketTransport,
    EastmoneyBrowserTransport,
    EastmoneyBrowserTransportError,
)
from myresearcher_collector.sources.eastmoney_guba.list_paging import list_page_parts
from myresearcher_collector.sources.eastmoney_guba.parser import is_not_found_page


class FakeRequest:
    """Enough of Playwright's Request for the document filter and the header read.

    `headers` deliberately HIDES `sec-fetch-*`, because that is what Playwright
    does: reading the navigation shape with `request.headers` reports "no
    Sec-Fetch-Site" for every request while looking like a measurement
    (scripts/ops/README.md, "Reader trap"). Only `all_headers()` tells the truth,
    and a test that used a permissive fake here could not catch a regression back
    to the blind reader.
    """

    def __init__(
        self,
        url: str,
        *,
        resource_type: str = "document",
        request_headers: dict[str, str] | None = None,
    ) -> None:
        self.url = url
        self.resource_type = resource_type
        self._headers = dict(request_headers or {})

    def all_headers(self) -> dict[str, str]:
        return dict(self._headers)

    @property
    def headers(self) -> dict[str, str]:
        return {
            key: value
            for key, value in self._headers.items()
            if not key.lower().startswith("sec-fetch")
        }


class FakeResponse:
    def __init__(
        self,
        url: str,
        *,
        status: int = 200,
        body: bytes = b"<script>var article_list={};</script>",
        resource_type: str = "document",
        request_headers: dict[str, str] | None = None,
    ) -> None:
        self.url = url
        self.status = status
        self._body = body
        self.request = FakeRequest(
            url, resource_type=resource_type, request_headers=request_headers
        )

    def body(self) -> bytes:
        return self._body

    def all_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "text/html; charset=utf-8",
            "Set-Cookie": "must-not-leave-browser",
        }


class FakePage:
    def __init__(self, response: FakeResponse | None) -> None:
        self.response = response
        self.calls: list[tuple[str, str, int]] = []

    def goto(self, url: str, *, wait_until: str, timeout: int) -> FakeResponse | None:
        self.calls.append((url, wait_until, timeout))
        return self.response


def test_browser_transport_returns_exact_main_document_response() -> None:
    url = "https://guba.eastmoney.com/list,601012,f.html"
    page = FakePage(FakeResponse(url))

    response = EastmoneyBrowserTransport(page).get(url, timeout=2.5)

    assert response.status_code == 200
    assert response.body == b"<script>var article_list={};</script>"
    assert response.final_url == url
    assert response.headers == {"content-type": "text/html; charset=utf-8"}
    assert page.calls == [(url, "domcontentloaded", 2500)]


def test_browser_transport_supports_approved_detail_host() -> None:
    url = "https://caifuhao.eastmoney.com/news/202608111234"
    response = EastmoneyBrowserTransport(FakePage(FakeResponse(url))).get(
        url, timeout=1.0
    )
    assert response.final_url == url


@pytest.mark.parametrize(
    "url",
    [
        "http://guba.eastmoney.com/list,601012,f.html",
        "https://example.com/list,601012,f.html",
        "https://user:password@guba.eastmoney.com/list,601012,f.html",
    ],
)
def test_browser_transport_rejects_navigation_outside_source_boundary(url: str) -> None:
    page = FakePage(None)
    with pytest.raises(EastmoneyBrowserBoundaryError):
        EastmoneyBrowserTransport(page).get(url, timeout=1.0)
    assert page.calls == []


def test_browser_transport_rejects_off_host_final_response() -> None:
    url = "https://guba.eastmoney.com/list,601012,f.html"
    page = FakePage(FakeResponse("https://example.com/challenge"))
    with pytest.raises(EastmoneyBrowserBoundaryError):
        EastmoneyBrowserTransport(page).get(url, timeout=1.0)


def test_browser_transport_requires_main_document_response() -> None:
    url = "https://guba.eastmoney.com/list,601012,f.html"
    with pytest.raises(EastmoneyBrowserTransportError):
        EastmoneyBrowserTransport(FakePage(None)).get(url, timeout=1.0)


def test_socket_transport_returns_sanitized_browser_host_response(monkeypatch) -> None:
    url = "https://guba.eastmoney.com/list,601012,f.html"
    body = b"<html><script>var article_list={};</script></html>"
    wire_response = json.dumps({
        "ok": True,
        "status_code": 200,
        "body_base64": base64.b64encode(body).decode(),
        "headers": {
            "content-type": "text/html",
            "set-cookie": "must-not-leave-host",
        },
        "final_url": url,
    }).encode() + b"\n"

    class FakeSocket:
        def __init__(self) -> None:
            self.sent = b""
            self.connected = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def settimeout(self, _timeout):
            return None

        def connect(self, path):
            self.connected = path

        def sendall(self, value):
            self.sent += value

        def recv(self, _size):
            value, self.response = self.response, b""
            return value

    fake = FakeSocket()
    fake.response = wire_response
    monkeypatch.setattr(transport_module.socket, "socket", lambda *_args: fake)

    response = EastmoneyBrowserSocketTransport("/tmp/em-browser.sock").get(
        url, timeout=2.5
    )

    assert json.loads(fake.sent) == {"method": "GET", "url": url, "timeout": 2.5}
    assert fake.connected == "/private/tmp/em-browser.sock"
    assert response.body == body
    assert response.status_code == 200
    assert response.final_url == url
    assert response.headers == {"content-type": "text/html"}


def test_socket_transport_fails_closed_when_host_is_missing(monkeypatch) -> None:
    class MissingSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def settimeout(self, _timeout):
            return None

        def connect(self, _path):
            raise FileNotFoundError

    monkeypatch.setattr(
        transport_module.socket, "socket", lambda *_args: MissingSocket()
    )
    transport = EastmoneyBrowserSocketTransport("/tmp/missing.sock")
    with pytest.raises(EastmoneyBrowserTransportError, match="host is unavailable"):
        transport.get("https://guba.eastmoney.com/list,601012,f.html", timeout=1.0)


# --------------------------------------------------------------------------
# detail_referer_from_list: reach the detail via its bar list page
# --------------------------------------------------------------------------


class FakeRefererPage:
    """A Page that can script-navigate and emit the response event.

    `evaluate(script)` (no argument) is the DOM challenge probe and returns
    `dom`; `evaluate(script, url)` is the navigation and fires the registered
    response listeners, which is how the real browser behaves.

    `detail_final_url` models a REDIRECT: the browser commits that URL (so
    `page.url` becomes it and `wait_for_url`'s predicate must tolerate it) while
    the response carries the redirected document. A deleted Eastmoney post does
    exactly this -- 302 into `error?type=2`.
    """

    def __init__(
        self,
        *,
        detail_final_url: str | None = None,
        detail_status: int = 200,
        detail_body: bytes = b"<script>var post_article={};</script>",
        fire_response: bool = True,
        document_responses: list[Any] | None = None,
        dom: dict | None = None,
    ) -> None:
        self.detail_final_url = detail_final_url
        self.detail_status = detail_status
        self.detail_body = detail_body
        self.fire_response = fire_response
        self.document_responses = document_responses
        self.dom = dom if dom is not None else {
            "url": "", "title": "正常帖子", "text_tokens": [],
            "structural_tokens": [], "visible_overlays": [],
        }
        self.gotos: list[tuple[str, str, int]] = []
        self.evaluated: list[tuple[object, object]] = []
        self.waited: list[tuple[str, int]] = []
        self.removed: list[tuple[str, object]] = []
        self._listeners: dict[str, list] = {}
        self.url = "about:blank"

    def on(self, event: str, handler) -> None:
        self._listeners.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler) -> None:
        self.removed.append((event, handler))
        self._listeners[event].remove(handler)

    def goto(self, url: str, *, wait_until: str, timeout: int):
        self.gotos.append((url, wait_until, timeout))
        self.url = url
        return FakeResponse(url)

    def evaluate(self, script, arg=None):
        self.evaluated.append((script, arg))
        if arg is None:
            return self.dom
        if self.fire_response:
            responses = self.document_responses
            if responses is None:
                responses = [FakeResponse(self.detail_final_url or arg,
                                          status=self.detail_status,
                                          body=self.detail_body)]
            for response in responses:
                for handler in list(self._listeners.get("response", [])):
                    handler(response)
        # The committed URL is the redirect target when there is one, which is
        # what makes the predicate assertion below meaningful.
        self.url = self.detail_final_url or arg
        return None

    def wait_for_url(self, predicate, *, wait_until: str, timeout: int) -> None:
        assert predicate(self.url), (
            f"wait_for_url predicate must accept the committed URL {self.url!r}"
        )
        self.waited.append((wait_until, timeout))

    def title(self) -> str:
        return "标题"


DETAIL = "https://guba.eastmoney.com/news,601012,1776571734.html"
LIST = "https://guba.eastmoney.com/list,601012,f.html"


def test_detail_referer_opens_the_bar_list_then_navigates_in_page() -> None:
    page = FakeRefererPage()
    transport = EastmoneyBrowserTransport(page, detail_referer_from_list=True)

    response = transport.get(DETAIL, timeout=2.5)

    assert response.status_code == 200
    assert response.final_url == DETAIL
    assert response.body == b"<script>var post_article={};</script>"
    assert [call[0] for call in page.gotos] == [LIST]
    assert page.gotos[0][2] == 2500
    # exactly two evaluate calls, in this order: the challenge probe on the LIST
    # page (while it is still displayed, which is the only chance to see it),
    # then the in-page navigation that reaches the detail
    assert len(page.evaluated) == 2
    assert page.evaluated[0] == (transport_module.DOM_CHALLENGE_JS, None)
    assert page.evaluated[1] == ("u => { location.href = u; }", DETAIL)
    assert page.waited == [("domcontentloaded", 2500)]


def test_detail_referer_is_off_by_default() -> None:
    page = FakeRefererPage()
    EastmoneyBrowserTransport(page).get(DETAIL, timeout=2.0)
    assert [call[0] for call in page.gotos] == [DETAIL]
    assert page.evaluated == []


@pytest.mark.parametrize(
    "url",
    [
        "https://guba.eastmoney.com/list,601012,f.html",          # a list page
        "https://caifuhao.eastmoney.com/news/202608111234",       # no bar list
    ],
)
def test_detail_referer_leaves_non_bar_detail_urls_alone(url: str) -> None:
    page = FakeRefererPage()
    EastmoneyBrowserTransport(page, detail_referer_from_list=True).get(url, timeout=2.0)
    assert [call[0] for call in page.gotos] == [url]
    assert page.evaluated == []


def test_detail_referer_fails_closed_when_no_response_was_captured() -> None:
    page = FakeRefererPage(fire_response=False)
    transport = EastmoneyBrowserTransport(page, detail_referer_from_list=True)
    # The scripted navigation produced no Response object, so there are no real
    # bytes to parse. Fail rather than fall back to a DOM snapshot, which would
    # silently change what the collector considers a response.
    with pytest.raises(EastmoneyBrowserTransportError, match="no main-document response"):
        transport.get(DETAIL, timeout=2.0)


def test_detail_referer_reports_the_cause_of_a_failed_navigation() -> None:
    """The old wrap discarded the cause, so a timeout looked identical to a
    missing response -- which is what made the redirect defect hard to find."""

    class TimingOutPage(FakeRefererPage):
        def wait_for_url(self, predicate, *, wait_until: str, timeout: int) -> None:
            raise TimeoutError("Timeout 2000ms exceeded")

    transport = EastmoneyBrowserTransport(
        TimingOutPage(), detail_referer_from_list=True
    )
    with pytest.raises(EastmoneyBrowserTransportError, match="TimeoutError"):
        transport.get(DETAIL, timeout=2.0)


# --------------------------------------------------------------------------
# redirects: a deleted post 302s into error?type=2
# --------------------------------------------------------------------------

ERROR_URL = "https://guba.eastmoney.com/error?type=2"
ERROR_BODY = b'<div id="pageContent" class="error404_page"></div>'


def test_detail_referer_follows_a_redirect_instead_of_reporting_a_transport_error() -> None:
    """The defect this test exists for (found in the 2026-09-23 run).

    The first revision matched `response.url == url` exactly. A redirected
    detail never produced such a response, so the capture stayed empty and the
    candidate came out as `EastmoneyBrowserTransportError` instead of the
    `detail_not_found` the direct `page.goto` path gives -- meaning deleted
    posts never reached the skip ledger and would be re-requested forever.
    """
    page = FakeRefererPage(detail_final_url=ERROR_URL, detail_body=ERROR_BODY)
    transport = EastmoneyBrowserTransport(page, detail_referer_from_list=True)

    response = transport.get(DETAIL, timeout=2.0)

    assert response.final_url == ERROR_URL, "the redirected document is what we got"
    assert response.body == ERROR_BODY
    # and the parser can now do its job: an error shell is `detail_not_found`
    assert is_not_found_page(response.body.decode())


def test_detail_referer_ignores_non_document_responses() -> None:
    """An XHR whose type is not `document` must not become the page."""
    page = FakeRefererPage(document_responses=[
        FakeResponse(f"{DETAIL}?xhr=1", resource_type="xhr", body=b"{}"),
        FakeResponse(DETAIL, resource_type="fetch", body=b"{}"),
        FakeResponse(DETAIL, resource_type="document", body=b"<script>var post_article={};</script>"),
    ])
    transport = EastmoneyBrowserTransport(page, detail_referer_from_list=True)

    response = transport.get(DETAIL, timeout=2.0)

    assert response.body == b"<script>var post_article={};</script>"


def test_detail_referer_falls_back_to_the_last_document_when_no_url_matches() -> None:
    """Better a document the parser can classify than a transport error."""
    page = FakeRefererPage(
        detail_final_url="https://guba.eastmoney.com/error?type=9",
        document_responses=[
            FakeResponse(DETAIL, body=b"<script>var post_article={};</script>"),
            FakeResponse(ERROR_URL, body=ERROR_BODY),
        ],
    )
    transport = EastmoneyBrowserTransport(page, detail_referer_from_list=True)

    response = transport.get(DETAIL, timeout=2.0)

    assert response.body == ERROR_BODY, "last document wins when the URL is unknown"


def test_detail_referer_does_not_leak_its_response_listener() -> None:
    page = FakeRefererPage()
    transport = EastmoneyBrowserTransport(page, detail_referer_from_list=True)
    transport.get(DETAIL, timeout=2.0)
    transport.get(DETAIL, timeout=2.0)
    assert len(page.removed) == 2, "one listener added, one removed, per fetch"
    assert page._listeners["response"] == []


def test_detail_referer_records_a_challenged_list_page_on_the_response() -> None:
    page = FakeRefererPage(dom={
        "url": "", "title": "请完成验证", "text_tokens": ["滑块", "拼图"],
        "structural_tokens": [], "visible_overlays": [],
    })
    transport = EastmoneyBrowserTransport(page, detail_referer_from_list=True)

    response = transport.get(DETAIL, timeout=2.0)

    assert response.diagnostics is not None
    assert response.diagnostics["list_challenge_reasons"] == [
        "title_contains:请完成验证", "visible_text:滑块", "visible_text:拼图",
    ]


def test_detail_referer_omits_the_key_when_the_list_page_looked_clean() -> None:
    page = FakeRefererPage()
    transport = EastmoneyBrowserTransport(page, detail_referer_from_list=True)
    response = transport.get(DETAIL, timeout=2.0)
    assert "list_challenge_reasons" not in (response.diagnostics or {})


def test_challenge_reasons_reads_the_live_dom() -> None:
    clean = FakeRefererPage()
    assert EastmoneyBrowserTransport(clean).challenge_reasons() == []

    blocked = FakeRefererPage(dom={
        "url": "", "title": "身份核实", "text_tokens": [],
        "structural_tokens": ["emcaptcha"], "visible_overlays": ["#emcaptcha"],
    })
    reasons = EastmoneyBrowserTransport(blocked).challenge_reasons()
    assert "dom_asset:emcaptcha" in reasons
    assert "visible_overlay:#emcaptcha" in reasons
    assert "title:身份核实" in reasons


def test_challenge_reasons_is_fail_open_when_the_probe_cannot_run() -> None:
    class BrokenPage(FakeRefererPage):
        def evaluate(self, script, arg=None):
            raise RuntimeError("page is gone")

    assert EastmoneyBrowserTransport(BrokenPage()).challenge_reasons() == []


# --------------------------------------------------------------------------
# list_click_paging: turn list pages by clicking the page's own pager anchor
# --------------------------------------------------------------------------

LIST_P1 = "https://guba.eastmoney.com/list,601012,f.html"
LIST_P2 = "https://guba.eastmoney.com/list,601012,f_2.html"
LIST_P3 = "https://guba.eastmoney.com/list,601012,f_3.html"

# What the browser puts on the wire for each scheme. The fake attaches these to
# the response's request, so a sample can only ever be the headers that were
# really sent -- the transport is not allowed to invent them.
GOTO_SHAPE = {
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
}
CLICK_SHAPE = {
    "referer": LIST_P1,
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
}


class FakeListPagingPage:
    """A Page that serves list pages and can click the pager.

    `click_outcome` is exactly what `CLICK_NEXT_LIST_PAGE_JS` returns -- the fake
    does not re-implement the DOM search, because the script only runs in a real
    browser; what is under test here is the transport's reading of that contract
    and what it does when the contract says "nothing to click".

    `click_commits` is the URL the browser lands on after the click. `None` models
    a click that did not navigate at all, which is what `wait_for_url` must then
    reject -- and which is the only case allowed to fall back to a URL navigation.
    """

    def __init__(
        self,
        *,
        pages: dict[int, bytes] | None = None,
        click_outcome: dict | None = None,
        click_commits: str | None = None,
        fire_document: bool = True,
    ) -> None:
        self.pages = dict(pages or {})
        self.click_outcome = (
            {"clicked": False, "href": None, "seen": []}
            if click_outcome is None
            else click_outcome
        )
        self.click_commits = click_commits
        self.fire_document = fire_document
        self.gotos: list[str] = []
        self.evaluated: list[tuple[str, object]] = []
        self.waited: list[tuple[str, int]] = []
        self.removed: list[tuple[str, object]] = []
        self._listeners: dict[str, list] = {}
        self.url = "about:blank"

    def on(self, event: str, handler) -> None:
        self._listeners.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler) -> None:
        self.removed.append((event, handler))
        self._listeners[event].remove(handler)

    def _body_for(self, url: str) -> bytes:
        page = list_page_parts(url)
        number = page[1] if page else 0
        return self.pages.get(number, b"<script>var article_list={};</script>")

    def goto(self, url: str, *, wait_until: str, timeout: int):
        self.gotos.append(url)
        self.url = url
        return FakeResponse(url, body=self._body_for(url), request_headers=GOTO_SHAPE)

    def evaluate(self, script, arg=None):
        self.evaluated.append((script, arg))
        if arg is None:
            return {
                "url": self.url, "title": "股吧列表", "text_tokens": [],
                "structural_tokens": [], "visible_overlays": [],
            }
        # the pager click: commit first (so `page.url` is readable when the
        # document is picked), then fire the response event like a real browser
        if self.click_commits is None:
            return self.click_outcome
        self.url = self.click_commits
        if self.fire_document:
            response = FakeResponse(
                self.click_commits,
                body=self._body_for(self.click_commits),
                request_headers=CLICK_SHAPE,
            )
            for handler in list(self._listeners.get("response", [])):
                handler(response)
        return self.click_outcome

    def wait_for_url(self, predicate, *, wait_until: str, timeout: int) -> None:
        if not predicate(self.url):
            raise TimeoutError(f"predicate rejected the committed URL {self.url!r}")
        self.waited.append((wait_until, timeout))

    def title(self) -> str:
        return "股吧列表"


def test_list_pages_are_counted_even_when_the_click_scheme_is_off() -> None:
    """The negative control: with the scheme off, `click` must stay 0.

    Without this, "we paged by clicking" would be unfalsifiable -- there would be
    no run in which the counter is *known* to read zero.
    """
    page = FakeListPagingPage(click_commits=LIST_P2)
    transport = EastmoneyBrowserTransport(page)

    transport.get(LIST_P1, timeout=2.0)
    transport.get(LIST_P2, timeout=2.0)

    assert page.gotos == [LIST_P1, LIST_P2]
    assert page.evaluated == []
    assert transport.list_navigation_counts["click"] == 0
    assert transport.list_navigation_counts["goto"] == 2


def test_list_click_paging_opens_the_first_page_by_url() -> None:
    page = FakeListPagingPage()
    transport = EastmoneyBrowserTransport(page, list_click_paging=True)

    response = transport.get(LIST_P1, timeout=2.5)

    assert response.final_url == LIST_P1
    assert page.gotos == [LIST_P1]
    assert page.gotos[0:1] == [LIST_P1]
    # nothing to click yet: there is no previous page to have paged from
    assert page.evaluated == []
    assert transport.list_navigation_counts == {
        "click": 0, "goto": 1, "click_no_anchor": 0, "click_not_taken": 0,
    }


def test_list_click_paging_turns_the_next_page_by_clicking_the_pager() -> None:
    page = FakeListPagingPage(
        pages={1: b"page-one", 2: b"page-two"},
        click_outcome={"clicked": True, "href": LIST_P2, "seen": [LIST_P2]},
        click_commits=LIST_P2,
    )
    transport = EastmoneyBrowserTransport(page, list_click_paging=True)

    first = transport.get(LIST_P1, timeout=2.5)
    second = transport.get(LIST_P2, timeout=2.5)

    assert first.body == b"page-one"
    assert second.body == b"page-two"
    # the second page was NOT requested by URL: only the first goto happened
    assert page.gotos == [LIST_P1]
    assert page.evaluated == [(transport_module.CLICK_NEXT_LIST_PAGE_JS, {"page": 2})]
    assert page.waited == [("domcontentloaded", 2500)]
    assert transport.list_navigation_counts["click"] == 1
    assert transport.list_navigation_counts["goto"] == 1
    assert (second.diagnostics or {})["list_navigation"] == "click"
    # the response listener is removed, or one closure leaks per page turned
    assert page.removed and page._listeners["response"] == []


def test_list_click_paging_falls_back_to_url_when_no_pager_anchor_matches() -> None:
    page = FakeListPagingPage(click_outcome={"clicked": False, "href": None, "seen": []})
    transport = EastmoneyBrowserTransport(page, list_click_paging=True)

    transport.get(LIST_P1, timeout=2.0)
    response = transport.get(LIST_P2, timeout=2.0)

    assert response.final_url == LIST_P2
    assert page.gotos == [LIST_P1, LIST_P2]
    assert transport.list_navigation_counts["click_no_anchor"] == 1
    assert transport.list_navigation_counts["click"] == 0
    assert transport.list_navigation_counts["goto"] == 2


def test_list_click_paging_falls_back_when_the_click_did_not_navigate() -> None:
    page = FakeListPagingPage(
        click_outcome={"clicked": True, "href": LIST_P2, "seen": [LIST_P2]},
        click_commits=None,
    )
    transport = EastmoneyBrowserTransport(page, list_click_paging=True)

    transport.get(LIST_P1, timeout=2.0)
    response = transport.get(LIST_P2, timeout=2.0)

    # the fallback is only legitimate because the browser never left page 1
    assert response.final_url == LIST_P2
    assert page.gotos == [LIST_P1, LIST_P2]
    assert transport.list_navigation_counts["click_not_taken"] == 1
    assert transport.list_navigation_counts["click"] == 0


def test_list_click_paging_raises_instead_of_re_navigating_after_an_uncapturable_click() -> None:
    """A click that committed a page we cannot capture must not be re-issued.

    This is the D-018 trap: treating a *committed* navigation as a failed one
    duplicates the request against the source.
    """
    page = FakeListPagingPage(
        click_outcome={"clicked": True, "href": LIST_P2, "seen": [LIST_P2]},
        click_commits=LIST_P2,
        fire_document=False,
    )
    transport = EastmoneyBrowserTransport(page, list_click_paging=True)
    transport.get(LIST_P1, timeout=2.0)

    with pytest.raises(EastmoneyBrowserTransportError, match="main-document"):
        transport.get(LIST_P2, timeout=2.0)

    assert page.gotos == [LIST_P1]
    assert transport.list_navigation_counts["click"] == 0
    assert transport.list_navigation_counts["click_not_taken"] == 0


def test_list_click_paging_does_not_click_when_the_pages_are_not_adjacent() -> None:
    """A jump (`--start-page`, a retry, a gap) has no previous page to click from."""
    page = FakeListPagingPage()
    transport = EastmoneyBrowserTransport(page, list_click_paging=True)

    transport.get(LIST_P1, timeout=2.0)
    transport.get(LIST_P3, timeout=2.0)

    assert page.gotos == [LIST_P1, LIST_P3]
    assert page.evaluated == []
    assert transport.list_navigation_counts["click"] == 0
    assert transport.list_navigation_counts["goto"] == 2


def test_list_navigation_samples_are_read_with_all_headers() -> None:
    """The recorded shape must come off the wire, not out of the reader's hopes.

    `FakeRequest.headers` hides `sec-fetch-*`, exactly as Playwright does; if the
    transport ever reads that property instead of `all_headers()`, the samples go
    empty and this test fails -- which is the point.
    """
    page = FakeListPagingPage(
        click_outcome={"clicked": True, "href": LIST_P2, "seen": [LIST_P2]},
        click_commits=LIST_P2,
    )
    transport = EastmoneyBrowserTransport(page, list_click_paging=True)
    transport.get(LIST_P1, timeout=2.0)
    transport.get(LIST_P2, timeout=2.0)

    samples = transport.list_navigation_samples
    assert samples["click"] == CLICK_SHAPE
    assert samples["goto"] == GOTO_SHAPE
    assert samples["goto"]["sec-fetch-site"] == "none"
    assert samples["click"]["sec-fetch-site"] == "same-origin"


def test_list_click_paging_leaves_detail_urls_on_the_direct_path() -> None:
    page = FakeListPagingPage()
    transport = EastmoneyBrowserTransport(page, list_click_paging=True)

    transport.get(DETAIL, timeout=2.0)

    assert page.gotos == [DETAIL]
    assert page.evaluated == []
    assert transport.list_navigation_counts["goto"] == 0
