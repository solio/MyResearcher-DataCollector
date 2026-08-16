"""Deterministic Xueqiu collection logic over a browser-owned transport."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlencode

from myresearcher_collector.models import CollectionResult, CollectionStatus, RuntimeCounters, SourceItem

from .browser_transport import XueqiuResponse, XueqiuTransport, redact_xueqiu_url
from .parser import (
    SCHEMA_VERSION,
    XueqiuPage,
    XueqiuPaginationError,
    XueqiuParseError,
    XueqiuSchemaMismatch,
    parse_item,
    parse_page,
)


SOURCE = "xueqiu"
XUEQIU_BOOTSTRAP_MIN_PAGES = 2


class RawEvidenceStore(Protocol):
    def put(self, kind: str, source_item_id: str | None, payload: bytes) -> str:
        """Store an immutable source response and return an opaque reference."""


class InMemoryRawEvidenceStore:
    """Small deterministic evidence store for offline adapter tests."""

    def __init__(self) -> None:
        self.snapshots: dict[str, bytes] = {}

    def put(self, kind: str, source_item_id: str | None, payload: bytes) -> str:
        digest = hashlib.sha256(payload).hexdigest()
        ref = f"memory://{kind}/{source_item_id or 'page'}/{digest}"
        self.snapshots[ref] = bytes(payload)
        return ref


@dataclass(frozen=True)
class CollectorConfig:
    timeout_seconds: float = 20.0
    max_pages: int = XUEQIU_BOOTSTRAP_MIN_PAGES
    max_attempts: int = 2
    base_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 10.0
    min_interval_seconds: float = 3.0


class XueqiuAccessFailure(RuntimeError):
    """401/403/WAF/challenge/session failure; never no-data."""


class XueqiuTransportFailure(RuntimeError):
    """Transport or browser observation failure."""


def symbol_for(stock_code: str) -> str:
    if not isinstance(stock_code, str) or len(stock_code) != 6 or not stock_code.isdigit():
        raise ValueError("stock_code must be six decimal digits")
    if not stock_code.startswith(("0", "2", "3", "6")):
        raise ValueError("only A-share stock codes are supported")
    return ("SH" if stock_code.startswith("6") else "SZ") + stock_code


class XueqiuCollector:
    def __init__(
        self,
        transport: XueqiuTransport,
        *,
        evidence_store: RawEvidenceStore | None = None,
        config: CollectorConfig | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
        monotonic_fn: Callable[[], float] = time.monotonic,
        cancel_check: Callable[[], bool] | None = None,
        min_interval_seconds: float | None = None,
    ) -> None:
        self.transport = transport
        self.evidence_store = evidence_store or InMemoryRawEvidenceStore()
        self.config = config or CollectorConfig()
        if min_interval_seconds is not None:
            self.config = replace(self.config, min_interval_seconds=min_interval_seconds)
        self.sleep_fn = sleep_fn
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.monotonic_fn = monotonic_fn
        self.cancel_check = cancel_check
        self._last_request_at: float | None = None
        if self.config.min_interval_seconds < 3.0:
            raise ValueError("min_interval_seconds must be at least 3.0")

    @staticmethod
    def _body(response: Any) -> tuple[int, bytes, dict[str, str], str | None]:
        status = int(getattr(response, "status_code", getattr(response, "status", 0)))
        raw = getattr(response, "body", None)
        if callable(raw):
            raw = raw()
        if raw is None:
            raw = getattr(response, "content", None)
        if raw is None:
            text = getattr(response, "text", "")
            raw = text.encode("utf-8") if isinstance(text, str) else bytes(text)
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        headers = {str(k).lower(): str(v) for k, v in getattr(response, "headers", {}).items()}
        final_url = getattr(response, "final_url", None) or getattr(response, "url", None)
        return status, bytes(raw), headers, final_url

    @staticmethod
    def _looks_like_challenge(status: int, body: bytes, headers: Mapping[str, str]) -> bool:
        if status in (401, 403):
            return True
        content_type = headers.get("content-type", "").lower()
        sample = body[:4096].lower()
        return (
            "text/html" in content_type
            or b"captcha" in sample
            or b"waf" in sample
            or b"challenge" in sample
            or b"access denied" in sample
        )

    def _rate_limit(self) -> None:
        now = self.monotonic_fn()
        if self._last_request_at is not None:
            remaining = self.config.min_interval_seconds - (now - self._last_request_at)
            if remaining > 0:
                self.sleep_fn(remaining)
        self._last_request_at = self.monotonic_fn()

    def _fetch(
        self,
        stock_code: str,
        page: int,
        last_id: str | None,
        counters: RuntimeCounters,
    ) -> tuple[bytes, dict[str, str], str, str]:
        for attempt in range(1, self.config.max_attempts + 1):
            counters.requests_total += 1
            try:
                self._rate_limit()
                fetch_page = getattr(self.transport, "fetch_page", None)
                if callable(fetch_page):
                    response = fetch_page(
                        stock_code,
                        page=page,
                        last_id=last_id,
                        timeout=self.config.timeout_seconds,
                    )
                else:
                    # Deterministic offline seam only. Production uses
                    # XueqiuBrowserTransport.fetch_page so the browser, not
                    # this collector, owns session/challenge state.
                    query = {
                        "symbol": symbol_for(stock_code), "count": "10",
                        "comment": "0", "hl": "0", "source": "all",
                        "sort": "time", "page": str(page), "q": "", "type": "11",
                    }
                    if page > 1 and last_id is not None:
                        query["last_id"] = last_id
                    url = (
                        "https://xueqiu.com/query/v1/symbol/search/status.json?"
                        + urlencode(query)
                    )
                    response = self.transport.get(url, timeout=self.config.timeout_seconds)
                status, body, headers, final_url = self._body(response)
                if self._looks_like_challenge(status, body, headers):
                    raise XueqiuAccessFailure(
                        f"source access failure HTTP {status}" if status else "source challenge response"
                    )
                if status != 200:
                    raise XueqiuTransportFailure(f"source request returned HTTP {status}")
                counters.requests_success += 1
                return body, headers, redact_xueqiu_url(final_url) or "https://xueqiu.com/query/v1/symbol/search/status.json", "success"
            except XueqiuAccessFailure:
                counters.requests_failed += 1
                raise
            except (XueqiuTransportFailure, TimeoutError, OSError) as exc:
                counters.requests_failed += 1
                if attempt >= self.config.max_attempts:
                    if isinstance(exc, XueqiuTransportFailure):
                        raise
                    raise XueqiuTransportFailure("browser transport failed after retry budget") from exc
                delay = min(self.config.max_backoff_seconds, self.config.base_backoff_seconds * (2 ** (attempt - 1)))
                if delay > 0:
                    self.sleep_fn(delay)
            except Exception as exc:
                counters.requests_failed += 1
                raise XueqiuTransportFailure("browser transport raised an unexpected error") from exc
        raise XueqiuTransportFailure("browser transport failed")

    @staticmethod
    def _fact_equivalent(current: SourceItem, prior: SourceItem) -> bool:
        current_metadata = dict(current.source_metadata)
        prior_metadata = dict(prior.source_metadata)
        # SQLite adds this nullable compatibility field while persisting an
        # observation; it is not a source fact for Xueqiu drift detection.
        if current_metadata.get("source_post_id") is None:
            current_metadata.pop("source_post_id", None)
        if prior_metadata.get("source_post_id") is None:
            prior_metadata.pop("source_post_id", None)
        return (
            current.source == prior.source
            and current.source_item_id == prior.source_item_id
            and current.requested_bar_code == prior.requested_bar_code
            and current.canonical_bar_code == prior.canonical_bar_code
            and current.canonical_bar_name == prior.canonical_bar_name
            and current.author_id == prior.author_id
            and current.author_name == prior.author_name
            and current.title == prior.title
            and current.content == prior.content
            and current.published_at == prior.published_at
            and current.last_updated_at == prior.last_updated_at
            and current.display_time == prior.display_time
            and current.url == prior.url
            and current.post_type == prior.post_type
            and current.post_state == prior.post_state
            and current.post_top_status == prior.post_top_status
            and current.read_count == prior.read_count
            and current.reply_count == prior.reply_count
            and current.like_count == prior.like_count
            and current.forward_count == prior.forward_count
            and current.source_times_raw == prior.source_times_raw
            and current_metadata == prior_metadata
        )

    def collect(
        self,
        stock_code: str,
        *,
        existing_ids: set[str] | None = None,
        existing_observations: Mapping[str, SourceItem] | None = None,
        watermark: datetime | None = None,
        max_pages: int | None = None,
        bootstrap: bool | None = None,
    ) -> CollectionResult:
        symbol_for(stock_code)
        page_limit = max_pages if max_pages is not None else self.config.max_pages
        if page_limit < 1:
            raise ValueError("max_pages must be at least 1")
        bootstrap = watermark is None if bootstrap is None else bootstrap
        if bootstrap and watermark is not None:
            raise ValueError("bootstrap requires checkpoint watermark to be NULL")
        if bootstrap and page_limit < XUEQIU_BOOTSTRAP_MIN_PAGES:
            raise ValueError(
                f"bootstrap requires max_pages >= {XUEQIU_BOOTSTRAP_MIN_PAGES}"
            )

        counters = RuntimeCounters()
        items = []
        failures: list[str] = []
        seen_ids = set(existing_ids or ())
        run_ids: set[str] = set()
        last_id: str | None = None
        pages_succeeded = 0
        boundary_reached = False
        stop_reason: str | None = None
        frontier_values: list[datetime] = []

        for page_number in range(1, page_limit + 1):
            if self.cancel_check and self.cancel_check():
                stop_reason = "cancelled"
                break
            counters.pages_requested += 1
            try:
                raw_page, headers, final_url, _ = self._fetch(
                    stock_code, page_number, last_id, counters
                )
                page_ref = self.evidence_store.put("list", None, raw_page)
                parsed = parse_page(raw_page)
                if page_number > 1 and parsed.page != page_number:
                    raise XueqiuPaginationError(
                        f"response page {parsed.page} does not match requested page {page_number}"
                    )
                pages_succeeded += 1
                counters.pages_success += 1
                counters.records_received += len(parsed.items)
            except XueqiuAccessFailure as exc:
                counters.pages_failed += 1
                failures.append(f"page {page_number}: access_failure")
                stop_reason = "access_failure"
                break
            except XueqiuSchemaMismatch as exc:
                counters.pages_failed += 1
                counters.records_failed += 1
                failures.append(f"page {page_number}: schema_mismatch")
                stop_reason = "schema_mismatch"
                break
            except XueqiuPaginationError:
                counters.pages_failed += 1
                failures.append(f"page {page_number}: pagination_failure")
                stop_reason = "pagination_failure"
                break
            except (XueqiuTransportFailure, XueqiuParseError) as exc:
                counters.pages_failed += 1
                counters.records_failed += 1
                failures.append(f"page {page_number}: parse_failure")
                stop_reason = "page_failure"
                break

            page_ids: set[str] = set()
            page_items = []
            page_all_known_old = bool(parsed.items)
            repeated_uncommitted_id = False
            for row in parsed.items:
                try:
                    parsed_item_id = str(row.get("id")) if row.get("id") is not None else ""
                    item = parse_item(
                        row,
                        stock_code,
                        collected_at=self.clock(),
                        raw_ref={"list": page_ref},
                        final_url=final_url,
                    )
                except XueqiuParseError:
                    counters.records_failed += 1
                    failures.append(f"item {row.get('id', '<missing>')}: schema_mismatch")
                    stop_reason = "item_schema_failure"
                    break
                already_known = item.source_item_id in seen_ids
                prior_item = (existing_observations or {}).get(item.source_item_id)
                historical_known = already_known and watermark is not None and item.published_at <= watermark
                committed_historical = (
                    item.source_item_id in (existing_ids or set())
                    and watermark is not None
                    and item.published_at <= watermark
                )
                if not committed_historical:
                    page_all_known_old = False
                duplicate_in_run = item.source_item_id in run_ids or item.source_item_id in page_ids
                if duplicate_in_run:
                    counters.duplicate_records += 1
                    if not committed_historical:
                        repeated_uncommitted_id = True
                    continue
                page_ids.add(item.source_item_id)
                run_ids.add(item.source_item_id)
                if historical_known:
                    if prior_item is None or self._fact_equivalent(item, prior_item):
                        counters.duplicate_records += 1
                        continue
                    # The normal page scan actually observed a known
                    # historical item whose facts drifted.  Preserve it for
                    # the existing SQLite fingerprint/version contract.
                    counters.identity_content_drifts += 1
                    item = replace(item, observation_version=prior_item.observation_version + 1)
                seen_ids.add(item.source_item_id)
                if prior_item is not None:
                    item = replace(item, observation_version=prior_item.observation_version + 1)
                page_items.append(item)
                frontier_values.append(item.published_at)
                counters.records_parsed += 1

            if stop_reason == "item_schema_failure":
                break
            if repeated_uncommitted_id and not page_items:
                failures.append(f"page {page_number}: pagination_failure")
                stop_reason = "pagination_failure"
                break
            if bootstrap and page_number > 1 and not page_items:
                failures.append(f"page {page_number}: pagination_failure")
                stop_reason = "pagination_failure"
                break
            items.extend(page_items)
            if not parsed.items:
                if bootstrap:
                    stop_reason = "empty_bootstrap_page"
                    break
                if watermark is not None:
                    boundary_reached = True
                    stop_reason = "known_boundary_reached"
                    break
            if not parsed.items:
                break

            if not bootstrap and watermark is not None and page_all_known_old:
                boundary_reached = True
                stop_reason = "known_boundary_reached"
                break
            if page_number < page_limit:
                next_last_id = str(parsed.items[-1].get("id", ""))
                if not next_last_id or next_last_id == last_id:
                    failures.append(f"page {page_number}: pagination_failure")
                    stop_reason = "pagination_failure"
                    break
                last_id = next_last_id
            elif page_number == page_limit:
                stop_reason = "max_pages"

            if bootstrap and page_number == XUEQIU_BOOTSTRAP_MIN_PAGES:
                stop_reason = "bootstrap_complete"
                break

        if stop_reason == "cancelled":
            return CollectionResult(CollectionStatus.CANCELLED, items, counters, failures, stop_reason, watermark)
        if bootstrap:
            if pages_succeeded < XUEQIU_BOOTSTRAP_MIN_PAGES or counters.records_failed:
                return CollectionResult(
                    CollectionStatus.COLLECTION_FAILED if pages_succeeded == 0 else CollectionStatus.PARTIAL_COLLECTION,
                    items, counters, failures, stop_reason, watermark, None,
                )
            if not frontier_values:
                return CollectionResult(CollectionStatus.PARTIAL_COLLECTION, items, counters, failures, "empty_bootstrap", watermark, None)
            frontier = max(frontier_values)
            return CollectionResult(
                CollectionStatus.SUCCESS,
                items,
                counters,
                failures,
                "bootstrap_complete",
                frontier,
                frontier,
            )

        if counters.records_failed or failures and stop_reason in {
            "access_failure", "schema_mismatch", "page_failure", "pagination_failure", "item_schema_failure"
        }:
            status = CollectionStatus.PARTIAL_COLLECTION if pages_succeeded else CollectionStatus.COLLECTION_FAILED
            return CollectionResult(status, items, counters, failures, stop_reason, None, None)
        if boundary_reached:
            status = CollectionStatus.NO_NEW_DATA if not items else CollectionStatus.SUCCESS
            # A completed incremental boundary proves the whole accepted
            # prefix, including new items collected before the boundary.
            # Partial collection never reaches this branch and therefore
            # cannot advance the committed checkpoint.
            frontier = max([watermark, *frontier_values], default=watermark)
            return CollectionResult(status, items, counters, failures, stop_reason, frontier, frontier)
        if stop_reason == "max_pages":
            return CollectionResult(CollectionStatus.PARTIAL_COLLECTION, items, counters, failures, stop_reason, None, None)
        return CollectionResult(CollectionStatus.SUCCESS if items else CollectionStatus.NO_NEW_DATA, items, counters, failures, stop_reason, watermark, None)
