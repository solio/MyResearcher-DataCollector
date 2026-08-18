"""Deterministic fake-Page tests for production browser pagination checks."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from myresearcher_collector.sources.xueqiu import BrowserTransportError, XueqiuBrowserTransport


class FakeResponse:
    def __init__(self, url: str) -> None:
        self.url = url
        self.status = 200
        self.request = SimpleNamespace(method="GET")

    def body(self) -> bytes:
        return b"{}"

    def all_headers(self) -> dict[str, str]:
        return {"content-type": "application/json"}


class _Expectation:
    def __init__(self, page: "FakePage", predicate) -> None:
        self.page = page
        self.predicate = predicate

    def __enter__(self) -> "_Expectation":
        self.page.expectation = self
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.page.expectation = None

    @property
    def value(self) -> FakeResponse:
        assert self.page.response is not None
        assert self.predicate(self.page.response)
        return self.page.response


class _Locator:
    def __init__(self, page: "FakePage") -> None:
        self.page = page

    def count(self) -> int:
        return 1

    @property
    def first(self) -> "_Locator":
        return self

    def click(self) -> None:
        self.page._emit()


class FakePage:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = iter(responses)
        self.response: FakeResponse | None = None
        self.expectation: _Expectation | None = None
        self.goto_urls: list[str] = []
        self.clicked = 0

    def expect_response(self, predicate, *, timeout: int):
        del timeout
        return _Expectation(self, predicate)

    def goto(self, url: str, *, wait_until: str) -> None:
        assert wait_until == "domcontentloaded"
        self.goto_urls.append(url)
        self._emit()

    def get_by_role(self, role: str, *, name: str, exact: bool):
        assert role == "link" and exact
        return _Locator(self)

    def get_by_text(self, name: str, *, exact: bool):
        del name, exact
        return _Locator(self)

    def _emit(self) -> None:
        self.response = next(self.responses)


def test_xq022a_browser_page_one_validates_symbol_and_page() -> None:
    page = FakePage([
        FakeResponse("https://xueqiu.com/query/v1/symbol/search/status.json?symbol=SH600519&page=1")
    ])
    response = XueqiuBrowserTransport(page).fetch_page(
        "600519", page=1, last_id=None, timeout=1.0
    )
    assert response.final_url.endswith("symbol=SH600519&page=1")
    assert page.goto_urls == ["https://xueqiu.com/S/SH600519"]


def test_xq022b_browser_page_two_validates_last_id_chain() -> None:
    page = FakePage([
        FakeResponse(
            "https://xueqiu.com/query/v1/symbol/search/status.json?symbol=SH600519&page=2&last_id=910002"
        )
    ])
    response = XueqiuBrowserTransport(page).fetch_page(
        "600519", page=2, last_id="910002", timeout=1.0
    )
    assert "last_id=910002" in (response.final_url or "")
    assert page.clicked == 0


def test_xq022c_wrong_browser_last_id_is_pagination_failure() -> None:
    page = FakePage([
        FakeResponse(
            "https://xueqiu.com/query/v1/symbol/search/status.json?symbol=SH600519&page=2&last_id=WRONG"
        )
    ])
    with pytest.raises(BrowserTransportError):
        XueqiuBrowserTransport(page).fetch_page(
            "600519", page=2, last_id="910002", timeout=1.0
        )


def test_xq022d_unsafe_browser_query_values_are_not_retained() -> None:
    page = FakePage([
        FakeResponse(
            "https://xueqiu.com/query/v1/symbol/search/status.json?symbol=SH600519&page=1&signature=secret&xq_a_token=secret"
        )
    ])
    response = XueqiuBrowserTransport(page).fetch_page(
        "600519", page=1, last_id=None, timeout=1.0
    )
    assert "secret" not in (response.final_url or "")
    assert "signature" not in (response.final_url or "")
    assert "xq_a_token" not in (response.final_url or "")


def test_browser_response_runs_runtime_safety_check_before_acceptance() -> None:
    page = FakePage([
        FakeResponse(
            "https://xueqiu.com/query/v1/symbol/search/status.json?symbol=SH600519&page=1"
        )
    ])
    checks: list[str] = []
    response = XueqiuBrowserTransport(
        page, safety_check=lambda: checks.append("safe")
    ).fetch_page("600519", page=1, last_id=None, timeout=1.0)
    assert response.status_code == 200
    assert checks == ["safe"]


def test_browser_response_rejects_runtime_safety_failure() -> None:
    page = FakePage([
        FakeResponse(
            "https://xueqiu.com/query/v1/symbol/search/status.json?symbol=SH600519&page=1"
        )
    ])

    def reject() -> None:
        raise RuntimeError("verification visible")

    with pytest.raises(BrowserTransportError):
        XueqiuBrowserTransport(page, safety_check=reject).fetch_page(
            "600519", page=1, last_id=None, timeout=1.0
        )
