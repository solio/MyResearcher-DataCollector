#!/usr/bin/env python3
"""Bounded Xueqiu probe: ordinary Chrome process, dedicated profile, CDP attach.

Chrome is started directly with a fixed loopback debugging port.  Playwright is
used only as a CDP client; it never launches or owns the browser profile.  The
probe does not read cookies, browser storage, credentials, or profile files and
does not modify browser-identifying JavaScript properties.

Apple Events are deliberately outside the control plane.  A read-only
AppleScript sampler observes the macOS frontmost process and the active-tab
identity exposed by the user's normal Chrome so focus interference can be
detected without navigating, clicking, executing JavaScript, or activating it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from urllib.request import urlopen

from myresearcher_collector.sources.xueqiu.dom_transport import (
    XueqiuDomTransport,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CHROME_BINARY = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
PROFILE_DIR = REPOSITORY_ROOT / ".runtime/browser-profiles/xueqiu-dedicated"
FIXED_CDP_PORT = 9227
TARGET_URL = "https://xueqiu.com/S/SH601012"
CHALLENGE_TOKENS = (
    "md5__1038",
    "验证码",
    "安全验证",
    "访问验证",
    "captcha",
    "人机验证",
)


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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_url(value: str) -> str:
    """Keep URL shape and query names while removing all query values."""
    parts = urlsplit(value)
    names = [name for name, _ in parse_qsl(parts.query, keep_blank_values=True)]
    query = "&".join(f"{name}=<redacted>" for name in names)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def opaque(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def sample_focus(started: float) -> dict[str, Any]:
    """Read focus metadata only; never activate Chrome or mutate a tab."""
    row: dict[str, Any] = {
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "observed_at": utc_now(),
    }
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
                # The user's unrelated browsing data is never persisted raw.
                "chrome_active_url_hash": opaque(parts[5]),
                "chrome_active_title_hash": opaque(parts[6]),
            }
        )
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    return row


class FocusSampler:
    def __init__(self, started: float, *, interval_seconds: float = 0.75) -> None:
        self.started = started
        self.interval_seconds = interval_seconds
        self.samples: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.samples.append(sample_focus(self.started))
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self.samples.append(sample_focus(self.started))

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.samples.append(sample_focus(self.started))


def assert_port_free(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(f"fixed CDP port {port} is already in use") from exc


def port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def assert_profile_not_running(profile_dir: Path) -> None:
    profile_text = str(profile_dir.resolve())
    completed = subprocess.run(
        ["/bin/ps", "-axo", "pid=,command="],
        check=True,
        capture_output=True,
        text=True,
    )
    matches = [line.strip() for line in completed.stdout.splitlines() if profile_text in line]
    if matches:
        pids = [line.split(None, 1)[0] for line in matches]
        raise RuntimeError(
            "dedicated profile is already owned by another process; pids=" + ",".join(pids)
        )


def wait_for_cdp(port: int, timeout_seconds: float = 15.0) -> dict[str, Any]:
    endpoint = f"http://127.0.0.1:{port}/json/version"
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(endpoint, timeout=1) as response:  # noqa: S310 - loopback only
                value = json.loads(response.read().decode("utf-8"))
                if value.get("webSocketDebuggerUrl"):
                    return value
        except (OSError, URLError, ValueError) as exc:
            last_error = exc
        time.sleep(0.2)
    raise RuntimeError(f"Chrome did not expose the fixed CDP endpoint: {last_error}")


def start_playwright_for_loopback() -> Any:
    """Start the CDP client without applying workstation proxies to localhost.

    The already-started Chrome process retains its original environment for
    normal site access.  Only the Playwright driver process receives this
    loopback-only proxy exclusion, preventing an ``ALL_PROXY=socks5h://...``
    setting from intercepting ``127.0.0.1``.
    """
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
    saved = {name: os.environ.get(name) for name in proxy_names}
    try:
        for name in proxy_names:
            os.environ.pop(name, None)
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        os.environ["no_proxy"] = "127.0.0.1,localhost"
        from playwright.sync_api import sync_playwright

        return sync_playwright().start()
    finally:
        for name in proxy_names:
            os.environ.pop(name, None)
            if saved[name] is not None:
                os.environ[name] = str(saved[name])


def page_facts(page: Any) -> dict[str, Any]:
    return page.evaluate(
        """
        tokens => {
          const body = document.body ? (document.body.innerText || '') : '';
          const searchable = (location.href + '\\n' + document.title + '\\n' + body).toLowerCase();
          return {
            url: location.href,
            title: document.title,
            ready_state: document.readyState,
            posts: document.querySelectorAll('article.timeline__item').length,
            challenge: tokens.filter(token => searchable.includes(token.toLowerCase())),
            user_agent: navigator.userAgent,
            webdriver: navigator.webdriver,
            languages: Array.from(navigator.languages || []),
            plugin_count: navigator.plugins ? navigator.plugins.length : null,
            has_window_chrome: Boolean(window.chrome)
          };
        }
        """,
        list(CHALLENGE_TOKENS),
    )


def public_post(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "status_id": str(item.get("status_id") or ""),
        "author_id": str(item.get("author_id") or ""),
        "author_name": item.get("author_name"),
        "title": item.get("title"),
        "content": item.get("content"),
        "time_text_observed": item.get("time_text_observed"),
        "url": redact_url(str(item.get("url") or "")),
    }


class AttachedRuntime:
    """The minimum runtime surface needed by XueqiuDomTransport."""

    acquisition_mode = "external-normal-chrome-cdp"
    profile_mode = "dedicated-existing"
    launch_method = "ordinary Chrome executable + fixed loopback CDP attach"

    def __init__(self, browser: Any, context: Any, page: Any) -> None:
        self.browser = browser
        self.context = context
        self.page = page


def create_background_target_and_reconnect(
    playwright: Any,
    browser: Any,
    *,
    port: int,
    label: str,
) -> tuple[Any, Any, Any, Any]:
    """Create a background target, then reattach so Playwright discovers it.

    Chromium creates the target correctly after an initial CDP attach, but
    Playwright does not surface that externally-created target as a ``Page``
    until the next attachment.  Reconnecting changes no browser or site state.
    """
    marker = f"about:blank#xueqiu-cdp-{label}-{time.monotonic_ns()}"
    session = browser.new_browser_cdp_session()
    try:
        session.send("Target.createTarget", {"url": marker, "background": True})
    finally:
        session.detach()
    playwright.stop()
    next_playwright = start_playwright_for_loopback()
    next_browser = next_playwright.chromium.connect_over_cdp(
        f"http://127.0.0.1:{port}", timeout=10_000
    )
    if len(next_browser.contexts) != 1:
        next_playwright.stop()
        raise RuntimeError(
            "background-target reconnect did not expose exactly one context"
        )
    next_context = next_browser.contexts[0]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        for candidate in next_context.pages:
            if candidate.url == marker:
                return next_playwright, next_browser, next_context, candidate
        time.sleep(0.05)
    next_playwright.stop()
    raise RuntimeError(f"background CDP target was not exposed as a page: {label}")


def action(report: dict[str, Any], started: float, name: str, **facts: Any) -> None:
    row = {
        "name": name,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "observed_at": utc_now(),
    }
    row.update(facts)
    report["actions"].append(row)
    print(f"probe_phase={name}", file=sys.stderr, flush=True)


def summarize_focus(
    samples: list[dict[str, Any]], *, owned_chrome_pid: int | None
) -> dict[str, Any]:
    valid = [row for row in samples if row.get("frontmost_application")]
    baseline = valid[0] if valid else {}
    final = valid[-1] if valid else {}
    tab_identity_fields = (
        "chrome_window_id",
        "chrome_active_tab_id",
        "chrome_active_tab_index",
    )
    content_signature_fields = (
        "chrome_active_url_hash",
        "chrome_active_title_hash",
    )
    baseline_identity = {name: baseline.get(name) for name in tab_identity_fields}
    final_identity = {name: final.get(name) for name in tab_identity_fields}
    baseline_signature = {
        name: baseline.get(name) for name in content_signature_fields
    }
    final_signature = {name: final.get(name) for name in content_signature_fields}
    baseline_frontmost = baseline.get("frontmost_application")
    chrome_takeovers = [
        row
        for row in valid[1:]
        if baseline_frontmost != "Google Chrome"
        and row.get("frontmost_application") == "Google Chrome"
    ]
    observed_identities = {
        tuple(row.get(name) for name in tab_identity_fields)
        for row in valid
        if row.get("chrome_state") == "WINDOW"
    }
    final_matches_baseline = (
        baseline_identity == final_identity
        and baseline_signature == final_signature
    )
    dedicated_frontmost = [
        row
        for row in valid
        if owned_chrome_pid is not None
        and row.get("frontmost_pid") == str(owned_chrome_pid)
    ]
    focus_passed = bool(
        valid
        and final_matches_baseline
        and not chrome_takeovers
        and not dedicated_frontmost
    )
    focus_result = "FAIL_OR_INCONCLUSIVE"
    if focus_passed:
        focus_result = (
            "PASS_BASELINE_CHROME_LIMITED"
            if baseline_frontmost == "Google Chrome"
            else "PASS"
        )
    return {
        "sampling_method": "read-only AppleScript; no activate, navigation, click, or execute-javascript",
        "chrome_identity_scope": (
            "Apple Events addresses the Google Chrome application bundle; while two Chrome "
            "main processes exist, an intermediate identity change is observable but not "
            "sufficient by itself to identify which process Apple Events selected"
        ),
        "sample_count": len(samples),
        "baseline_frontmost_application": baseline_frontmost,
        "final_frontmost_application": final.get("frontmost_application"),
        "baseline_user_chrome_identity": baseline_identity,
        "final_user_chrome_identity": final_identity,
        "baseline_user_chrome_content_signature": baseline_signature,
        "final_user_chrome_content_signature": final_signature,
        "final_user_chrome_matches_baseline": final_matches_baseline,
        "distinct_chrome_identities_observed": len(observed_identities),
        "owned_chrome_pid": owned_chrome_pid,
        "owned_chrome_frontmost_count": len(dedicated_frontmost),
        "frontmost_chrome_takeover_count": len(chrome_takeovers),
        "frontmost_chrome_takeover_elapsed_seconds": [
            row.get("elapsed_seconds") for row in chrome_takeovers
        ],
        "result": focus_result,
    }


def terminate_owned_process(process: subprocess.Popen[Any] | None) -> dict[str, Any]:
    if process is None:
        return {"owned_process_started": False}
    result: dict[str, Any] = {"owned_process_started": True, "pid": process.pid}
    if process.poll() is not None:
        result.update({"already_exited": True, "returncode": process.returncode})
        return result
    process.terminate()
    try:
        process.wait(timeout=8)
        result.update({"terminated": True, "returncode": process.returncode})
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        result.update({"terminated": True, "forced_kill": True, "returncode": process.returncode})
    return result


def run(*, profile_dir: Path, port: int, observe_seconds: int) -> tuple[int, dict[str, Any]]:
    started = time.monotonic()
    report: dict[str, Any] = {
        "experiment": "external ordinary Chrome + dedicated profile + fixed CDP attach",
        "started_at": utc_now(),
        "target_url": TARGET_URL,
        "chrome_binary": str(CHROME_BINARY),
        "profile_dir": str(profile_dir.resolve()),
        "fixed_cdp_endpoint": f"http://127.0.0.1:{port}",
        "control_plane": {
            "browser_launch": "subprocess.Popen ordinary Google Chrome executable",
            "browser_control": "Playwright connect_over_cdp only",
            "playwright_launch_used": False,
            "playwright_launch_persistent_context_used": False,
            "apple_events_browser_control_used": False,
            "apple_events_focus_observation_only": True,
        },
        "actions": [],
        "navigation_chain": [],
        "page1": {},
        "page2": {},
        "detail": {},
        "result": "FAIL",
    }
    process: subprocess.Popen[Any] | None = None
    playwright = None
    browser = None
    page = None
    sampler = FocusSampler(started)
    sampler.start()
    exit_code = 1
    try:
        if not CHROME_BINARY.is_file():
            raise RuntimeError(f"Chrome executable does not exist: {CHROME_BINARY}")
        profile_dir.mkdir(parents=True, exist_ok=True)
        assert_profile_not_running(profile_dir)
        assert_port_free(port)

        action(report, started, "launch_external_chrome")
        process = subprocess.Popen(
            [
                str(CHROME_BINARY),
                f"--user-data-dir={profile_dir.resolve()}",
                f"--remote-debugging-port={port}",
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
        report["chrome_pid"] = process.pid
        version = wait_for_cdp(port)
        report["cdp_version"] = {
            "browser": version.get("Browser"),
            "protocol_version": version.get("Protocol-Version"),
            "user_agent": version.get("User-Agent"),
        }
        action(report, started, "cdp_endpoint_ready")

        playwright = start_playwright_for_loopback()
        browser = playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{port}", timeout=10_000
        )
        if len(browser.contexts) != 1:
            raise RuntimeError(
                f"expected one dedicated Chrome context, observed {len(browser.contexts)}"
            )
        context = browser.contexts[0]
        action(report, started, "cdp_attached", contexts=len(browser.contexts))

        bound_page_ids: set[int] = set()

        def bind_page_events(observed_page: Any) -> None:
            if id(observed_page) in bound_page_ids:
                return
            bound_page_ids.add(id(observed_page))
            observed_page.on(
                "framenavigated",
                lambda frame: (
                    report["navigation_chain"].append(
                        {
                            "elapsed_seconds": round(time.monotonic() - started, 3),
                            "url": redact_url(str(frame.url)),
                        }
                    )
                    if frame is observed_page.main_frame
                    else None
                ),
            )

        (
            playwright,
            browser,
            context,
            page,
        ) = create_background_target_and_reconnect(
            playwright,
            browser,
            port=port,
            label="main",
        )
        action(report, started, "cdp_reattached_for_background_main")
        context.on("page", bind_page_events)
        bind_page_events(page)
        page.set_default_timeout(20_000)
        page.set_default_navigation_timeout(20_000)
        runtime = AttachedRuntime(browser, context, page)
        transport = XueqiuDomTransport(page, runtime=runtime, timeout_ms=20_000)

        action(report, started, "open_stock", url=TARGET_URL)
        transport.open_stock("601012")
        facts = page_facts(page)
        if facts["challenge"] or facts["posts"] <= 0:
            raise RuntimeError(f"entry page did not reach a clean post DOM: {facts}")
        first = transport.read_current_page()
        first_ids = tuple(str(value) for value in first["active_ids"])
        if not first_ids or len(first_ids) != len(set(first_ids)):
            raise RuntimeError("page 1 IDs are empty or duplicated")
        report["page1"] = {
            "page_no": first["page_no"],
            "count": len(first["items"]),
            "ids": list(first_ids),
            "posts": [public_post(item) for item in first["items"]],
            "facts": {**facts, "url": redact_url(str(facts["url"]))},
        }
        action(report, started, "entry_posts_visible", count=len(first_ids))

        # Keep the entry page stable long enough to catch a delayed verification loop.
        observation_samples: list[dict[str, Any]] = []
        observation_deadline = time.monotonic() + observe_seconds
        while time.monotonic() < observation_deadline:
            time.sleep(min(2.0, max(0.0, observation_deadline - time.monotonic())))
            current = page_facts(page)
            observation_samples.append(
                {
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "url": redact_url(str(current["url"])),
                    "posts": current["posts"],
                    "challenge": current["challenge"],
                }
            )
            if current["challenge"] or current["posts"] <= 0:
                raise RuntimeError("entry page became blocked during bounded observation")
        report["entry_observation"] = observation_samples

        page_delay = random.SystemRandom().uniform(3.0, 10.0)
        report["page2"]["delay_seconds"] = round(page_delay, 3)
        action(report, started, "pre_page2_random_wait", delay_seconds=round(page_delay, 3))
        time.sleep(page_delay)
        action(report, started, "goto_page2")
        second = transport.goto_page(2, previous_ids=first_ids)
        page2_deadline = time.monotonic() + 20
        while True:
            second = transport.read_current_page()
            second_ids = tuple(str(value) for value in second["active_ids"])
            second_facts = page_facts(page)
            if second_facts["challenge"]:
                raise RuntimeError("page 2 entered a verification state")
            if (
                int(second["page_no"]) == 2
                and second_ids
                and second_ids != first_ids
            ):
                break
            if time.monotonic() >= page2_deadline:
                raise RuntimeError(
                    "page 2 did not settle to a non-empty, changed ID signature"
                )
            time.sleep(0.25)
        report["page2"].update(
            {
                "page_no": second["page_no"],
                "count": len(second["items"]),
                "ids": list(second_ids),
                "overlap_with_page1": len(set(first_ids) & set(second_ids)),
                "posts": [public_post(item) for item in second["items"]],
                "facts": {**second_facts, "url": redact_url(str(second_facts["url"]))},
            }
        )
        action(report, started, "page2_posts_visible", count=len(second_ids))

        selected = next(
            (item for item in second["items"] if item.get("url") and item.get("status_id")),
            None,
        )
        if selected is None:
            raise RuntimeError("page 2 has no detail URL")
        detail_delay = random.SystemRandom().uniform(3.0, 10.0)
        report["detail"] = {
            "delay_seconds": round(detail_delay, 3),
            "selected_status_id": str(selected["status_id"]),
            "url": redact_url(str(selected["url"])),
        }
        action(report, started, "pre_detail_random_wait", delay_seconds=round(detail_delay, 3))
        time.sleep(detail_delay)
        action(report, started, "open_detail", url=redact_url(str(selected["url"])))
        (
            playwright,
            browser,
            context,
            detail_page,
        ) = create_background_target_and_reconnect(
            playwright,
            browser,
            port=port,
            label="detail",
        )
        action(report, started, "cdp_reattached_for_background_detail")
        main_candidates = [candidate for candidate in context.pages if candidate is not detail_page]
        page = next(
            (
                candidate
                for candidate in main_candidates
                if urlsplit(str(candidate.url)).path == urlsplit(TARGET_URL).path
            ),
            None,
        )
        if page is None:
            raise RuntimeError("main page was not preserved across detail CDP reconnect")
        bind_page_events(page)
        bind_page_events(detail_page)
        runtime = AttachedRuntime(browser, context, page)
        transport = XueqiuDomTransport(page, runtime=runtime, timeout_ms=20_000)
        transport.current_page = 2
        detail_page.set_default_timeout(20_000)
        detail_page.set_default_navigation_timeout(20_000)
        detail_page.goto(str(selected["url"]), wait_until="domcontentloaded")
        detail_page.wait_for_function(
            "() => Boolean(window.SNOWMAN_STATUS && "
            "window.SNOWMAN_STATUS.created_at !== undefined && "
            "window.SNOWMAN_STATUS.created_at !== null)",
            timeout=20_000,
        )
        detail_facts = page_facts(detail_page)
        if detail_facts["challenge"]:
            raise RuntimeError("detail page entered a verification state")
        detail = detail_page.evaluate("() => window.SNOWMAN_STATUS")
        if not isinstance(detail, dict):
            raise RuntimeError("detail SNOWMAN_STATUS is not an object")
        resolved_id = str(detail.get("id") or detail.get("status_id") or "")
        if resolved_id != str(selected["status_id"]):
            raise RuntimeError(
                f"detail identity mismatch: selected={selected['status_id']} resolved={resolved_id}"
            )
        transport.assert_current_page(2, expected_ids=second_ids)
        report["detail"].update(
            {
                "resolved_status_id": resolved_id,
                "created_at_present": detail.get("created_at") is not None,
                "facts": {
                    **detail_facts,
                    "url": redact_url(str(detail_facts["url"])),
                },
                "main_page2_preserved": True,
            }
        )
        action(report, started, "detail_resolved", status_id=resolved_id)
        report["result"] = "PASS"
        exit_code = 0
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        if page is not None:
            try:
                report["final_page_facts"] = {
                    **page_facts(page),
                    "url": redact_url(str(page.url)),
                }
            except Exception as facts_exc:
                report["final_page_facts_error"] = (
                    f"{type(facts_exc).__name__}: {facts_exc}"
                )
    finally:
        action(report, started, "cleanup_begin")
        # Do not close or foreground individual windows during cleanup.  The
        # CDP client disconnects first, then the exact owned Chrome PID exits.
        if playwright is not None:
            try:
                playwright.stop()
            except Exception as exc:
                report["playwright_stop_error"] = f"{type(exc).__name__}: {exc}"
        report["process_cleanup"] = terminate_owned_process(process)
        time.sleep(1.0)
        report["cdp_port_closed_after_cleanup"] = not port_is_open(port)
        sampler.stop()
        report["focus_samples"] = sampler.samples
        report["focus_analysis"] = summarize_focus(
            sampler.samples,
            owned_chrome_pid=process.pid if process is not None else None,
        )
        report["finished_at"] = utc_now()
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        if (
            report["result"] == "PASS"
            and not str(report["focus_analysis"]["result"]).startswith("PASS")
        ):
            report["result"] = "DATA_PASS_FOCUS_FAIL_OR_INCONCLUSIVE"
            exit_code = 2
        action(report, started, "cleanup_complete")
    return exit_code, report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-dir", type=Path, default=PROFILE_DIR)
    parser.add_argument("--port", type=int, default=FIXED_CDP_PORT)
    parser.add_argument("--observe-seconds", type=int, default=20)
    args = parser.parse_args()
    if not 20 <= args.observe_seconds <= 30:
        parser.error("--observe-seconds must be between 20 and 30")
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    exit_code, report = run(
        profile_dir=args.profile_dir,
        port=args.port,
        observe_seconds=args.observe_seconds,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
