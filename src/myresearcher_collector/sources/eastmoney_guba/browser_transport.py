"""Browser-owned transport for the approved Eastmoney HTML surfaces.

The caller owns the normal browser page/context.  This adapter navigates only
to approved public Eastmoney URLs and returns the exact main-document response
bytes to the existing Collector.  It never reads or exports browser cookies,
storage, challenge values or credentials.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


_ALLOWED_HOSTS = {"guba.eastmoney.com", "caifuhao.eastmoney.com"}


class EastmoneyBrowserTransportError(OSError):
    """A normal browser navigation did not yield an approved response."""


class EastmoneyBrowserBoundaryError(RuntimeError):
    """A requested or final URL leaves the approved source boundary."""


def _validate_browser_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise EastmoneyBrowserBoundaryError("browser URL is invalid") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise EastmoneyBrowserBoundaryError(
            "browser navigation is outside approved Eastmoney HTTPS hosts"
        )


@dataclass(frozen=True)
class EastmoneyBrowserResponse:
    status_code: int
    body: bytes
    headers: dict[str, str]
    final_url: str | None = None

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class EastmoneyBrowserTransport:
    """Use a caller-owned synchronous Playwright-like Page for HTML GETs."""

    def __init__(self, page: Any) -> None:
        self.page = page

    def get(self, url: str, *, timeout: float) -> EastmoneyBrowserResponse:
        _validate_browser_url(url)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        try:
            response = self.page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=max(1, int(timeout * 1000)),
            )
            if response is None:
                raise EastmoneyBrowserTransportError(
                    "browser navigation returned no main-document response"
                )
            final_url = str(response.url)
            _validate_browser_url(final_url)
            body = bytes(response.body())
            headers = {
                str(key).lower(): str(value)
                for key, value in response.all_headers().items()
                if str(key).lower() != "set-cookie"
            }
            return EastmoneyBrowserResponse(
                status_code=int(response.status),
                body=body,
                headers=headers,
                final_url=final_url,
            )
        except (EastmoneyBrowserBoundaryError, EastmoneyBrowserTransportError):
            raise
        except Exception as exc:
            raise EastmoneyBrowserTransportError(
                "browser navigation did not yield an Eastmoney response"
            ) from exc
