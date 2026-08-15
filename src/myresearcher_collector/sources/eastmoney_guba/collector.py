"""Isolated Eastmoney Guba acquisition behavior."""

from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from myresearcher_collector.models import (
    CollectionResult,
    CollectionStatus,
    GubaSourceItem,
    RuntimeCounters,
)

from .parser import (
    GubaDetailMismatch,
    GubaParseError,
    GubaSchemaMismatch,
    SCHEMA_VERSION,
    SOURCE,
    merge_list_and_detail,
    parse_detail_page,
    parse_list_page,
    is_access_block_page,
)
from .acquisition import BROWSER_DOM_SNAPSHOT, HTTP_RESPONSE
from myresearcher_collector.page_anchor import PageProbe


class Transport(Protocol):
    def get(self, url: str, *, timeout: float) -> Any:
        """Return truthful HTTP-response or DOM acquired-document evidence."""


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes
    headers: dict[str, str]
    final_url: str | None = None

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    @property
    def capture_method(self) -> str:
        return HTTP_RESPONSE


_ALLOWED_SOURCE_HOSTS = {"guba.eastmoney.com", "caifuhao.eastmoney.com"}
BOOTSTRAP_MIN_PAGES = 3


class RedirectPolicyError(RuntimeError):
    """A redirect leaves the approved HTTPS source boundary."""


def _validate_source_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_SOURCE_HOSTS:
        raise RedirectPolicyError("redirect outside approved HTTPS source hosts")
    if parsed.username or parsed.password:
        raise RedirectPolicyError("redirect URL contains userinfo")


class _SourceRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Request | None:
        _validate_source_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UrllibTransport:
    """Small production transport; tests inject a local fake instead."""

    def __init__(self, user_agent: str = "MyResearcher-DataCollector/eastmoney_guba") -> None:
        self.user_agent = user_agent
        self._opener = build_opener(_SourceRedirectHandler())

    def get(self, url: str, *, timeout: float) -> HttpResponse:
        request = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml",
            },
            method="GET",
        )
        try:
            _validate_source_url(url)
            with self._opener.open(request, timeout=timeout) as response:
                return HttpResponse(
                    status_code=int(response.status),
                    body=response.read(),
                    headers={key.lower(): value for key, value in response.headers.items()},
                    final_url=response.geturl(),
                )
        except HTTPError as exc:
            return HttpResponse(
                status_code=int(exc.code),
                body=exc.read(),
                headers={key.lower(): value for key, value in exc.headers.items()},
                final_url=exc.geturl(),
            )


class RawEvidenceStore(Protocol):
    def put(self, kind: str, source_item_id: str | None, payload: bytes) -> str:
        """Store immutable parser-consumed evidence and return an opaque reference."""


class InMemoryRawEvidenceStore:
    """Developer/test-only evidence store; no persistence decision is implied."""

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
    max_pages: int = BOOTSTRAP_MIN_PAGES
    max_attempts: int = 3
    access_block_attempts: int = 2
    base_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 30.0
    max_retry_after_seconds: float = 60.0
    min_interval_seconds: float = 3.0
    max_interval_seconds: float = 10.0
    randomize_pacing: bool = False


@dataclass(frozen=True)
class BackfillCollectionResult:
    """Backfill traversal statistics plus the shared runtime result."""

    result: CollectionResult
    pages_scanned: int
    records_received: int
    records_in_range: int
    records_failed: int
    earliest_observed_at: datetime | None
    latest_observed_at: datetime | None
    range_complete: bool


class FetchFailure(RuntimeError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class EastmoneyGubaCollector:
    """Collect standard Guba posts without cleaning or semantic filtering."""

    def __init__(
        self,
        transport: Transport | None = None,
        *,
        evidence_store: RawEvidenceStore | None = None,
        config: CollectorConfig | None = None,
        sleep_fn: Any = time.sleep,
        clock: Any | None = None,
        monotonic_fn: Callable[[], float] = time.monotonic,
        jitter_fn: Callable[[float, float], float] = random.uniform,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        if transport is None:
            raise RuntimeError(
                "Eastmoney transport must be supplied explicitly; no plain-HTTP fallback"
            )
        self.transport = transport
        self.evidence_store = evidence_store or InMemoryRawEvidenceStore()
        self.config = config or CollectorConfig()
        self.sleep_fn = sleep_fn
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.monotonic_fn = monotonic_fn
        self.jitter_fn = jitter_fn
        self.cancel_check = cancel_check
        self._last_request_at: float | None = None
        if self.config.min_interval_seconds < 2.5:
            raise ValueError("min_interval_seconds must be at least 2.5")

    @staticmethod
    def list_url(stock_code: str, page: int) -> str:
        return (
            f"https://guba.eastmoney.com/list,{stock_code},f.html"
            if page == 1
            else f"https://guba.eastmoney.com/list,{stock_code},f_{page}.html"
        )

    def detail_url(self, url: str) -> str:
        return url

    @staticmethod
    def _body(response: Any) -> tuple[int | None, str, bytes, dict[str, str], str | None, str]:
        raw_status = getattr(response, "status_code", getattr(response, "status", None))
        status = None if raw_status is None else int(raw_status)
        raw = getattr(response, "body", None)
        if raw is None:
            raw = getattr(response, "content", None)
        if raw is None:
            text = getattr(response, "text", "")
            raw = text.encode("utf-8") if isinstance(text, str) else bytes(text)
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        body = bytes(raw)
        text = body.decode("utf-8", errors="replace")
        headers = {str(k).lower(): str(v) for k, v in getattr(response, "headers", {}).items()}
        final_url = getattr(response, "final_url", None) or getattr(response, "url", None)
        capture_method = str(getattr(response, "capture_method", HTTP_RESPONSE))
        if capture_method not in {HTTP_RESPONSE, BROWSER_DOM_SNAPSHOT}:
            raise ValueError("unsupported acquisition capture method")
        return status, text, body, headers, final_url, capture_method

    def _fetch(self, url: str, counters: RuntimeCounters) -> tuple[str, bytes, dict[str, str], str]:
        attempts = self.config.max_attempts
        for attempt in range(1, self.config.max_attempts + 1):
            counters.requests_total += 1
            try:
                self._rate_limit()
                response = self.transport.get(url, timeout=self.config.timeout_seconds)
                status, text, body, headers, final_url, capture_method = self._body(response)
                _validate_source_url(final_url or url)
            except RedirectPolicyError as exc:
                counters.requests_failed += 1
                raise FetchFailure("redirect", str(exc)) from exc
            except URLError as exc:
                if isinstance(getattr(exc, "reason", None), RedirectPolicyError):
                    counters.requests_failed += 1
                    raise FetchFailure("redirect", str(exc.reason)) from exc
                counters.requests_failed += 1
                if attempt >= self.config.max_attempts:
                    raise FetchFailure("transport", "request failed after retry budget") from exc
                self._backoff(attempt)
                continue
            except (TimeoutError, OSError, URLError) as exc:
                counters.requests_failed += 1
                failure_kind = str(getattr(exc, "kind", "transport"))
                if failure_kind == "invalid_document":
                    raise FetchFailure(failure_kind, str(exc)) from exc
                if attempt >= self.config.max_attempts:
                    raise FetchFailure(
                        failure_kind, "request failed after retry budget"
                    ) from exc
                self._backoff(attempt)
                continue
            except Exception as exc:
                counters.requests_failed += 1
                raise FetchFailure("transport", "request raised an unexpected error") from exc

            acquisition_succeeded = (
                status == 200
                if capture_method == HTTP_RESPONSE
                else status is None and capture_method == BROWSER_DOM_SNAPSHOT
            )
            if acquisition_succeeded:
                if is_access_block_page(text):
                    counters.requests_failed += 1
                    attempts = self.config.access_block_attempts
                    if attempt >= min(self.config.max_attempts, attempts):
                        raise FetchFailure(
                            "access_block",
                            "source returned an identity-verification page",
                        )
                    self._backoff(attempt, headers)
                    continue
                counters.requests_success += 1
                return text, body, headers, final_url or url

            counters.requests_failed += 1
            if status is None:
                raise FetchFailure(
                    "invalid_document",
                    "acquisition did not provide valid success semantics",
                )
            retryable = status == 429 or status >= 500 or status == 403
            if status == 403:
                attempts = self.config.access_block_attempts
            if not retryable or attempt >= min(self.config.max_attempts, attempts):
                kind = "access_block" if status in (403, 429) else f"http_{status}"
                raise FetchFailure(kind, f"source request returned HTTP {status}")
            self._backoff(attempt, headers)
        raise FetchFailure("transport", "request failed")

    def probe_list_page(self, stock_code: str, page_no: int) -> PageProbe:
        """Acquire and parse one list page for bounded historical seeking."""
        counters = RuntimeCounters()
        html, _raw, _headers, _final_url = self._fetch(
            self.list_url(stock_code, page_no), counters
        )
        parsed = parse_list_page(html, stock_code)
        if not parsed.rows:
            raise FetchFailure("empty_page", "time-seek probe returned no rows")
        times = [row.published_at for row in parsed.rows]
        return PageProbe(
            page_no=page_no, page_min_time=min(times), page_max_time=max(times),
            source_count=parsed.source_count,
            page_size=len(parsed.rows) + len(parsed.out_of_scope_rows),
        )

    def _rate_limit(self) -> None:
        now = self.monotonic_fn()
        if self._last_request_at is not None:
            interval = self.config.min_interval_seconds
            if self.config.randomize_pacing:
                interval = self.jitter_fn(max(3.0, interval), self.config.max_interval_seconds)
            remaining = interval - (now - self._last_request_at)
            if remaining > 0:
                self.sleep_fn(remaining)
        self._last_request_at = self.monotonic_fn()

    def _backoff(self, attempt: int, headers: Mapping[str, str] | None = None) -> None:
        exponential = min(
            self.config.max_backoff_seconds,
            self.config.base_backoff_seconds * (2 ** (attempt - 1)),
        )
        jitter = 0.0
        if exponential > 0:
            jitter = self.jitter_fn(0.0, min(exponential * 0.25, self.config.max_backoff_seconds - exponential))
        delay = min(self.config.max_backoff_seconds, exponential + jitter)
        retry_after = self._retry_after(headers or {})
        if retry_after is not None:
            delay = max(delay, retry_after)
        if delay > 0:
            self.sleep_fn(delay)

    def _retry_after(self, headers: Mapping[str, str]) -> float | None:
        value = headers.get("retry-after")
        if value is None:
            return None
        try:
            parsed = float(value.strip())
        except (AttributeError, ValueError):
            return None
        if parsed < 0 or parsed > self.config.max_retry_after_seconds:
            return None
        return parsed

    @staticmethod
    def _row_fingerprint(row: Any) -> tuple[Any, ...]:
        return (
            row.source_item_id,
            row.requested_bar_code,
            row.canonical_bar_code,
            row.author_id,
            row.author_name,
            row.title,
            row.published_at,
            row.last_updated_at,
            row.display_time,
            row.url,
            row.post_type,
            row.post_state,
            row.post_top_status,
            row.read_count,
            row.reply_count,
            row.like_count,
            row.forward_count,
            row.source_post_id,
            row.source_times_raw,
            row.source_metadata,
        )

    @staticmethod
    def _row_matches_item(row: Any, item: GubaSourceItem) -> bool:
        return (
            row.source_item_id == item.source_item_id
            and row.requested_bar_code == item.requested_bar_code
            and row.canonical_bar_code == item.canonical_bar_code
            and row.author_id == item.author_id
            and row.author_name == item.author_name
            and row.title == item.title
            and row.published_at == item.published_at
            and row.last_updated_at == item.last_updated_at
            and row.display_time == item.display_time
            and row.url == item.url
            and row.post_type == item.post_type
            and row.post_state == item.post_state
            and row.post_top_status == item.post_top_status
            and row.read_count == item.read_count
            and row.reply_count == item.reply_count
            and row.like_count == item.like_count
            and row.forward_count == item.forward_count
            and row.source_post_id == item.source_post_id
            and row.source_times_raw == item.source_times_raw
        )

    @staticmethod
    def _merged_fingerprint(merged: Mapping[str, Any]) -> tuple[Any, ...]:
        return tuple(
            (name, merged.get(name))
            for name in (
                "source_item_id", "canonical_bar_code", "canonical_bar_name",
                "author_id", "author_name", "title", "content", "published_at",
                "last_updated_at", "display_time", "post_type", "post_state",
                "post_top_status", "read_count", "reply_count", "like_count",
                "forward_count", "source_post_id", "source_times_raw",
                "source_metadata",
            )
        )

    @staticmethod
    def _item_detail_fingerprint(item: GubaSourceItem) -> tuple[Any, ...]:
        metadata = dict(item.source_metadata)
        metadata.pop("final_urls", None)
        return tuple(
            (name, metadata if name == "source_metadata" else getattr(item, name))
            for name in (
                "source_item_id", "canonical_bar_code", "canonical_bar_name",
                "author_id", "author_name", "title", "content", "published_at",
                "last_updated_at", "display_time", "post_type", "post_state",
                "post_top_status", "read_count", "reply_count", "like_count",
                "forward_count", "source_post_id", "source_times_raw",
                "source_metadata",
            )
        )

    def collect(
        self,
        stock_code: str,
        *,
        existing_ids: set[str] | None = None,
        existing_observations: Mapping[str, GubaSourceItem] | None = None,
        watermark: datetime | None = None,
        max_pages: int | None = None,
        bootstrap: bool | None = None,
    ) -> CollectionResult:
        if not isinstance(stock_code, str) or len(stock_code) != 6 or not stock_code.isdigit():
            raise ValueError("stock_code must be six decimal digits")
        page_limit = max_pages if max_pages is not None else self.config.max_pages
        if page_limit < 1:
            raise ValueError("max_pages must be at least 1")
        bootstrap = watermark is None if bootstrap is None else bootstrap
        if bootstrap and watermark is not None:
            raise ValueError("bootstrap requires checkpoint watermark to be NULL")
        if bootstrap and page_limit < BOOTSTRAP_MIN_PAGES:
            raise ValueError(
                f"bootstrap requires max_pages >= {BOOTSTRAP_MIN_PAGES}"
            )
        traversal_limit = BOOTSTRAP_MIN_PAGES if bootstrap else page_limit

        counters = RuntimeCounters()
        items: list[GubaSourceItem] = []
        failures: list[str] = []
        seen_ids = set(existing_ids or ()) | set(existing_observations or ())
        observed_rows: dict[str, Any] = {}
        observed_detail_fingerprints: dict[str, tuple[Any, ...]] = {}
        observation_versions = {
            source_item_id: item.observation_version
            for source_item_id, item in (existing_observations or {}).items()
        }
        boundary_pages = 0
        stop_reason: str | None = None
        had_successful_page = False
        any_page_failure = False
        completed_page_frontier: datetime | None = None
        bootstrap_frontier: datetime | None = None
        safe_prefix_open = True
        safe_frontier_valid = True

        for page_number in range(1, traversal_limit + 1):
            if self.cancel_check and self.cancel_check():
                stop_reason = "cancelled"
                break
            counters.pages_requested += 1
            page_url = self.list_url(stock_code, page_number)
            try:
                html, raw_page, _, page_final_url = self._fetch(page_url, counters)
            except FetchFailure as exc:
                counters.pages_failed += 1
                any_page_failure = True
                failures.append(f"page {page_number}: {exc.kind}")
                if not had_successful_page:
                    return CollectionResult(CollectionStatus.COLLECTION_FAILED, items, counters, failures, "page_failure", watermark)
                stop_reason = "page_failure"
                break

            page_ref = self.evidence_store.put("list", None, raw_page)
            try:
                parsed_page = parse_list_page(html, stock_code)
            except GubaSchemaMismatch as exc:
                counters.pages_failed += 1
                failures.append(f"page {page_number}: schema_mismatch")
                return CollectionResult(CollectionStatus.SPEC_MISMATCH, items, counters, failures, "schema_mismatch", watermark)
            except GubaParseError as exc:
                counters.pages_failed += 1
                counters.records_failed += 1
                failures.append(f"page {page_number}: parse_failure")
                if not had_successful_page:
                    return CollectionResult(CollectionStatus.COLLECTION_FAILED, items, counters, failures, "parse_failure", watermark)
                return CollectionResult(CollectionStatus.PARTIAL_COLLECTION, items, counters, failures, "parse_failure", watermark)

            had_successful_page = True
            counters.pages_success += 1
            counters.records_received += len(parsed_page.rows) + len(parsed_page.out_of_scope_rows)
            counters.records_out_of_scope += len(parsed_page.out_of_scope_rows)
            if (
                not bootstrap
                and not parsed_page.rows
                and not parsed_page.out_of_scope_rows
            ):
                stop_reason = "empty_page"
                break

            page_all_old_or_seen = bool(parsed_page.rows)
            page_has_new = False
            page_detail_failure = False
            page_items: list[GubaSourceItem] = []
            for row in parsed_page.rows:
                if self.cancel_check and self.cancel_check():
                    stop_reason = "cancelled"
                    break
                prior_row = observed_rows.get(row.source_item_id)
                prior_item = (existing_observations or {}).get(row.source_item_id)
                list_drift = (
                    prior_row is not None and self._row_fingerprint(prior_row) != self._row_fingerprint(row)
                ) or (
                    prior_row is None
                    and prior_item is not None
                    and not self._row_matches_item(row, prior_item)
                )
                already_seen = row.source_item_id in seen_ids
                if already_seen:
                    counters.duplicate_records += 1
                eligible_new = watermark is None or row.published_at > watermark
                historical_known = already_seen and watermark is not None and not eligible_new
                if historical_known:
                    seen_ids.add(row.source_item_id)
                    observed_rows[row.source_item_id] = row
                    continue
                # The watermark may suppress only already-known historical IDs.
                # An unknown ID remains eligible even when its publication time
                # is at or before the committed watermark.
                if eligible_new or not already_seen:
                    page_all_old_or_seen = False
                    page_has_new = True
                seen_ids.add(row.source_item_id)
                observed_rows[row.source_item_id] = row
                counters.details_requested += 1
                try:
                    detail_html, raw_detail, _, detail_final_url = self._fetch(self.detail_url(row.url), counters)
                    detail_ref = self.evidence_store.put("detail", row.source_item_id, raw_detail)
                    detail = parse_detail_page(detail_html)
                    merged = merge_list_and_detail(row, detail)
                except GubaSchemaMismatch:
                    counters.details_failed += 1
                    counters.records_failed += 1
                    page_detail_failure = True
                    safe_prefix_open = False
                    if completed_page_frontier is None or row.published_at <= completed_page_frontier:
                        completed_page_frontier = None
                        safe_frontier_valid = False
                    failures.append(f"detail {row.source_item_id}: schema_mismatch")
                    return CollectionResult(CollectionStatus.SPEC_MISMATCH, items, counters, failures, "detail_schema_mismatch", watermark)
                except (GubaDetailMismatch, GubaParseError, FetchFailure) as exc:
                    counters.details_failed += 1
                    counters.records_failed += 1
                    page_detail_failure = True
                    safe_prefix_open = False
                    if completed_page_frontier is None or row.published_at <= completed_page_frontier:
                        completed_page_frontier = None
                        safe_frontier_valid = False
                    failures.append(f"detail {row.source_item_id}: {self._failure_name(exc)}")
                    continue

                if bootstrap and (
                    bootstrap_frontier is None
                    or merged["published_at"] > bootstrap_frontier
                ):
                    bootstrap_frontier = merged["published_at"]

                detail_fingerprint = self._merged_fingerprint(merged)
                prior_detail_fingerprint = observed_detail_fingerprints.get(row.source_item_id)
                if prior_detail_fingerprint is None and prior_item is not None:
                    prior_detail_fingerprint = self._item_detail_fingerprint(prior_item)
                detail_drift = (
                    prior_detail_fingerprint is not None
                    and detail_fingerprint != prior_detail_fingerprint
                )
                drift = list_drift or detail_drift
                observed_detail_fingerprints[row.source_item_id] = detail_fingerprint
                counters.details_success += 1
                first_watermark_eligible_observation = (
                    watermark is not None and eligible_new and prior_row is None
                )
                if already_seen and not drift and not first_watermark_eligible_observation:
                    continue
                if drift:
                    counters.identity_content_drifts += 1
                collected_at = self.clock()
                if collected_at.tzinfo is None:
                    collected_at = collected_at.replace(tzinfo=timezone.utc)
                source_metadata = dict(merged["source_metadata"])
                source_metadata["final_urls"] = {"list": page_final_url, "detail": detail_final_url}
                observation_version = observation_versions.get(row.source_item_id, 0) + 1
                observation_versions[row.source_item_id] = observation_version
                item = GubaSourceItem(
                    source=SOURCE,
                    schema_version=SCHEMA_VERSION,
                    source_item_id=merged["source_item_id"],
                    requested_bar_code=merged["requested_bar_code"],
                    canonical_bar_code=merged["canonical_bar_code"],
                    canonical_bar_name=merged["canonical_bar_name"],
                    author_id=merged["author_id"],
                    author_name=merged["author_name"],
                    title=merged["title"],
                    content=merged["content"],
                    published_at=merged["published_at"],
                    last_updated_at=merged["last_updated_at"],
                    display_time=merged["display_time"],
                    url=merged["url"],
                    post_type=merged["post_type"],
                    post_state=merged["post_state"],
                    post_top_status=merged["post_top_status"],
                    read_count=merged["read_count"],
                    reply_count=merged["reply_count"],
                    like_count=merged["like_count"],
                    forward_count=merged["forward_count"],
                    source_post_id=merged["source_post_id"],
                    collected_at=collected_at,
                    source_times_raw=merged["source_times_raw"],
                    source_metadata=source_metadata,
                    raw_ref={"list": page_ref, "detail": detail_ref},
                    observation_version=observation_version,
                    final_url=detail_final_url,
                )
                items.append(item)
                page_items.append(item)
                counters.records_parsed += 1

            if stop_reason == "cancelled":
                break

            # A completed page is a runtime-owned safe prefix candidate. A
            # later detail failure may leave that earlier prefix safe, while a
            # failed page or coverage cap is never silently promoted here.
            if safe_prefix_open and not page_detail_failure and page_items:
                completed_page_frontier = max(
                    item.published_at for item in page_items
                )

            if not bootstrap and watermark is not None and page_all_old_or_seen and not page_has_new:
                boundary_pages += 1
                if boundary_pages >= 2:
                    stop_reason = "watermark_confirmed"
                    break
            elif not bootstrap and watermark is not None:
                boundary_pages = 0

            if bootstrap and page_number == traversal_limit:
                stop_reason = "bootstrap_complete"
            elif page_number == page_limit:
                stop_reason = "max_pages"

        if stop_reason == "cancelled":
            return CollectionResult(CollectionStatus.CANCELLED, items, counters, failures, stop_reason, watermark)
        if not had_successful_page:
            return CollectionResult(CollectionStatus.COLLECTION_FAILED, items, counters, failures, stop_reason, watermark)
        if bootstrap:
            if counters.pages_success != BOOTSTRAP_MIN_PAGES:
                status = CollectionStatus.PARTIAL_COLLECTION
                return CollectionResult(
                    status,
                    items,
                    counters,
                    failures,
                    stop_reason,
                    watermark,
                    None,
                )
            if counters.records_failed or counters.details_failed:
                return CollectionResult(
                    CollectionStatus.PARTIAL_COLLECTION,
                    items,
                    counters,
                    failures,
                    stop_reason,
                    watermark,
                    None,
                )
            return CollectionResult(
                CollectionStatus.SUCCESS,
                items,
                counters,
                failures,
                "bootstrap_complete",
                bootstrap_frontier,
                bootstrap_frontier,
            )
        if any_page_failure or counters.records_failed or counters.details_failed:
            status = CollectionStatus.PARTIAL_COLLECTION
        elif stop_reason == "watermark_confirmed":
            status = CollectionStatus.SUCCESS if items else CollectionStatus.NO_NEW_DATA
        elif stop_reason == "empty_page":
            status = CollectionStatus.SUCCESS if items else CollectionStatus.NO_NEW_DATA
        elif stop_reason == "max_pages":
            status = CollectionStatus.PARTIAL_COLLECTION
        else:
            status = CollectionStatus.SUCCESS if items else CollectionStatus.NO_NEW_DATA
        resolved_watermarks = [item.published_at for item in items]
        if watermark is not None:
            resolved_watermarks.append(watermark)
        new_watermark = (
            max(resolved_watermarks, default=None)
            if status in (CollectionStatus.SUCCESS, CollectionStatus.NO_NEW_DATA)
            else watermark
        )
        safe_frontier = None
        if status is CollectionStatus.SUCCESS:
            safe_frontier = max(
                (
                    value
                    for value in (watermark, completed_page_frontier)
                    if value is not None
                ),
                default=None,
            )
        elif status is CollectionStatus.NO_NEW_DATA:
            # A confirmed incremental boundary proves the prior committed
            # frontier even when no detail was re-fetched in this run.
            safe_frontier = completed_page_frontier or (
                watermark if stop_reason == "watermark_confirmed" else None
            )
        elif (
            status is CollectionStatus.PARTIAL_COLLECTION
            and not any_page_failure
            and counters.details_failed > 0
            and safe_frontier_valid
        ):
            safe_frontier = completed_page_frontier
        return CollectionResult(status, items, counters, failures, stop_reason, new_watermark, safe_frontier)

    def collect_backfill(
        self,
        stock_code: str,
        *,
        from_time: datetime,
        to_time: datetime,
        max_pages: int | None = None,
        include_details: bool = True,
        start_page: int = 1,
        coverage_stop: Callable[[datetime, datetime], bool] | None = None,
        page_anchor_callback: Callable[[int, datetime, datetime, int | None, int], None] | None = None,
        page_success_callback: Callable[
            [int, list[GubaSourceItem], datetime | None, datetime | None, int | None, int],
            None,
        ] | None = None,
    ) -> BackfillCollectionResult:
        """Traverse newest-to-oldest pages for an inclusive historical range.

        This deliberately does not consult known IDs or a checkpoint.  The
        returned shared result never declares a safe frontier, so callers can
        reuse the normal persistence tables without advancing forward state.

        ``coverage_stop`` optionally receives one successfully parsed page's
        (page_min, page_max) and returns True when the page lies entirely
        inside previously completed coverage and the remaining requested
        range below it is fully covered; the traversal then stops with
        ``existing_coverage_reached`` instead of re-acquiring covered pages.
        """
        if not isinstance(stock_code, str) or len(stock_code) != 6 or not stock_code.isdigit():
            raise ValueError("stock_code must be six decimal digits")
        if from_time.tzinfo is None or to_time.tzinfo is None:
            raise ValueError("backfill range requires timezone-aware datetimes")
        if from_time > to_time:
            raise ValueError("backfill from_time must be at or before to_time")
        page_limit = max_pages
        if page_limit is not None and page_limit < 1:
            raise ValueError("max_pages must be at least 1")
        if start_page < 1 or (page_limit is not None and start_page > page_limit + 1):
            raise ValueError("start_page is outside the configured page range")

        counters = RuntimeCounters()
        items: list[GubaSourceItem] = []
        failures: list[str] = []
        earliest: datetime | None = None
        latest: datetime | None = None
        records_in_range = 0
        range_complete = False
        stop_reason: str | None = None
        previous_page_signature: tuple[str, ...] | None = None
        run_seen_ids: set[str] = set()

        def diagnostic(url: str, previous: tuple[str, ...] | None = None) -> None:
            capture = getattr(self.transport, "diagnostic_snapshot", None)
            if not callable(capture):
                return
            try:
                previous_hash = hashlib.sha256("|".join(previous or ()).encode()).hexdigest() if previous else None
                row = capture(url, previous_ids_hash=previous_hash)
                out = Path("runtime/diagnostics")
                out.mkdir(parents=True, exist_ok=True)
                with (out / "eastmoney-navigation.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            except Exception:
                pass

        def build_list_only_item(row: Any, list_ref: str, list_final_url: str) -> GubaSourceItem:
            collected_at = self.clock()
            if collected_at.tzinfo is None:
                collected_at = collected_at.replace(tzinfo=timezone.utc)
            metadata = dict(row.source_metadata)
            metadata["final_urls"] = {"list": list_final_url}
            metadata["content_source"] = "list_title"
            return GubaSourceItem(
                source=SOURCE, schema_version=SCHEMA_VERSION,
                source_item_id=row.source_item_id,
                requested_bar_code=row.requested_bar_code,
                canonical_bar_code=row.canonical_bar_code,
                canonical_bar_name=row.canonical_bar_name,
                author_id=row.author_id, author_name=row.author_name,
                title=row.title, content=row.title or "",
                published_at=row.published_at,
                last_updated_at=row.last_updated_at,
                display_time=row.display_time, url=row.url,
                post_type=row.post_type, post_state=row.post_state,
                post_top_status=row.post_top_status,
                read_count=row.read_count, reply_count=row.reply_count,
                like_count=row.like_count, forward_count=row.forward_count,
                source_post_id=row.source_post_id, collected_at=collected_at,
                source_times_raw=row.source_times_raw, source_metadata=metadata,
                raw_ref={"list": list_ref}, observation_version=1,
                final_url=list_final_url,
            )

        def build_detail_item(merged: Mapping[str, Any], list_ref: str, detail_ref: str,
                              list_final_url: str, detail_final_url: str) -> GubaSourceItem:
            collected_at = self.clock()
            if collected_at.tzinfo is None:
                collected_at = collected_at.replace(tzinfo=timezone.utc)
            metadata = dict(merged["source_metadata"])
            metadata["final_urls"] = {"list": list_final_url, "detail": detail_final_url}
            return GubaSourceItem(
                source=SOURCE, schema_version=SCHEMA_VERSION,
                source_item_id=merged["source_item_id"], requested_bar_code=merged["requested_bar_code"],
                canonical_bar_code=merged["canonical_bar_code"], canonical_bar_name=merged["canonical_bar_name"],
                author_id=merged["author_id"], author_name=merged["author_name"], title=merged["title"],
                content=merged["content"], published_at=merged["published_at"],
                last_updated_at=merged["last_updated_at"], display_time=merged["display_time"], url=merged["url"],
                post_type=merged["post_type"], post_state=merged["post_state"], post_top_status=merged["post_top_status"],
                read_count=merged["read_count"], reply_count=merged["reply_count"],
                like_count=merged["like_count"], forward_count=merged["forward_count"],
                source_post_id=merged["source_post_id"], collected_at=collected_at,
                source_times_raw=merged["source_times_raw"], source_metadata=metadata,
                raw_ref={"list": list_ref, "detail": detail_ref}, observation_version=1,
                final_url=detail_final_url,
            )

        page_numbers = (
            range(start_page, page_limit + 1)
            if page_limit is not None
            else iter(int(n) for n in __import__("itertools").count(start_page))
        )
        for page_number in page_numbers:
            if self.cancel_check and self.cancel_check():
                stop_reason = "cancelled"
                break
            counters.pages_requested += 1
            page_url = self.list_url(stock_code, page_number)
            try:
                html, raw_page, _, page_final_url = self._fetch(page_url, counters)
            except FetchFailure as exc:
                diagnostic(page_url, previous_page_signature)
                counters.pages_failed += 1
                failures.append(f"page {page_number}: {exc.kind}")
                stop_reason = "pagination_failure" if counters.pages_success else "source_failure"
                break
            page_ref = self.evidence_store.put("list", None, raw_page)
            try:
                parsed_page = parse_list_page(html, stock_code)
            except GubaSchemaMismatch:
                counters.pages_failed += 1
                counters.records_failed += 1
                failures.append(f"page {page_number}: schema_mismatch")
                stop_reason = "schema_mismatch"
                break
            except GubaParseError:
                counters.pages_failed += 1
                counters.records_failed += 1
                failures.append(f"page {page_number}: parse_failure")
                stop_reason = "source_failure"
                break

            signature = tuple(row.source_item_id for row in parsed_page.rows)
            if signature and signature == previous_page_signature:
                diagnostic(page_url, previous_page_signature)
                failures.append(f"page {page_number}: pagination_not_progressing")
                stop_reason = "pagination_failure"
                break
            previous_page_signature = signature
            counters.pages_success += 1
            counters.records_received += len(parsed_page.rows) + len(parsed_page.out_of_scope_rows)
            counters.records_out_of_scope += len(parsed_page.out_of_scope_rows)
            page_times = [row.published_at for row in parsed_page.rows]
            if page_times:
                page_min, page_max = min(page_times), max(page_times)
                earliest = page_min if earliest is None else min(earliest, page_min)
                latest = page_max if latest is None else max(latest, page_max)
                if page_anchor_callback is not None:
                    page_anchor_callback(
                        page_number, page_min, page_max, parsed_page.source_count,
                        len(parsed_page.rows) + len(parsed_page.out_of_scope_rows),
                    )

            page_detail_failure = False
            page_items: list[GubaSourceItem] = []
            for row in parsed_page.rows:
                if row.published_at > to_time or row.published_at < from_time:
                    continue
                if row.source_item_id in run_seen_ids:
                    continue
                records_in_range += 1
                if not include_details:
                    item = build_list_only_item(row, page_ref, page_final_url)
                    items.append(item)
                    page_items.append(item)
                    run_seen_ids.add(row.source_item_id)
                    counters.records_parsed += 1
                    continue
                counters.details_requested += 1
                try:
                    detail_html, raw_detail, _, detail_final_url = self._fetch(self.detail_url(row.url), counters)
                    detail_ref = self.evidence_store.put("detail", row.source_item_id, raw_detail)
                    detail = parse_detail_page(detail_html)
                    merged = merge_list_and_detail(row, detail)
                except GubaSchemaMismatch:
                    counters.details_failed += 1
                    counters.records_failed += 1
                    page_detail_failure = True
                    failures.append(f"detail {row.source_item_id}: schema_mismatch")
                    result = CollectionResult(CollectionStatus.SPEC_MISMATCH, items, counters, failures, "detail_schema_mismatch", None, None)
                    return BackfillCollectionResult(result=result, pages_scanned=counters.pages_success, records_received=counters.records_received, records_in_range=records_in_range, records_failed=counters.records_failed, earliest_observed_at=earliest, latest_observed_at=latest, range_complete=False)
                except (GubaDetailMismatch, GubaParseError, FetchFailure) as exc:
                    counters.details_failed += 1
                    counters.records_failed += 1
                    page_detail_failure = True
                    failures.append(f"detail {row.source_item_id}: {self._failure_name(exc)}")
                    continue
                item = build_detail_item(
                    merged, page_ref, detail_ref, page_final_url, detail_final_url
                )
                items.append(item)
                page_items.append(item)
                run_seen_ids.add(row.source_item_id)
                counters.details_success += 1
                counters.records_parsed += 1

            if page_success_callback is not None:
                page_success_callback(
                    page_number,
                    page_items,
                    page_min if page_times else None,
                    page_max if page_times else None,
                    parsed_page.source_count,
                    len(parsed_page.rows) + len(parsed_page.out_of_scope_rows),
                )

            if page_times and max(page_times) < from_time:
                range_complete = True
                stop_reason = "backfill_range_complete"
                break
            if not parsed_page.rows and not parsed_page.out_of_scope_rows:
                range_complete = True
                stop_reason = "backfill_range_complete"
                break
            if (
                page_times
                and coverage_stop is not None
                and coverage_stop(min(page_times), max(page_times))
            ):
                range_complete = True
                stop_reason = "existing_coverage_reached"
                break
            if page_detail_failure:
                # Keep traversing to establish coverage, but unresolved range
                # work prevents SUCCESS below.
                continue

        if stop_reason == "cancelled":
            status = CollectionStatus.CANCELLED
        elif stop_reason == "schema_mismatch":
            status = CollectionStatus.SPEC_MISMATCH
        elif (
            counters.details_requested > 0
            and counters.details_success == 0
            and counters.details_failed == counters.details_requested
        ):
            status = CollectionStatus.COLLECTION_FAILED
            stop_reason = "all_candidate_details_failed"
        elif stop_reason in {"source_failure", "pagination_failure"}:
            status = CollectionStatus.COLLECTION_FAILED if counters.pages_success == 0 else CollectionStatus.PARTIAL_COLLECTION
        elif range_complete and counters.records_failed == 0:
            status = CollectionStatus.SUCCESS
        elif page_limit is not None and counters.pages_success > 0 and not range_complete:
            status = CollectionStatus.PARTIAL_COLLECTION
            stop_reason = "max_pages_reached"
        elif counters.records_failed:
            status = CollectionStatus.PARTIAL_COLLECTION
            stop_reason = stop_reason or "source_failure"
        else:
            status = CollectionStatus.PARTIAL_COLLECTION
            stop_reason = stop_reason or "max_pages_reached"
        result = CollectionResult(
            status, items, counters, failures, stop_reason, None, None,
        )
        return BackfillCollectionResult(
            result=result,
            pages_scanned=counters.pages_success,
            records_received=counters.records_received,
            records_in_range=records_in_range,
            records_failed=counters.records_failed,
            earliest_observed_at=earliest,
            latest_observed_at=latest,
            range_complete=range_complete and status is CollectionStatus.SUCCESS,
        )

    @staticmethod
    def _failure_name(exc: Exception) -> str:
        if isinstance(exc, FetchFailure):
            return exc.kind
        if isinstance(exc, GubaDetailMismatch):
            return "detail_mismatch"
        return "parse_failure"
