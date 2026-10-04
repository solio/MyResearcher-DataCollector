"""Small, non-authoritative page anchors and bounded historical time seek."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Sequence


@dataclass(frozen=True)
class PageAnchor:
    source: str
    stock_code: str
    observed_at: datetime
    page_no: int
    page_min_time: datetime
    page_max_time: datetime
    source_count: int | None
    page_size: int


@dataclass(frozen=True)
class PageProbe:
    page_no: int
    page_min_time: datetime
    page_max_time: datetime
    source_count: int | None
    page_size: int


@dataclass(frozen=True)
class SeekProof:
    target_to: datetime
    start_page: int
    verified_page: int
    verified_page_min_time: datetime
    verified_page_max_time: datetime
    probe_count: int
    anchor_used: PageAnchor | None
    # The pages actually probed, in order. Carried so a seek can be audited from
    # its result alone: on 2026-09-27 the only record of which pages had been
    # requested was the browser's URL bar, so a converging search could not be
    # told apart from a loop without re-running it.
    probe_pages: tuple[int, ...] = ()


class SeekFailure(RuntimeError):
    """Bounded live probing could not establish a time boundary."""


def predict_page(anchor: PageAnchor, current_source_count: int | None) -> int:
    """Return a navigation hint; this is never a proof."""
    if (
        current_source_count is None
        or anchor.source_count is None
        or anchor.page_size <= 0
    ):
        return max(1, anchor.page_no)
    shift = round((current_source_count - anchor.source_count) / anchor.page_size)
    return max(1, anchor.page_no + shift)


def choose_anchor(anchors: Sequence[PageAnchor], target_to: datetime) -> PageAnchor | None:
    if not anchors:
        return None
    return min(
        anchors,
        key=lambda item: (
            0 if item.page_min_time <= target_to <= item.page_max_time else
            min(abs((target_to - item.page_min_time).total_seconds()), abs((target_to - item.page_max_time).total_seconds())),
            -item.observed_at.timestamp(),
        ),
    )


def seek_historical_page(
    *,
    target_to: datetime,
    anchors: Sequence[PageAnchor],
    probe: Callable[[int], PageProbe],
    max_probes: int = 20,
    safety_pages: int = 1,
) -> SeekProof:
    """Find a live page containing ``target_to`` with bounded probes.

    BRACKETED SEARCH, NOT A STEP WALK
    ---------------------------------
    Page numbers are newest-first, so ``page_min_time`` and ``page_max_time``
    fall monotonically as the page number rises. That makes the page number a
    sorted key, and a bracketed search over it is well founded: keep
    ``too_new`` (highest page known to start after the target) and ``too_old``
    (lowest page known to end before it), and only ever probe strictly inside
    the open interval they define.

    The previous revision walked ``page + direction * step`` with a step that
    doubled and halved on each direction change. That can compute its way back
    onto an already probed page and then give up with "time seek exhausted
    valid page candidates" -- observed 2026-09-27 on 603997, where the probe
    sequence was 12, 14, 18, 16 and the fifth step returned to 12, aborting the
    seek while the pages that actually bracket the target (13, 15, 17) had
    never been probed. The abort looked like "the target cannot be found"
    when it was really "the walk ran out of unvisited squares".

    A bracketed search cannot revisit: every probe either returns a proof or
    becomes a new bound, so the interval strictly shrinks and the pages inside
    it are by construction unvisited. Two phases, both of them binary-search
    shaped: (a) with only one side of the target seen, DOUBLE the page number
    until the other side appears, and (b) with both sides seen, halve the
    interval with ``(too_new + too_old) // 2`` until it is one page wide.

    WHY DOUBLING AND NOT A RATE-BASED JUMP: the exchange rate between dates and
    pages is not stable. On 601012 (2026-09-27) the real captured anchors show
    page 121 spanning 18.7h and page 122 spanning 1.25h -- a 15x swing between
    adjacent pages -- so "days divided by this page's span" is a guess that can
    be wrong by an order of magnitude, while doubling is bounded by construction
    (one probe per octave, then log2 of the bracket). Measured over 400 bursty
    maps, a rate-based jump averaged 5.8 probes against 11.8 for
    doubling-then-bisecting, but its worst case was unbounded and it depended on
    the spans being representative, which they are not. The bounded version was
    chosen deliberately; if the probe budget ever becomes the bottleneck rather
    than correctness, the measurement to revisit is in this docstring.

    ``probe`` is expected to report a page past the end of the bar as one whose
    times are older than any real post rather than raising, so that an
    over-deep step lands as a ``too_old`` bound instead of a fatal error. The
    times of such a page are never returned as a proof: a ``too_new`` page or a
    real bracket is.
    """
    if max_probes < 1:
        raise ValueError("max_probes must be positive")
    if safety_pages < 0:
        raise ValueError("safety_pages must not be negative")

    anchor = choose_anchor(anchors, target_to)
    first_page = anchor.page_no if anchor else 1
    visited: set[int] = set()
    seen: dict[int, PageProbe] = {}
    too_new: int | None = None  # page_min_time > target: the answer is deeper
    too_old: int | None = None  # page_max_time < target: the answer is shallower
    page = first_page

    def proof_for(entry: PageProbe, *, start_page: int) -> SeekProof:
        return SeekProof(
            target_to=target_to,
            start_page=max(1, start_page),
            verified_page=entry.page_no,
            verified_page_min_time=entry.page_min_time,
            verified_page_max_time=entry.page_max_time,
            probe_count=len(visited),
            anchor_used=anchor,
            probe_pages=tuple(seen),
        )

    for _ in range(max_probes):
        if page < 1:
            raise SeekFailure("time seek exhausted valid page candidates")
        if page in visited:
            raise SeekFailure("time seek exhausted valid page candidates")
        current = probe(page)
        seen[page] = current

        if current.page_no == 1 and current.page_max_time < target_to:
            # Resolved BEFORE the prediction on purpose, and this ordering is
            # load-bearing: page 1 is the newest page that exists, so "the target
            # is newer than everything" is already proven and no count-based
            # prediction can improve on it. Applying the prediction first (as the
            # step walk did) jumped away from page 1, and the walk back down
            # re-entered page 1 -- already probed -- and gave up. That is a
            # second, independent reason the drivers bypass the seek entirely.
            return proof_for(current, start_page=1)

        if current.page_no == first_page and anchor is not None:
            # Applied BEFORE the bracket check, deliberately: the anchor's own
            # time range is stale by however long collection was paused, so a
            # page that appears to bracket the target at the anchor's old page
            # number is not evidence. The count shift is what relocates it. The
            # result is still only a prediction -- the relocated page has to
            # bracket like any other probe.
            predicted = predict_page(anchor, current.source_count)
            if (
                predicted != current.page_no
                and predicted >= 1
                and predicted not in visited
            ):
                    # RELOCATED PAGES ARE NOT SEARCHED SQUARES (2026-10-02). This
                # branch preempts the bracket check on purpose (see the comment
                # above, pinned by test_case1_source_count_drift_predicts_without_page1),
                # so the page's measurement is deliberately not used here -- but
                # it must stay RE-PROBEABLE, because the bisection can compute
                # its way back onto it and then has nothing else to try.
                #
                # It used to be added to `visited` before this branch, which made
                # the returning midpoint an abort instead of a probe:
                #   603039 -> 130 (anchor page, which DOES bracket the target)
                #   -> 131 -> 65 -> 98 -> 114 -> 122 -> 126 -> 128 -> 129, then
                #   (129+131)//2 == 130 was in `visited` -> SeekFailure after 9
                #   of 20 probes, with the answer already measured once.
                # Marking only the pages actually used as bounds also means
                # `probe_count` counts searched squares rather than requests.
                page = predicted
                continue

        # Only now is this page a searched square: it either proves the target
        # or becomes one of the two bounds.
        visited.add(current.page_no)

        if current.page_min_time <= target_to <= current.page_max_time:
            return proof_for(current, start_page=current.page_no - safety_pages)

        if current.page_min_time > target_to:
            too_new = current.page_no
        else:
            too_old = current.page_no

        if too_new is not None and too_old is not None:
            if too_old - too_new <= 1:
                # No page lies between the bounds, so the target falls in a gap
                # with no posts, or past the end of the bar (which the caller
                # reports as an "older than any post" page). Either way the walk
                # only needs a page provably at or newer than the target, and
                # `too_new` is exactly that.
                return proof_for(seen[too_new], start_page=too_new)
            page = (too_new + too_old) // 2
            continue

        # Only one side of the target has been seen so far: double the page
        # number (or halve it, when the target turned out to be shallower) until
        # the other side shows up. Once both bounds exist the loop above bisects
        # them. Nothing in either phase depends on how many days a page happens
        # to span, which is the quantity that is NOT stable: on 601012
        # (2026-09-27) page 121 spans 18.7h and page 122 spans 1.25h, so a
        # rate-based jump is a guess while a doubling step is bounded by
        # construction. Worst case is one doubling per octave plus log2 of the
        # final bracket.
        if too_new is not None:
            page = max(2, too_new * 2)
        else:
            page = max(1, too_old // 2)
    raise SeekFailure(f"time seek exceeded probe limit ({max_probes})")
