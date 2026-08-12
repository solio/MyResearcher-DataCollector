"""Long-lived local browser host for Eastmoney production navigation."""

from __future__ import annotations

import base64
import json
import os
import signal
import socket
import stat
import time
from pathlib import Path
from types import FrameType
from typing import Any

from .browser_transport import (
    EastmoneyBrowserResponse,
    EastmoneyBrowserTransport,
    _validate_browser_url,
)
from .collector import EastmoneyGubaCollector
from .parser import is_access_block_page, parse_list_page


_MAX_REQUEST_BYTES = 64 * 1024


class _PreflightCache:
    def __init__(self, url: str, payload: dict[str, Any]) -> None:
        self.url = url
        self.payload = payload
        self.consumed = False

    def take(self, request: dict[str, Any]) -> dict[str, Any] | None:
        if self.consumed or request.get("method") != "GET" or request.get("url") != self.url:
            return None
        self.consumed = True
        return self.payload


class BrowserHostConfigError(ValueError):
    """The local browser host cannot start safely."""


def _prepare_socket_path(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    if len(str(path).encode()) >= 100:
        raise BrowserHostConfigError("browser socket path is too long")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        mode = path.lstat().st_mode
        if not stat.S_ISSOCK(mode):
            raise BrowserHostConfigError(
                "browser socket path exists and is not a Unix socket"
            )
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.2)
            probe.connect(str(path))
        except OSError:
            path.unlink()
        else:
            raise BrowserHostConfigError("browser socket is already active")
        finally:
            probe.close()
    return path


def _read_request(connection: socket.socket) -> dict[str, Any]:
    request = bytearray()
    while b"\n" not in request:
        chunk = connection.recv(4096)
        if not chunk:
            break
        request.extend(chunk)
        if len(request) > _MAX_REQUEST_BYTES:
            raise ValueError("request exceeded the size limit")
    if b"\n" not in request:
        raise ValueError("request is incomplete")
    value = json.loads(bytes(request).split(b"\n", 1)[0])
    if not isinstance(value, dict):
        raise ValueError("request is not an object")
    return value


def _make_server(path: Path) -> socket.socket:
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(path))
        os.chmod(path, 0o600)
        server.listen(8)
        server.settimeout(0.5)
        return server
    except Exception:
        server.close()
        raise


def _send_payload(connection: socket.socket, payload: dict[str, Any]) -> bool:
    try:
        connection.sendall(
            json.dumps(payload, separators=(",", ":")).encode() + b"\n"
        )
        return True
    except (BrokenPipeError, ConnectionResetError):
        # The CLI/operator may cancel while a browser navigation is still in
        # flight.  That ends this request, not the long-lived host.
        return False


def _handle_request(transport: EastmoneyBrowserTransport, request: dict[str, Any]) -> dict[str, Any]:
    if request.get("method") != "GET":
        raise ValueError("only GET is supported")
    url = request.get("url")
    timeout = request.get("timeout")
    if not isinstance(url, str) or not isinstance(timeout, (int, float)):
        raise ValueError("request URL or timeout is invalid")
    response = transport.get(url, timeout=float(timeout))
    return {
        "ok": True,
        "status_code": response.status_code,
        "body_base64": base64.b64encode(response.body).decode("ascii"),
        "headers": response.headers,
        "final_url": response.final_url,
    }


def _captured_document_response(response: Any) -> EastmoneyBrowserResponse:
    final_url = str(response.url)
    _validate_browser_url(final_url)
    return EastmoneyBrowserResponse(
        status_code=int(response.status),
        body=bytes(response.body()),
        headers={
            str(key).lower(): str(value)
            for key, value in response.all_headers().items()
            if str(key).lower() != "set-cookie"
        },
        final_url=final_url,
    )


def _wait_for_operator_verification(
    *,
    page: Any,
    captured_documents: list[Any],
    preflight_url: str,
    preflight_stock: str,
    timeout_seconds: float,
) -> tuple[EastmoneyBrowserResponse, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        time.sleep(1.0)
        try:
            current_html = page.content()
        except Exception:
            continue
        if is_access_block_page(current_html) or "var article_list=" not in current_html:
            continue
        for captured in reversed(captured_documents):
            try:
                response = _captured_document_response(captured)
            except Exception:
                continue
            if response.final_url != preflight_url or response.status_code != 200:
                continue
            if is_access_block_page(response.text):
                continue
            try:
                parsed = parse_list_page(response.text, preflight_stock)
            except ValueError:
                continue
            return response, parsed
    raise BrowserHostConfigError(
        "operator verification did not yield an exact approved list response before timeout"
    )


def serve_browser_host(
    *,
    socket_path: str | Path,
    profile_dir: str | Path,
    channel: str = "chrome",
    headless: bool = True,
    min_interval_seconds: float = 3.0,
    preflight_stock: str = "601012",
    operator_wait_seconds: float = 0.0,
) -> None:
    """Own one browser context until interrupted and serve sequential GETs."""
    if min_interval_seconds < 2.5:
        raise BrowserHostConfigError(
            "browser host min_interval_seconds must be at least 2.5"
        )
    if len(preflight_stock) != 6 or not preflight_stock.isdigit():
        raise BrowserHostConfigError("browser host preflight stock must be six digits")
    if operator_wait_seconds < 0:
        raise BrowserHostConfigError("operator_wait_seconds cannot be negative")
    if operator_wait_seconds > 0 and headless:
        raise BrowserHostConfigError(
            "operator verification wait requires --headful"
        )
    path = _prepare_socket_path(socket_path)
    profile = Path(profile_dir).expanduser().resolve()
    profile.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserHostConfigError(
            "Playwright is required; install with: pip install -e '.[browser]'"
        ) from exc

    stop = False

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        nonlocal stop
        stop = True

    prior_int = signal.signal(signal.SIGINT, request_stop)
    prior_term = signal.signal(signal.SIGTERM, request_stop)
    server = _make_server(path)
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                channel=channel,
                headless=headless,
            )
            page = context.pages[0] if context.pages else context.new_page()
            transport = EastmoneyBrowserTransport(page)
            captured_documents: list[Any] = []

            def capture_document(response: Any) -> None:
                try:
                    if response.request.resource_type == "document":
                        captured_documents.append(response)
                except Exception:
                    return

            page.on("response", capture_document)
            preflight_url = EastmoneyGubaCollector.list_url(preflight_stock, 1)
            preflight_response = transport.get(preflight_url, timeout=30.0)
            operator_verified = False
            if preflight_response.status_code != 200:
                raise BrowserHostConfigError(
                    f"Eastmoney browser preflight returned HTTP {preflight_response.status_code}"
                )
            if is_access_block_page(preflight_response.text):
                if operator_wait_seconds <= 0:
                    raise BrowserHostConfigError(
                        "Eastmoney browser preflight returned access verification"
                    )
                print(
                    json.dumps(
                        {
                            "status": "WAITING_FOR_OPERATOR_VERIFICATION",
                            "automation": False,
                            "timeout_seconds": operator_wait_seconds,
                            "instruction": (
                                "Complete the visible verification manually; "
                                "the host will not inspect or submit it."
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                preflight_response, preflight_page = _wait_for_operator_verification(
                    page=page,
                    captured_documents=captured_documents,
                    preflight_url=preflight_url,
                    preflight_stock=preflight_stock,
                    timeout_seconds=operator_wait_seconds,
                )
                operator_verified = True
            else:
                preflight_page = parse_list_page(
                    preflight_response.text, preflight_stock
                )
            preflight_payload = {
                "ok": True,
                "status_code": preflight_response.status_code,
                "body_base64": base64.b64encode(preflight_response.body).decode("ascii"),
                "headers": preflight_response.headers,
                "final_url": preflight_response.final_url,
                "served_from_preflight_cache": True,
            }
            preflight_cache = _PreflightCache(preflight_url, preflight_payload)
            last_navigation = time.monotonic()
            print(
                json.dumps(
                    {
                        "status": "READY",
                        "runtime_mode": "EXPERIMENTAL_OPERATOR_ASSISTED",
                        "unattended_production_ready": False,
                        "socket": str(path),
                        "headless": headless,
                        "browser_context": "LONG_LIVED_PERSISTENT",
                        "preflight_stock": preflight_stock,
                        "preflight_rows": len(preflight_page.rows)
                        + len(preflight_page.out_of_scope_rows),
                        "minimum_navigation_interval_seconds": min_interval_seconds,
                        "operator_verification_observed": operator_verified,
                        "storage_inspected_or_exported": False,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            while not stop:
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    continue
                with connection:
                    try:
                        request = _read_request(connection)
                        payload = preflight_cache.take(request)
                        if payload is None:
                            remaining = min_interval_seconds - (
                                time.monotonic() - last_navigation
                            )
                            if remaining > 0:
                                time.sleep(remaining)
                            try:
                                payload = _handle_request(transport, request)
                            finally:
                                last_navigation = time.monotonic()
                    except Exception as exc:
                        payload = {
                            "ok": False,
                            "error": f"{type(exc).__name__}: browser navigation failed",
                        }
                    _send_payload(connection, payload)
            context.close()
    finally:
        server.close()
        signal.signal(signal.SIGINT, prior_int)
        signal.signal(signal.SIGTERM, prior_term)
        if path.exists() and stat.S_ISSOCK(path.lstat().st_mode):
            path.unlink()
