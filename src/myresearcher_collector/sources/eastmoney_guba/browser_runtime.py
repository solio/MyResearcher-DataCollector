"""Small selectable browser runtimes for Eastmoney detail acquisition."""

from __future__ import annotations

import os
import subprocess
import sys
import hashlib
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .acquisition import AcquiredDocument, BROWSER_DOM_SNAPSHOT
from .browser_transport import (
    EastmoneyBrowserSocketTransport,
    EastmoneyBrowserTransport,
    EastmoneyBrowserTransportError,
)
from .existing_chrome import EastmoneyExistingChromeDomTransport


DEFAULT_CHROME_PROFILE = Path(".runtime/browser-profiles/eastmoney-chrome")
FRESH_MANAGED_PROFILES_ROOT = Path(".runtime/browser-profiles/eastmoney-managed")


def _fresh_managed_profile_path() -> Path:
    """A new per-run profile directory; never reused across CLI runs."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    return FRESH_MANAGED_PROFILES_ROOT / stamp


def create_eastmoney_transport(
    acquisition_mode: str,
    *,
    profile_dir: str | Path | None = None,
    browser_socket: str | Path | None = None,
    detail_referer_from_list: bool = False,
    list_click_paging: bool = False,
    detail_dwell_seconds: tuple[float, float] | None = None,
):
    """Create the shared Eastmoney browser acquisition transport.

    managed-chromium without an explicit profile_dir selects a new per-run
    profile; an explicit profile_dir keeps persistent reuse.

    ``detail_referer_from_list`` reaches the detail surface by first opening the
    bar's list page and then navigating in-page with JS, so the detail request
    carries a `Referer` and a consistent `Sec-Fetch-Site: same-origin`. It is OFF
    by default: it doubles the navigations per detail, so it must be requested
    explicitly rather than silently changing every run.

    ``list_click_paging`` turns a bar's list pages by clicking the pager instead
    of navigating to `f_<n>.html`, for the same reason. Also OFF by default, and
    only ``managed-chromium`` can do it: the other modes hand the request to
    AppleScript/Chrome-CDP paths that only know how to set a tab URL, so
    accepting the flag there would silently do nothing.
    """
    if list_click_paging and acquisition_mode not in ("managed-chromium", "chrome-cdp"):
        raise ValueError(
            "--list-click-paging requires --acquisition-mode managed-chromium or "
            f"chrome-cdp; {acquisition_mode!r} can only navigate to a URL"
        )
    if acquisition_mode == "existing-chrome":
        return EastmoneyExistingChromeDomTransport()
    if acquisition_mode == "chrome-clean":
        return ChromeCleanDomTransport(profile_dir=profile_dir or DEFAULT_CHROME_PROFILE)
    if acquisition_mode == "chrome-cdp":
        return ChromeCdpAttachTransport(
            detail_referer_from_list=detail_referer_from_list,
            list_click_paging=list_click_paging,
            detail_dwell_seconds=detail_dwell_seconds,
        )
    if acquisition_mode == "managed-chromium":
        return ManagedChromiumTransport(
            profile_dir=profile_dir,
            detail_referer_from_list=detail_referer_from_list,
            list_click_paging=list_click_paging,
            detail_dwell_seconds=detail_dwell_seconds,
        )
    if acquisition_mode == "browser-socket":
        if browser_socket is None:
            raise ValueError("browser-socket acquisition requires a socket path")
        return EastmoneyBrowserSocketTransport(browser_socket)
    raise ValueError(f"unsupported Eastmoney acquisition mode: {acquisition_mode}")


class ChromeCleanDomTransport:
    """Use a persistent dedicated Chrome data directory with DOM acquisition."""

    acquisition_mode = "chrome-clean"

    def __init__(self, *, profile_dir: str | Path = DEFAULT_CHROME_PROFILE) -> None:
        self.profile_dir = Path(profile_dir).expanduser().resolve()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        executable = os.environ.get(
            "MYRESEARCHER_CHROME_EXECUTABLE",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        )
        self.executable = Path(executable)
        self.process: subprocess.Popen[bytes] | None = None
        self.delegate = EastmoneyExistingChromeDomTransport(
            focus_log_path="runtime/logs/eastmoney-focus-chrome-clean.jsonl"
        )

    def _ensure_started(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        if not self.executable.exists():
            raise RuntimeError(f"dedicated Chrome executable not found: {self.executable}")
        self.process = subprocess.Popen(
            [str(self.executable), f"--user-data-dir={self.profile_dir}",
             "--no-first-run", "--no-default-browser-check", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def get(self, url: str, *, timeout: float) -> AcquiredDocument:
        self._ensure_started()
        return self.delegate.get(url, timeout=timeout)

    def current_document(self) -> AcquiredDocument:
        return self.delegate.current_document()

    def close(self) -> None:
        self.delegate.close()
        # Keep the persistent profile/browser process alive for manual reuse.


class ManagedChromiumTransport:
    """Launch a visible persistent Playwright Chromium/Chrome context.

    Without an explicit profile_dir each construction selects a new per-run
    profile under ``FRESH_MANAGED_PROFILES_ROOT``; an explicit profile_dir
    keeps the previous persistent reuse behavior.  The selected identity is
    printed to stderr at construction so operators can tell runs apart.
    """

    acquisition_mode = "managed-chromium"

    def __init__(
        self,
        *,
        profile_dir: str | Path | None = None,
        record_dialogs: bool = True,
        auto_dismiss_dialogs: bool = True,
        detail_referer_from_list: bool = False,
        list_click_paging: bool = False,
        detail_dwell_seconds: tuple[float, float] | None = None,
    ) -> None:
        if profile_dir is None:
            self.profile_dir = _fresh_managed_profile_path().expanduser().resolve()
            self.profile_mode = "fresh"
        else:
            self.profile_dir = Path(profile_dir).expanduser().resolve()
            self.profile_mode = "explicit-reuse"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = None
        self.context = None
        self.page = None
        self.delegate = None
        self.dialogs: list[dict[str, str]] = []
        self.record_dialogs = bool(record_dialogs)
        self.auto_dismiss_dialogs = bool(auto_dismiss_dialogs)
        self.detail_referer_from_list = bool(detail_referer_from_list)
        self.list_click_paging = bool(list_click_paging)
        self.detail_dwell_seconds = detail_dwell_seconds
        self.diagnostics_dir = Path("runtime/diagnostics")
        # Deliberately NOT added to this line: operators verify backfill runs by
        # the exact byte count of the per-stock .err files, so widening the only
        # line they contain would invalidate that check. The flag is echoed by
        # the driver instead (RUN_START), the same way the pacing is.
        print(
            f"acquisition_mode={self.acquisition_mode} "
            f"profile_mode={self.profile_mode} "
            f"profile_dir={self.profile_dir}",
            file=sys.stderr,
            flush=True,
        )

    def _ensure_started(self) -> None:
        if self.delegate is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("managed-chromium requires optional Playwright dependency") from exc
        self._playwright = sync_playwright().start()
        executable = os.environ.get("MYRESEARCHER_MANAGED_CHROMIUM_EXECUTABLE")
        kwargs = {"user_data_dir": str(self.profile_dir), "headless": False}
        if executable:
            kwargs["executable_path"] = executable
        else:
            kwargs["channel"] = os.environ.get("MYRESEARCHER_MANAGED_CHROMIUM_CHANNEL", "chrome")
        self.context = self._playwright.chromium.launch_persistent_context(**kwargs)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self._bind_dialog_handler(self.page)
        self.delegate = self._new_delegate()

    def _new_delegate(self) -> EastmoneyBrowserTransport:
        return EastmoneyBrowserTransport(
            self.page,
            detail_referer_from_list=self.detail_referer_from_list,
            list_click_paging=self.list_click_paging,
            detail_dwell_seconds=self.detail_dwell_seconds,
        )

    def _bind_dialog_handler(self, page) -> None:
        page.on("dialog", self._on_dialog)

    def _on_dialog(self, dialog) -> None:
        """Record a JS dialog, print it into the run trace, then dismiss it.

        THE TRACE LINE IS THE POINT (added 2026-09-28). This used to append to
        `self.dialogs`, dismiss, and stop there -- and the only reader of
        `self.dialogs` is `diagnostic_snapshot`, which the enrich path calls only
        when a challenge wait wants a screenshot. So in a normal run a JS alert
        (the source pops `验证错误,请重试(0)` when a slider check fails) was held
        in memory, auto-dismissed, and **written nowhere**: not in the trace, not
        in the report, not in the ledger. An operator watching an alert on screen
        had no artefact saying it happened, and the run's own log said nothing.

        Printing before dismissing means it lands in the same `<stock>.err`
        stream as FETCH_MODE/JUMP, so "was there an alert, and what did it say"
        is answerable from the trace alone.
        """
        entry = {"type": str(dialog.type), "message": str(dialog.message)}
        if self.record_dialogs:
            self.dialogs.append(entry)
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        print(
            f"{stamp} DIALOG type={entry['type']} message={entry['message']}",
            file=sys.stderr, flush=True,
        )
        if self.auto_dismiss_dialogs:
            try:
                dialog.dismiss()
            except Exception:
                pass

    def _ensure_page(self) -> None:
        """Recreate a usable page after the previous one was closed by the page."""
        self._ensure_started()
        if self.page is None or self.page.is_closed():
            self.page = self.context.new_page()
            self._bind_dialog_handler(self.page)
            self.delegate = self._new_delegate()

    def challenge_reasons(self) -> list[str]:
        """Live-DOM challenge signals from the delegate; [] when unavailable."""
        probe = getattr(self.delegate, "challenge_reasons", None)
        if not callable(probe):
            return []
        try:
            self._ensure_page()
            return list(probe())
        except Exception:  # noqa: BLE001
            return []

    @property
    def list_navigation_counts(self) -> dict[str, int]:
        """Paging-scheme counters from the live delegate; {} once closed.

        Read by the backfill report so a run states which navigation each list
        page used instead of leaving it to be assumed (`list_paging.py`).
        """
        return dict(getattr(self.delegate, "list_navigation_counts", None) or {})

    @property
    def list_navigation_samples(self) -> dict[str, dict[str, str]]:
        """One sampled request shape per scheme: the `Referer`/`Sec-Fetch-*` proof."""
        return dict(getattr(self.delegate, "list_navigation_samples", None) or {})

    def get(self, url: str, *, timeout: float):
        self._ensure_started()
        for attempt in range(2):
            self._ensure_page()
            try:
                return self.delegate.get(url, timeout=timeout)
            except EastmoneyBrowserTransportError:
                if attempt == 0 and self.page.is_closed():
                    continue
                raise

    def current_document(self) -> AcquiredDocument:
        self._ensure_started()
        self._ensure_page()
        html = self.page.content()
        return AcquiredDocument(
            payload=html.encode("utf-8"), request_url=self.page.url,
            observed_url=self.page.url, capture_method=BROWSER_DOM_SNAPSHOT,
            fetched_at=datetime.now(timezone.utc),
            http_status=None, content_type=None, metadata={},
        )

    def diagnostic_snapshot(self, requested_url: str, *, previous_ids_hash: str | None = None) -> dict[str, object]:
        self._ensure_started()
        html = self.page.content()
        ids = re.findall(r"news,\d{6},(\d+)\.html", html)
        title = str(self.page.title())
        actual = str(self.page.url)
        challenge = any(token in (title + " " + html).lower() for token in ("验证码", "验证", "robot", "安全验证"))
        stamp = time.strftime("%Y%m%dT%H%M%S")
        self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
        shot = self.diagnostics_dir / f"eastmoney-{stamp}.png"
        self.page.screenshot(path=str(shot), full_page=False)
        return {
            "requested_url": requested_url, "actual_url": actual, "page_title": title,
            "timestamp": stamp, "acquisition_mode": self.acquisition_mode,
            "profile": str(self.profile_dir), "dialogs": list(self.dialogs),
            "challenge_detected": challenge,
            "previous_page_post_id_hash": previous_ids_hash,
            "current_dom_post_id_hash": hashlib.sha256("|".join(ids).encode()).hexdigest(),
            "screenshot": str(shot),
        }

    def close(self) -> None:
        if self.context is not None:
            self.context.close()
        if self._playwright is not None:
            self._playwright.stop()
        self.delegate = None
        self.context = None
        self.page = None
        self._playwright = None


class ChromeCdpAttachTransport(ManagedChromiumTransport):
    """Drive a Chrome the operator launched, over the DevTools protocol.

    WHY THIS EXISTS (2026-09-28)
    ----------------------------
    Measured on this machine, the only difference page JavaScript can read
    between our runs and a browser the operator launches himself is
    ``navigator.webdriver``: **True** for anything Playwright launches, **False**
    for a Chrome started from a shell. Everything else measured identical --
    userAgent, ``navigator.plugins.length``, ``hardwareConcurrency``,
    ``languages`` and ``window.chrome`` all matched exactly.

    Two routes to a False were closed first:
      * *configuration*: ``ignore_default_args=["--enable-automation"]`` does
        NOT clear it (tested) -- it is set by Playwright, not by that switch;
      * *launching Chrome here*: blocked, because this environment refuses
        Chrome's own sandbox (``sandbox initialization failed: Operation not
        permitted``). That is also why every launch path in this repo carries
        ``--no-sandbox`` -- it is forced, not a disguise.

    So the browser is launched by the operator (outside this sandbox, with its
    normal sandbox, four flags and a persistent profile) and this transport
    attaches to it.

    FOCUS -- the reason the AppleScript modes are unusable
    -----------------------------------------------------
    Attaching and driving do not steal focus. Measured: with another app
    frontmost, 10 x ``Page.navigate`` + ``Runtime.evaluate`` left the frontmost
    application unchanged. ``existing-chrome`` cannot say that: every command is
    a ``tell application "Google Chrome"``, which brings Chrome to the front.
    (That repo already logs the symptom -- ``focus_log_path``.)

    CLOSE -- disconnect only
    ------------------------
    ``ManagedChromiumTransport.close()`` closes the context. For an attached
    browser that context belongs to the operator, so closing it would close his
    browser. Here we only drop the connection.
    """

    acquisition_mode = "chrome-cdp"

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        record_dialogs: bool = True,
        auto_dismiss_dialogs: bool = True,
        detail_referer_from_list: bool = False,
        list_click_paging: bool = False,
        detail_dwell_seconds: tuple[float, float] | None = None,
    ) -> None:
        self.endpoint = str(
            endpoint
            or os.environ.get("MYRESEARCHER_CDP_ENDPOINT")
            or "http://127.0.0.1:9222"
        )
        # Not a directory: nothing here may create or own a profile. Kept as a
        # string so an accidental `.mkdir()` on it cannot happen.
        self.profile_dir = self.endpoint
        self.profile_mode = "attached"
        self._playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.delegate = None
        self.dialogs: list[dict[str, str]] = []
        self.record_dialogs = bool(record_dialogs)
        self.auto_dismiss_dialogs = bool(auto_dismiss_dialogs)
        self.detail_referer_from_list = bool(detail_referer_from_list)
        self.list_click_paging = bool(list_click_paging)
        self.detail_dwell_seconds = detail_dwell_seconds
        self.diagnostics_dir = Path("runtime/diagnostics")
        print(
            f"acquisition_mode={self.acquisition_mode} "
            f"profile_mode={self.profile_mode} "
            f"profile_dir={self.profile_dir}",
            file=sys.stderr,
            flush=True,
        )

    @staticmethod
    def _exempt_loopback_from_proxy() -> None:
        """Keep the DevTools endpoint off the HTTP proxy (2026-10-02).

        This environment exports HTTP_PROXY/HTTPS_PROXY and no NO_PROXY, and
        Playwright's driver honours them -- so `connect_over_cdp` asked the proxy
        for `http://127.0.0.1:9222/json/version` and came back
        `Unexpected status 502 ... This does not look like a DevTools server`,
        which reads like a broken browser rather than a proxy. Loopback is never
        supposed to be proxied, so make that explicit for this process instead of
        asking every caller to remember `NO_PROXY=127.0.0.1`.
        """
        wanted = ("127.0.0.1", "localhost", "::1")
        for key in ("NO_PROXY", "no_proxy"):
            current = [h.strip() for h in os.environ.get(key, "").split(",") if h.strip()]
            for host in wanted:
                if host not in current:
                    current.append(host)
            os.environ[key] = ",".join(current)

    def _ensure_started(self) -> None:
        if self.delegate is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("chrome-cdp requires optional Playwright dependency") from exc
        self._playwright = sync_playwright().start()
        self._exempt_loopback_from_proxy()
        try:
            self.browser = self._playwright.chromium.connect_over_cdp(self.endpoint)
        except Exception as exc:  # noqa: BLE001
            self._playwright.stop()
            self._playwright = None
            raise RuntimeError(
                f"chrome-cdp could not attach to {self.endpoint}: {exc}. Launch "
                "Chrome first with --remote-debugging-port=<port> "
                "--remote-allow-origins=* and a dedicated --user-data-dir, then "
                "pass http://127.0.0.1:<port> here."
            ) from exc
        self.context = (
            self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self._bind_dialog_handler(self.page)
        self.delegate = self._new_delegate()

    def close(self) -> None:
        """Disconnect. Never close the context -- that browser is the operator's."""
        self.delegate = None
        self.page = None
        self.context = None
        self.browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
