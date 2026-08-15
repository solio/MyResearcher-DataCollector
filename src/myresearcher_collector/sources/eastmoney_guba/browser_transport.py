"""Browser-owned transport for the approved Eastmoney HTML surfaces.

The caller owns the normal browser page/context.  This adapter navigates only
to approved public Eastmoney URLs and returns the exact main-document response
bytes to the existing Collector.  It never reads or exports browser cookies,
storage, challenge values or credentials.
"""

from __future__ import annotations

import math
import base64
import json
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .acquisition import HTTP_RESPONSE


_ALLOWED_HOSTS = {"guba.eastmoney.com", "caifuhao.eastmoney.com"}
_MAX_SOCKET_RESPONSE_BYTES = 32 * 1024 * 1024


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
    diagnostics: dict[str, object] | None = None

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    @property
    def capture_method(self) -> str:
        return HTTP_RESPONSE


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
                diagnostics={
                    "actual_url": str(getattr(self.page, "url", final_url)),
                    "page_title": str(self.page.title()) if callable(getattr(self.page, "title", None)) else None,
                },
            )
        except (EastmoneyBrowserBoundaryError, EastmoneyBrowserTransportError):
            raise
        except Exception as exc:
            raise EastmoneyBrowserTransportError(
                "browser navigation did not yield an Eastmoney response"
            ) from exc


class EastmoneyBrowserSocketTransport:
    """Request navigation from a long-lived local browser host.

    The Unix socket contains no browser cookies or storage.  Each response is
    the same sanitized main-document shape returned by
    :class:`EastmoneyBrowserTransport`.
    """

    def __init__(self, socket_path: str | Path) -> None:
        resolved = Path(socket_path).expanduser().resolve()
        if len(str(resolved).encode()) >= 100:
            raise ValueError("browser socket path is too long")
        self.socket_path = resolved

    def get(self, url: str, *, timeout: float) -> EastmoneyBrowserResponse:
        _validate_browser_url(url)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        request = json.dumps(
            {"method": "GET", "url": url, "timeout": timeout},
            separators=(",", ":"),
        ).encode() + b"\n"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(timeout + 5.0)
                client.connect(str(self.socket_path))
                client.sendall(request)
                response_bytes = bytearray()
                while b"\n" not in response_bytes:
                    chunk = client.recv(65536)
                    if not chunk:
                        break
                    response_bytes.extend(chunk)
                    if len(response_bytes) > _MAX_SOCKET_RESPONSE_BYTES:
                        raise EastmoneyBrowserTransportError(
                            "browser host response exceeded the size limit"
                        )
        except EastmoneyBrowserTransportError:
            raise
        except (OSError, TimeoutError) as exc:
            raise EastmoneyBrowserTransportError(
                "browser host is unavailable or navigation timed out"
            ) from exc
        if b"\n" not in response_bytes:
            raise EastmoneyBrowserTransportError(
                "browser host returned an incomplete response"
            )
        try:
            payload = json.loads(bytes(response_bytes).split(b"\n", 1)[0])
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EastmoneyBrowserTransportError(
                "browser host returned an invalid response"
            ) from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            message = (
                payload.get("error")
                if isinstance(payload, dict) and isinstance(payload.get("error"), str)
                else "browser host navigation failed"
            )
            raise EastmoneyBrowserTransportError(message)
        try:
            final_url = payload.get("final_url")
            if not isinstance(final_url, str):
                raise ValueError("missing final URL")
            _validate_browser_url(final_url)
            body = base64.b64decode(payload["body_base64"], validate=True)
            headers = payload.get("headers")
            if not isinstance(headers, dict):
                raise ValueError("invalid headers")
            sanitized_headers = {
                str(key).lower(): str(value)
                for key, value in headers.items()
                if str(key).lower() != "set-cookie"
            }
            return EastmoneyBrowserResponse(
                status_code=int(payload["status_code"]),
                body=body,
                headers=sanitized_headers,
                final_url=final_url,
            )
        except (KeyError, TypeError, ValueError, base64.binascii.Error) as exc:
            raise EastmoneyBrowserTransportError(
                "browser host response has an invalid shape"
            ) from exc
