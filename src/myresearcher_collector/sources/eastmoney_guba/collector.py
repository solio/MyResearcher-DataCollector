"""Isolated Eastmoney Guba acquisition behavior."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class UrllibTransport:
    """Small production transport; tests inject a local fake instead."""

    def __init__(self, user_agent: str = "MyResearcher-DataCollector/eastmoney_guba") -> None:
        self.user_agent = user_agent

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
            with urlopen(request, timeout=timeout) as response:
                return HttpResponse(
                    status_code=int(response.status),
                    body=response.read(),
                    headers={key.lower(): value for key, value in response.headers.items()},
                )
        except HTTPError as exc:
            return HttpResponse(
                status_code=int(exc.code),
                body=exc.read(),
                headers={key.lower(): value for key, value in exc.headers.items()},
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
    base_backoff_seconds: float = 0.0


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
    ) -> None:
        self.transport = transport or UrllibTransport()
        self.evidence_store = evidence_store or InMemoryRawEvidenceStore()
        self.config = config or CollectorConfig()
        self.sleep_fn = sleep_fn
        self.clock = clock or (lambda: datetime.now(timezone.utc))

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
    def _body(response: Any) -> tuple[int, str, bytes, dict[str, str]]:
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
        return status, text, body, headers

    def _fetch(self, url: str, counters: RuntimeCounters) -> tuple[str, bytes, dict[str, str]]:
        attempts = self.config.access_block_attempts
        for attempt in range(1, self.config.max_attempts + 1):
            counters.requests_total += 1
            try:
                response = self.transport.get(url, timeout=self.config.timeout_seconds)
                status, text, body, headers = self._body(response)
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
                return text, body, headers

            counters.requests_failed += 1
            retryable = status == 429 or status >= 500 or status == 403
            if status == 403:
                attempts = self.config.access_block_attempts
            if not retryable or attempt >= min(self.config.max_attempts, attempts):
                kind = "access_block" if status in (403, 429) else f"http_{status}"
                raise FetchFailure(kind, f"source request returned HTTP {status}")
            self._backoff(attempt)
        raise FetchFailure("transport", "request failed")

    def _backoff(self, attempt: int) -> None:
        delay = self.config.base_backoff_seconds * (2 ** (attempt - 1))
        if delay > 0:
            self.sleep_fn(delay)

    def collect(
        self,
        stock_code: str,
        *,
        existing_ids: set[str] | None = None,
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
        boundary_pages = 0
        stop_reason: str | None = None
        had_successful_page = False
        any_page_failure = False

        for page_number in range(1, page_limit + 1):
            counters.pages_requested += 1
            page_url = self.list_url(stock_code, page_number)
            try:
                html, raw_page, _ = self._fetch(page_url, counters)
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
                if row.source_item_id in seen_ids:
                    counters.duplicate_records += 1
                    continue
                if watermark is not None and row.published_at <= watermark:
                    seen_ids.add(row.source_item_id)
                    continue
                page_all_old_or_seen = False
                page_has_new = True
                seen_ids.add(row.source_item_id)
                counters.details_requested += 1
                try:
                    detail_html, raw_detail, _ = self._fetch(self.detail_url(row.url), counters)
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
                item = GubaSourceItem(
                    source=SOURCE,
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
                    source_metadata=merged["source_metadata"],
                    raw_ref={"list": page_ref, "detail": detail_ref},
                )
                items.append(item)
                counters.details_success += 1
                counters.records_parsed += 1

            if watermark is not None and page_all_old_or_seen and not page_has_new:
                boundary_pages += 1
                if boundary_pages >= 2:
                    stop_reason = "watermark_confirmed"
                    break
            elif watermark is not None:
                boundary_pages = 0

            if page_number == page_limit:
                stop_reason = "max_pages"

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
        new_watermark = max((item.published_at for item in items), default=watermark)
        return CollectionResult(status, items, counters, failures, stop_reason, new_watermark)

    @staticmethod
    def _failure_name(exc: Exception) -> str:
        if isinstance(exc, FetchFailure):
            return exc.kind
        if isinstance(exc, GubaDetailMismatch):
            return "detail_mismatch"
        return "parse_failure"
