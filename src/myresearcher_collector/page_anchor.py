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
    """Find a live page containing ``target_to`` with bounded probes."""
    if max_probes < 1:
        raise ValueError("max_probes must be positive")
    anchor = choose_anchor(anchors, target_to)
    first_page = anchor.page_no if anchor else 1
    visited: set[int] = set()
    current_source_count: int | None = None
    page = first_page
    step = 1
    direction: int | None = None
    last: PageProbe | None = None

    for _ in range(max_probes):
        if page < 1 or page in visited:
            raise SeekFailure("time seek exhausted valid page candidates")
        visited.add(page)
        current = probe(page)
        last = current
        current_source_count = current.source_count
        if anchor is not None and page == first_page:
            predicted = predict_page(anchor, current_source_count)
            if predicted != page:
                page = predicted
                continue
        if current.page_min_time <= target_to <= current.page_max_time:
            return SeekProof(
                target_to=target_to,
                start_page=max(1, current.page_no - safety_pages),
                verified_page=current.page_no,
                verified_page_min_time=current.page_min_time,
                verified_page_max_time=current.page_max_time,
                probe_count=len(visited),
                anchor_used=anchor,
            )
        if current.page_no == 1 and current.page_max_time < target_to:
            # The requested top is newer than the currently visible newest
            # page; page 1 is the conservative starting boundary.
            return SeekProof(
                target_to=target_to, start_page=1, verified_page=1,
                verified_page_min_time=current.page_min_time,
                verified_page_max_time=current.page_max_time,
                probe_count=len(visited), anchor_used=anchor,
            )
        if current.page_min_time > target_to:
            wanted_direction = 1  # page number increases toward older posts
        else:
            wanted_direction = -1
        if direction is not None and wanted_direction != direction:
            step = max(1, step // 2)
        else:
            step = min(step * 2, 1 << 20)
        direction = wanted_direction
        page = current.page_no + direction * step
    raise SeekFailure(f"time seek exceeded probe limit ({max_probes})")
