"""Xueqiu public-DOM control through the user's normal macOS Chrome.

The adapter uses Chrome's supported Apple Events JavaScript bridge.  It does
not launch Chrome with automation flags, copy a profile, or inspect browser
cookies/storage.  Only tabs created by this adapter are closed.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from ..eastmoney_guba.existing_chrome import (
    CLOSE_TAB,
    CREATE_TAB,
    EXECUTE_JS,
    FRONTMOST_APP,
    NAVIGATE_TAB,
    TAB_LOADING,
    _osascript,
)
from .browser_transport import ENTRY_URL
from .collector import symbol_for
from .dom_scripts import (
    ACTIVE_PAGE_JS,
    CLICK_PAGE_JS,
    DETAIL_STATE_JS,
    PAGE_STATE_JS,
    READ_PAGE_JS,
    clean_embedded_status_json as _clean_embedded_status_json,
)
from .dom_transport import XueqiuDomTransportError


CREATE_BACKGROUND_TAB = r'''
on run argv
  tell application "Google Chrome"
    if (count of windows) is 0 then make new window
    set targetURL to item 1 of argv
    set w to front window
    set t to make new tab at end of tabs of w with properties {URL:targetURL}
    return ((id of w) as text) & "|" & ((id of t) as text)
  end tell
end run
'''


CREATE_TAB_IN_WINDOW = r'''
on run argv
  tell application "Google Chrome"
    set windowID to (item 1 of argv) as integer
    set targetURL to item 2 of argv
    set w to first window whose id is windowID
    set t to make new tab at end of tabs of w with properties {URL:targetURL}
    return ((id of w) as text) & "|" & ((id of t) as text)
  end tell
end run
'''


# Kept as a compatibility alias for callers/tests that imported the old name.
# The script itself is now explicitly background-only.
CREATE_ACTIVE_TAB = CREATE_BACKGROUND_TAB


CHROME_FOCUS_STATE = r'''
on run argv
  tell application "Google Chrome"
    if (count of windows) is 0 then return "NO_WINDOWS"
    set w to front window
    set t to active tab of w
    return ((id of w) as text) & "|" & ((id of t) as text) & "|" & ((active tab index of w) as text)
  end tell
end run
'''


def _validate_xueqiu_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"xueqiu.com", "www.xueqiu.com"}
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("browser navigation is outside approved Xueqiu HTTPS hosts")


def _redact_url(url: str) -> str:
    parts = urlsplit(url)
    names = [name for name, _ in parse_qsl(parts.query, keep_blank_values=True)]
    query = "&".join(f"{name}=<redacted>" for name in names)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


class XueqiuExistingChromePage:
    """Small page-shaped facade consumed by :class:`XueqiuDomTransport`."""

    acquisition_mode = "existing-chrome"
    profile_mode = "existing-user"
    launch_method = "macOS Apple Events -> existing Google Chrome"

    def __init__(
        self,
        *,
        script_runner: Callable[..., str] = _osascript,
        poll_interval_seconds: float = 0.5,
        timeout_ms: int = 20_000,
        monotonic_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.script_runner = script_runner
        self.poll_interval_seconds = poll_interval_seconds
        self.timeout_ms = timeout_ms
        self.monotonic_fn = monotonic_fn
        self.sleep_fn = sleep_fn
        self.window_id: str | None = None
        self.tab_id: str | None = None
        self.navigation_urls: list[str] = []

    def observe_user_focus(self) -> dict[str, str | None]:
        """Read focus state without activating Chrome or changing any tab."""
        try:
            frontmost = self.script_runner(FRONTMOST_APP).strip() or None
        except Exception:
            frontmost = None
        try:
            raw = self.script_runner(CHROME_FOCUS_STATE).strip()
            parts = raw.split("|", 2)
            if len(parts) != 3:
                return {
                    "frontmost_application": frontmost,
                    "chrome_window_id": None,
                    "chrome_active_tab_id": None,
                    "chrome_active_tab_index": None,
                }
            return {
                "frontmost_application": frontmost,
                "chrome_window_id": parts[0],
                "chrome_active_tab_id": parts[1],
                "chrome_active_tab_index": parts[2],
            }
        except Exception:
            return {
                "frontmost_application": frontmost,
                "chrome_window_id": None,
                "chrome_active_tab_id": None,
                "chrome_active_tab_index": None,
            }

    def _run_script(self, script: str, *values: object) -> str:
        try:
            return self.script_runner(script, *values)
        except XueqiuDomTransportError:
            raise
        except Exception as exc:
            raise XueqiuDomTransportError(
                "existing Chrome Apple Event execution failed"
            ) from exc

    def _tab_identity(self) -> tuple[str, str]:
        if self.window_id is None or self.tab_id is None:
            raise XueqiuDomTransportError("existing Chrome tab is unavailable")
        return self.window_id, self.tab_id

    def _open_or_navigate(self, url: str) -> None:
        _validate_xueqiu_url(url)
        if self.window_id is None or self.tab_id is None:
            identity = self._run_script(CREATE_TAB, url)
            try:
                self.window_id, self.tab_id = identity.split("|", 1)
            except ValueError as exc:
                raise XueqiuDomTransportError(
                    "existing Chrome did not return a tab identity"
                ) from exc
        else:
            window_id, tab_id = self._tab_identity()
            self._run_script(NAVIGATE_TAB, window_id, tab_id, url)

    def _execute(self, script: str, *, window_id: str | None = None, tab_id: str | None = None) -> str:
        if window_id is None or tab_id is None:
            window_id, tab_id = self._tab_identity()
        return self._run_script(EXECUTE_JS, window_id, tab_id, script)

    def _execute_json(self, script: str, *, window_id: str | None = None, tab_id: str | None = None) -> Any:
        raw = self._execute(script, window_id=window_id, tab_id=tab_id)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise XueqiuDomTransportError(
                "existing Chrome returned invalid JavaScript JSON"
            ) from exc

    def _record_url(self, url: str) -> None:
        value = _redact_url(url)
        if not self.navigation_urls or self.navigation_urls[-1] != value:
            self.navigation_urls.append(value)

    def open_stock(self, stock_code: str) -> None:
        url = ENTRY_URL.format(symbol=symbol_for(stock_code))
        self._open_or_navigate(url)
        self._record_url(url)

    def wait_posts_loaded(self, timeout_ms: int) -> None:
        deadline = self.monotonic_fn() + timeout_ms / 1000
        last: dict[str, Any] | None = None
        while self.monotonic_fn() < deadline:
            state = self._execute_json(PAGE_STATE_JS)
            if not isinstance(state, dict):
                raise XueqiuDomTransportError("existing Chrome page state is invalid")
            last = state
            url = str(state.get("url") or "")
            if url:
                self._record_url(url)
            if int(state.get("posts") or 0) > 0:
                return
            if state.get("challenge"):
                raise XueqiuDomTransportError("Xueqiu access verification is visible")
            self.sleep_fn(self.poll_interval_seconds)
        raise XueqiuDomTransportError(
            f"Xueqiu posts did not load in existing Chrome; observed={last}"
        )

    def active_page(self) -> int:
        raw = self._execute(ACTIVE_PAGE_JS)
        try:
            return int(raw.strip())
        except ValueError as exc:
            raise XueqiuDomTransportError("active page is not observable") from exc

    def read_dom_page(self) -> list[dict[str, Any]]:
        value = self._execute_json(READ_PAGE_JS)
        if not isinstance(value, list):
            raise XueqiuDomTransportError("existing Chrome post DOM is not a list")
        return value

    def goto_page(self, page_no: int) -> None:
        if page_no < 1:
            raise ValueError("page_no must be positive")
        result = self._execute_json(CLICK_PAGE_JS % page_no)
        if not isinstance(result, dict) or result.get("clicked") is not True:
            raise XueqiuDomTransportError(
                f"pagination control {page_no} is unavailable"
            )

    def read_detail_status(self, url: str) -> dict[str, Any]:
        _validate_xueqiu_url(url)
        collector_window_id, _ = self._tab_identity()
        identity = self._run_script(CREATE_TAB_IN_WINDOW, collector_window_id, url)
        try:
            detail_window_id, detail_tab_id = identity.split("|", 1)
        except ValueError as exc:
            raise XueqiuDomTransportError(
                "existing Chrome did not return a detail tab identity"
            ) from exc
        try:
            deadline = self.monotonic_fn() + self.timeout_ms / 1000
            last_state: dict[str, Any] | None = None
            while self.monotonic_fn() < deadline:
                state = self._execute_json(
                    DETAIL_STATE_JS,
                    window_id=detail_window_id,
                    tab_id=detail_tab_id,
                )
                if not isinstance(state, dict):
                    raise XueqiuDomTransportError("detail page state is invalid")
                last_state = state
                status = state.get("status")
                embedded = state.get("embeddedStatusJson")
                if status is None and isinstance(embedded, str):
                    try:
                        status = json.loads(_clean_embedded_status_json(embedded))
                    except json.JSONDecodeError as exc:
                        raise XueqiuDomTransportError(
                            "embedded SNOWMAN_STATUS is not valid JSON"
                        ) from exc
                if isinstance(status, dict):
                    return status
                if state.get("challenge"):
                    raise XueqiuDomTransportError(
                        "Xueqiu detail access verification is visible"
                    )
                self.sleep_fn(self.poll_interval_seconds)
            observed = None
            if last_state is not None:
                observed = {
                    "url": _redact_url(str(last_state.get("url") or "")),
                    "title": str(last_state.get("title") or ""),
                    "readyState": str(last_state.get("readyState") or ""),
                    "challenge": list(last_state.get("challenge") or []),
                }
            raise XueqiuDomTransportError(
                f"Xueqiu detail timestamp is unavailable; observed={observed}"
            )
        finally:
            self._run_script(CLOSE_TAB, detail_window_id, detail_tab_id)

    def close(self) -> None:
        if self.window_id is None or self.tab_id is None:
            return
        try:
            self._run_script(CLOSE_TAB, self.window_id, self.tab_id)
        finally:
            self.window_id = None
            self.tab_id = None
