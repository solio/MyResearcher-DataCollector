"""The manual-verification wait must not declare victory on bytes alone.

Regression under test (2026-09-28): the wait decided "the operator cleared it"
from ``is_access_block_page`` -- an *asset* signature that cannot see the
captcha form this project actually meets, an iframe injected after load on top
of an otherwise normal page.  On that form the bytes look clean while the
slider is still on screen, so the wait returned on its **first poll, five
seconds in**, logged ``manual_verification_resumed`` for a challenge nobody
cleared, and sent the caller on to re-request -- which on the list-page path
re-issues ``page.goto(list_url)`` and wipes the page the operator is solving.

The pair of tests that matters is (1) and (3): identical bytes, identical
timing, and the ONLY difference is whether the transport can report the
rendered page.  If (1) ever stops failing, the veto has been lost again.
"""

from __future__ import annotations

from datetime import datetime, timezone

from myresearcher_collector.sources.eastmoney_guba.acquisition import (
    BROWSER_DOM_SNAPSHOT,
    AcquiredDocument,
)
from myresearcher_collector.sources.eastmoney_guba.challenge_wait import (
    live_challenge_reasons,
    wait_for_manual_verification,
)

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)

# Bytes only. Deliberately a healthy-looking detail page: no challenge <title>,
# nothing from the asset tier. This is exactly what the source serves for the
# overlay form.
CLEAN_BYTES = (
    '<html><head><title>某个帖子标题_601012股吧_东方财富网股吧</title></head>'
    '<body><script>var post_article={"post_id":1};</script></body></html>'
)

# One poll's worth of "the slider is still on screen", as the live probe reports
# it: an injected iframe, no challenge title, and therefore no asset-tier hit.
OVERLAY_REASONS = [
    "visible_overlay:iframe[src*=captcha]",
    "dom_asset:em_capt.js",
]


class Clock:
    """Deterministic monotonic clock: sleeping advances it, nothing else does."""

    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


class RenderedPage:
    """A transport that can report both the bytes and the rendered page."""

    def __init__(self, reasons_per_poll) -> None:
        # A callable returning the live reasons at each poll, or a fixed list.
        self.reasons_per_poll = reasons_per_poll
        self.polls = 0

    def current_document(self):
        self.polls += 1
        return AcquiredDocument(
            CLEAN_BYTES.encode(), "u", "u", BROWSER_DOM_SNAPSHOT, NOW, None, None, {}
        )

    def challenge_reasons(self):
        if callable(self.reasons_per_poll):
            return list(self.reasons_per_poll(self.polls))
        return list(self.reasons_per_poll)


class BytesOnlyTransport(RenderedPage):
    """`existing-chrome` shape: a live document, but no live-DOM probe.

    `challenge_reasons = None` as a class attribute is what such a transport
    actually looks like to `getattr(transport, "challenge_reasons", None)`.
    """

    challenge_reasons = None

    def __init__(self) -> None:
        super().__init__([])


def _wait(transport, *, seconds=180.0, sleep_fn=None, monotonic_fn=None):
    clock = Clock()
    return (
        wait_for_manual_verification(
            transport,
            timeout_seconds=seconds,
            sleep_fn=sleep_fn or clock.sleep,
            monotonic_fn=monotonic_fn or clock.monotonic,
        ),
        clock,
    )


def test_wait_does_not_clear_while_the_rendered_page_is_challenged():
    """THE REGRESSION: bytes clean, page challenged -> must not report recovery.

    Before the fix this returned a document after a single 5s poll; the ledger
    then claimed the operator had cleared a captcha that was still on screen.
    """
    transport = RenderedPage(OVERLAY_REASONS)
    recovered, clock = _wait(transport, seconds=12.0)

    assert recovered is None, "the live DOM veto was lost -- bytes alone decided"
    assert transport.polls >= 2, (
        "the wait must keep polling, not answer on the first sample"
    )
    assert clock.slept and all(s <= 5.0 for s in clock.slept)


def test_wait_clears_once_the_rendered_page_also_stops_reporting_a_challenge():
    """The other half of the veto: agreement clears the wait, and promptly."""

    def reasons(poll: int) -> list[str]:
        return [] if poll >= 3 else OVERLAY_REASONS

    transport = RenderedPage(reasons)
    recovered, clock = _wait(transport, seconds=60.0)

    assert recovered is not None
    assert transport.polls == 3
    assert clock.t >= 10.0  # two sleeps of 5s before the third poll


def test_byte_oracle_alone_still_blocks_for_a_fully_armed_shell():
    """A byte-visible shell keeps its old meaning: keep waiting until it clears."""
    shell = "<title>身份核实</title><script>fd_guba_validate</script>"

    class ShellThenClean(RenderedPage):
        def current_document(self):
            self.polls += 1
            html = shell if self.polls < 2 else CLEAN_BYTES
            return AcquiredDocument(
                html.encode(), "u", "u", BROWSER_DOM_SNAPSHOT, NOW, None, None, {}
            )

    transport = ShellThenClean([])
    recovered, _ = _wait(transport, seconds=60.0)

    assert recovered is not None
    assert transport.polls == 2


def test_control_a_transport_without_a_live_probe_keeps_the_old_behaviour():
    """NEGATIVE CONTROL for the two tests above.

    Same bytes, same timing, and the only difference is that this transport
    cannot probe the rendered page. It must still clear on the first poll --
    otherwise `existing-chrome`, which has no other oracle, would hang for the
    full timeout on every block.
    """
    transport = BytesOnlyTransport()
    assert live_challenge_reasons(transport) == []

    recovered, clock = _wait(transport, seconds=180.0)

    assert recovered is not None
    assert transport.polls == 1


def test_a_failing_live_probe_is_fail_open_not_fail_shut():
    """A probe that raises must not pin the wait open for the whole timeout."""

    class ExplodingProbe(RenderedPage):
        def challenge_reasons(self):
            raise RuntimeError("execution context was destroyed")

    transport = ExplodingProbe([])
    assert live_challenge_reasons(transport) == []

    recovered, _ = _wait(transport, seconds=180.0)
    assert recovered is not None


class PhotographedPage(RenderedPage):
    """A transport that can also photograph the page, like managed-chromium."""

    def __init__(self, reasons_per_poll) -> None:
        super().__init__(reasons_per_poll)
        self.shots: list[str] = []

    def diagnostic_snapshot(self, requested_url, *, previous_ids_hash=None):
        self.shots.append(requested_url)
        return {"screenshot": f"runtime/diagnostics/{requested_url}.png"}


def test_the_disagreement_is_photographed_and_the_agreement_is_not():
    """The instrument must be bounded to the case it exists to prove.

    A per-poll snapshot would write hundreds of megabytes over a blocked night,
    so the rule is: photograph only when the two oracles disagree, at most twice.
    """

    def reasons(poll: int) -> list[str]:
        return [] if poll >= 3 else OVERLAY_REASONS

    transport = PhotographedPage(reasons)
    recovered, _ = _wait(transport, seconds=60.0)

    assert recovered is not None
    assert transport.shots == ["wait:live_dom", "wait:cleared"]

    # The control: both oracles agree throughout, so nothing is photographed.
    agreeing = PhotographedPage([])
    recovered, _ = _wait(agreeing, seconds=60.0)
    assert recovered is not None
    assert agreeing.shots == []


def test_a_transport_that_cannot_photograph_still_waits_correctly():
    """No snapshot capability must not change the verdict, only the evidence."""

    def reasons(poll: int) -> list[str]:
        return [] if poll >= 2 else OVERLAY_REASONS

    transport = RenderedPage(reasons)
    recovered, _ = _wait(transport, seconds=60.0)

    assert recovered is not None
    assert transport.polls == 2
