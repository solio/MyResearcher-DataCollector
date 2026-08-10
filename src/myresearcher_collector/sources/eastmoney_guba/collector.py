"""Isolated Eastmoney Guba acquisition behavior."""

from __future__ import annotations

import hashlib
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol
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
)


class Transport(Protocol):
    def get(self, url: str, *, timeout: float) -> Any:
        """Return a response-like object with ``status_code`` and body."""


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes
    headers: dict[str, str]
    final_url: str | None = None

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


_ALLOWED_SOURCE_HOSTS = {"guba.eastmoney.com", "caifuhao.eastmoney.com"}


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
        """Store one immutable response and return an opaque reference."""


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
    max_pages: int = 2
    max_attempts: int = 3
    access_block_attempts: int = 2
    base_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 30.0
    max_retry_after_seconds: float = 60.0
    min_interval_seconds: float = 3.0


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
        self.transport = transport or UrllibTransport()
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
    def _body(response: Any) -> tuple[int, str, bytes, dict[str, str], str | None]:
        status = int(getattr(response, "status_code", getattr(response, "status", 0)))
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
        return status, text, body, headers, final_url

    def _fetch(self, url: str, counters: RuntimeCounters) -> tuple[str, bytes, dict[str, str], str]:
        attempts = self.config.access_block_attempts
        for attempt in range(1, self.config.max_attempts + 1):
            counters.requests_total += 1
            try:
                self._rate_limit()
                response = self.transport.get(url, timeout=self.config.timeout_seconds)
                status, text, body, headers, final_url = self._body(response)
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
                if attempt >= self.config.max_attempts:
                    raise FetchFailure("transport", "request failed after retry budget") from exc
                self._backoff(attempt)
                continue
            except Exception as exc:
                counters.requests_failed += 1
                raise FetchFailure("transport", "request raised an unexpected error") from exc

            if status == 200:
                counters.requests_success += 1
                return text, body, headers, final_url or url

            counters.requests_failed += 1
            retryable = status == 429 or status >= 500 or status == 403
            if status == 403:
                attempts = self.config.access_block_attempts
            if not retryable or attempt >= min(self.config.max_attempts, attempts):
                kind = "access_block" if status in (403, 429) else f"http_{status}"
                raise FetchFailure(kind, f"source request returned HTTP {status}")
            self._backoff(attempt, headers)
        raise FetchFailure("transport", "request failed")

    def _rate_limit(self) -> None:
        now = self.monotonic_fn()
        if self._last_request_at is not None:
            remaining = self.config.min_interval_seconds - (now - self._last_request_at)
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

    def collect(
        self,
        stock_code: str,
        *,
        existing_ids: set[str] | None = None,
        existing_observations: Mapping[str, GubaSourceItem] | None = None,
        watermark: datetime | None = None,
        max_pages: int | None = None,
    ) -> CollectionResult:
        if not isinstance(stock_code, str) or len(stock_code) != 6 or not stock_code.isdigit():
            raise ValueError("stock_code must be six decimal digits")
        page_limit = max_pages if max_pages is not None else self.config.max_pages
        if page_limit < 1:
            raise ValueError("max_pages must be at least 1")

        counters = RuntimeCounters()
        items: list[GubaSourceItem] = []
        failures: list[str] = []
        seen_ids = set(existing_ids or ())
        observed_rows: dict[str, Any] = {}
        observation_versions = {
            source_item_id: item.observation_version
            for source_item_id, item in (existing_observations or {}).items()
        }
        boundary_pages = 0
        stop_reason: str | None = None
        had_successful_page = False
        any_page_failure = False

        for page_number in range(1, page_limit + 1):
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
            page_ref = self.evidence_store.put("list", None, raw_page)
            if not parsed_page.rows and not parsed_page.out_of_scope_rows:
                stop_reason = "empty_page"
                break

            page_all_old_or_seen = bool(parsed_page.rows)
            page_has_new = False
            for row in parsed_page.rows:
                if self.cancel_check and self.cancel_check():
                    stop_reason = "cancelled"
                    break
                prior_row = observed_rows.get(row.source_item_id)
                prior_item = (existing_observations or {}).get(row.source_item_id)
                drift = (
                    prior_row is not None and self._row_fingerprint(prior_row) != self._row_fingerprint(row)
                ) or (
                    prior_row is None
                    and prior_item is not None
                    and not self._row_matches_item(row, prior_item)
                )
                already_seen = row.source_item_id in seen_ids
                if already_seen:
                    counters.duplicate_records += 1
                if already_seen and not drift:
                    continue
                eligible_new = watermark is None or row.published_at > watermark
                if watermark is not None and not eligible_new and not drift:
                    seen_ids.add(row.source_item_id)
                    observed_rows[row.source_item_id] = row
                    continue
                if eligible_new:
                    page_all_old_or_seen = False
                    page_has_new = True
                seen_ids.add(row.source_item_id)
                observed_rows[row.source_item_id] = row
                if drift:
                    counters.identity_content_drifts += 1
                counters.details_requested += 1
                try:
                    detail_html, raw_detail, _, detail_final_url = self._fetch(self.detail_url(row.url), counters)
                    detail = parse_detail_page(detail_html)
                    merged = merge_list_and_detail(row, detail)
                except GubaSchemaMismatch:
                    counters.details_failed += 1
                    counters.records_failed += 1
                    failures.append(f"detail {row.source_item_id}: schema_mismatch")
                    return CollectionResult(CollectionStatus.SPEC_MISMATCH, items, counters, failures, "detail_schema_mismatch", watermark)
                except (GubaDetailMismatch, GubaParseError, FetchFailure) as exc:
                    counters.details_failed += 1
                    counters.records_failed += 1
                    failures.append(f"detail {row.source_item_id}: {self._failure_name(exc)}")
                    continue

                detail_ref = self.evidence_store.put("detail", row.source_item_id, raw_detail)
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
                counters.details_success += 1
                counters.records_parsed += 1

            if stop_reason == "cancelled":
                break

            if watermark is not None and page_all_old_or_seen and not page_has_new:
                boundary_pages += 1
                if boundary_pages >= 2:
                    stop_reason = "watermark_confirmed"
                    break
            elif watermark is not None:
                boundary_pages = 0

            if page_number == page_limit:
                stop_reason = "max_pages"

        if stop_reason == "cancelled":
            return CollectionResult(CollectionStatus.CANCELLED, items, counters, failures, stop_reason, watermark)
        if not had_successful_page:
            return CollectionResult(CollectionStatus.COLLECTION_FAILED, items, counters, failures, stop_reason, watermark)
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
        new_watermark = (
            max((item.published_at for item in items), default=watermark)
            if status in (CollectionStatus.SUCCESS, CollectionStatus.NO_NEW_DATA)
            else watermark
        )
        return CollectionResult(status, items, counters, failures, stop_reason, new_watermark)

    @staticmethod
    def _failure_name(exc: Exception) -> str:
        if isinstance(exc, FetchFailure):
            return exc.kind
        if isinstance(exc, GubaDetailMismatch):
            return "detail_mismatch"
        return "parse_failure"
