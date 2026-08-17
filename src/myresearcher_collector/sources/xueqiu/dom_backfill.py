"""Range-aware Xueqiu DOM backfill using the shared simple posts store."""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ...backfill import BackfillRange, resolve_effective_backfill_range
from ...integration import plan_backfill
from ...page_anchor import PageAnchor
from ...simple_store import SimplePostStore
from .dom_parser import XueqiuDomItem, XueqiuDomParseError, parse_detail_status, parse_dom_page
from .dom_transport import XueqiuDomTransportError


@dataclass(frozen=True)
class XueqiuDomBackfillExecution:
    run_id: str
    source: str
    stock_code: str
    from_time: datetime
    to_time: datetime
    status: str
    stop_reason: str
    pages_scanned: int
    records_received: int
    records_in_range: int
    records_new: int
    records_existing: int
    records_failed: int
    records_versioned: int
    earliest_observed_at: datetime | None
    latest_observed_at: datetime | None
    range_complete: bool
    start_page: int
    modified_posts_resolved: int

    def as_dict(self) -> dict[str, Any]:
        value = self.__dict__.copy()
        for key in ("from_time", "to_time", "earliest_observed_at", "latest_observed_at"):
            if value[key] is not None:
                value[key] = value[key].astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return value


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _item_record(item: XueqiuDomItem, *, stock_code: str, published_at: datetime, edited_at: datetime | None) -> dict[str, Any]:
    return {
        "source": "xueqiu", "source_item_id": item.status_id,
        "stock_code": stock_code,
        "title": item.title, "content": item.content,
        "author_id": item.author_id, "author_name": item.author_name,
        "published_at": _iso(published_at), "url": item.url,
        "read_count": item.read_count, "reply_count": item.reply_count,
        "like_count": item.like_count, "forward_count": item.forward_count,
        "created_at": _iso(published_at), "updated_at": _iso(edited_at or published_at),
    }


def execute_xueqiu_dom_backfill(
    *,
    db_path: str | Path,
    stock_code: str,
    requested: BackfillRange,
    transport: Any,
    max_pages: int | None = None,
    start_page: int | None = None,
    min_interval: float = 3.0,
    max_interval: float = 10.0,
    clock: Callable[[], datetime] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    run_id: str | None = None,
) -> XueqiuDomBackfillExecution:
    if requested.source != "xueqiu" or requested.stock_code != stock_code:
        raise ValueError("Xueqiu backfill request/source mismatch")
    if min_interval < 3.0 or max_interval < min_interval:
        raise ValueError("Xueqiu pacing must be 3..10 seconds")
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be positive")
    if start_page is not None and start_page < 1:
        raise ValueError("start_page must be positive")
    clock = clock or (lambda: datetime.now(timezone.utc))
    sleep_fn = sleep_fn or (lambda seconds: __import__("time").sleep(seconds))
    started_at = clock()
    effective = resolve_effective_backfill_range(requested, started_at)
    resume_eligible = False
    run_id = run_id or uuid.uuid4().hex
    store = SimplePostStore(db_path)
    plan = plan_backfill(
        store, source="xueqiu", stock_code=stock_code,
        from_time=effective.from_time, to_time=effective.to_time,
        explicit_start_page=start_page, started_at=started_at,
    )
    # Match the shared backfill rule: only a frozen exact range with a valid
    # start/resume proof may create a reusable page resume row.  A moving top
    # is deliberately excluded even when the traversal begins at page 1.
    resume_eligible = plan.coverage_eligible and effective.to_time < started_at
    if plan.already_covered:
        store.close()
        return XueqiuDomBackfillExecution(
            run_id, "xueqiu", stock_code, requested.from_time, requested.to_time,
            "SUCCESS", "already_covered", 0, 0, 0, 0, 0, 0, 0, None, None, True,
            plan.start_page, 0,
        )

    pages_scanned = records_received = records_in_range = records_failed = 0
    records_new = records_existing = records_versioned = modified_resolved = 0
    earliest: datetime | None = None
    latest: datetime | None = None
    seen_ids: set[str] = set()
    previous_ids: tuple[str, ...] = ()
    range_complete = False
    status = "PARTIAL_COLLECTION"
    stop_reason = "max_pages_reached"
    unresolved = False
    try:
        transport.open_stock(stock_code)
        if plan.start_page > 1:
            page_payload = transport.goto_page(plan.start_page, previous_ids=())
        else:
            page_payload = transport.read_current_page()
        page_no = plan.start_page
        while True:
            if max_pages is not None and pages_scanned >= max_pages:
                break
            if int(page_payload.get("page_no", page_no)) != page_no:
                status, stop_reason = "PARTIAL_COLLECTION", "pagination_failure"
                break
            page = parse_dom_page(
                list(page_payload.get("items", [])), page_no=page_no, now=clock()
            )
            if not page.items:
                status, stop_reason = "COLLECTION_FAILED", "source_failure"
                break
            if previous_ids and page.active_ids == previous_ids:
                status, stop_reason = "COLLECTION_FAILED", "pagination_failure"
                break
            previous_ids = page.active_ids
            pages_scanned += 1
            records_received += len(page.items)
            page_records: list[tuple[dict[str, Any], datetime]] = []
            page_times: list[datetime] = []
            page_unresolved = False
            for item in page.items:
                if item.status_id in seen_ids:
                    continue
                seen_ids.add(item.status_id)
                resolved_item = item
                published_at = item.published_at
                edited_at = item.edited_at
                if published_at is None:
                    try:
                        sleep_fn(random.uniform(min_interval, max_interval))
                        detail = transport.read_detail_created_at(item.url)
                        detail_id, published_at, edited_at = parse_detail_status(detail)
                        if detail_id != item.status_id:
                            raise XueqiuDomParseError("detail status_id does not match list")
                        assert_main_page = getattr(transport, "assert_current_page", None)
                        if callable(assert_main_page):
                            assert_main_page(page_no, expected_ids=page.active_ids)
                        modified_resolved += 1
                        resolved_item = item
                    except (XueqiuDomTransportError, XueqiuDomParseError, ValueError):
                        records_failed += 1
                        page_unresolved = True
                        continue
                if published_at is None:
                    page_unresolved = True
                    records_failed += 1
                    continue
                earliest = published_at if earliest is None else min(earliest, published_at)
                latest = published_at if latest is None else max(latest, published_at)
                page_times.append(published_at)
                if published_at > effective.to_time or published_at < effective.from_time:
                    continue
                records_in_range += 1
                page_records.append((_item_record(resolved_item, stock_code=stock_code, published_at=published_at, edited_at=edited_at), published_at))
            unresolved = unresolved or page_unresolved
            with store.transaction():
                for record, published_at in page_records:
                    inserted = store.upsert_post(**record)
                    if inserted:
                        records_new += 1
                    else:
                        records_existing += 1
                if page_times:
                    store.save_page_anchor(PageAnchor(
                        source="xueqiu", stock_code=stock_code, observed_at=clock(),
                        page_no=page_no, page_min_time=min(page_times), page_max_time=max(page_times),
                        source_count=None, page_size=len(page.items),
                    ))
                if resume_eligible:
                    store.save_backfill_resume(
                        "xueqiu", stock_code, effective.from_time,
                        effective.to_time, page_no,
                    )
            if page_times and not page_unresolved and max(page_times) < effective.from_time:
                range_complete = True
                stop_reason = "backfill_range_complete"
                break
            next_page = page_no + 1
            if max_pages is not None and pages_scanned >= max_pages:
                break
            sleep_fn(random.uniform(min_interval, max_interval))
            page_payload = transport.goto_page(next_page, previous_ids=page.active_ids)
            page_no = next_page
        if range_complete and not unresolved and plan.coverage_eligible:
            status = "SUCCESS"
            with store.transaction():
                store.clear_backfill_resume("xueqiu", stock_code, effective.from_time, effective.to_time)
                store.add_coverage("xueqiu", stock_code, effective.from_time, effective.to_time)
        elif range_complete and not unresolved:
            # A manually selected page (or an otherwise unproven resume) may
            # have a complete-looking local suffix, but cannot certify the
            # requested range because earlier pages were not observed here.
            status, stop_reason = "PARTIAL_COLLECTION", "coverage_proof_incomplete"
        elif records_failed and records_failed >= records_in_range and records_in_range > 0:
            status, stop_reason = "COLLECTION_FAILED", "all_candidate_details_failed"
        elif not range_complete and status != "COLLECTION_FAILED":
            status = "PARTIAL_COLLECTION"
            stop_reason = stop_reason if stop_reason != "backfill_range_complete" else "source_failure"
    except (XueqiuDomTransportError, XueqiuDomParseError, ValueError):
        status, stop_reason = ("PARTIAL_COLLECTION" if pages_scanned else "COLLECTION_FAILED"), "source_failure"
    finally:
        store.close()
    return XueqiuDomBackfillExecution(
        run_id, "xueqiu", stock_code, requested.from_time, requested.to_time,
        status, stop_reason, pages_scanned, records_received, records_in_range,
        records_new, records_existing, records_failed, records_versioned,
        earliest, latest, range_complete, plan.start_page, modified_resolved,
    )
