"""Backfill range validation and reporting helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Iterable, Sequence


class BackfillConfigError(ValueError):
    """A backfill request is invalid before source access."""


SHANGHAI = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class BackfillRange:
    source: str
    stock_code: str
    from_time: datetime
    to_time: datetime


def _parse_bound(value: str, *, end_of_day: bool) -> datetime:
    text = value.strip()
    try:
        if len(text) == 10:
            parsed_date = date.fromisoformat(text)
            local = datetime.combine(parsed_date, time.max if end_of_day else time.min)
            return local.replace(tzinfo=SHANGHAI).astimezone(timezone.utc)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BackfillConfigError("range bounds must be ISO date or timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def resolve_backfill_range(
    *,
    source: str,
    stock_code: str,
    from_value: str | None = None,
    to_value: str | None = None,
    days: int | None = None,
    now: datetime | None = None,
) -> BackfillRange:
    if source not in {"eastmoney_guba", "xueqiu"}:
        raise BackfillConfigError("unsupported backfill source")
    if not isinstance(stock_code, str) or len(stock_code) != 6 or not stock_code.isdigit():
        raise BackfillConfigError("stock must be six decimal digits")
    if days is not None and (from_value is not None or to_value is not None):
        raise BackfillConfigError("--days cannot be combined with --from/--to")
    if days is not None:
        if isinstance(days, bool) or days < 1:
            raise BackfillConfigError("days must be a positive integer")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        local_date = current.astimezone(SHANGHAI).date()
        from_local = datetime.combine(
            local_date - timedelta(days=days - 1), time.min, tzinfo=SHANGHAI
        )
        to_local = datetime.combine(local_date, time.max, tzinfo=SHANGHAI)
        from_time = from_local.astimezone(timezone.utc)
        to_time = to_local.astimezone(timezone.utc)
    else:
        if from_value is None or to_value is None:
            raise BackfillConfigError("--from and --to must be supplied together")
        from_time = _parse_bound(from_value, end_of_day=False)
        to_time = _parse_bound(to_value, end_of_day=True)
    if from_time > to_time:
        raise BackfillConfigError("from_time must be at or before to_time")
    return BackfillRange(source, stock_code, from_time, to_time)


def range_as_dict(value: BackfillRange) -> dict[str, str]:
    return {
        "source": value.source,
        "stock_code": value.stock_code,
        "from_time": value.from_time.isoformat().replace("+00:00", "Z"),
        "to_time": value.to_time.isoformat().replace("+00:00", "Z"),
    }


def merge_coverage_intervals(
    intervals: Iterable[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Merge overlapping or adjacent [covered_from, covered_to] intervals.

    Coverage intervals only ever come from fully completed backfill ranges, so
    no interval ever exceeds the caller-validated [from, to] shape.
    """
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        if start > end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def coverage_covers(
    covered: Sequence[tuple[datetime, datetime]],
    from_time: datetime,
    to_time: datetime,
) -> bool:
    """True when the union of merged coverage intervals fully covers the range.

    Only completed backfills produce coverage, so a True result proves the
    requested range was already acquired and persisted.
    """
    covered_until = from_time
    for start, end in covered:
        if end < covered_until:
            continue
        if start > covered_until:
            return False
        covered_until = end
        if covered_until >= to_time:
            return True
    return covered_until >= to_time


def coverage_boundary(
    covered: Sequence[tuple[datetime, datetime]],
    from_time: datetime,
) -> datetime | None:
    """Largest ``to`` such that [from_time, to] is fully covered, else None.

    None means the requested from_time itself is not covered, so an overlap
    stop can never be declared while scanning.
    """
    covered_until = from_time
    boundary: datetime | None = None
    for start, end in covered:
        if end < covered_until:
            continue
        if start > covered_until:
            return boundary
        covered_until = end
        boundary = end
    return boundary


def coverage_stop_predicate(
    covered: Sequence[tuple[datetime, datetime]],
    from_time: datetime,
) -> Callable[[datetime, datetime], bool]:
    """Build the conservative early-stop check for one backfill traversal.

    The returned predicate receives one successfully parsed page's
    (page_min, page_max) and returns True only when the whole page lies inside
    the covered region below from_time: page_min >= from_time and
    page_max <= boundary, where boundary is the farthest covered point
    reachable continuously from from_time.  A page that merely grazes the
    covered region never stops the traversal; over-collecting one page is
    acceptable, missing data is not.
    """
    boundary = coverage_boundary(covered, from_time)
    if boundary is None:
        return lambda _page_min, _page_max: False
    return lambda page_min, page_max: (
        page_min >= from_time and page_max <= boundary
    )
