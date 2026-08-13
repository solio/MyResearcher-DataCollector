"""Small selectable browser runtimes for Eastmoney detail acquisition."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .acquisition import AcquiredDocument, BROWSER_DOM_SNAPSHOT
from .browser_transport import EastmoneyBrowserTransport
from .existing_chrome import EastmoneyExistingChromeDomTransport


DEFAULT_CHROME_PROFILE = Path(".runtime/browser-profiles/eastmoney-chrome")
DEFAULT_MANAGED_PROFILE = Path(".runtime/browser-profiles/eastmoney-managed-chromium")


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
    """Launch a visible persistent Playwright Chromium/Chrome context."""

    acquisition_mode = "managed-chromium"

    def __init__(self, *, profile_dir: str | Path = DEFAULT_MANAGED_PROFILE) -> None:
        self.profile_dir = Path(profile_dir).expanduser().resolve()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = None
        self.context = None
        self.page = None
        self.delegate = None

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
        self.delegate = EastmoneyBrowserTransport(self.page)

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

    def close(self) -> None:
        if self.context is not None:
            self.context.close()
        if self._playwright is not None:
            self._playwright.stop()
        self.delegate = None
        self.context = None
        self.page = None
        self._playwright = None
