"""Process-isolated normal Chrome runtime for Xueqiu public DOM collection.

This is the production form of the 2026-08-18 live experiment.  It starts the
official Chrome executable directly, owns a persistent dedicated profile and a
fixed loopback CDP port, and uses Playwright only as a CDP client.  It never
calls ``playwright.launch``/``launch_persistent_context`` and never controls the
user's normal Chrome through Apple Events.

The page-shaped facade is intentionally stable while the underlying Playwright
connection is replaced.  Chromium can create a truly background target through
``Target.createTarget(background=true)``; Playwright discovers that externally
created target after a CDP reconnect.  Keeping the reconnect inside this facade
prevents the parser/backfill layers from retaining invalid raw ``Page`` objects.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from urllib.request import urlopen

from .browser_transport import ENTRY_URL
from .collector import symbol_for
from .dom_scripts import (
    ACTIVE_PAGE_JS,
    CLICK_PAGE_JS,
    DETAIL_STATE_JS,
    PAGE_STATE_JS,
    READ_PAGE_JS,
    clean_embedded_status_json,
)
from .dom_transport import XueqiuDomTransportError


DEFAULT_XUEQIU_CHROME_EXECUTABLE = Path(
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
)
DEFAULT_XUEQIU_PROFILE = Path(".runtime/browser-profiles/xueqiu-dedicated")
DEFAULT_XUEQIU_CDP_PORT = 9227
MAX_TRANSIENT_CHALLENGE_NAVIGATIONS = 2
_PLAYWRIGHT_ENV_LOCK = threading.Lock()


FOCUS_SAMPLE_SCRIPT = r'''
set fieldSeparator to "|"
tell application "System Events"
  set frontProcess to first application process whose frontmost is true
  set frontName to name of frontProcess
  set frontPID to unix id of frontProcess
  set chromeRunning to exists application process "Google Chrome"
end tell
if not chromeRunning then
  return frontName & fieldSeparator & (frontPID as text) & fieldSeparator & "NO_CHROME"
end if
tell application "Google Chrome"
  if (count of windows) is 0 then
    return frontName & fieldSeparator & (frontPID as text) & fieldSeparator & "NO_WINDOWS"
  end if
  set w to front window
  set t to active tab of w
  return frontName & fieldSeparator & (frontPID as text) & fieldSeparator & ¬
    ((id of w) as text) & fieldSeparator & ((id of t) as text) & fieldSeparator & ¬
    ((active tab index of w) as text) & fieldSeparator & (URL of t as text) & fieldSeparator & ¬
    (title of t as text)
end tell
'''


def _opaque(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def _redact_navigation_url(url: str) -> str:
    """Retain query names for diagnostics while dropping every query value."""
    parts = urlsplit(url)
    names = [name for name, _ in parse_qsl(parts.query, keep_blank_values=True)]
    query = "&".join(f"{name}=<redacted>" for name in names)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _validate_xueqiu_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"xueqiu.com", "www.xueqiu.com"}
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("browser navigation is outside approved Xueqiu HTTPS hosts")


def _sample_focus(action: str) -> dict[str, Any]:
    """Read macOS/Chrome focus state without activating or mutating Chrome."""
    row: dict[str, Any] = {"action": action, "monotonic_seconds": time.monotonic()}
    try:
        completed = subprocess.run(
            ["/usr/bin/osascript", "-e", FOCUS_SAMPLE_SCRIPT],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if completed.returncode != 0:
            row["error"] = (completed.stderr or "osascript failed").strip()
            return row
        parts = completed.stdout.rstrip("\n").split("|", 6)
        if len(parts) < 3:
            row["error"] = "unexpected focus-sample shape"
            return row
        row.update({"frontmost_application": parts[0], "frontmost_pid": parts[1]})
        if parts[2] in {"NO_CHROME", "NO_WINDOWS"}:
            row["chrome_state"] = parts[2]
            return row
        if len(parts) != 7:
            row["error"] = "unexpected Chrome focus-sample shape"
            return row
        row.update(
            {
                "chrome_state": "WINDOW",
                "chrome_window_id": parts[2],
                "chrome_active_tab_id": parts[3],
                "chrome_active_tab_index": parts[4],
                # Never persist the user's unrelated URL or title in plaintext.
                "chrome_active_url_hash": _opaque(parts[5]),
                "chrome_active_title_hash": _opaque(parts[6]),
            }
        )
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    return row


def _start_playwright_for_loopback() -> Any:
    """Start only the CDP client, excluding loopback from workstation proxies."""
    proxy_names = (
        "ALL_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "all_proxy",
        "https_proxy",
        "http_proxy",
        "NO_PROXY",
        "no_proxy",
    )
    with _PLAYWRIGHT_ENV_LOCK:
        saved = {name: os.environ.get(name) for name in proxy_names}
        try:
            for name in proxy_names:
                os.environ.pop(name, None)
            os.environ["NO_PROXY"] = "127.0.0.1,localhost"
            os.environ["no_proxy"] = "127.0.0.1,localhost"
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise RuntimeError(
                    "dedicated-chrome-cdp requires the optional Playwright dependency"
                ) from exc
            return sync_playwright().start()
        finally:
            for name in proxy_names:
                os.environ.pop(name, None)
                if saved[name] is not None:
                    os.environ[name] = str(saved[name])


class XueqiuDedicatedChromePage:
    """Page facade backed only by the Collector-owned Chrome/CDP endpoint."""

    acquisition_mode = "dedicated-chrome-cdp"
    profile_mode = "dedicated-persistent"
    launch_method = "ordinary Chrome executable + fixed loopback CDP attach"

    def __init__(
        self,
        *,
        profile_dir: str | Path = DEFAULT_XUEQIU_PROFILE,
        cdp_port: int = DEFAULT_XUEQIU_CDP_PORT,
        chrome_executable: str | Path | None = None,
        timeout_ms: int = 20_000,
        poll_interval_seconds: float = 0.25,
        max_transient_challenge_navigations: int = MAX_TRANSIENT_CHALLENGE_NAVIGATIONS,
        observe_focus: bool = True,
        process_factory: Callable[..., Any] | None = None,
        playwright_starter: Callable[[], Any] | None = None,
        cdp_probe: Callable[[int, Any], dict[str, Any]] | None = None,
        port_free_checker: Callable[[int], None] | None = None,
        port_open_checker: Callable[[int], bool] | None = None,
        focus_observer: Callable[[str], dict[str, Any]] | None = None,
        monotonic_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 1024 <= int(cdp_port) <= 65535:
            raise ValueError("Xueqiu CDP port must be between 1024 and 65535")
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if max_transient_challenge_navigations < 0:
            raise ValueError("max_transient_challenge_navigations must be non-negative")

        self.profile_dir = Path(profile_dir).expanduser().resolve()
        configured_executable = chrome_executable or os.environ.get(
            "MYRESEARCHER_CHROME_EXECUTABLE",
            str(DEFAULT_XUEQIU_CHROME_EXECUTABLE),
        )
        self.chrome_executable = Path(configured_executable).expanduser().resolve()
        self.cdp_port = int(cdp_port)
        self.timeout_ms = int(timeout_ms)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.max_transient_challenge_navigations = int(
            max_transient_challenge_navigations
        )
        self.observe_focus = bool(observe_focus)
        self.process_factory = process_factory or subprocess.Popen
        self.playwright_starter = playwright_starter or _start_playwright_for_loopback
        self.cdp_probe = cdp_probe or self._wait_for_cdp
        self.port_free_checker = port_free_checker
        self.port_open_checker = port_open_checker
        self.focus_observer = focus_observer or _sample_focus
        self.monotonic_fn = monotonic_fn
        self.sleep_fn = sleep_fn

        self.process: Any | None = None
        self._lock_file: Any | None = None
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._main_page: Any | None = None
        self._main_target_id: str | None = None
        self._stock_code: str | None = None
        self._operation_navigation_start = 0
        self._closed = False

        self.navigation_urls: list[str] = []
        self.focus_samples: list[dict[str, Any]] = []
        self.diagnostics: list[str] = []
        self.browser_version: dict[str, Any] = {}
        self.process_cleanup: dict[str, Any] = {}
        self.cdp_port_closed_after_cleanup: bool | None = None

    @property
    def runtime_report(self) -> dict[str, Any]:
        return {
            "acquisition_mode": self.acquisition_mode,
            "launch_method": self.launch_method,
            "profile_mode": self.profile_mode,
            "profile_dir": str(self.profile_dir),
            "fixed_cdp_endpoint": f"http://127.0.0.1:{self.cdp_port}",
            "chrome_pid": getattr(self.process, "pid", None),
            "browser_version": dict(self.browser_version),
            "navigation_urls": list(self.navigation_urls),
            "focus": self._focus_summary(),
            "process_cleanup": dict(self.process_cleanup),
            "cdp_port_closed_after_cleanup": self.cdp_port_closed_after_cleanup,
            "diagnostics": list(self.diagnostics),
            "playwright_launch_used": False,
            "playwright_launch_persistent_context_used": False,
            "apple_events_browser_control_used": False,
        }

    def browser_page(self) -> Any:
        """Return the owned raw Page for the approved response-observer adapter."""
        self._ensure_started()
        return self._main_page

    def assert_safe_state(self) -> None:
        """Fail closed when the response-observer path enters verification."""
        self._page_state()
        # The response-observer adapter calls this once per accepted page, so
        # the next page receives a fresh bounded transient-navigation budget.
        self._operation_navigation_start = len(self.navigation_urls)

    def _focus_summary(self) -> dict[str, Any]:
        valid = [row for row in self.focus_samples if row.get("frontmost_application")]
        if not valid:
            return {"sample_count": len(self.focus_samples), "result": "UNAVAILABLE"}
        baseline = valid[0]
        final = valid[-1]
        identity_fields = (
            "chrome_window_id",
            "chrome_active_tab_id",
            "chrome_active_tab_index",
            "chrome_active_url_hash",
            "chrome_active_title_hash",
        )
        baseline_identity = {name: baseline.get(name) for name in identity_fields}
        final_identity = {name: final.get(name) for name in identity_fields}
        owned_pid = str(getattr(self.process, "pid", ""))
        owned_frontmost = [
            row for row in valid if owned_pid and row.get("frontmost_pid") == owned_pid
        ]
        return {
            "sample_count": len(self.focus_samples),
            "baseline_frontmost_application": baseline.get("frontmost_application"),
            "final_frontmost_application": final.get("frontmost_application"),
            "baseline_user_chrome_identity": baseline_identity,
            "final_user_chrome_identity": final_identity,
            "final_user_chrome_matches_baseline": baseline_identity == final_identity,
            "owned_chrome_frontmost_count": len(owned_frontmost),
            "sampling_method": "read-only AppleScript at browser action boundaries",
            "result": (
                "PASS"
                if baseline_identity == final_identity and not owned_frontmost
                else "FAIL_OR_INCONCLUSIVE"
            ),
        }

    def _sample_focus(self, action: str) -> None:
        if not self.observe_focus:
            return
        self.focus_samples.append(self.focus_observer(action))

    def _acquire_profile_lock(self) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.profile_dir.parent / f".{self.profile_dir.name}.collector.lock"
        lock_file = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            lock_file.close()
            raise XueqiuDomTransportError(
                f"dedicated Xueqiu profile is already owned: {self.profile_dir}"
            ) from exc
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(str(os.getpid()))
        lock_file.flush()
        self._lock_file = lock_file

    def _release_profile_lock(self) -> None:
        if self._lock_file is None:
            return
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            self._lock_file.close()
            self._lock_file = None

    def _assert_port_free(self) -> None:
        if self.port_free_checker is not None:
            self.port_free_checker(self.cdp_port)
            return
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", self.cdp_port))
            except OSError as exc:
                raise XueqiuDomTransportError(
                    f"fixed Xueqiu CDP port {self.cdp_port} is already in use"
                ) from exc

    def _port_is_open(self) -> bool:
        if self.port_open_checker is not None:
            return bool(self.port_open_checker(self.cdp_port))
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex(("127.0.0.1", self.cdp_port)) == 0

    def _wait_for_cdp(self, port: int, process: Any) -> dict[str, Any]:
        endpoint = f"http://127.0.0.1:{port}/json/version"
        deadline = self.monotonic_fn() + 15
        last_error: Exception | None = None
        while self.monotonic_fn() < deadline:
            if process.poll() is not None:
                raise XueqiuDomTransportError(
                    f"dedicated Chrome exited before CDP was ready: {process.returncode}"
                )
            try:
                with urlopen(endpoint, timeout=1) as response:  # noqa: S310 - loopback only
                    value = json.loads(response.read().decode("utf-8"))
                    if value.get("webSocketDebuggerUrl"):
                        return value
            except (OSError, URLError, ValueError) as exc:
                last_error = exc
            self.sleep_fn(0.2)
        raise XueqiuDomTransportError(
            f"dedicated Chrome did not expose its fixed CDP endpoint: {last_error}"
        )

    def _ensure_started(self) -> None:
        if self._closed:
            raise XueqiuDomTransportError("dedicated Chrome runtime is already closed")
        if self._main_page is not None:
            return
        try:
            if not self.chrome_executable.is_file():
                raise XueqiuDomTransportError(
                    f"Google Chrome executable is unavailable: {self.chrome_executable}"
                )
            self._sample_focus("before_launch")
            self._acquire_profile_lock()
            self._assert_port_free()
            self.process = self.process_factory(
                [
                    str(self.chrome_executable),
                    f"--user-data-dir={self.profile_dir}",
                    f"--remote-debugging-port={self.cdp_port}",
                    "--remote-debugging-address=127.0.0.1",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--no-startup-window",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            version = self.cdp_probe(self.cdp_port, self.process)
            self.browser_version = {
                "browser": version.get("Browser"),
                "protocol_version": version.get("Protocol-Version"),
                "user_agent": version.get("User-Agent"),
            }
            self._connect()
            page, target_id = self._create_background_target_and_reconnect("main")
            self._main_page = page
            self._main_target_id = target_id
            self._configure_page(page)
            self._bind_navigation(page)
            self._sample_focus("after_background_main_ready")
        except Exception:
            self._close_resources(permanent=False)
            raise

    def _connect(self) -> None:
        playwright = self.playwright_starter()
        try:
            browser = playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{self.cdp_port}", timeout=10_000
            )
            if len(browser.contexts) != 1:
                raise XueqiuDomTransportError(
                    f"expected one dedicated Chrome context, observed {len(browser.contexts)}"
                )
        except Exception:
            playwright.stop()
            raise
        self._playwright = playwright
        self._browser = browser
        self._context = browser.contexts[0]

    def _disconnect(self) -> None:
        if self._playwright is not None:
            try:
                self._playwright.stop()
            finally:
                self._playwright = None
                self._browser = None
                self._context = None

    def _create_background_target_and_reconnect(self, label: str) -> tuple[Any, str]:
        if self._browser is None:
            raise XueqiuDomTransportError("dedicated Chrome CDP connection is unavailable")
        marker = f"about:blank#xueqiu-{label}-{time.monotonic_ns()}"
        session = self._browser.new_browser_cdp_session()
        try:
            result = session.send(
                "Target.createTarget", {"url": marker, "background": True}
            )
        finally:
            session.detach()
        target_id = str(result.get("targetId") or "")
        if not target_id:
            raise XueqiuDomTransportError("Chrome did not return a background target id")
        self._disconnect()
        self._connect()
        deadline = self.monotonic_fn() + 10
        while self.monotonic_fn() < deadline:
            for candidate in self._context.pages:
                if str(candidate.url) == marker:
                    return candidate, target_id
            self.sleep_fn(0.05)
        raise XueqiuDomTransportError(
            f"background Chrome target was not exposed after CDP reconnect: {label}"
        )

    def _bind_navigation(self, page: Any) -> None:
        def record(frame: Any) -> None:
            try:
                if frame is not page.main_frame:
                    return
            except Exception:
                pass
            self.navigation_urls.append(
                _redact_navigation_url(str(getattr(frame, "url", "")))
            )

        page.on("framenavigated", record)

    def _configure_page(self, page: Any) -> None:
        set_timeout = getattr(page, "set_default_timeout", None)
        if callable(set_timeout):
            set_timeout(self.timeout_ms)
        set_navigation_timeout = getattr(page, "set_default_navigation_timeout", None)
        if callable(set_navigation_timeout):
            set_navigation_timeout(self.timeout_ms)

    def _evaluate_json(self, page: Any, expression: str) -> Any:
        try:
            raw = page.evaluate(expression)
            return json.loads(raw)
        except XueqiuDomTransportError:
            raise
        except Exception as exc:
            raise XueqiuDomTransportError(
                "dedicated Chrome returned invalid Xueqiu DOM JSON"
            ) from exc

    def _challenge_navigation_count(self, start: int | None = None) -> int:
        values = self.navigation_urls[start or 0 :]
        return sum(
            1
            for url in values
            if any(
                name in {key for key, _ in parse_qsl(urlsplit(url).query)}
                for name in ("md5__1038", "alichlgref")
            )
        )

    def _assert_challenge_budget(self) -> None:
        count = self._challenge_navigation_count(self._operation_navigation_start)
        if count > self.max_transient_challenge_navigations:
            raise XueqiuDomTransportError(
                f"Xueqiu challenge navigation did not settle; observed={count}"
            )

    def _page_state(self, page: Any | None = None) -> dict[str, Any]:
        target = page or self._main_page
        if target is None:
            raise XueqiuDomTransportError("dedicated Chrome main page is unavailable")
        state = self._evaluate_json(target, PAGE_STATE_JS)
        if not isinstance(state, dict):
            raise XueqiuDomTransportError("Xueqiu page state is invalid")
        self._assert_challenge_budget()
        if state.get("challenge"):
            raise XueqiuDomTransportError("Xueqiu access verification is visible")
        return state

    def _find_main_after_reconnect(self, detail_page: Any) -> Any:
        if self._stock_code is None:
            raise XueqiuDomTransportError("Xueqiu stock page is not open")
        expected_path = urlsplit(
            ENTRY_URL.format(symbol=symbol_for(self._stock_code))
        ).path
        candidates = [
            candidate
            for candidate in self._context.pages
            if candidate is not detail_page
            and urlsplit(str(candidate.url)).hostname in {"xueqiu.com", "www.xueqiu.com"}
            and urlsplit(str(candidate.url)).path == expected_path
        ]
        if len(candidates) != 1:
            raise XueqiuDomTransportError(
                f"main Xueqiu page identity is ambiguous after reconnect: {len(candidates)}"
            )
        return candidates[0]

    def open_stock(self, stock_code: str) -> None:
        self._ensure_started()
        url = ENTRY_URL.format(symbol=symbol_for(stock_code))
        _validate_xueqiu_url(url)
        self._stock_code = stock_code
        self._operation_navigation_start = len(self.navigation_urls)
        self._sample_focus("before_open_stock")
        self._main_page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)

    def wait_posts_loaded(self, timeout_ms: int) -> None:
        deadline = self.monotonic_fn() + timeout_ms / 1000
        last: dict[str, Any] | None = None
        while self.monotonic_fn() < deadline:
            last = self._page_state()
            if int(last.get("posts") or 0) > 0:
                self._sample_focus("after_posts_ready")
                return
            self.sleep_fn(self.poll_interval_seconds)
        raise XueqiuDomTransportError(
            f"Xueqiu posts did not load in dedicated Chrome; observed={last}"
        )

    def active_page(self) -> int:
        self._page_state()
        try:
            return int(str(self._main_page.evaluate(ACTIVE_PAGE_JS)).strip())
        except Exception as exc:
            raise XueqiuDomTransportError("active page is not observable") from exc

    def read_dom_page(self) -> list[dict[str, Any]]:
        self._page_state()
        value = self._evaluate_json(self._main_page, READ_PAGE_JS)
        if not isinstance(value, list):
            raise XueqiuDomTransportError("Xueqiu post DOM is not a list")
        return value

    def goto_page(self, page_no: int) -> None:
        if page_no < 1:
            raise ValueError("page_no must be positive")
        self._operation_navigation_start = len(self.navigation_urls)
        self._sample_focus(f"before_page_{page_no}")
        result = self._evaluate_json(self._main_page, CLICK_PAGE_JS % page_no)
        if not isinstance(result, dict) or result.get("clicked") is not True:
            raise XueqiuDomTransportError(
                f"pagination control {page_no} is unavailable"
            )

    def read_detail_status(self, url: str) -> dict[str, Any]:
        _validate_xueqiu_url(url)
        self._ensure_started()
        self._operation_navigation_start = len(self.navigation_urls)
        self._sample_focus("before_detail")
        detail_page: Any | None = None
        detail_target_id: str | None = None
        try:
            detail_page, detail_target_id = self._create_background_target_and_reconnect(
                "detail"
            )
            self._main_page = self._find_main_after_reconnect(detail_page)
            self._configure_page(self._main_page)
            self._configure_page(detail_page)
            self._bind_navigation(self._main_page)
            self._bind_navigation(detail_page)
            detail_page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            deadline = self.monotonic_fn() + self.timeout_ms / 1000
            last_state: dict[str, Any] | None = None
            while self.monotonic_fn() < deadline:
                state = self._evaluate_json(detail_page, DETAIL_STATE_JS)
                if not isinstance(state, dict):
                    raise XueqiuDomTransportError("Xueqiu detail state is invalid")
                last_state = state
                self._assert_challenge_budget()
                if state.get("challenge"):
                    raise XueqiuDomTransportError(
                        "Xueqiu detail access verification is visible"
                    )
                status = state.get("status")
                embedded = state.get("embeddedStatusJson")
                if status is None and isinstance(embedded, str):
                    try:
                        status = json.loads(clean_embedded_status_json(embedded))
                    except json.JSONDecodeError as exc:
                        raise XueqiuDomTransportError(
                            "embedded SNOWMAN_STATUS is not valid JSON"
                        ) from exc
                if isinstance(status, dict):
                    self._sample_focus("after_detail_ready")
                    return status
                self.sleep_fn(self.poll_interval_seconds)
            observed = None
            if last_state is not None:
                observed = {
                    "url": _redact_navigation_url(str(last_state.get("url") or "")),
                    "title": str(last_state.get("title") or ""),
                    "readyState": str(last_state.get("readyState") or ""),
                    "challenge": list(last_state.get("challenge") or []),
                }
            raise XueqiuDomTransportError(
                f"Xueqiu detail timestamp is unavailable; observed={observed}"
            )
        finally:
            if detail_target_id and self._browser is not None:
                session = self._browser.new_browser_cdp_session()
                try:
                    session.send("Target.closeTarget", {"targetId": detail_target_id})
                except Exception as exc:
                    self.diagnostics.append(
                        f"detail_target_close_failed:{type(exc).__name__}"
                    )
                finally:
                    session.detach()

    def _close_resources(self, *, permanent: bool) -> None:
        self._disconnect()
        process = self.process
        if process is not None:
            cleanup: dict[str, Any] = {"pid": getattr(process, "pid", None)}
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                    cleanup["terminated"] = True
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                    cleanup.update({"terminated": True, "forced_kill": True})
            cleanup["returncode"] = getattr(process, "returncode", None)
            self.process_cleanup = cleanup
        deadline = self.monotonic_fn() + 3
        while self._port_is_open() and self.monotonic_fn() < deadline:
            self.sleep_fn(0.1)
        self.cdp_port_closed_after_cleanup = not self._port_is_open()
        if not self.cdp_port_closed_after_cleanup:
            self.diagnostics.append("fixed_cdp_port_remained_open_after_owned_pid_exit")
        self._sample_focus("after_cleanup")
        self._release_profile_lock()
        self._main_page = None
        self._main_target_id = None
        if permanent:
            self._closed = True

    def close(self) -> None:
        if self._closed:
            return
        self._close_resources(permanent=True)
