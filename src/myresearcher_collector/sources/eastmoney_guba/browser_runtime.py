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
from .browser_transport import EastmoneyBrowserSocketTransport, EastmoneyBrowserTransport
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
):
    """Create the shared Eastmoney browser acquisition transport.

    managed-chromium without an explicit profile_dir selects a new per-run
    profile; an explicit profile_dir keeps persistent reuse.
    """
    if acquisition_mode == "existing-chrome":
        return EastmoneyExistingChromeDomTransport()
    if acquisition_mode == "chrome-clean":
        return ChromeCleanDomTransport(profile_dir=profile_dir or DEFAULT_CHROME_PROFILE)
    if acquisition_mode == "managed-chromium":
        return ManagedChromiumTransport(profile_dir=profile_dir)
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

    def __init__(self, *, profile_dir: str | Path | None = None) -> None:
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
        self.diagnostics_dir = Path("runtime/diagnostics")
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
        self.page.on("dialog", self._on_dialog)
        self.delegate = EastmoneyBrowserTransport(self.page)

    def _on_dialog(self, dialog) -> None:
        self.dialogs.append({"type": str(dialog.type), "message": str(dialog.message)})
        try:
            dialog.dismiss()
        except Exception:
            pass

    def get(self, url: str, *, timeout: float):
        self._ensure_started()
        return self.delegate.get(url, timeout=timeout)

    def current_document(self) -> AcquiredDocument:
        self._ensure_started()
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
