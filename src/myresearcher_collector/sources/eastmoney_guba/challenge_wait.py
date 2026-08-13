"""Shared Eastmoney challenge-wait recovery for browser-managed acquisition.

When a visible browser page lands on the identity-verification shell it is
left open for the operator; the caller polls the live DOM until the shell is
gone.  The recovered document is consumed in place — no re-navigation is
issued, and no challenge is ever solved automatically.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .parser import is_access_block_page


def document_text(document: Any) -> str | None:
    """Best-effort decoded HTML text of an acquired document."""
    value = None
    for attr in ("payload", "body", "content"):
        value = getattr(document, attr, None)
        if value is not None:
            break
    if value is None:
        value = getattr(document, "text", None)
        if value is None:
            return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def wait_for_manual_verification(
    transport: Any,
    *,
    timeout_seconds: float,
    poll_seconds: float = 5.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> Any | None:
    """Wait until the visible browser page leaves the access-block shell.

    Polls ``transport.current_document()`` and never navigates.  Returns the
    recovered document, or None when the transport has no live-DOM polling
    or the wait times out.
    """
    current = getattr(transport, "current_document", None)
    if not callable(current):
        sleep_fn(max(0.0, timeout_seconds))
        return None
    deadline = monotonic_fn() + max(0.0, timeout_seconds)
    while monotonic_fn() < deadline:
        sleep_fn(min(poll_seconds, max(0.0, deadline - monotonic_fn())))
        try:
            candidate = current()
        except Exception:
            continue
        html = document_text(candidate)
        if html is not None and not is_access_block_page(html):
            return candidate
    return None


class ChallengeAwareEastmoneyTransport:
    """Wrap a browser-managed transport with manual challenge recovery.

    When a fetched page is the access-block shell the visible browser stays
    open and :func:`wait_for_manual_verification` polls the live DOM; the
    recovered document replaces the blocked response.  If the shell persists
    the original blocked response is returned so the collector can fail
    closed.
    """

    def __init__(
        self,
        delegate: Any,
        *,
        challenge_wait_seconds: float = 180.0,
        sleep_fn: Callable[[float], None] = time.sleep,
        prompt: Callable[[str], None] | None = None,
    ) -> None:
        self.delegate = delegate
        self.challenge_wait_seconds = challenge_wait_seconds
        self.sleep_fn = sleep_fn
        self._prompt = prompt or (lambda message: None)

    def get(self, url: str, *, timeout: float):
        response = self.delegate.get(url, timeout=timeout)
        html = document_text(response)
        if html is None or not is_access_block_page(html):
            return response
        self._prompt(
            f"access block for {url}; complete visible Chrome verification "
            f"within {self.challenge_wait_seconds:.0f}s; polling current DOM every 5s"
        )
        recovered = wait_for_manual_verification(
            self.delegate,
            timeout_seconds=self.challenge_wait_seconds,
            sleep_fn=self.sleep_fn,
        )
        return recovered if recovered is not None else response

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)
