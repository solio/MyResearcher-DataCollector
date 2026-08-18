"""Narrow browser-owned transport boundary for Xueqiu.

The adapter never constructs the discussion API URL or challenge parameters.
The supplied browser page creates those requests; this class only observes the
page's own JSON response.  Tests inject a fake object implementing the same
``fetch_page`` protocol and therefore never start a browser.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from urllib.parse import parse_qsl, parse_qs, urlencode, urlsplit, urlunsplit


ENTRY_URL = "https://xueqiu.com/S/{symbol}"
DISCUSSION_PATH = "/query/v1/symbol/search/status.json"
_SAFE_QUERY_FIELDS = {
    "symbol", "count", "comment", "hl", "source", "sort", "page", "q", "type", "last_id",
}


def redact_xueqiu_url(url: str | None) -> str | None:
    """Drop browser-added challenge/signature query values from provenance."""
    if url is None:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.hostname != "xueqiu.com":
        return url
    safe = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key in _SAFE_QUERY_FIELDS]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(safe), ""))


@dataclass(frozen=True)
class XueqiuResponse:
    status_code: int
    body: bytes
    headers: dict[str, str]
    final_url: str | None = None

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class XueqiuTransport(Protocol):
    def fetch_page(
        self,
        stock_code: str,
        *,
        page: int,
        last_id: str | None,
        timeout: float,
    ) -> Any:
        """Return the browser-observed discussion JSON response."""


class BrowserTransportError(RuntimeError):
    """The browser could not observe an approved source response."""


class XueqiuBrowserTransport:
    """Observe page-owned requests from a Playwright-like synchronous Page.

    The page/context is created by the caller so cookie/session/challenge state
    remains browser-owned and is never exposed to Collector code.
    """

    def __init__(
        self,
        page: Any,
        *,
        response_timeout_ms: int = 20_000,
        safety_check: Callable[[], None] | None = None,
    ) -> None:
        self.page = page
        self.response_timeout_ms = response_timeout_ms
        self.safety_check = safety_check

    @staticmethod
    def symbol_for(stock_code: str) -> str:
        if not isinstance(stock_code, str) or len(stock_code) != 6 or not stock_code.isdigit():
            raise ValueError("stock_code must be six decimal digits")
        if not stock_code.startswith(("0", "3", "6")):
            raise ValueError("only A-share stock codes are supported")
        return ("SH" if stock_code.startswith("6") else "SZ") + stock_code

    def fetch_page(
        self,
        stock_code: str,
        *,
        page: int,
        last_id: str | None,
        timeout: float,
    ) -> XueqiuResponse:
        symbol = self.symbol_for(stock_code)
        if page < 1:
            raise ValueError("page must be positive")
        predicate = lambda response: (
            DISCUSSION_PATH in response.url
            and response.request.method == "GET"
        )
        try:
            with self.page.expect_response(predicate, timeout=int(timeout * 1000)) as info:
                if page == 1:
                    self.page.goto(ENTRY_URL.format(symbol=symbol), wait_until="domcontentloaded")
                else:
                    self._click_page(page)
            response = info.value
            if self.safety_check is not None:
                self.safety_check()
            safe_url = redact_xueqiu_url(response.url)
            self._validate_response_pagination(
                safe_url, symbol=symbol, page=page, last_id=last_id
            )
            body = response.body()
            headers = {str(k).lower(): str(v) for k, v in response.all_headers().items()}
            return XueqiuResponse(
                status_code=int(response.status),
                body=bytes(body),
                headers=headers,
                final_url=safe_url,
            )
        except Exception as exc:
            raise BrowserTransportError("browser session did not yield the discussion response") from exc

    @staticmethod
    def _validate_response_pagination(
        url: str | None,
        *,
        symbol: str,
        page: int,
        last_id: str | None,
    ) -> None:
        if not url:
            raise BrowserTransportError("browser response URL is missing")
        parsed = urlsplit(url)
        if parsed.hostname != "xueqiu.com" or parsed.path != DISCUSSION_PATH:
            raise BrowserTransportError("browser response is outside the approved discussion route")
        query = parse_qs(parsed.query, keep_blank_values=True)
        if query.get("symbol", [None])[-1] != symbol:
            raise BrowserTransportError("browser response symbol does not match requested scope")
        try:
            response_page = int(query.get("page", [""])[-1])
        except ValueError as exc:
            raise BrowserTransportError("browser response page is invalid") from exc
        if response_page != page:
            raise BrowserTransportError("browser response page does not match requested page")
        if page > 1 and query.get("last_id", [None])[-1] != last_id:
            raise BrowserTransportError("browser response last_id does not match pagination chain")

    def _click_page(self, page: int) -> None:
        # The visible pagination control is the browser's source of query and
        # signature state; no API parameters are synthesized here.
        locator = self.page.get_by_role("link", name=str(page), exact=True)
        if locator.count() == 0:
            locator = self.page.get_by_text(str(page), exact=True)
        if locator.count() == 0:
            raise BrowserTransportError(f"visible pagination control {page} is unavailable")
        locator.first.click()
