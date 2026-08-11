"""Minimal offline-capable Collector to persistence application boundary."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .models import CollectionResult, CollectionStatus
from .sources.eastmoney_guba.collector import (
    CollectorConfig,
    EastmoneyGubaCollector,
    Transport,
)
from .storage import PersistenceError, RawEvidenceStore, SafeFrontier, SQLitePersistence
from .storage.models import PublishedRaw


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


def _response_parts(response: Any) -> tuple[int, bytes, dict[str, str], str | None]:
    status = int(getattr(response, "status_code", getattr(response, "status", 0)))
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
    return status, bytes(body), headers, final_url


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
            self.events.append(
                _RequestEvent(
                    ordinal, url, self._kind(url), retry_number, started, finished,
                    None, {}, None, None, type(exc).__name__, str(exc),
                )
            )
            raise

        status, body, headers, final_url = _response_parts(response)
        published = self.raw_store.publish(self.run_id, ordinal, body)
        self.events.append(
            _RequestEvent(
                ordinal, url, self._kind(url), retry_number, started, self.clock(),
                status, headers, final_url, body, published=published,
            )
        )
        return response

    def consume_success(self, kind: str, payload: bytes) -> str:
        for event in reversed(self.events):
            if event.status == 200 and event.request_kind == kind and not event.evidence_consumed:
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


@dataclass(frozen=True)
class PersistentCollection:
    run_id: str
    result: CollectionResult
    db_path: Path
    raw_data_dir: Path
    attempt_count: int
    evidence_count: int
    failure_count: int


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
    if event.status == 200:
        return "success"
    if event.status is not None:
        return "http_error"
    error = (event.error_class or "").lower()
    return "timeout" if "timeout" in error else "transport_error"


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
    if result.watermark is None:
        return None
    if result.status in (CollectionStatus.SUCCESS, CollectionStatus.NO_NEW_DATA):
        return SafeFrontier(result.watermark)
    if result.status is CollectionStatus.PARTIAL_COLLECTION:
        gaps = tuple(result.failures) or (result.stop_reason or "partial_collection",)
        return SafeFrontier(result.watermark, all_required_persisted=False, unresolved_gaps=gaps)
    return None


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
) -> PersistentCollection:
    """Run the existing Collector once and persist its execution atomically."""
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
                    retry_budget=config.access_block_attempts if event.status == 403 else config.max_attempts,
                    http_status=event.status,
                    retry_after_seconds=_retry_after(event.headers),
                    error_class=event.error_class or (None if event.status == 200 else f"http_{event.status}"),
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
                for role in ("list", "detail"):
                    token = item.raw_ref.get(role)
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
