"""Minimal offline-capable Collector to persistence application boundary."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlencode

from .models import CollectionResult, CollectionStatus
from .sources.eastmoney_guba.collector import (
    BackfillCollectionResult,
    BOOTSTRAP_MIN_PAGES,
    CollectorConfig,
    EastmoneyGubaCollector,
    Transport,
)
from .sources.eastmoney_guba.acquisition import BROWSER_DOM_SNAPSHOT, HTTP_RESPONSE
from .sources.eastmoney_guba.parser import is_access_block_page
from .sources.xueqiu.browser_transport import XueqiuTransport, redact_xueqiu_url
from .sources.xueqiu.collector import (
    CollectorConfig as XueqiuCollectorConfig,
    XUEQIU_BOOTSTRAP_MIN_PAGES,
    XueqiuCollector,
    symbol_for,
)
from .storage import PersistenceError, RawEvidenceStore, SafeFrontier, SQLitePersistence
from .storage.models import PublishedRaw
from .simple_store import SimplePostStore


@dataclass
class _RequestEvent:
    ordinal: int
    url: str
    request_kind: str
    retry_number: int
    started_at: datetime
    finished_at: datetime
    status: int | None
    headers: dict[str, str]
    final_url: str | None
    body: bytes | None
    error_class: str | None = None
    error_message: str | None = None
    published: PublishedRaw | None = None
    evidence_consumed: bool = False
    capture_method: str = HTTP_RESPONSE


def _response_parts(response: Any) -> tuple[int | None, bytes, dict[str, str], str | None, str]:
    raw_status = getattr(response, "status_code", getattr(response, "status", None))
    status = None if raw_status is None else int(raw_status)
    body = getattr(response, "body", None)
    if body is None:
        body = getattr(response, "content", None)
    if body is None:
        text = getattr(response, "text", "")
        body = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    if isinstance(body, str):
        body = body.encode("utf-8")
    headers = {str(k).lower(): str(v) for k, v in getattr(response, "headers", {}).items()}
    final_url = getattr(response, "final_url", None) or getattr(response, "url", None)
    capture_method = str(getattr(response, "capture_method", HTTP_RESPONSE))
    return status, bytes(body), headers, final_url, capture_method


class _CapturingTransport:
    """Capture the exact responses consumed by the existing Collector."""

    def __init__(
        self,
        delegate: Transport,
        raw_store: RawEvidenceStore,
        run_id: str,
        clock: Callable[[], datetime],
    ) -> None:
        self.delegate = delegate
        self.raw_store = raw_store
        self.run_id = run_id
        self.clock = clock
        self.events: list[_RequestEvent] = []
        self._per_url_attempts: dict[str, int] = {}

    @staticmethod
    def _kind(url: str) -> str:
        return "list" if "/list," in url else "detail"

    def get(self, url: str, *, timeout: float) -> Any:
        ordinal = len(self.events)
        retry_number = self._per_url_attempts.get(url, 0) + 1
        self._per_url_attempts[url] = retry_number
        started = self.clock()
        try:
            response = self.delegate.get(url, timeout=timeout)
        except Exception as exc:
            finished = self.clock()
            error_class = str(getattr(exc, "kind", type(exc).__name__))
            self.events.append(
                _RequestEvent(
                    ordinal, url, self._kind(url), retry_number, started, finished,
                    None, {}, None, None, error_class, str(exc),
                    capture_method=str(
                        getattr(self.delegate, "capture_method", HTTP_RESPONSE)
                    ),
                )
            )
            raise

        status, body, headers, final_url, capture_method = _response_parts(response)
        published = self.raw_store.publish(self.run_id, ordinal, body)
        acquisition_succeeded = status == 200 or (
            status is None and capture_method == BROWSER_DOM_SNAPSHOT
        )
        access_block = acquisition_succeeded and is_access_block_page(
            body.decode("utf-8", errors="replace")
        )
        finished = getattr(response, "fetched_at", None)
        if not isinstance(finished, datetime):
            finished = self.clock()
        self.events.append(
            _RequestEvent(
                ordinal, url, self._kind(url), retry_number, started, finished,
                status, headers, final_url, body, capture_method=capture_method,
                published=published,
                error_class="access_block" if access_block else None,
                error_message="identity-verification page" if access_block else None,
            )
        )
        return response

    def consume_success(self, kind: str, payload: bytes) -> str:
        for event in reversed(self.events):
            successful = event.status == 200 or (
                event.status is None and event.capture_method == BROWSER_DOM_SNAPSHOT
            )
            if successful and event.request_kind == kind and not event.evidence_consumed:
                if event.body != bytes(payload):
                    raise PersistenceError("collector evidence bytes differ from response bytes")
                event.evidence_consumed = True
                return f"capture://event/{event.ordinal}"
        raise PersistenceError("collector evidence event is missing")


class _CapturingEvidenceStore:
    """Adapt Collector's ``put`` protocol to published raw response events."""

    def __init__(self, transport: _CapturingTransport) -> None:
        self.transport = transport

    def put(self, kind: str, source_item_id: str | None, payload: bytes) -> str:
        del source_item_id
        return self.transport.consume_success(kind, payload)


class _NoopEvidenceStore:
    """Collector protocol adapter for the post-centric path; no disk writes."""
    def put(self, kind: str, source_item_id: str | None, payload: bytes) -> str:
        return f"noop://{kind}/{source_item_id or 'page'}"


def execute_and_persist_simple_backfill_collection(
    *, db_path: str | Path, stock_code: str, from_time: datetime, to_time: datetime,
    transport: Transport, collector_config: CollectorConfig | None = None,
    clock: Callable[[], datetime] | None = None, sleep_fn: Callable[[float], None] = time.sleep,
    start_page: int = 1, max_pages: int | None = None,
) -> PersistentBackfillCollection:
    """Run list-only Eastmoney collection directly into one-row ``posts``."""
    clock = clock or (lambda: datetime.now(timezone.utc))
    store = SimplePostStore(db_path)
    if start_page < 1:
        raise ValueError("start_page must be at least 1")
    config = collector_config or CollectorConfig()
    collector = EastmoneyGubaCollector(
        transport, evidence_store=_NoopEvidenceStore(), config=config,
        sleep_fn=sleep_fn, clock=clock,
    )
    try:
        execution = collector.collect_backfill(
            stock_code, from_time=from_time, to_time=to_time,
            max_pages=max_pages, include_details=False, start_page=start_page,
        )
        for item in execution.result.items:
            store.upsert_source_item(item, stock_code=stock_code, content=None)
        if execution.pages_scanned:
            store.mark_page("eastmoney_guba", stock_code, start_page + execution.pages_scanned - 1)
        return PersistentBackfillCollection(
            run_id="simple-" + uuid.uuid4().hex,
            execution=execution, db_path=Path(db_path), raw_data_dir=Path(db_path).parent,
            records_new=0, records_existing=0, records_versioned=0,
            checkpoint_before=None, checkpoint_after=None,
        )
    finally:
        store.close()


@dataclass(frozen=True)
class PersistentCollection:
    run_id: str
    result: CollectionResult
    db_path: Path
    raw_data_dir: Path
    attempt_count: int
    evidence_count: int
    failure_count: int


@dataclass(frozen=True)
class PersistentBackfillCollection:
    run_id: str
    execution: BackfillCollectionResult
    db_path: Path
    raw_data_dir: Path
    records_new: int
    records_existing: int
    records_versioned: int
    checkpoint_before: str | None
    checkpoint_after: str | None


class _XueqiuCapturingTransport:
    """Capture browser-observed Xueqiu responses for existing RawEvidence."""

    def __init__(self, delegate: XueqiuTransport, raw_store: RawEvidenceStore, run_id: str, clock: Callable[[], datetime]) -> None:
        self.delegate = delegate
        self.raw_store = raw_store
        self.run_id = run_id
        self.clock = clock
        self.events: list[_RequestEvent] = []

    def fetch_page(self, stock_code: str, *, page: int, last_id: str | None, timeout: float) -> Any:
        ordinal = len(self.events)
        started = self.clock()
        request_url = f"https://xueqiu.com/S/{symbol_for(stock_code)}"
        try:
            fetch_page = getattr(self.delegate, "fetch_page", None)
            if callable(fetch_page):
                response = fetch_page(
                    stock_code, page=page, last_id=last_id, timeout=timeout
                )
            else:
                query = {
                    "symbol": symbol_for(stock_code), "count": "10",
                    "comment": "0", "hl": "0", "source": "all",
                    "sort": "time", "page": str(page), "q": "", "type": "11",
                }
                if page > 1 and last_id is not None:
                    query["last_id"] = last_id
                response = self.delegate.get(
                    "https://xueqiu.com/query/v1/symbol/search/status.json?" + urlencode(query),
                    timeout=timeout,
                )
        except Exception as exc:
            self.events.append(_RequestEvent(
                ordinal, request_url, "list", 1, started, self.clock(),
                None, {}, None, None, type(exc).__name__, "browser transport failure",
            ))
            raise
        status, body, headers, final_url, capture_method = _response_parts(response)
        published = self.raw_store.publish(self.run_id, ordinal, body)
        safe_final_url = redact_xueqiu_url(final_url)
        self.events.append(_RequestEvent(
            ordinal, safe_final_url or request_url, "list", 1, started, self.clock(),
            status, headers, safe_final_url, body, capture_method=capture_method,
            published=published,
        ))
        return response

    def consume_success(self, kind: str, payload: bytes) -> str:
        for event in reversed(self.events):
            if event.status == 200 and event.request_kind == kind and not event.evidence_consumed:
                if event.body != bytes(payload):
                    raise PersistenceError("collector evidence bytes differ from response bytes")
                event.evidence_consumed = True
                return f"capture://event/{event.ordinal}"
        raise PersistenceError("collector evidence event is missing")


def _xueqiu_failure_event(events: list[_RequestEvent]) -> _RequestEvent | None:
    return events[-1] if events else None


def _watermark(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _retry_after(headers: Mapping[str, str]) -> float | None:
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        value = float(raw.strip())
    except (AttributeError, ValueError):
        return None
    return value if value >= 0 else None


def _attempt_outcome(event: _RequestEvent) -> str:
    if event.error_class == "access_block":
        return "http_error" if event.status is not None else "transport_error"
    if event.status == 200 or (
        event.status is None
        and event.capture_method == BROWSER_DOM_SNAPSHOT
        and event.body is not None
        and event.error_class is None
    ):
        return "success"
    if event.status is not None:
        return "http_error"
    error = (event.error_class or "").lower()
    return "timeout" if "timeout" in error else "transport_error"


def _evidence_kind(event: _RequestEvent) -> str:
    return f"{event.request_kind}:{event.capture_method}"


def _failure_event(
    message: str,
    events: list[_RequestEvent],
    stock_code: str,
) -> _RequestEvent | None:
    page = re.match(r"page (\d+):", message)
    detail = re.match(r"detail ([^:]+):", message)
    if page:
        target = EastmoneyGubaCollector.list_url(stock_code, int(page.group(1)))
        candidates = [event for event in events if event.url == target]
    elif detail:
        item_id = detail.group(1)
        candidates = [event for event in events if event.request_kind == "detail" and item_id in event.url]
    else:
        candidates = []
    return candidates[-1] if candidates else (events[-1] if events else None)


def _safe_frontier(result: CollectionResult) -> SafeFrontier | None:
    if result.safe_frontier is None:
        return None
    if result.status not in (
        CollectionStatus.SUCCESS,
        CollectionStatus.NO_NEW_DATA,
        CollectionStatus.PARTIAL_COLLECTION,
    ):
        return None
    # The Collector has already proven this declaration. Integration only
    # translates it; it does not derive a timestamp from items or failures.
    return SafeFrontier(result.safe_frontier)


def execute_and_persist_collection(
    *,
    db_path: str | Path,
    raw_data_dir: str | Path,
    stock_code: str,
    transport: Transport,
    run_id: str | None = None,
    collector_config: CollectorConfig | None = None,
    clock: Callable[[], datetime] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    max_pages: int | None = None,
    bootstrap_if_no_checkpoint: bool = True,
) -> PersistentCollection:
    """Run one persistent collection, bootstrapping any NULL checkpoint scope."""
    run_id = run_id or uuid.uuid4().hex
    clock = clock or (lambda: datetime.now(timezone.utc))
    db_path = Path(db_path)
    raw_data_dir = Path(raw_data_dir)
    raw_store = RawEvidenceStore(raw_data_dir, source="eastmoney_guba")
    store = SQLitePersistence(db_path, raw_store)
    scope_key = f"stock:{stock_code}"
    checkpoint = store.checkpoint("eastmoney_guba", scope_key)
    watermark_before = _watermark(checkpoint[0] if checkpoint else None)
    known_ids = store.known_item_ids("eastmoney_guba", scope_key)
    started_at = clock()
    config = collector_config or CollectorConfig()
    page_limit = max_pages if max_pages is not None else config.max_pages
    bootstrap = bootstrap_if_no_checkpoint and watermark_before is None
    if bootstrap and page_limit < BOOTSTRAP_MIN_PAGES:
        store.close()
        raise ValueError(
            f"bootstrap requires max_pages >= {BOOTSTRAP_MIN_PAGES}"
        )
    capture = _CapturingTransport(transport, raw_store, run_id, clock)
    evidence_store = _CapturingEvidenceStore(capture)
    try:
        store.start_run(
            run_id,
            "eastmoney_guba",
            scope_key,
            started_at=started_at,
            collector_version="eastmoney_guba.collector.v1",
            parser_version="eastmoney_guba.parser.v1",
            schema_version="eastmoney_guba.raw.v1",
            watermark_before=watermark_before,
        )
        collector = EastmoneyGubaCollector(
            capture,
            evidence_store=evidence_store,
            config=config,
            sleep_fn=sleep_fn,
            clock=clock,
        )
        result = collector.collect(
            stock_code,
            existing_ids=known_ids,
            watermark=watermark_before,
            max_pages=max_pages,
            bootstrap=bootstrap,
        )
        evidence_ids: dict[int, str] = {}
        attempt_ids: dict[int, str] = {}
        with store.transaction():
            for event in capture.events:
                attempt_id = f"{run_id}-attempt-{event.ordinal}"
                attempt_ids[event.ordinal] = attempt_id
                store.record_attempt(
                    run_id,
                    attempt_id,
                    ordinal=event.ordinal,
                    request_kind=event.request_kind,
                    request_url=event.url,
                    started_at=event.started_at,
                    finished_at=event.finished_at,
                    outcome=_attempt_outcome(event),
                    retry_number=event.retry_number,
                    retry_budget=(
                        config.access_block_attempts
                        if event.status == 403 or event.error_class == "access_block"
                        else config.max_attempts
                    ),
                    http_status=event.status,
                    retry_after_seconds=_retry_after(event.headers),
                    error_class=event.error_class or (
                        None if _attempt_outcome(event) == "success"
                        else (f"http_{event.status}" if event.status is not None else "acquisition_failure")
                    ),
                    error_message=event.error_message,
                )
                if event.published is not None:
                    evidence_id = f"{run_id}-evidence-{event.ordinal}"
                    evidence_ids[event.ordinal] = evidence_id
                    store.record_raw_evidence(
                        run_id,
                        attempt_id,
                        evidence_id,
                        event.published,
                        evidence_kind=_evidence_kind(event),
                        request_url=event.url,
                        final_url=event.final_url,
                        fetched_at=event.finished_at,
                        http_status=event.status,
                        content_type=event.headers.get("content-type"),
                    )

            for index, message in enumerate(result.failures):
                event = _failure_event(message, capture.events, stock_code)
                failure_class = message.split(":", 1)[-1].strip() or "collection_failure"
                store.record_failure(
                    run_id,
                    f"{run_id}-failure-{index}",
                    phase="collect",
                    failure_class=failure_class,
                    occurred_at=clock(),
                    message=message,
                    attempt_id=attempt_ids.get(event.ordinal) if event else None,
                    evidence_id=evidence_ids.get(event.ordinal) if event else None,
                )

            observations = []
            for item in result.items:
                links = []
                for role, token in item.raw_ref.items():
                    if token is None or not token.startswith("capture://event/"):
                        raise PersistenceError("collector item has no persisted raw evidence token")
                    ordinal = int(token.rsplit("/", 1)[-1])
                    evidence_id = evidence_ids.get(ordinal)
                    if evidence_id is None:
                        raise PersistenceError("collector item evidence was not published")
                    links.append((evidence_id, role))
                observations.append((item, scope_key, links))

            store.persist_result(
                run_id,
                observations,
                status=result.status.value,
                finished_at=clock(),
                counters=result.counters,
                safe_frontier=_safe_frontier(result),
            )
        return PersistentCollection(
            run_id, result, db_path, raw_data_dir,
            len(capture.events), len(evidence_ids), len(result.failures),
        )
    finally:
        store.close()


def execute_and_persist_backfill_collection(
    *,
    db_path: str | Path,
    raw_data_dir: str | Path,
    stock_code: str,
    from_time: datetime,
    to_time: datetime,
    transport: Transport,
    run_id: str | None = None,
    collector_config: CollectorConfig | None = None,
    clock: Callable[[], datetime] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    max_pages: int | None = None,
    include_details: bool = True,
    start_page: int = 1,
) -> PersistentBackfillCollection:
    """Persist one Eastmoney backfill without ever advancing its checkpoint."""
    run_id = run_id or uuid.uuid4().hex
    clock = clock or (lambda: datetime.now(timezone.utc))
    db_path = Path(db_path)
    raw_data_dir = Path(raw_data_dir)
    raw_store = RawEvidenceStore(raw_data_dir, source="eastmoney_guba")
    store = SQLitePersistence(db_path, raw_store)
    scope_key = f"stock:{stock_code}"
    checkpoint = store.checkpoint("eastmoney_guba", scope_key)
    checkpoint_before = checkpoint[0] if checkpoint else None
    watermark_before = _watermark(checkpoint_before)
    started_at = clock()
    config = collector_config or CollectorConfig()
    capture = _CapturingTransport(transport, raw_store, run_id, clock)
    evidence_store = _CapturingEvidenceStore(capture)
    try:
        store.start_run(
            run_id, "eastmoney_guba", scope_key, started_at=started_at,
            collector_version="eastmoney_guba.collector.v1",
            parser_version="eastmoney_guba.parser.v1",
            schema_version="eastmoney_guba.raw.v1",
            watermark_before=watermark_before,
        )
        collector = EastmoneyGubaCollector(
            capture, evidence_store=evidence_store, config=config,
            sleep_fn=sleep_fn, clock=clock,
        )
        execution = collector.collect_backfill(
            stock_code, from_time=from_time, to_time=to_time, max_pages=max_pages,
            include_details=include_details,
            start_page=start_page,
        )
        result = execution.result
        evidence_ids: dict[int, str] = {}
        attempt_ids: dict[int, str] = {}
        with store.transaction():
            for event in capture.events:
                attempt_id = f"{run_id}-attempt-{event.ordinal}"
                attempt_ids[event.ordinal] = attempt_id
                store.record_attempt(
                    run_id, attempt_id, ordinal=event.ordinal,
                    request_kind=event.request_kind, request_url=event.url,
                    started_at=event.started_at, finished_at=event.finished_at,
                    outcome=_attempt_outcome(event), retry_number=event.retry_number,
                    retry_budget=(
                        config.access_block_attempts
                        if event.status == 403 or event.error_class == "access_block"
                        else config.max_attempts
                    ),
                    http_status=event.status, retry_after_seconds=_retry_after(event.headers),
                    error_class=event.error_class or (
                        None if _attempt_outcome(event) == "success"
                        else (f"http_{event.status}" if event.status is not None else "acquisition_failure")
                    ),
                    error_message=event.error_message,
                )
                if event.published is not None:
                    evidence_id = f"{run_id}-evidence-{event.ordinal}"
                    evidence_ids[event.ordinal] = evidence_id
                    store.record_raw_evidence(
                        run_id, attempt_id, evidence_id, event.published,
                        evidence_kind=_evidence_kind(event), request_url=event.url,
                        final_url=event.final_url, fetched_at=event.finished_at,
                        http_status=event.status, content_type=event.headers.get("content-type"),
                    )
            for index, message in enumerate(result.failures):
                event = _failure_event(message, capture.events, stock_code)
                store.record_failure(
                    run_id, f"{run_id}-failure-{index}", phase="collect",
                    failure_class=message.split(":", 1)[-1].strip() or "collection_failure",
                    occurred_at=clock(), message=message,
                    attempt_id=attempt_ids.get(event.ordinal) if event else None,
                    evidence_id=evidence_ids.get(event.ordinal) if event else None,
                )
            observations = []
            for item in result.items:
                links = []
                for role, token in item.raw_ref.items():
                    if token is None or not token.startswith("capture://event/"):
                        raise PersistenceError("collector item has no persisted raw evidence token")
                    ordinal = int(token.rsplit("/", 1)[-1])
                    evidence_id = evidence_ids.get(ordinal)
                    if evidence_id is None:
                        raise PersistenceError("collector item evidence was not published")
                    links.append((evidence_id, role))
                observations.append((item, scope_key, links))
            created, _advanced = store.persist_result(
                run_id, observations, status=result.status.value,
                finished_at=clock(), counters=result.counters, safe_frontier=None,
            )
        records_new = sum(1 for _oid, version, made in created if made and version == 1)
        records_versioned = sum(1 for _oid, version, made in created if made and version > 1)
        records_existing = sum(1 for _oid, _version, made in created if not made)
        checkpoint_after_row = store.checkpoint("eastmoney_guba", scope_key)
        checkpoint_after = checkpoint_after_row[0] if checkpoint_after_row else None
        if checkpoint_after != checkpoint_before:
            raise PersistenceError("backfill changed forward checkpoint")
        return PersistentBackfillCollection(
            run_id, execution, db_path, raw_data_dir, records_new,
            records_existing, records_versioned, checkpoint_before, checkpoint_after,
        )
    finally:
        store.close()


def execute_and_persist_xueqiu_collection(
    *,
    db_path: str | Path,
    raw_data_dir: str | Path,
    stock_code: str,
    transport: XueqiuTransport,
    run_id: str | None = None,
    collector_config: XueqiuCollectorConfig | None = None,
    clock: Callable[[], datetime] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    max_pages: int | None = None,
    bootstrap_if_no_checkpoint: bool = True,
) -> PersistentCollection:
    """Run Xueqiu through the existing RawEvidence/SQLite boundary.

    This is deliberately source-specific orchestration; the persistence schema
    and checkpoint ownership remain shared and unchanged.
    """
    run_id = run_id or uuid.uuid4().hex
    clock = clock or (lambda: datetime.now(timezone.utc))
    db_path = Path(db_path)
    raw_data_dir = Path(raw_data_dir)
    raw_store = RawEvidenceStore(raw_data_dir, source="xueqiu")
    store = SQLitePersistence(db_path, raw_store)
    scope_key = f"stock:{stock_code}"
    checkpoint = store.checkpoint("xueqiu", scope_key)
    watermark_before = _watermark(checkpoint[0] if checkpoint else None)
    existing_observations = store.latest_observations("xueqiu", scope_key)
    known_ids = set(existing_observations)
    started_at = clock()
    config = collector_config or XueqiuCollectorConfig()
    page_limit = max_pages if max_pages is not None else config.max_pages
    bootstrap = bootstrap_if_no_checkpoint and watermark_before is None
    if bootstrap and page_limit < XUEQIU_BOOTSTRAP_MIN_PAGES:
        store.close()
        raise ValueError(
            f"bootstrap requires max_pages >= {XUEQIU_BOOTSTRAP_MIN_PAGES}"
        )
    capture = _XueqiuCapturingTransport(transport, raw_store, run_id, clock)
    evidence_store = _CapturingEvidenceStore(capture)  # type: ignore[arg-type]
    try:
        store.start_run(
            run_id,
            "xueqiu",
            scope_key,
            started_at=started_at,
            collector_version="xueqiu.collector.v1",
            parser_version="xueqiu.parser.v1",
            schema_version="xueqiu.raw.v1",
            watermark_before=watermark_before,
        )
        collector = XueqiuCollector(
            capture,
            evidence_store=evidence_store,
            config=config,
            sleep_fn=sleep_fn,
            clock=clock,
        )
        result = collector.collect(
            stock_code,
            existing_ids=known_ids,
            existing_observations=existing_observations,
            watermark=watermark_before,
            max_pages=max_pages,
            bootstrap=bootstrap,
        )
        evidence_ids: dict[int, str] = {}
        attempt_ids: dict[int, str] = {}
        with store.transaction():
            for event in capture.events:
                attempt_id = f"{run_id}-attempt-{event.ordinal}"
                attempt_ids[event.ordinal] = attempt_id
                store.record_attempt(
                    run_id,
                    attempt_id,
                    ordinal=event.ordinal,
                    request_kind=event.request_kind,
                    request_url=event.url,
                    started_at=event.started_at,
                    finished_at=event.finished_at,
                    outcome=_attempt_outcome(event),
                    retry_number=event.retry_number,
                    retry_budget=config.max_attempts,
                    http_status=event.status,
                    retry_after_seconds=_retry_after(event.headers),
                    error_class=event.error_class or (
                        None if _attempt_outcome(event) == "success"
                        else (f"http_{event.status}" if event.status is not None else "acquisition_failure")
                    ),
                    error_message=event.error_message,
                )
                if event.published is not None:
                    evidence_id = f"{run_id}-evidence-{event.ordinal}"
                    evidence_ids[event.ordinal] = evidence_id
                    store.record_raw_evidence(
                        run_id,
                        attempt_id,
                        evidence_id,
                        event.published,
                        evidence_kind=event.request_kind,
                        request_url=event.url,
                        final_url=event.final_url,
                        fetched_at=event.finished_at,
                        http_status=event.status,
                        content_type=event.headers.get("content-type"),
                    )

            for index, message in enumerate(result.failures):
                event = _xueqiu_failure_event(capture.events)
                store.record_failure(
                    run_id,
                    f"{run_id}-failure-{index}",
                    phase="collect",
                    failure_class=message.split(":", 1)[-1].strip() or "collection_failure",
                    occurred_at=clock(),
                    message=message,
                    attempt_id=attempt_ids.get(event.ordinal) if event else None,
                    evidence_id=evidence_ids.get(event.ordinal) if event else None,
                )

            observations = []
            for item in result.items:
                links = []
                for role, token in item.raw_ref.items():
                    if not token.startswith("capture://event/"):
                        raise PersistenceError("collector item has no persisted raw evidence token")
                    ordinal = int(token.rsplit("/", 1)[-1])
                    evidence_id = evidence_ids.get(ordinal)
                    if evidence_id is None:
                        raise PersistenceError("collector item evidence was not published")
                    links.append((evidence_id, role))
                observations.append((item, scope_key, links))

            store.persist_result(
                run_id,
                observations,
                status=result.status.value,
                finished_at=clock(),
                counters=result.counters,
                safe_frontier=_safe_frontier(result),
            )
        return PersistentCollection(
            run_id, result, db_path, raw_data_dir,
            len(capture.events), len(evidence_ids), len(result.failures),
        )
    finally:
        store.close()
