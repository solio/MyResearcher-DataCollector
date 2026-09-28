"""Shared Eastmoney challenge-wait recovery for browser-managed acquisition.

When a visible browser page lands on the identity-verification shell it is
left open for the operator; the caller polls the live DOM until the shell is
gone.  The recovered document is consumed in place — no re-navigation is
issued, and no challenge is ever solved automatically.

BOTH ORACLES, OR THE WAIT LIES (2026-09-28)
-------------------------------------------
This function used to decide "the operator has cleared it" from the response
bytes alone (``is_access_block_page``).  That oracle is an *asset* signature:
it needs a challenge ``<title>`` **and** one of ``em_capt.js`` /
``validate.js`` / ``emcaptcha`` / ``fd_guba_validate``.  The form that actually
shows up in the referer scheme is a captcha **iframe injected after load**
(`runtime/diagnostics/eastmoney-20260924T123054.png`: a slider modal on top of
a fully rendered list page), where the live-DOM probe reports
``visible_overlay:iframe[src*=captcha]`` — and its asset tier does not fire at
all.  So on that form the bytes look clean while the captcha is still on
screen, and the wait returned **on its first poll, five seconds in**.

Measured over the production ledger (7 managed-chromium detail blocks that
reached a wait), the split is perfect:

    byte oracle ALSO red  (asset_signature_blocked=True)  -> 1-3 polls -> 3/3 success
    byte oracle blind     (asset_signature_blocked=False) -> exactly 1 poll -> 4/4 access_block

The second group is not a wait, it is a lie with a 5-second stamp.  Two costs,
both real: the ledger records ``manual_verification_resumed`` for a challenge
nobody cleared, and the caller then re-issues the request — on the list-page
path that re-issues ``page.goto(list_url)``, i.e. **it navigates away from the
very page the operator is trying to solve**, which reads as "the captcha page
refreshed itself for no reason".  Eleven of eleven blocks in the trace corpus
are followed by a navigation back to that same list URL.

The live DOM therefore gets a veto: the wait reports recovery only when the
bytes AND the rendered page agree.  A transport that cannot probe the live DOM
(``existing-chrome`` reads a tab, not a Playwright page) keeps the old
byte-only behaviour, because for it the bytes are the only oracle there is.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable

from .parser import is_access_block_page


def _trace(event: str, **fields: object) -> None:
    """One line on stderr per wait decision, same shape as the transport trace.

    ``HH:MM:SS.mmm EVENT k=v`` so one grep reads the navigation trace and this
    together. Never raises: an instrument that can break what it measures is
    worse than no instrument.
    """
    try:
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        detail = " ".join(f"{key}={value}" for key, value in fields.items())
        print(f"{stamp} {event} {detail}".rstrip(), file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001
        pass


def live_challenge_reasons(transport: Any) -> list[str]:
    """Rendered-page challenge signals, or [] when the transport cannot say.

    Fail-open by construction, exactly like every other live probe here: an
    empty list means "no signal", never "proven clean". A transport without the
    method (``existing-chrome``) degrades to the byte oracle instead of blocking
    forever.
    """
    probe = getattr(transport, "challenge_reasons", None)
    if not callable(probe):
        return []
    try:
        return list(probe() or [])
    except Exception:  # noqa: BLE001
        return []


def _photograph(transport: Any, moment: str) -> str | None:
    """Ask the transport to photograph the page. Returns the path, or None.

    WHY: the claim under test is "the captcha was still on screen when the wait
    called it cleared". Every other signal here is a string comparison against
    a page nobody looked at -- the exact shape of the mistake that let a live
    slider be reported as no-block for six weeks (2026-08-13 -> 09-23). A PNG at
    the decision is the only reading that can settle it.

    BOUNDED ON PURPOSE: only called when the byte and live oracles disagree, and
    at most twice per wait (first disagreement, then the clear). A per-poll
    snapshot would write hundreds of megabytes over a blocked night.
    """
    snapshot = getattr(transport, "diagnostic_snapshot", None)
    if not callable(snapshot):
        return None
    try:
        info = snapshot(f"wait:{moment}")
    except Exception:  # noqa: BLE001
        return None
    path = info.get("screenshot") if isinstance(info, dict) else None
    return str(path) if path else None


def _dialogs(transport: Any) -> str | None:
    """JS dialogs seen so far, rendered for a trace line.

    A browser alert is a signal the *page* raised -- the source pops
    `验证错误,请重试(0)` when a slider check is rejected. In the enrich path
    nothing used to persist it: `ManagedChromiumRuntime._on_dialog` appends to a
    list that only `diagnostic_snapshot` reads, so an alert was auto-dismissed
    and written nowhere while the operator watched it on screen and the run's log
    said nothing. These lines make the trace answer "was there an alert, and what
    did it say" on its own.
    """
    seen = getattr(transport, "dialogs", None)
    if not seen:
        return None
    try:
        return ";".join(
            f"{item.get('type')}:{item.get('message')}" for item in list(seen)[-3:]
        )
    except Exception:  # noqa: BLE001
        return None


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

    Recovery requires **both** oracles to agree (module docstring): the bytes
    must stop matching the asset signature AND the rendered page must stop
    reporting challenge reasons.  The byte-only test is what let a live captcha
    be declared cleared after a single 5s poll.
    """
    current = getattr(transport, "current_document", None)
    if not callable(current):
        sleep_fn(max(0.0, timeout_seconds))
        return None
    started = monotonic_fn()
    deadline = started + max(0.0, timeout_seconds)
    vetoed_at: float | None = None
    while monotonic_fn() < deadline:
        sleep_fn(min(poll_seconds, max(0.0, deadline - monotonic_fn())))
        try:
            candidate = current()
        except Exception:
            continue
        html = document_text(candidate)
        if html is None or is_access_block_page(html):
            continue
        reasons = live_challenge_reasons(transport)
        if reasons:
            # The bytes look clean and the page does not. Trust the page: this
            # is the injected-overlay form, and clearing here would hand the
            # caller a "recovered" document that is still a captcha.
            if vetoed_at is None:
                vetoed_at = monotonic_fn()
                shot = _photograph(transport, "live_dom")
                _trace(
                    "WAIT_BLOCKED",
                    why="live_dom", after=round(vetoed_at - started, 1),
                    reasons=";".join(reasons[:4]), screenshot=shot,
                    dialogs=_dialogs(transport),
                )
            continue
        # Clearing after a veto is the transition worth a picture: it is the
        # moment the operator's captcha visibly goes away.
        shot = _photograph(transport, "cleared") if vetoed_at is not None else None
        _trace(
            "WAIT_CLEAR", after=round(monotonic_fn() - started, 1),
            after_veto=round(monotonic_fn() - vetoed_at, 1) if vetoed_at else None,
            screenshot=shot,
            dialogs=_dialogs(transport),
        )
        return candidate
    _trace(
        "WAIT_TIMEOUT", after=round(monotonic_fn() - started, 1),
        dialogs=_dialogs(transport),
    )
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
