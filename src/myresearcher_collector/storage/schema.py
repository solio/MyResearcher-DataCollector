"""Explicit SQLite schema creation and drift validation."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

SCHEMA_VERSION = 2

UTC_GLOB = "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'"

MIGRATION_SQL = f"""
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at_utc TEXT NOT NULL CHECK (
        length(applied_at_utc) = 27 AND applied_at_utc GLOB {UTC_GLOB}
    ),
    checksum TEXT NOT NULL
);

CREATE TABLE collection_runs (
    run_id TEXT PRIMARY KEY CHECK (length(trim(run_id)) > 0),
    source TEXT NOT NULL CHECK (length(trim(source)) > 0),
    scope_key TEXT NOT NULL CHECK (length(trim(scope_key)) > 0),
    status TEXT NOT NULL CHECK (status IN ('RUNNING','SUCCESS','NO_NEW_DATA','PARTIAL_COLLECTION','COLLECTION_FAILED','SPEC_MISMATCH','CANCELLED')),
    started_at_utc TEXT NOT NULL CHECK (length(started_at_utc) = 27 AND started_at_utc GLOB {UTC_GLOB}),
    finished_at_utc TEXT CHECK (finished_at_utc IS NULL OR (length(finished_at_utc) = 27 AND finished_at_utc GLOB {UTC_GLOB})),
    collector_version TEXT NOT NULL CHECK (length(trim(collector_version)) > 0),
    parser_version TEXT NOT NULL CHECK (length(trim(parser_version)) > 0),
    schema_version TEXT NOT NULL CHECK (length(trim(schema_version)) > 0),
    watermark_before_utc TEXT CHECK (watermark_before_utc IS NULL OR (length(watermark_before_utc) = 27 AND watermark_before_utc GLOB {UTC_GLOB})),
    watermark_after_utc TEXT CHECK (watermark_after_utc IS NULL OR (length(watermark_after_utc) = 27 AND watermark_after_utc GLOB {UTC_GLOB})),
    safe_frontier_utc TEXT CHECK (safe_frontier_utc IS NULL OR (length(safe_frontier_utc) = 27 AND safe_frontier_utc GLOB {UTC_GLOB})),
    counters_json TEXT NOT NULL CHECK (length(trim(counters_json)) > 0)
);

CREATE TABLE collection_attempts (
    attempt_id TEXT PRIMARY KEY CHECK (length(trim(attempt_id)) > 0),
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
    attempt_ordinal INTEGER NOT NULL CHECK (attempt_ordinal >= 0),
    request_kind TEXT NOT NULL CHECK (length(trim(request_kind)) > 0),
    request_url TEXT NOT NULL CHECK (length(trim(request_url)) > 0),
    started_at_utc TEXT NOT NULL CHECK (length(started_at_utc) = 27 AND started_at_utc GLOB {UTC_GLOB}),
    finished_at_utc TEXT CHECK (finished_at_utc IS NULL OR (length(finished_at_utc) = 27 AND finished_at_utc GLOB {UTC_GLOB})),
    outcome TEXT NOT NULL CHECK (outcome IN ('success','http_error','timeout','transport_error','redirect_rejected','cancelled')),
    http_status INTEGER CHECK (http_status IS NULL OR http_status >= 100),
    retry_after_seconds REAL CHECK (retry_after_seconds IS NULL OR retry_after_seconds >= 0),
    retry_number INTEGER NOT NULL CHECK (retry_number >= 1),
    retry_budget INTEGER NOT NULL CHECK (retry_budget >= 1),
    error_class TEXT,
    error_message TEXT,
    UNIQUE(run_id, attempt_ordinal)
);

CREATE TABLE raw_evidence (
    evidence_id TEXT PRIMARY KEY CHECK (length(trim(evidence_id)) > 0),
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
    attempt_id TEXT NOT NULL REFERENCES collection_attempts(attempt_id),
    evidence_kind TEXT NOT NULL CHECK (length(trim(evidence_kind)) > 0),
    request_url TEXT NOT NULL CHECK (length(trim(request_url)) > 0),
    final_url TEXT,
    fetched_at_utc TEXT NOT NULL CHECK (length(fetched_at_utc) = 27 AND fetched_at_utc GLOB {UTC_GLOB}),
    http_status INTEGER CHECK (http_status IS NULL OR http_status >= 100),
    content_type TEXT,
    content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    filesystem_path TEXT NOT NULL CHECK (length(trim(filesystem_path)) > 0),
    storage_version TEXT NOT NULL CHECK (length(trim(storage_version)) > 0)
);

CREATE TABLE raw_body_state (
    source TEXT NOT NULL CHECK (length(trim(source)) > 0),
    content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
    body_state TEXT NOT NULL CHECK (body_state IN ('PRESENT','PURGED')),
    purged_at_utc TEXT CHECK (purged_at_utc IS NULL OR (length(purged_at_utc) = 27 AND purged_at_utc GLOB {UTC_GLOB})),
    updated_at_utc TEXT NOT NULL CHECK (length(updated_at_utc) = 27 AND updated_at_utc GLOB {UTC_GLOB}),
    PRIMARY KEY(source, content_sha256)
);

CREATE TABLE source_item_observations (
    observation_id TEXT PRIMARY KEY CHECK (length(trim(observation_id)) > 0),
    source TEXT NOT NULL CHECK (length(trim(source)) > 0),
    source_item_id TEXT NOT NULL CHECK (length(trim(source_item_id)) > 0),
    observation_version INTEGER NOT NULL CHECK (observation_version >= 1),
    observed_at_utc TEXT NOT NULL CHECK (length(observed_at_utc) = 27 AND observed_at_utc GLOB {UTC_GLOB}),
    published_at_utc TEXT NOT NULL CHECK (length(published_at_utc) = 27 AND published_at_utc GLOB {UTC_GLOB}),
    source_updated_at_utc TEXT CHECK (source_updated_at_utc IS NULL OR (length(source_updated_at_utc) = 27 AND source_updated_at_utc GLOB {UTC_GLOB})),
    display_time_utc TEXT CHECK (display_time_utc IS NULL OR (length(display_time_utc) = 27 AND display_time_utc GLOB {UTC_GLOB})),
    author_id TEXT,
    author_name TEXT,
    title TEXT,
    content TEXT NOT NULL,
    content_sha256 TEXT,
    url TEXT NOT NULL CHECK (length(trim(url)) > 0),
    canonical_bar_code TEXT,
    canonical_bar_name TEXT,
    post_type INTEGER NOT NULL CHECK (post_type >= 0),
    post_state INTEGER,
    post_top_status INTEGER,
    read_count INTEGER CHECK (read_count IS NULL OR read_count >= 0),
    reply_count INTEGER CHECK (reply_count IS NULL OR reply_count >= 0),
    like_count INTEGER CHECK (like_count IS NULL OR like_count >= 0),
    forward_count INTEGER CHECK (forward_count IS NULL OR forward_count >= 0),
    source_times_raw_json TEXT NOT NULL,
    source_metadata_json TEXT NOT NULL,
    fact_fingerprint TEXT NOT NULL CHECK (length(fact_fingerprint) = 64),
    schema_version TEXT NOT NULL CHECK (length(trim(schema_version)) > 0),
    collector_version TEXT NOT NULL CHECK (length(trim(collector_version)) > 0),
    parser_version TEXT NOT NULL CHECK (length(trim(parser_version)) > 0),
    drift_from_observation_id TEXT REFERENCES source_item_observations(observation_id),
    UNIQUE(source, source_item_id, observation_version)
);

CREATE TABLE observation_evidence (
    observation_id TEXT NOT NULL REFERENCES source_item_observations(observation_id),
    evidence_id TEXT NOT NULL REFERENCES raw_evidence(evidence_id),
    evidence_role TEXT NOT NULL CHECK (length(trim(evidence_role)) > 0),
    PRIMARY KEY(observation_id, evidence_id, evidence_role)
);

CREATE TABLE observation_scopes (
    observation_id TEXT NOT NULL REFERENCES source_item_observations(observation_id),
    scope_key TEXT NOT NULL CHECK (length(trim(scope_key)) > 0),
    requested_bar_code TEXT NOT NULL CHECK (length(trim(requested_bar_code)) > 0),
    PRIMARY KEY(observation_id, scope_key)
);

CREATE TABLE collection_failures (
    failure_id TEXT PRIMARY KEY CHECK (length(trim(failure_id)) > 0),
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
    attempt_id TEXT REFERENCES collection_attempts(attempt_id),
    evidence_id TEXT REFERENCES raw_evidence(evidence_id),
    phase TEXT NOT NULL CHECK (length(trim(phase)) > 0),
    failure_class TEXT NOT NULL CHECK (length(trim(failure_class)) > 0),
    occurred_at_utc TEXT NOT NULL CHECK (length(occurred_at_utc) = 27 AND occurred_at_utc GLOB {UTC_GLOB}),
    message TEXT NOT NULL
);

CREATE TABLE collector_checkpoints (
    source TEXT NOT NULL CHECK (length(trim(source)) > 0),
    scope_key TEXT NOT NULL CHECK (length(trim(scope_key)) > 0),
    watermark_utc TEXT CHECK (watermark_utc IS NULL OR (length(watermark_utc) = 27 AND watermark_utc GLOB {UTC_GLOB})),
    last_safe_run_id TEXT REFERENCES collection_runs(run_id),
    updated_at_utc TEXT NOT NULL CHECK (length(updated_at_utc) = 27 AND updated_at_utc GLOB {UTC_GLOB}),
    PRIMARY KEY(source, scope_key)
);

CREATE INDEX idx_attempts_run_kind ON collection_attempts(run_id, request_kind, attempt_ordinal);
CREATE INDEX idx_evidence_hash ON raw_evidence(content_sha256);
CREATE INDEX idx_observations_identity ON source_item_observations(source, source_item_id, observation_version);
CREATE INDEX idx_observations_published ON source_item_observations(source, published_at_utc);
CREATE INDEX idx_failures_run_class ON collection_failures(run_id, failure_class);

CREATE TRIGGER immutable_raw_evidence_update BEFORE UPDATE ON raw_evidence
BEGIN SELECT RAISE(ABORT, 'raw_evidence is immutable'); END;
CREATE TRIGGER immutable_raw_evidence_delete BEFORE DELETE ON raw_evidence
BEGIN SELECT RAISE(ABORT, 'raw_evidence is immutable'); END;
CREATE TRIGGER immutable_observation_update BEFORE UPDATE ON source_item_observations
BEGIN SELECT RAISE(ABORT, 'source_item_observations is immutable'); END;
CREATE TRIGGER immutable_observation_delete BEFORE DELETE ON source_item_observations
BEGIN SELECT RAISE(ABORT, 'source_item_observations is immutable'); END;
"""

MIGRATION_CHECKSUM = hashlib.sha256(MIGRATION_SQL.encode("utf-8")).hexdigest()

TABLES = {
    "schema_migrations", "collection_runs", "collection_attempts", "raw_evidence",
    "raw_body_state",
    "source_item_observations", "observation_evidence", "observation_scopes",
    "collection_failures", "collector_checkpoints",
}
TRIGGERS = {
    "immutable_raw_evidence_update", "immutable_raw_evidence_delete",
    "immutable_observation_update", "immutable_observation_delete",
}
INDEXES = {
    "idx_attempts_run_kind", "idx_evidence_hash", "idx_observations_identity",
    "idx_observations_published", "idx_failures_run_class",
}


class SchemaError(RuntimeError):
    """The target is not an empty DB or the expected current schema."""


def utc_text(value: datetime | str) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("UTC timestamp requires timezone-aware datetime")
        value = value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if not isinstance(value, str):
        raise TypeError("timestamp must be datetime or string")
    validate_utc_timestamp(value)
    return value


def validate_utc_timestamp(value: str, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or len(value) != 27 or not value.endswith("Z"):
        raise ValueError("timestamp must be YYYY-MM-DDTHH:MM:SS.ffffffZ")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("invalid UTC timestamp") from exc
    if parsed.tzinfo != timezone.utc:
        raise ValueError("timestamp must be UTC")
    if parsed.isoformat(timespec="microseconds").replace("+00:00", "Z") != value:
        raise ValueError("timestamp is not canonical UTC")


def _objects(conn: sqlite3.Connection, object_type: str) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%'",
        (object_type,),
    ).fetchall()
    return {row[0] for row in rows}


def _schema_sql(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL"
    ).fetchall()
    return {
        (row[0], row[1]): " ".join(row[2].split()).rstrip(";")
        for row in rows
    }


def _reference_schema_sql() -> dict[tuple[str, str], str]:
    reference = sqlite3.connect(":memory:")
    try:
        reference.executescript(MIGRATION_SQL)
        return _schema_sql(reference)
    finally:
        reference.close()


def validate_schema(conn: sqlite3.Connection) -> None:
    if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        raise SchemaError("unsupported schema version")
    if _objects(conn, "table") != TABLES:
        raise SchemaError("schema tables drifted or unknown tables present")
    if _objects(conn, "trigger") != TRIGGERS:
        raise SchemaError("schema triggers drifted or unknown triggers present")
    if _objects(conn, "index") != INDEXES:
        raise SchemaError("schema indexes drifted or unknown indexes present")
    if _schema_sql(conn) != _reference_schema_sql():
        raise SchemaError("schema SQL definitions drifted")
    migration = conn.execute(
        "SELECT version, checksum FROM schema_migrations WHERE version = ?",
        (SCHEMA_VERSION,),
    ).fetchone()
    if migration is None or migration[1] != MIGRATION_CHECKSUM:
        raise SchemaError("migration history/checksum mismatch")
    expected_columns = {
        "schema_migrations": {"version", "applied_at_utc", "checksum"},
        "collection_runs": {"run_id", "source", "scope_key", "status", "started_at_utc", "finished_at_utc", "collector_version", "parser_version", "schema_version", "watermark_before_utc", "watermark_after_utc", "safe_frontier_utc", "counters_json"},
        "collection_attempts": {"attempt_id", "run_id", "attempt_ordinal", "request_kind", "request_url", "started_at_utc", "finished_at_utc", "outcome", "http_status", "retry_after_seconds", "retry_number", "retry_budget", "error_class", "error_message"},
        "raw_evidence": {"evidence_id", "run_id", "attempt_id", "evidence_kind", "request_url", "final_url", "fetched_at_utc", "http_status", "content_type", "content_sha256", "byte_size", "filesystem_path", "storage_version"},
        "raw_body_state": {"source", "content_sha256", "body_state", "purged_at_utc", "updated_at_utc"},
        "source_item_observations": {"observation_id", "source", "source_item_id", "observation_version", "observed_at_utc", "published_at_utc", "source_updated_at_utc", "display_time_utc", "author_id", "author_name", "title", "content", "content_sha256", "url", "canonical_bar_code", "canonical_bar_name", "post_type", "post_state", "post_top_status", "read_count", "reply_count", "like_count", "forward_count", "source_times_raw_json", "source_metadata_json", "fact_fingerprint", "schema_version", "collector_version", "parser_version", "drift_from_observation_id"},
        "observation_evidence": {"observation_id", "evidence_id", "evidence_role"},
        "observation_scopes": {"observation_id", "scope_key", "requested_bar_code"},
        "collection_failures": {"failure_id", "run_id", "attempt_id", "evidence_id", "phase", "failure_class", "occurred_at_utc", "message"},
        "collector_checkpoints": {"source", "scope_key", "watermark_utc", "last_safe_run_id", "updated_at_utc"},
    }
    for table, expected in expected_columns.items():
        actual = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if actual != expected:
            raise SchemaError(f"schema columns drifted for {table}")


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Add retention state without rewriting existing evidence/observations."""
    now = utc_text(datetime.now(timezone.utc))
    with conn:
        conn.execute(
            """CREATE TABLE raw_body_state (
                source TEXT NOT NULL CHECK (length(trim(source)) > 0),
                content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
                body_state TEXT NOT NULL CHECK (body_state IN ('PRESENT','PURGED')),
                purged_at_utc TEXT CHECK (purged_at_utc IS NULL OR (length(purged_at_utc) = 27 AND purged_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z')),
                updated_at_utc TEXT NOT NULL CHECK (length(updated_at_utc) = 27 AND updated_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'),
                PRIMARY KEY(source, content_sha256)
            )"""
        )
        conn.execute(
            """INSERT INTO raw_body_state(source, content_sha256, body_state, purged_at_utc, updated_at_utc)
               SELECT r.source, e.content_sha256, 'PRESENT', NULL, ?
                 FROM raw_evidence AS e
                 JOIN collection_runs AS r ON r.run_id = e.run_id
                GROUP BY r.source, e.content_sha256""",
            (now,),
        )
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at_utc, checksum) VALUES (?,?,?)",
            (SCHEMA_VERSION, now, MIGRATION_CHECKSUM),
        )
        conn.execute("PRAGMA user_version = 2")


def connect_database(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists() and path.stat().st_size > 0
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        if not existed:
            with conn:
                conn.executescript(MIGRATION_SQL)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at_utc, checksum) VALUES (?,?,?)",
                    (SCHEMA_VERSION, utc_text(datetime.now(timezone.utc)), MIGRATION_CHECKSUM),
                )
        elif conn.execute("PRAGMA user_version").fetchone()[0] == 1:
            _migrate_v1_to_v2(conn)
        validate_schema(conn)
        return conn
    except Exception:
        conn.close()
        raise
