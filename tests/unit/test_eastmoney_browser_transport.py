"""Deterministic tests for the browser-owned Eastmoney HTML transport."""

from __future__ import annotations

import pytest

from myresearcher_collector.sources.eastmoney_guba import (
    EastmoneyBrowserBoundaryError,
    EastmoneyBrowserTransport,
    EastmoneyBrowserTransportError,
)


class FakeResponse:
    def __init__(
        self,
        url: str,
        *,
        status: int = 200,
        body: bytes = b"<script>var article_list={};</script>",
    ) -> None:
        self.url = url
        self.status = status
        self._body = body

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
