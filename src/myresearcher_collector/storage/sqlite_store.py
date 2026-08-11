"""Minimal SQLite persistence boundary for Collector runtime results."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from myresearcher_collector.models import RuntimeCounters, SourceItem

from .models import PublishedRaw, SafeFrontier
from .raw_store import RawEvidenceStore
from .schema import connect_database, utc_text


TERMINAL_STATUSES = {
    "SUCCESS", "NO_NEW_DATA", "PARTIAL_COLLECTION", "COLLECTION_FAILED",
    "SPEC_MISMATCH", "CANCELLED",
}


class PersistenceError(RuntimeError):
    """A persistence operation cannot satisfy the storage contract."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _counters(value: RuntimeCounters | Mapping[str, int] | None) -> dict[str, int]:
    if value is None:
        return {}
    result = value.as_dict() if isinstance(value, RuntimeCounters) else dict(value)
    for name, count in result.items():
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError(f"counter {name} must be a non-negative integer")
    return result


def _optional_utc(value: datetime | str | None) -> str | None:
    return None if value is None else utc_text(value)


def _utc_order(value: str) -> datetime:
    """Parse the canonical UTC text used by the persistence schema."""
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _item_fingerprint(item: SourceItem) -> str:
    metadata = dict(item.source_metadata)
    metadata.pop("final_urls", None)
    canonical = {
        "source": item.source,
        "source_item_id": item.source_item_id,
        "canonical_bar_code": item.canonical_bar_code,
        "canonical_bar_name": item.canonical_bar_name,
        "author_id": item.author_id,
        "author_name": item.author_name,
        "title": item.title,
        "content": item.content,
        "published_at": utc_text(item.published_at),
        "last_updated_at": _optional_utc(item.last_updated_at),
        "display_time": _optional_utc(item.display_time),
        "url": item.url,
        "post_type": item.post_type,
        "post_state": item.post_state,
        "post_top_status": item.post_top_status,
        "read_count": item.read_count,
        "reply_count": item.reply_count,
        "like_count": item.like_count,
        "forward_count": item.forward_count,
        "source_post_id": item.source_post_id,
        "source_times_raw": item.source_times_raw,
        "source_metadata": metadata,
    }
    return hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()


class SQLitePersistence:
    """Small explicit API; callers never need to compose SQL."""

    def __init__(self, db_path: str | Path, raw_store: RawEvidenceStore) -> None:
        self.db_path = Path(db_path)
        self.raw_store = raw_store
        self.conn = connect_database(self.db_path)
        self._tx_depth = 0

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self):
        """Group attempts, evidence, observations and terminal state atomically."""
        if self._tx_depth:
            yield self
            return
        self._tx_depth = 1
        try:
            yield self
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            self._tx_depth = 0

    def _write(self):
        return self.transaction()

    def start_run(
        self,
        run_id: str,
        source: str,
        scope_key: str,
        *,
        started_at: datetime | str,
        collector_version: str,
        parser_version: str,
        schema_version: str,
        watermark_before: datetime | str | None = None,
        counters: RuntimeCounters | Mapping[str, int] | None = None,
    ) -> None:
        started = utc_text(started_at)
        before = _optional_utc(watermark_before)
        with self._write():
            self.conn.execute(
                """INSERT INTO collection_runs(
                    run_id, source, scope_key, status, started_at_utc,
                    collector_version, parser_version, schema_version,
                    watermark_before_utc, counters_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (run_id, source, scope_key, "RUNNING", started, collector_version,
                 parser_version, schema_version, before, _json(_counters(counters))),
            )

    def record_attempt(
        self,
        run_id: str,
        attempt_id: str,
        *,
        ordinal: int,
        request_kind: str,
        request_url: str,
        started_at: datetime | str,
        finished_at: datetime | str | None,
        outcome: str,
        retry_number: int,
        retry_budget: int,
        http_status: int | None = None,
        retry_after_seconds: float | None = None,
        error_class: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if ordinal < 0 or retry_number < 1 or retry_budget < 1:
            raise ValueError("attempt ordinals and retry values must be positive ranges")
        with self._write():
            self.conn.execute(
                """INSERT INTO collection_attempts(
                    attempt_id, run_id, attempt_ordinal, request_kind, request_url,
                    started_at_utc, finished_at_utc, outcome, http_status,
                    retry_after_seconds, retry_number, retry_budget, error_class,
                    error_message
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (attempt_id, run_id, ordinal, request_kind, request_url,
                 utc_text(started_at), _optional_utc(finished_at), outcome,
                 http_status, retry_after_seconds, retry_number, retry_budget,
                 error_class, error_message),
            )

    def record_raw_evidence(
        self,
        run_id: str,
        attempt_id: str,
        evidence_id: str,
        published: PublishedRaw,
        *,
        evidence_kind: str,
        request_url: str,
        final_url: str | None,
        fetched_at: datetime | str,
        http_status: int | None,
        content_type: str | None,
        storage_version: str = "raw.v1",
    ) -> None:
        # Verification occurs before opening the SQLite transaction/reference.
        self.raw_store.verify(str(published.relative_path), published.sha256, published.byte_size)
        with self._write():
            attempt = self.conn.execute(
                "SELECT run_id FROM collection_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if attempt is None or attempt[0] != run_id:
                raise PersistenceError("attempt does not belong to run")
            self.conn.execute(
                """INSERT INTO raw_evidence(
                    evidence_id, run_id, attempt_id, evidence_kind, request_url,
                    final_url, fetched_at_utc, http_status, content_type,
                    content_sha256, byte_size, filesystem_path, storage_version
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (evidence_id, run_id, attempt_id, evidence_kind, request_url,
                 final_url, utc_text(fetched_at), http_status, content_type,
                 published.sha256, published.byte_size, published.relative_path,
                 storage_version),
            )

    def record_failure(
        self,
        run_id: str,
        failure_id: str,
        *,
        phase: str,
        failure_class: str,
        occurred_at: datetime | str,
        message: str,
        attempt_id: str | None = None,
        evidence_id: str | None = None,
    ) -> None:
        with self._write():
            self.conn.execute(
                """INSERT INTO collection_failures(
                    failure_id, run_id, attempt_id, evidence_id, phase,
                    failure_class, occurred_at_utc, message
                ) VALUES (?,?,?,?,?,?,?,?)""",
                (failure_id, run_id, attempt_id, evidence_id, phase,
                 failure_class, utc_text(occurred_at), message),
            )

    def record_observation(
        self,
        run_id: str,
        item: SourceItem,
        *,
        scope_key: str,
        evidence_links: Iterable[tuple[str, str]],
        observation_id: str | None = None,
        collector_version: str = "unknown",
        parser_version: str | None = None,
    ) -> tuple[str, int, bool]:
        """Persist or link an observation; return ``(id, version, created)``."""
        links = list(evidence_links)
        if not links:
            raise PersistenceError("observation requires supporting evidence")
        fingerprint = _item_fingerprint(item)
        with self._write():
            run = self.conn.execute(
                "SELECT source, status FROM collection_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run is None or run[0] != item.source or run[1] != "RUNNING":
                raise PersistenceError("observations require a running run")
            for evidence_id, role in links:
                evidence = self.conn.execute(
                    "SELECT run_id FROM raw_evidence WHERE evidence_id = ?",
                    (evidence_id,),
                ).fetchone()
                if evidence is None or evidence[0] != run_id or not role.strip():
                    raise PersistenceError("supporting evidence is missing or unrelated")

            latest = self.conn.execute(
                """SELECT observation_id, observation_version, fact_fingerprint
                   FROM source_item_observations
                   WHERE source = ? AND source_item_id = ?
                   ORDER BY observation_version DESC LIMIT 1""",
                (item.source, item.source_item_id),
            ).fetchone()
            created = latest is None or latest[2] != fingerprint
            if created:
                version = 1 if latest is None else int(latest[1]) + 1
                previous_id = None if latest is None else latest[0]
                if observation_id is None:
                    observation_id = hashlib.sha256(
                        f"observation-v1\n{run_id}\n{item.source}\n{item.source_item_id}\n{version}".encode()
                    ).hexdigest()
                metadata = dict(item.source_metadata)
                metadata.setdefault("source_post_id", item.source_post_id)
                self.conn.execute(
                    """INSERT INTO source_item_observations(
                        observation_id, source, source_item_id, observation_version,
                        observed_at_utc, published_at_utc, source_updated_at_utc,
                        display_time_utc, author_id, author_name, title, content,
                        content_sha256, url, canonical_bar_code, canonical_bar_name,
                        post_type, post_state, post_top_status, read_count,
                        reply_count, like_count, forward_count, source_times_raw_json,
                        source_metadata_json, fact_fingerprint, schema_version,
                        collector_version, parser_version, drift_from_observation_id
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        observation_id, item.source, item.source_item_id, version,
                        utc_text(item.collected_at), utc_text(item.published_at),
                        _optional_utc(item.last_updated_at), _optional_utc(item.display_time),
                        item.author_id, item.author_name, item.title, item.content,
                        hashlib.sha256(item.content.encode("utf-8")).hexdigest(), item.url,
                        item.canonical_bar_code, item.canonical_bar_name, item.post_type,
                        item.post_state, item.post_top_status, item.read_count,
                        item.reply_count, item.like_count, item.forward_count,
                        _json(item.source_times_raw), _json(metadata), fingerprint,
                        item.schema_version, collector_version,
                        parser_version or item.schema_version, previous_id,
                    ),
                )
            else:
                observation_id, version = latest[0], int(latest[1])

            self.conn.executemany(
                "INSERT OR IGNORE INTO observation_evidence(observation_id, evidence_id, evidence_role) VALUES (?,?,?)",
                [(observation_id, evidence_id, role) for evidence_id, role in links],
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO observation_scopes(observation_id, scope_key, requested_bar_code) VALUES (?,?,?)",
                (observation_id, scope_key, item.requested_bar_code),
            )
        return observation_id, version, created

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: datetime | str,
        counters: RuntimeCounters | Mapping[str, int] | None = None,
        safe_frontier: SafeFrontier | None = None,
    ) -> bool:
        if status not in TERMINAL_STATUSES:
            raise ValueError("invalid terminal status")
        finish = utc_text(finished_at)
        counts = _json(_counters(counters))
        advance = False
        frontier_text = None
        if safe_frontier is not None:
            frontier_text = utc_text(safe_frontier.watermark_utc)
            advance = (
                status in {"SUCCESS", "NO_NEW_DATA", "PARTIAL_COLLECTION"}
                and safe_frontier.all_required_persisted
                and not safe_frontier.unresolved_gaps
            )
        with self._write():
            current = self.conn.execute(
                "SELECT status, source, scope_key FROM collection_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if current is None or current[0] != "RUNNING":
                raise PersistenceError("run is missing or already terminal")
            if advance:
                existing = self.conn.execute(
                    "SELECT watermark_utc FROM collector_checkpoints "
                    "WHERE source=? AND scope_key=?",
                    (current[1], current[2]),
                ).fetchone()
                if existing is not None and _utc_order(frontier_text) < _utc_order(existing[0]):
                    raise PersistenceError("checkpoint frontier regression")
                self.conn.execute(
                    """INSERT INTO collector_checkpoints(source, scope_key, watermark_utc, last_safe_run_id, updated_at_utc)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(source, scope_key) DO UPDATE SET
                         watermark_utc=excluded.watermark_utc,
                         last_safe_run_id=excluded.last_safe_run_id,
                         updated_at_utc=excluded.updated_at_utc""",
                    (current[1], current[2], frontier_text, run_id, finish),
                )
            self.conn.execute(
                """UPDATE collection_runs SET status=?, finished_at_utc=?,
                   watermark_after_utc=?, safe_frontier_utc=?, counters_json=?
                   WHERE run_id=?""",
                (status, finish, frontier_text if advance else None,
                 frontier_text if safe_frontier is not None else None, counts, run_id),
            )
        return advance

    def persist_result(
        self,
        run_id: str,
        observations: Iterable[tuple[SourceItem, str, Iterable[tuple[str, str]]]],
        *,
        status: str,
        finished_at: datetime | str,
        counters: RuntimeCounters | Mapping[str, int] | None = None,
        safe_frontier: SafeFrontier | None = None,
    ) -> tuple[list[tuple[str, int, bool]], bool]:
        """Atomically append observations and finalize run/checkpoint state.

        Raw files and their evidence rows must already have been published by
        the caller; this method makes the observation/finalization boundary
        one SQLite transaction.
        """
        with self.transaction():
            created = [
                self.record_observation(
                    run_id,
                    observation,
                    scope_key=scope_key,
                    evidence_links=evidence_links,
                )
                for observation, scope_key, evidence_links in observations
            ]
            advanced = self.finish_run(
                run_id,
                status=status,
                finished_at=finished_at,
                counters=counters,
                safe_frontier=safe_frontier,
            )
        return created, advanced

    def checkpoint(self, source: str, scope_key: str) -> tuple[str | None, str | None] | None:
        return self.conn.execute(
            "SELECT watermark_utc, last_safe_run_id FROM collector_checkpoints WHERE source=? AND scope_key=?",
            (source, scope_key),
        ).fetchone()

    def known_item_ids(self, source: str, scope_key: str) -> set[str]:
        """Return source identities already observed in a requested scope."""
        rows = self.conn.execute(
            """SELECT DISTINCT o.source_item_id
               FROM source_item_observations AS o
               JOIN observation_scopes AS s ON s.observation_id = o.observation_id
               WHERE o.source=? AND s.scope_key=?""",
            (source, scope_key),
        ).fetchall()
        return {row[0] for row in rows}

    def verify_evidence(self, evidence_id: str) -> Path:
        row = self.conn.execute(
            "SELECT filesystem_path, content_sha256, byte_size FROM raw_evidence WHERE evidence_id=?",
            (evidence_id,),
        ).fetchone()
        if row is None:
            raise PersistenceError("evidence row does not exist")
        try:
            return self.raw_store.verify(row[0], row[1], row[2])
        except Exception as exc:
            raise PersistenceError("referenced raw evidence failed integrity check") from exc
