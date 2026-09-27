"""Bounded Eastmoney detail enrichment for legacy and canonical storage."""

from __future__ import annotations

import json
import random
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .models import GubaSourceItem, RuntimeCounters, SourceItem
from .simple_store import SimplePostStore
from .sources.eastmoney_guba.challenge_wait import wait_for_manual_verification
from .sources.eastmoney_guba.content_rules import (
    LIST_TITLE_CONTENT_SOURCE,
    detail_body_metadata,
    detail_enrichment_trigger,
    normalized_title_length,
)
from .sources.eastmoney_guba.existing_chrome import ExistingChromeAcquisitionError
from .sources.eastmoney_guba.parser import (
    GubaDetailMismatch,
    GubaParseError,
    GubaSchemaMismatch,
    SCHEMA_VERSION,
    SOURCE,
    is_access_block_page,
    is_not_found_page,
    merge_list_and_detail,
    parse_detail_page,
)
from .storage import RawEvidenceStore, SQLitePersistence


@dataclass(frozen=True)
class _Candidate:
    source_item_id: str
    url: str
    title: str | None
    trigger: str
    canonical_item: SourceItem | None = None


class _DetailFetchFailure(RuntimeError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _body(response: Any) -> bytes:
    value = getattr(response, "payload", None)
    if value is None:
        value = getattr(response, "body", None)
    if value is None:
        value = getattr(response, "content", None)
    if value is None:
        value = getattr(response, "text", "")
    return value.encode("utf-8") if isinstance(value, str) else bytes(value)


def _live_challenge_reasons(transport: Any, response: Any = None) -> list[str]:
    """Challenge signals that only the rendered page can supply.

    `is_access_block_page` reads the response bytes, so it structurally cannot
    see the challenge observed on 2026-08-13: a slider overlay injected after
    load on top of an otherwise normal page, with `<title>` and the embedded
    `post_article` payload intact. `parse_detail_page` therefore also succeeds,
    and without this probe an overlay block is recorded as a plain success while
    the source is actively challenging us -- which is precisely the input
    `enrich_all_stocks.sh` relies on to fail closed.

    Fail-open by construction: a transport that cannot probe returns `[]`, and an
    empty list means "no signal", never "proven clean".
    """
    reasons: list[str] = []
    diagnostics = getattr(response, "diagnostics", None)
    if isinstance(diagnostics, dict):
        # Recorded by the transport when the bar list page itself was challenged
        # before the detail was reached.
        for reason in diagnostics.get("list_challenge_reasons") or []:
            reasons.append(f"list_page:{reason}")
    probe = getattr(transport, "challenge_reasons", None)
    if callable(probe):
        try:
            reasons.extend(str(reason) for reason in (probe() or []))
        except Exception:  # noqa: BLE001
            pass
    ordered: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            ordered.append(reason)
    return ordered


def _challenge_tier(reasons: list[str]) -> str | None:
    """Separate hard evidence from the visible-text tier, which can be prose.

    `structural` = a captcha container in the DOM, a challenge asset, a challenge
    title, or a challenge on the list page. `text_only` = the rendered words were
    the only signal, which is what catches the 2026-08-13 overlay but could in
    principle appear in ordinary prose. Both block; the tier is kept so a run can
    report suspected rather than confirmed.
    """
    if not reasons:
        return None
    if any(not reason.startswith("visible_text:") for reason in reasons):
        return "structural"
    return "text_only"


def _utc_now(clock: Callable[[], object] | None) -> datetime:
    value = clock() if clock is not None else datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise TypeError("clock must return datetime")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _table_names(db_path: Path) -> set[str]:
    if not db_path.is_file():
        raise ValueError(f"collector database does not exist: {db_path}")
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()


SKIP_LEDGER_TABLE = "detail_enrichment_skips"
SKIP_REASON_NOT_FOUND = "detail_not_found"
_SKIP_LEDGER_SUFFIX = ".detail_enrichment_skips.db"


def _skip_ledger_path(db_path: Path) -> Path:
    """Sidecar sqlite file remembering detail posts that no longer exist.

    The ledger deliberately lives *outside* the collector database. That schema
    is a frozen contract re-validated on every open (see ``storage.schema``), so
    an extra table there is rejected as drift. A sibling file also means a
    missing or corrupt ledger can never block enrichment.
    """
    return db_path.with_name(f"{db_path.stem}{_SKIP_LEDGER_SUFFIX}")


class _SkipLedger:
    """Best-effort record of posts known not to exist at their detail URL.

    Every method degrades to a no-op / empty result on error: skipping is an
    optimisation, never a correctness gate. Reads fall back to the previous
    re-request behaviour rather than failing.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.execute(
            f"""CREATE TABLE IF NOT EXISTS {SKIP_LEDGER_TABLE} (
                  source TEXT NOT NULL,
                  source_item_id TEXT NOT NULL,
                  stock_code TEXT NOT NULL,
                  reason TEXT NOT NULL,
                  first_seen_at TEXT NOT NULL,
                  last_seen_at TEXT NOT NULL,
                  attempts INTEGER NOT NULL,
                  PRIMARY KEY(source, source_item_id))"""
        )
        conn.commit()
        return conn

    def skipped_ids(self, source: str) -> set[str]:
        try:
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT source_item_id FROM {SKIP_LEDGER_TABLE} WHERE source=?",
                    (source,),
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            print(
                f"[detail-enrichment] skip-ledger unavailable ({exc}); "
                "continuing without skips",
                file=sys.stderr,
            )
            return set()
        return {str(row[0]) for row in rows}

    def record(
        self,
        *,
        source: str,
        source_item_id: str,
        stock_code: str,
        reason: str,
        observed_at: datetime,
    ) -> None:
        stamp = observed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            conn = self._connect()
            try:
                conn.execute(
                    f"""INSERT INTO {SKIP_LEDGER_TABLE}
                          (source, source_item_id, stock_code, reason, first_seen_at, last_seen_at, attempts)
                        VALUES (?, ?, ?, ?, ?, ?, 1)
                        ON CONFLICT(source, source_item_id) DO UPDATE SET
                          last_seen_at=excluded.last_seen_at,
                          reason=excluded.reason,
                          attempts={SKIP_LEDGER_TABLE}.attempts + 1""",
                    (source, source_item_id, stock_code, reason, stamp, stamp),
                )
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            print(
                f"[detail-enrichment] could not record skip for {source_item_id}: {exc}",
                file=sys.stderr,
            )


def _failure_reason(exc: BaseException) -> str:
    if isinstance(exc, _DetailFetchFailure):
        return exc.kind
    if isinstance(exc, ExistingChromeAcquisitionError):
        return str(getattr(exc, "kind", "browser_failure"))
    if isinstance(exc, GubaDetailMismatch):
        return "detail_identity_mismatch"
    if isinstance(exc, GubaSchemaMismatch):
        return "detail_schema_mismatch"
    if isinstance(exc, GubaParseError):
        return "detail_parse_failure"
    return f"{type(exc).__name__}: {exc}"


def _canonical_candidates(
    store: SQLitePersistence,
    stock_code: str,
    *,
    include_short_titles: bool,
    skipped: set[str] | frozenset[str] = frozenset(),
) -> list[_Candidate]:
    result: list[_Candidate] = []
    for item in store.latest_observations(SOURCE, f"stock:{stock_code}").values():
        if item.source_metadata.get("content_source") != LIST_TITLE_CONTENT_SOURCE:
            continue
        trigger = detail_enrichment_trigger(
            item.title, include_short_titles=include_short_titles
        )
        if trigger is None or not item.url:
            continue
        result.append(
            _Candidate(
                source_item_id=item.source_item_id,
                url=item.url,
                title=item.title,
                trigger=trigger,
                canonical_item=item,
            )
        )
    result = [candidate for candidate in result if candidate.source_item_id not in skipped]
    return sorted(result, key=lambda candidate: candidate.canonical_item.published_at)


def _legacy_candidates(
    store: SimplePostStore,
    stock_code: str,
    *,
    include_short_titles: bool,
    skipped: set[str] | frozenset[str] = frozenset(),
) -> list[_Candidate]:
    rows = store.conn.execute(
        """SELECT source_item_id,url,title,published_at FROM posts
           WHERE source=? AND stock_code=? AND content IS NULL
             AND url IS NOT NULL
           ORDER BY published_at""",
        (SOURCE, stock_code),
    ).fetchall()
    result: list[_Candidate] = []
    for item_id, url, title, _published_at in rows:
        trigger = detail_enrichment_trigger(
            title, include_short_titles=include_short_titles
        )
        if trigger is not None:
            result.append(_Candidate(str(item_id), str(url), title, trigger))
    return [candidate for candidate in result if candidate.source_item_id not in skipped]


def _canonical_item(
    prior: SourceItem,
    merged: dict[str, Any],
    *,
    trigger: str,
    evidence_id: str,
    final_url: str,
    collected_at: datetime,
) -> GubaSourceItem:
    metadata = detail_body_metadata(
        merged["source_metadata"],
        title=prior.title,
        trigger=trigger,
        from_observation_version=prior.observation_version,
    )
    final_urls = dict(prior.source_metadata.get("final_urls") or {})
    final_urls["detail"] = final_url
    metadata["final_urls"] = final_urls
    return GubaSourceItem(
        source=SOURCE,
        schema_version=SCHEMA_VERSION,
        source_item_id=merged["source_item_id"],
        requested_bar_code=prior.requested_bar_code,
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
        source_metadata=metadata,
        raw_ref={"detail": evidence_id},
        observation_version=prior.observation_version + 1,
        final_url=final_url,
    )


def execute_detail_enrichment(
    *,
    db_path: str | Path,
    stock_code: str,
    transport: Any,
    raw_data_dir: str | Path | None = None,
    clock: Callable[[], object] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    jitter_fn: Callable[[float, float], float] = random.uniform,
    min_delay: float = 3.0,
    max_delay: float = 10.0,
    challenge_wait_seconds: float = 180.0,
    challenge_retries: int = 3,
    # Required, deliberately, and keyword-only. The run log is an append-only
    # production ledger (`runtime/logs/eastmoney-detail-enrichment.jsonl`); a
    # silent default pointed at that file meant every caller that omitted it --
    # including the unit tests -- appended into the real ledger. Measured
    # 2026-09-23: one full `pytest` run grew the production log by 13 rows.
    # Making the argument mandatory turns "I forgot" into a TypeError at the call
    # site instead of quiet contamination of a file used to audit the source.
    log_path: str | Path,
    limit: int | None = None,
    include_short_titles: bool = False,
    acquisition_mode: str = "existing-chrome",
    profile_path: str | None = None,
    profile_mode: str | None = None,
) -> dict[str, object]:
    """Enrich one storage contract without mutating canonical observations.

    Legacy ``posts`` databases retain their historical in-place update. A
    canonical database instead appends an immutable observation version and
    records the exact detail document as raw evidence.
    """
    path = Path(db_path)
    tables = _table_names(path)
    canonical = "source_item_observations" in tables
    legacy = "posts" in tables
    if canonical == legacy:
        raise ValueError(
            "collector database must contain exactly one supported observation contract"
        )

    skip_ledger = _SkipLedger(_skip_ledger_path(path))
    known_missing = skip_ledger.skipped_ids(SOURCE)

    persistence: SQLitePersistence | None = None
    legacy_store: SimplePostStore | None = None
    if canonical:
        persistence = SQLitePersistence(
            path,
            RawEvidenceStore(raw_data_dir or path.parent, SOURCE),
        )
        candidates = _canonical_candidates(
            persistence,
            stock_code,
            include_short_titles=include_short_titles,
            skipped=known_missing,
        )
        storage_mode = "canonical_observations"
    else:
        legacy_store = SimplePostStore(path)
        candidates = _legacy_candidates(
            legacy_store,
            stock_code,
            include_short_titles=include_short_titles,
            skipped=known_missing,
        )
        storage_mode = "legacy_posts"
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        candidates = candidates[:limit]

    requested = len(candidates)
    run_id = uuid.uuid4().hex
    scope_key = f"stock:{stock_code}"
    log_file = Path(log_path)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    seq = 0
    access_blocks = 0
    success_since_last_block = 0
    last_block_monotonic: float | None = None
    windows: list[dict[str, object]] = []
    current_window: dict[str, object] | None = None
    counters = RuntimeCounters(details_requested=requested if canonical else 0)
    attempt_ordinal = 0

    if persistence is not None:
        persistence.start_run(
            run_id,
            SOURCE,
            scope_key,
            started_at=_utc_now(clock),
            collector_version="eastmoney_guba.detail_enrichment.v1",
            parser_version=SCHEMA_VERSION,
            schema_version=SCHEMA_VERSION,
            counters=counters,
        )

    def write_log(
        source_item_id: str,
        result: str,
        started: float,
        *,
        event: str | None = None,
        **extra: object,
    ) -> None:
        nonlocal seq
        seq += 1
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "run_id": run_id,
            "seq": seq,
            "source_item_id": str(source_item_id),
            "sleep_before_sec": None,
            "request_duration_sec": round(time.monotonic() - started, 3),
            "result": result,
            "success_since_last_block": success_since_last_block,
            "acquisition_mode": acquisition_mode,
            "profile_path": profile_path,
            "profile_mode": profile_mode,
            "elapsed_since_last_block_sec": (
                round(time.monotonic() - last_block_monotonic, 3)
                if last_block_monotonic is not None
                else None
            ),
        }
        if event is not None:
            row["event"] = event
        row.update(extra)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    def persist_response(
        candidate: _Candidate,
        response: Any,
        *,
        started_at: datetime,
        retry_number: int,
        request_kind: str = "detail_enrichment",
    ) -> str | None:
        nonlocal attempt_ordinal
        if persistence is None:
            return None
        payload = _body(response)
        finished_at = _utc_now(clock)
        status = getattr(response, "http_status", getattr(response, "status_code", None))
        outcome = "success" if status is None or 200 <= int(status) < 300 else "http_error"
        attempt_id = f"{run_id}-attempt-{attempt_ordinal}"
        evidence_id = f"{run_id}-evidence-{attempt_ordinal}"
        ordinal = attempt_ordinal
        attempt_ordinal += 1
        counters.requests_total += 1
        if outcome == "success":
            counters.requests_success += 1
        else:
            counters.requests_failed += 1
        persistence.record_attempt(
            run_id,
            attempt_id,
            ordinal=ordinal,
            request_kind=request_kind,
            request_url=candidate.url,
            started_at=started_at,
            finished_at=finished_at,
            outcome=outcome,
            retry_number=retry_number,
            retry_budget=max(1, challenge_retries + 1),
            http_status=status,
        )
        published = persistence.raw_store.publish(run_id, ordinal, payload)
        final_url = str(
            getattr(response, "observed_url", None)
            or getattr(response, "final_url", None)
            or candidate.url
        )
        content_type = getattr(response, "content_type", None)
        if content_type is None:
            headers = getattr(response, "headers", {}) or {}
            content_type = headers.get("content-type") or headers.get("Content-Type")
        capture_method = str(getattr(response, "capture_method", "http_response"))
        persistence.record_raw_evidence(
            run_id,
            attempt_id,
            evidence_id,
            published,
            evidence_kind=f"detail:{capture_method}",
            request_url=candidate.url,
            final_url=final_url,
            fetched_at=getattr(response, "fetched_at", finished_at),
            http_status=status,
            content_type=content_type,
        )
        return evidence_id

    success = 0
    legacy_filled = 0
    canonical_versioned = 0
    skipped_not_found = 0
    failures: list[dict[str, str]] = []
    samples: list[dict[str, object]] = []
    stopped = False
    try:
        for index, candidate in enumerate(candidates):
            sleep_before = 0.0
            if index and max_delay > 0:
                sleep_before = jitter_fn(min_delay, max_delay)
                sleep_fn(sleep_before)
            request_started = time.monotonic()
            latest_evidence_id: str | None = None
            latest_response: Any = None
            last_challenge: list[str] = []
            try:
                html = ""
                for attempt in range(max(1, challenge_retries + 1)):
                    request_started = time.monotonic()
                    started_at = _utc_now(clock)
                    try:
                        latest_response = transport.get(candidate.url, timeout=30.0)
                    except Exception as exc:
                        if persistence is not None:
                            attempt_id = f"{run_id}-attempt-{attempt_ordinal}"
                            persistence.record_attempt(
                                run_id,
                                attempt_id,
                                ordinal=attempt_ordinal,
                                request_kind="detail_enrichment",
                                request_url=candidate.url,
                                started_at=started_at,
                                finished_at=_utc_now(clock),
                                outcome="transport_error",
                                retry_number=attempt + 1,
                                retry_budget=max(1, challenge_retries + 1),
                                error_class=type(exc).__name__,
                                error_message=str(exc),
                            )
                            attempt_ordinal += 1
                            counters.requests_total += 1
                            counters.requests_failed += 1
                        raise
                    latest_evidence_id = persist_response(
                        candidate,
                        latest_response,
                        started_at=started_at,
                        retry_number=attempt + 1,
                    )
                    body = _body(latest_response)
                    html = body.decode("utf-8", errors="replace")
                    status = getattr(
                        latest_response,
                        "http_status",
                        getattr(latest_response, "status_code", None),
                    )
                    if status is not None and not 200 <= int(status) < 300:
                        raise _DetailFetchFailure(
                            f"http_{status}", f"detail request returned HTTP {status}"
                        )
                    last_challenge = _live_challenge_reasons(transport, latest_response)
                    asset_blocked = is_access_block_page(html)
                    if not asset_blocked and not last_challenge:
                        break
                    access_blocks += 1
                    write_log(
                        candidate.source_item_id,
                        "access_block",
                        request_started,
                        sleep_before_sec=round(sleep_before, 3),
                        # Both oracles are recorded separately on purpose: after a
                        # block it must be possible to tell "the response bytes
                        # matched the known shell" from "only the rendered page
                        # gave it away", because only the second is new evidence.
                        asset_signature_blocked=asset_blocked,
                        challenge_reasons=last_challenge or None,
                        challenge_tier=_challenge_tier(last_challenge),
                    )
                    now = time.monotonic()
                    if current_window is not None:
                        current_window["elapsed_seconds"] = round(
                            now - float(current_window["_started_monotonic"]), 3
                        )
                        current_window.pop("_started_monotonic", None)
                        windows.append(current_window)
                    current_window = {
                        "success_count": success_since_last_block,
                        "_started_monotonic": now,
                    }
                    success_since_last_block = 0
                    last_block_monotonic = now
                    if attempt >= challenge_retries:
                        break
                    print(
                        f"access block for {candidate.source_item_id}; complete visible Chrome verification "
                        f"within {challenge_wait_seconds:.0f}s; polling current DOM every 5s",
                        file=sys.stderr,
                        flush=True,
                    )
                    manual = wait_for_manual_verification(
                        transport,
                        timeout_seconds=challenge_wait_seconds,
                        sleep_fn=sleep_fn,
                    )
                    if manual is not None:
                        latest_response = manual
                        latest_evidence_id = persist_response(
                            candidate,
                            manual,
                            started_at=_utc_now(clock),
                            retry_number=attempt + 1,
                            request_kind="detail_manual_verification",
                        )
                        html = _body(manual).decode("utf-8", errors="replace")
                        write_log(
                            candidate.source_item_id,
                            "manual_verification_resumed",
                            request_started,
                            event="manual_verification_resumed",
                        )
                    if manual is not None:
                        last_challenge = _live_challenge_reasons(transport, latest_response)
                    if html and not is_access_block_page(html) and not last_challenge:
                        break

                if is_access_block_page(html) or last_challenge:
                    raise _DetailFetchFailure(
                        "access_block",
                        "Eastmoney detail remained behind verification"
                        + (f" [{' ; '.join(last_challenge)}]" if last_challenge else ""),
                    )
                if is_not_found_page(html):
                    raise _DetailFetchFailure(
                        SKIP_REASON_NOT_FOUND,
                        "Eastmoney detail returned the not-found shell",
                    )
                detail = parse_detail_page(html)
                if detail.source_item_id != candidate.source_item_id:
                    raise GubaDetailMismatch("list/detail source_item_id mismatch")
                if not detail.content.strip():
                    raise GubaParseError("detail body is empty")

                if candidate.canonical_item is not None:
                    if persistence is None or latest_evidence_id is None:
                        raise RuntimeError("canonical detail evidence was not persisted")
                    merged = merge_list_and_detail(candidate.canonical_item, detail)
                    final_url = str(
                        getattr(latest_response, "observed_url", None)
                        or getattr(latest_response, "final_url", None)
                        or candidate.url
                    )
                    item = _canonical_item(
                        candidate.canonical_item,
                        merged,
                        trigger=candidate.trigger,
                        evidence_id=latest_evidence_id,
                        final_url=final_url,
                        collected_at=_utc_now(clock),
                    )
                    _observation_id, _version, created = persistence.record_observation(
                        run_id,
                        item,
                        scope_key=scope_key,
                        evidence_links=[(latest_evidence_id, "detail")],
                        collector_version="eastmoney_guba.detail_enrichment.v1",
                        parser_version=SCHEMA_VERSION,
                    )
                    if not created:
                        raise RuntimeError("detail enrichment did not create a new observation version")
                    canonical_versioned += 1
                    counters.details_success += 1
                    counters.records_parsed += 1
                else:
                    if legacy_store is None:
                        raise RuntimeError("legacy store is unavailable")
                    if not legacy_store.update_content(
                        SOURCE, candidate.source_item_id, detail.content
                    ):
                        raise RuntimeError("legacy post disappeared during enrichment")
                    legacy_filled += 1

                success += 1
                success_since_last_block += 1
                write_log(
                    candidate.source_item_id,
                    "success",
                    request_started,
                    sleep_before_sec=round(sleep_before, 3),
                    trigger=candidate.trigger,
                )
                if len(samples) < 10:
                    samples.append(
                        {
                            "source_item_id": candidate.source_item_id,
                            "title": candidate.title,
                            "title_length": normalized_title_length(candidate.title),
                            "content": detail.content,
                            "content_length": len(detail.content),
                            "url": candidate.url,
                            "trigger": candidate.trigger,
                        }
                    )
            except Exception as exc:
                reason = _failure_reason(exc)
                result = (
                    "fetch_failure"
                    if isinstance(exc, (ExistingChromeAcquisitionError, _DetailFetchFailure))
                    else "parse_failure"
                )
                write_log(
                    candidate.source_item_id,
                    result,
                    request_started,
                    sleep_before_sec=round(sleep_before, 3),
                    failure_reason=reason,
                )
                failures.append(
                    {"source_item_id": candidate.source_item_id, "reason": reason}
                )
                if reason == SKIP_REASON_NOT_FOUND:
                    skip_ledger.record(
                        source=SOURCE,
                        source_item_id=candidate.source_item_id,
                        stock_code=stock_code,
                        reason=reason,
                        observed_at=_utc_now(clock),
                    )
                    skipped_not_found += 1
                if candidate.canonical_item is not None and persistence is not None:
                    counters.details_failed += 1
                    counters.records_failed += 1
                    persistence.record_failure(
                        run_id,
                        uuid.uuid4().hex,
                        phase="detail_enrichment",
                        failure_class=reason,
                        occurred_at=_utc_now(clock),
                        message=str(exc),
                        evidence_id=latest_evidence_id,
                    )
                if reason in {"access_block", "challenge", "browser_blocked"}:
                    stopped = True
                    break

        if persistence is not None:
            if failures and success:
                status = "PARTIAL_COLLECTION"
            elif failures:
                status = "COLLECTION_FAILED"
            elif not candidates:
                status = "NO_NEW_DATA"
            else:
                status = "SUCCESS"
            persistence.finish_run(
                run_id,
                status=status,
                finished_at=_utc_now(clock),
                counters=counters,
            )

        refreshed_skips = skip_ledger.skipped_ids(SOURCE)
        if canonical:
            remaining = len(
                _canonical_candidates(
                    persistence,
                    stock_code,
                    include_short_titles=include_short_titles,
                    skipped=refreshed_skips,
                )
            )
        else:
            remaining = len(
                _legacy_candidates(
                    legacy_store,
                    stock_code,
                    include_short_titles=include_short_titles,
                    skipped=refreshed_skips,
                )
            )
        if current_window is not None:
            current_window["elapsed_seconds"] = round(
                time.monotonic() - float(current_window["_started_monotonic"]), 3
            )
            current_window.pop("_started_monotonic", None)
            windows.append(current_window)
        return {
            "run_id": run_id,
            "storage_mode": storage_mode,
            "requested": requested,
            "success": success,
            "failed": len(failures),
            "content_filled": legacy_filled,
            "canonical_observations_versioned": canonical_versioned,
            "candidates_remaining": int(remaining),
            "stopped": stopped,
            "access_block_count": access_blocks,
            "skipped_not_found_added": skipped_not_found,
            "challenge_windows": windows,
            "jsonl_path": str(log_file),
            "acquisition_mode": acquisition_mode,
            # Read off the transport rather than echoed from a CLI flag: this is
            # evidence of how the run was actually configured, which is what a
            # reader needs in order to interpret the counters above.
            "detail_referer_from_list": bool(
                getattr(transport, "detail_referer_from_list", False)
            ),
            "include_short_titles": include_short_titles,
            "failures": failures,
            "samples": samples,
        }
    finally:
        if persistence is not None:
            persistence.close()
        if legacy_store is not None:
            legacy_store.close()
