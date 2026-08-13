"""Existing-user Google Chrome DOM acquisition through macOS Apple Events."""

from __future__ import annotations

import json
import math
import subprocess
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Callable

from .acquisition import AcquiredDocument, BROWSER_DOM_SNAPSHOT
from .browser_transport import _validate_browser_url


CREATE_TAB = r'''
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

NAVIGATE_TAB = r'''
on run argv
  tell application "Google Chrome"
    set windowID to (item 1 of argv) as integer
    set tabID to (item 2 of argv) as integer
    set targetURL to item 3 of argv
    set w to first window whose id is windowID
    set t to first tab of w whose id is tabID
    set URL of t to targetURL
    return targetURL
  end tell
end run
'''

TAB_LOADING = r'''
on run argv
  tell application "Google Chrome"
    set windowID to (item 1 of argv) as integer
    set tabID to (item 2 of argv) as integer
    set w to first window whose id is windowID
    set t to first tab of w whose id is tabID
    return (loading of t) as text
  end tell
end run
'''

EXECUTE_JS = r'''
on run argv
  tell application "Google Chrome"
    set windowID to (item 1 of argv) as integer
    set tabID to (item 2 of argv) as integer
    set scriptText to item 3 of argv
    set w to first window whose id is windowID
    set t to first tab of w whose id is tabID
    return execute t javascript scriptText
  end tell
end run
'''

CLOSE_TAB = r'''
on run argv
  tell application "Google Chrome"
    set windowID to (item 1 of argv) as integer
    set tabID to (item 2 of argv) as integer
    set w to first window whose id is windowID
    close (first tab of w whose id is tabID)
  end tell
end run
'''

DOM_SNAPSHOT_JS = r'''
JSON.stringify({
  html: document.documentElement ? document.documentElement.outerHTML : null,
  observedUrl: location.href,
  title: document.title,
  readyState: document.readyState
})
'''

FRONTMOST_APP = r'''
tell application "System Events"
  set frontmostProcess to first application process whose frontmost is true
  return name of frontmostProcess
end tell
'''


class ExistingChromeAcquisitionError(OSError):
    """Existing Chrome could not produce a trustworthy acquired document."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _osascript(source: str, *values: object) -> str:
    try:
        completed = subprocess.run(
            ["osascript", "-e", source, "--", *(str(value) for value in values)],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExistingChromeAcquisitionError(
            "navigation_failure", "Google Chrome Apple Event execution failed"
        ) from exc
    if completed.returncode != 0:
        message = " ".join(completed.stderr.strip().split())
        raise ExistingChromeAcquisitionError(
            "navigation_failure", message or "Google Chrome Apple Event failed"
        )
    return completed.stdout.strip()


class EastmoneyExistingChromeDomTransport:
    """Navigate one dedicated tab and return the parser-consumed DOM snapshot."""

    capture_method = BROWSER_DOM_SNAPSHOT

    def __init__(
        self,
        *,
        script_runner: Callable[..., str] = _osascript,
        poll_interval_seconds: float = 0.5,
        settle_seconds: float = 1.0,
        clock: Callable[[], datetime] | None = None,
        monotonic_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
        focus_log_path: str | Path = "runtime/logs/eastmoney-focus.jsonl",
    ) -> None:
        self.script_runner = script_runner
        self.poll_interval_seconds = poll_interval_seconds
        self.settle_seconds = settle_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.monotonic_fn = monotonic_fn
        self.sleep_fn = sleep_fn
        self.window_id: str | None = None
        self.tab_id: str | None = None
        self.focus_log_path = Path(focus_log_path)

    def _frontmost(self) -> str:
        try:
            value = subprocess.run(
                ["osascript", "-e", FRONTMOST_APP], capture_output=True,
                text=True, timeout=5, check=False,
            )
            return value.stdout.strip() or "UNKNOWN"
        except Exception:
            return "UNKNOWN"

    def _run_script(self, operation: str, script: str, *values: object) -> str:
        before = self._frontmost()
        result = self.script_runner(script, *values)
        after = self._frontmost()
        row = {
            "operation": operation, "frontmost_before": before,
            "frontmost_after": after,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }
        try:
            self.focus_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.focus_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            pass
        return result

    def get(self, url: str, *, timeout: float) -> AcquiredDocument:
        _validate_browser_url(url)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        try:
            if self.window_id is None or self.tab_id is None:
                identity = self._run_script("CREATE_TAB", CREATE_TAB, url)
                self.window_id, self.tab_id = identity.split("|", 1)
            else:
                self._run_script("NAVIGATE_TAB", NAVIGATE_TAB, self.window_id, self.tab_id, url)
            self._wait_loaded(timeout)
            encoded = self._run_script("EXECUTE_JS", EXECUTE_JS, self.window_id, self.tab_id, DOM_SNAPSHOT_JS
            )
        except ExistingChromeAcquisitionError:
            raise
        except Exception as exc:
            raise ExistingChromeAcquisitionError(
                "navigation_failure", "existing Chrome navigation failed"
            ) from exc
        try:
            snapshot = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise ExistingChromeAcquisitionError(
                "invalid_document", "Chrome DOM snapshot is not valid JSON"
            ) from exc
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("html"), str):
            raise ExistingChromeAcquisitionError(
                "invalid_document", "Chrome DOM snapshot has no document HTML"
            )
        observed_url = snapshot.get("observedUrl")
        if not isinstance(observed_url, str):
            raise ExistingChromeAcquisitionError(
                "invalid_document", "Chrome DOM snapshot has no observed URL"
            )
        _validate_browser_url(observed_url)
        payload = snapshot["html"].encode("utf-8")
        if not payload:
            raise ExistingChromeAcquisitionError(
                "invalid_document", "Chrome DOM snapshot is empty"
            )
        return AcquiredDocument(
            payload=payload,
            request_url=url,
            observed_url=observed_url,
            capture_method=BROWSER_DOM_SNAPSHOT,
            fetched_at=self.clock(),
            http_status=None,
            content_type=None,
            metadata={
                "document_title": str(snapshot.get("title") or ""),
                "ready_state": str(snapshot.get("readyState") or ""),
                "serialization": "document.documentElement.outerHTML:utf-8",
            },
        )

    def _wait_loaded(self, timeout: float) -> None:
        if self.window_id is None or self.tab_id is None:
            raise ExistingChromeAcquisitionError(
                "navigation_failure", "Chrome tab was not created"
            )
        deadline = self.monotonic_fn() + timeout
        while self.monotonic_fn() < deadline:
            loading = self._run_script("TAB_LOADING", TAB_LOADING, self.window_id, self.tab_id)
            if loading.lower() == "false":
                if self.settle_seconds > 0:
                    self.sleep_fn(self.settle_seconds)
                return
            self.sleep_fn(self.poll_interval_seconds)
        raise ExistingChromeAcquisitionError(
            "navigation_failure", "Chrome tab navigation timed out"
        )

    def current_document(self) -> AcquiredDocument:
        """Read current tab DOM without navigating."""
        if self.window_id is None or self.tab_id is None:
            raise ExistingChromeAcquisitionError("navigation_failure", "Chrome tab is unavailable")
        try:
            snapshot = json.loads(self._run_script("EXECUTE_JS", EXECUTE_JS, self.window_id, self.tab_id, DOM_SNAPSHOT_JS))
        except Exception as exc:
            raise ExistingChromeAcquisitionError("navigation_failure", "current Chrome DOM read failed") from exc
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("html"), str):
            raise ExistingChromeAcquisitionError("invalid_document", "current Chrome DOM has no HTML")
        observed_url = snapshot.get("observedUrl")
        if not isinstance(observed_url, str):
            raise ExistingChromeAcquisitionError("invalid_document", "current Chrome DOM has no URL")
        _validate_browser_url(observed_url)
        payload = snapshot["html"].encode("utf-8")
        if not payload:
            raise ExistingChromeAcquisitionError("invalid_document", "current Chrome DOM is empty")
        return AcquiredDocument(payload=payload, request_url=observed_url, observed_url=observed_url,
                                capture_method=BROWSER_DOM_SNAPSHOT, fetched_at=self.clock(),
                                http_status=None, content_type=None, metadata={})

    def close(self) -> None:
        if self.window_id is None or self.tab_id is None:
            return
        try:
            self.script_runner(CLOSE_TAB, self.window_id, self.tab_id)
        finally:
            self.window_id = None
            self.tab_id = None
