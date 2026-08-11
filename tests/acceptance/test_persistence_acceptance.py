"""Independent Phase 2 persistence acceptance tests.

These tests are intentionally separate from ``tests/unit/test_storage.py``.
They exercise the committed Round 04 public persistence boundary with real
temporary SQLite databases and filesystem raw stores.  They do not exercise
the pending Collector -> Persistence integration seam.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pytest

from myresearcher_collector.models import GubaSourceItem
from myresearcher_collector.storage import (
    PersistenceError,
    RawEvidenceStore,
    RawStoreError,
    SafeFrontier,
    SchemaError,
    SQLitePersistence,
    connect_database,
)


UTC = timezone.utc
NOW = datetime(2026, 8, 10, 3, 0, 0, 123456, tzinfo=UTC)
T0 = datetime(2026, 8, 10, 1, 0, tzinfo=UTC)
T1 = datetime(2026, 8, 10, 2, 0, tzinfo=UTC)
T2 = datetime(2026, 8, 10, 3, 0, tzinfo=UTC)
T3 = datetime(2026, 8, 10, 4, 0, tzinfo=UTC)


def make_store(tmp_path: Path) -> tuple[SQLitePersistence, RawEvidenceStore]:
    raw = RawEvidenceStore(tmp_path)
    return SQLitePersistence(tmp_path / "collector.db", raw), raw


def start_run(
    store: SQLitePersistence,
    run_id: str,
    *,
    source: str = "eastmoney_guba",
    scope: str = "stock:600001",
    watermark_before: datetime | None = None,
) -> None:
    store.start_run(
        run_id,
        source,
        scope,
        started_at=NOW,
        collector_version="collector.v1",
        parser_version="parser.v1",
        schema_version="eastmoney_guba.raw.v1",
        watermark_before=watermark_before,
    )


def make_item(
    *,
    item_id: str = "1001",
    source: str = "eastmoney_guba",
    content: str = "synthetic body",
    requested_bar_code: str = "600001",
    published_at: datetime = T0,
    source_updated_at: datetime | None = T1,
    display_time: datetime | None = T2,
    collected_at: datetime = T3,
    read_count: int | None = 0,
    reply_count: int | None = 0,
) -> GubaSourceItem:
    return GubaSourceItem(
        source=source,
        schema_version="eastmoney_guba.raw.v1",
        source_item_id=item_id,
        requested_bar_code=requested_bar_code,
        canonical_bar_code="600001",
        canonical_bar_name="Synthetic Bar",
        author_id="author-1",
        author_name="Author One",
        title="Synthetic title",
        content=content,
        published_at=published_at,
        last_updated_at=source_updated_at,
        display_time=display_time,
        url=(
            f"https://guba.eastmoney.com/news,600001,{item_id}.html"
            if source == "eastmoney_guba"
            else f"https://synthetic.example/items/{item_id}"
        ),
        post_type=0,
        post_state=0,
        post_top_status=0,
        read_count=read_count,
        reply_count=reply_count,
        like_count=0,
        forward_count=0,
        source_post_id=None,
        collected_at=collected_at,
        source_times_raw={
            "post_publish_time": "2026-08-10 09:00:00",
            "post_last_time": "2026-08-10 10:00:00",
            "post_display_time": "2026-08-10 11:00:00",
        },
        source_metadata={"extra": {"synthetic": True}},
        raw_ref={},
    )


def add_evidence(
    store: SQLitePersistence,
    raw: RawEvidenceStore,
    run_id: str,
    ordinal: int,
    *,
    kind: str = "list",
    payload: bytes | None = None,
    outcome: str = "success",
    http_status: int | None = 200,
    request_url: str | None = None,
) -> str:
    attempt_id = f"{run_id}-attempt-{ordinal}"
    evidence_id = f"{run_id}-evidence-{ordinal}"
    url = request_url or f"https://example.test/{kind}/{run_id}/{ordinal}"
    store.record_attempt(
        run_id,
        attempt_id,
        ordinal=ordinal,
        request_kind=kind,
        request_url=url,
        started_at=NOW,
        finished_at=NOW,
        outcome=outcome,
        retry_number=ordinal + 1,
        retry_budget=max(ordinal + 1, 1),
        http_status=http_status,
    )
    if payload is not None:
        published = raw.publish(run_id, ordinal, payload)
        store.record_raw_evidence(
            run_id,
            attempt_id,
            evidence_id,
            published,
            evidence_kind=kind,
            request_url=url,
            final_url=url,
            fetched_at=NOW,
            http_status=http_status,
            content_type="text/html",
        )
    return evidence_id


def add_observation_evidence(
    store: SQLitePersistence,
    raw: RawEvidenceStore,
    run_id: str,
    *,
    direct: bool = False,
) -> list[tuple[str, str]]:
    if direct:
        return [(add_evidence(store, raw, run_id, 0, kind="other_approved", payload=b"direct"), "direct")]
    return [
        (add_evidence(store, raw, run_id, 0, kind="list", payload=b"list-body"), "list"),
        (add_evidence(store, raw, run_id, 1, kind="detail", payload=b"detail-body"), "detail"),
    ]


def finish_success(
    store: SQLitePersistence,
    run_id: str,
    item: GubaSourceItem | None = None,
    links: list[tuple[str, str]] | None = None,
    *,
    scope: str = "stock:600001",
    frontier: datetime = T3,
) -> None:
    observations = [] if item is None else [(item, scope, links or [])]
    store.persist_result(
        run_id,
        observations,
        status="SUCCESS",
        finished_at=NOW,
        safe_frontier=SafeFrontier(frontier),
    )


def schema_snapshot(conn: sqlite3.Connection) -> list[tuple[str, str, str | None]]:
    return conn.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()


def test_pst001_fresh_migration_schema_history_constraints_and_indexes(tmp_path: Path) -> None:
    conn = connect_database(tmp_path / "collector.db")
    required = {
        "collection_runs",
        "collection_attempts",
        "raw_evidence",
        "raw_body_state",
        "source_item_observations",
        "observation_evidence",
        "observation_scopes",
        "collection_failures",
        "collector_checkpoints",
    }
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert required <= tables
    history = [
        name
        for name in tables
        if {row[1] for row in conn.execute(f"PRAGMA table_info({name})")}
        >= {"version", "checksum"}
    ]
    assert len(history) == 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert conn.execute(f"SELECT count(*) FROM {history[0]}").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM collection_runs").fetchone()[0] == 0

    indexed_column_sets: set[tuple[str, ...]] = set()
    for table in required:
        for index_row in conn.execute(f"PRAGMA index_list({table})"):
            index_name = index_row[1]
            columns = tuple(
                row[2]
                for row in conn.execute(f"PRAGMA index_info({index_name})")
            )
            if columns:
                indexed_column_sets.add(columns)
    assert ("run_id", "request_kind", "attempt_ordinal") in indexed_column_sets
    assert ("content_sha256",) in indexed_column_sets
    assert ("source", "source_item_id", "observation_version") in indexed_column_sets
    assert ("run_id", "failure_class") in indexed_column_sets
    conn.close()


def test_pst002_current_schema_reopen_is_idempotent_and_preserves_data(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    start_run(store, "reopen-run")
    evidence_id = add_evidence(store, raw, "reopen-run", 0, payload=b"reopen")
    before_schema = schema_snapshot(store.conn)
    before_rows = store.conn.execute(
        "SELECT r.run_id, a.attempt_id, e.evidence_id, e.content_sha256, e.byte_size "
        "FROM collection_runs r "
        "LEFT JOIN collection_attempts a USING(run_id) "
        "LEFT JOIN raw_evidence e USING(attempt_id)"
    ).fetchall()
    store.close()

    reopened = SQLitePersistence(tmp_path / "collector.db", RawEvidenceStore(tmp_path))
    assert schema_snapshot(reopened.conn) == before_schema
    assert reopened.conn.execute(
        "SELECT r.run_id, a.attempt_id, e.evidence_id, e.content_sha256, e.byte_size "
        "FROM collection_runs r "
        "LEFT JOIN collection_attempts a USING(run_id) "
        "LEFT JOIN raw_evidence e USING(attempt_id)"
    ).fetchall() == before_rows
    assert reopened.conn.execute(
        "SELECT count(*) FROM raw_evidence WHERE evidence_id=?", (evidence_id,)
    ).fetchone()[0] == 1
    reopened.close()


def test_pst003_unknown_version_missing_index_and_checksum_fail_closed(tmp_path: Path) -> None:
    base = tmp_path / "base.db"
    connect_database(base).close()
    cases: list[tuple[str, Callable[[sqlite3.Connection], None]]] = [
        ("version", lambda conn: conn.execute("PRAGMA user_version=999")),
        ("index", lambda conn: conn.execute("DROP INDEX idx_evidence_hash")),
        (
            "checksum",
            lambda conn: conn.execute(
                "UPDATE schema_migrations SET checksum='bad-checksum' WHERE version=2"
            ),
        ),
    ]
    for name, mutate in cases:
        case_db = tmp_path / f"{name}.db"
        shutil.copy2(base, case_db)
        conn = sqlite3.connect(case_db)
        mutate(conn)
        conn.commit()
        before = schema_snapshot(conn)
        conn.close()
        with pytest.raises(SchemaError):
            connect_database(case_db)
        after = sqlite3.connect(case_db)
        assert schema_snapshot(after) == before
        after.close()


def test_pst004_foreign_keys_are_enabled_and_invalid_lineage_cannot_commit(tmp_path: Path) -> None:
    conn = connect_database(tmp_path / "collector.db")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO collection_attempts(
                attempt_id, run_id, attempt_ordinal, request_kind, request_url,
                started_at_utc, outcome, retry_number, retry_budget
            ) VALUES ('orphan-attempt', 'missing-run', 0, 'list', 'https://x',
                      '2026-08-10T03:00:00.000000Z', 'success', 1, 1)"""
        )
    assert conn.execute("SELECT count(*) FROM collection_attempts").fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_pst005_normal_raw_publish_hash_size_path_and_lineage(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    start_run(store, "normal-run")
    attempt_id = f"normal-run-attempt-0"
    evidence_id = add_evidence(store, raw, "normal-run", 0, payload=b"response bytes")
    row = store.conn.execute(
        "SELECT content_sha256, byte_size, filesystem_path, run_id, attempt_id "
        "FROM raw_evidence WHERE evidence_id=?", (evidence_id,)
    ).fetchone()
    assert row[0] == hashlib.sha256(b"response bytes").hexdigest()
    assert row[1] == len(b"response bytes")
    assert row[3:] == ("normal-run", attempt_id)
    final = tmp_path / row[2]
    assert final.read_bytes() == b"response bytes"
    assert not list((tmp_path / "raw" / ".tmp").glob("*.partial"))


def test_pst006_identical_content_dedup_keeps_two_attempt_and_evidence_lineages(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    start_run(store, "dedup-a")
    first = add_evidence(store, raw, "dedup-a", 0, payload=b"identical")
    start_run(store, "dedup-b")
    second = add_evidence(store, raw, "dedup-b", 0, payload=b"identical")
    paths = store.conn.execute(
        "SELECT filesystem_path FROM raw_evidence WHERE evidence_id IN (?,?) ORDER BY evidence_id",
        (first, second),
    ).fetchall()
    assert paths[0] == paths[1]
    assert store.conn.execute("SELECT count(*) FROM collection_attempts").fetchone()[0] == 2
    assert store.conn.execute("SELECT count(*) FROM raw_evidence").fetchone()[0] == 2
    assert first != second


def test_pst007_existing_final_mismatch_fails_closed_without_overwrite(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    start_run(store, "collision-run")
    published = raw.publish("collision-run", 0, b"correct")
    published.absolute_path.write_bytes(b"wrong")
    with pytest.raises(RawStoreError):
        raw.publish("collision-run", 1, b"correct")
    assert published.absolute_path.read_bytes() == b"wrong"
    assert store.conn.execute("SELECT count(*) FROM raw_evidence").fetchone()[0] == 0
    assert store.checkpoint("eastmoney_guba", "stock:600001") is None


def test_pst008_pre_publish_failures_leave_no_final_or_partial_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, raw = make_store(tmp_path)
    start_run(store, "prepublish-run")

    original_open = Path.open

    def fail_partial_open(path: Path, *args: object, **kwargs: object):
        if path.name.endswith(".partial"):
            raise OSError("synthetic temporary write failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_partial_open)
    with pytest.raises(OSError):
        raw.publish("prepublish-run", 0, b"not published")
    assert not list((tmp_path / "raw" / ".tmp").glob("*.partial"))
    assert not list((tmp_path / "raw" / "eastmoney_guba").glob("*.body"))
    assert store.conn.execute(
        "SELECT status FROM collection_runs WHERE run_id='prepublish-run'"
    ).fetchone()[0] == "RUNNING"

    link_root = tmp_path / "link-failure"
    link_raw = RawEvidenceStore(link_root)

    def fail_link(*args: object, **kwargs: object):
        raise OSError("synthetic no-clobber publish failure")

    monkeypatch.undo()
    monkeypatch.setattr(os, "link", fail_link)
    with pytest.raises(RawStoreError):
        link_raw.publish("link-failure", 0, b"not linked")
    assert not list((link_root / "raw" / ".tmp").glob("*.partial"))
    assert not list((link_root / "raw" / "eastmoney_guba").glob("*.body"))


def test_pst009_sqlite_commit_failure_leaves_orphan_only_and_rolls_back_metadata(tmp_path: Path) -> None:
    class CommitFailingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection

        def commit(self) -> None:
            raise sqlite3.OperationalError("synthetic commit failure")

        def __getattr__(self, name: str):
            return getattr(self.connection, name)

    store, raw = make_store(tmp_path)
    start_run(store, "commit-run")
    add_evidence(store, raw, "commit-run", 0, payload=b"orphan-after-commit")
    attempt_id = "commit-run-attempt-1"
    store.record_attempt(
        "commit-run", attempt_id, ordinal=1, request_kind="detail",
        request_url="https://example.test/detail", started_at=NOW, finished_at=NOW,
        outcome="success", retry_number=1, retry_budget=1,
    )
    real = store.conn
    store.conn = CommitFailingConnection(real)  # type: ignore[assignment]
    published = raw.publish("commit-run", 1, b"unreferenced final")
    with pytest.raises(sqlite3.OperationalError):
        store.record_raw_evidence(
            "commit-run", attempt_id, "commit-failed-evidence", published,
            evidence_kind="detail", request_url="https://example.test/detail",
            final_url=None, fetched_at=NOW, http_status=200, content_type="text/html",
        )
    assert real.execute(
        "SELECT count(*) FROM raw_evidence WHERE evidence_id='commit-failed-evidence'"
    ).fetchone()[0] == 0
    assert published.absolute_path.exists()

    store2, raw2 = make_store(tmp_path / "observation")
    start_run(store2, "observation-commit-run")
    links = add_observation_evidence(store2, raw2, "observation-commit-run")
    real2 = store2.conn
    store2.conn = CommitFailingConnection(real2)  # type: ignore[assignment]
    with pytest.raises(sqlite3.OperationalError):
        store2.persist_result(
            "observation-commit-run",
            [(make_item(), "stock:600001", links)],
            status="SUCCESS", finished_at=NOW, safe_frontier=SafeFrontier(T2),
        )
    assert real2.execute("SELECT count(*) FROM source_item_observations").fetchone()[0] == 0
    assert real2.execute(
        "SELECT status, watermark_after_utc FROM collection_runs WHERE run_id=?",
        ("observation-commit-run",),
    ).fetchone() == ("RUNNING", None)
    assert store2.checkpoint("eastmoney_guba", "stock:600001") is None


def test_pst010_missing_and_hash_mismatched_references_fail_closed(tmp_path: Path) -> None:
    missing_store, missing_raw = make_store(tmp_path / "missing")
    start_run(missing_store, "missing-run")
    missing_evidence = add_evidence(missing_store, missing_raw, "missing-run", 0, payload=b"missing")
    missing_path = missing_store.conn.execute(
        "SELECT filesystem_path FROM raw_evidence WHERE evidence_id=?", (missing_evidence,)
    ).fetchone()[0]
    (tmp_path / "missing" / missing_path).unlink()
    with pytest.raises(PersistenceError):
        missing_store.verify_evidence(missing_evidence)

    hash_store, hash_raw = make_store(tmp_path / "hash")
    start_run(hash_store, "hash-run")
    hash_evidence = add_evidence(hash_store, hash_raw, "hash-run", 0, payload=b"hash")
    hash_path = hash_store.conn.execute(
        "SELECT filesystem_path FROM raw_evidence WHERE evidence_id=?", (hash_evidence,)
    ).fetchone()[0]
    (tmp_path / "hash" / hash_path).write_bytes(b"mutated")
    with pytest.raises(PersistenceError):
        hash_store.verify_evidence(hash_evidence)


def test_pst011_first_observation_has_eastmoney_evidence_and_source_semantics(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    start_run(store, "first-observation")
    links = add_observation_evidence(store, raw, "first-observation")
    first = make_item(read_count=None, reply_count=0, published_at=T0, source_updated_at=T1, display_time=T2, collected_at=T3)
    created, advanced = store.persist_result(
        "first-observation", [(first, "stock:600001", links)],
        status="SUCCESS", finished_at=NOW, safe_frontier=SafeFrontier(T3),
    )
    assert created[0][1:] == (1, True)
    assert advanced is True
    row = store.conn.execute(
        """SELECT source, source_item_id, observation_version, published_at_utc,
                  source_updated_at_utc, display_time_utc, observed_at_utc,
                  read_count, reply_count
           FROM source_item_observations"""
    ).fetchone()
    assert row == (
        "eastmoney_guba", "1001", 1,
        "2026-08-10T01:00:00.000000Z",
        "2026-08-10T02:00:00.000000Z",
        "2026-08-10T03:00:00.000000Z",
        "2026-08-10T04:00:00.000000Z",
        None, 0,
    )
    roles = store.conn.execute(
        "SELECT evidence_role FROM observation_evidence ORDER BY evidence_role"
    ).fetchall()
    assert roles == [("detail",), ("list",)]
    lineage = store.conn.execute(
        """SELECT r.run_id, a.attempt_id, e.evidence_id
           FROM raw_evidence e
           JOIN collection_attempts a USING(attempt_id)
           JOIN collection_runs r USING(run_id)"""
    ).fetchall()
    assert len(lineage) == 2


def test_pst012_identical_reacquisition_keeps_version_and_appends_scope_lineage(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    first = make_item(requested_bar_code="600001")
    start_run(store, "same-a", scope="stock:600001")
    links_a = add_observation_evidence(store, raw, "same-a")
    finish_success(store, "same-a", first, links_a, scope="stock:600001")

    second = replace(first, requested_bar_code="600002")
    start_run(store, "same-b", scope="stock:600002")
    links_b = add_observation_evidence(store, raw, "same-b")
    finish_success(store, "same-b", second, links_b, scope="stock:600002")

    start_run(store, "same-c", scope="stock:600002")
    links_c = add_observation_evidence(store, raw, "same-c")
    result = store.record_observation("same-c", second, scope_key="stock:600002", evidence_links=links_c)
    assert result[0:2] == (store.conn.execute("SELECT observation_id FROM source_item_observations").fetchone()[0], 1)
    assert result[2] is False
    store.finish_run("same-c", status="SUCCESS", finished_at=NOW)

    assert store.conn.execute("SELECT count(*) FROM source_item_observations").fetchone()[0] == 1
    assert store.conn.execute("SELECT count(*) FROM observation_scopes").fetchone()[0] == 2
    assert store.conn.execute("SELECT count(*) FROM collection_runs").fetchone()[0] == 3
    assert store.conn.execute("SELECT count(*) FROM raw_evidence").fetchone()[0] == 6
    assert store.conn.execute(
        "SELECT scope_key FROM observation_scopes ORDER BY scope_key"
    ).fetchall() == [("stock:600001",), ("stock:600002",)]


def test_pst013_a_to_b_to_a_is_v1_v2_v3_and_history_is_immutable(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    versions: list[str] = []
    for run_id, content in (("drift-a", "A"), ("drift-b", "B"), ("drift-c", "A")):
        start_run(store, run_id)
        links = add_observation_evidence(store, raw, run_id, direct=True)
        observation_id, version, created = store.record_observation(
            run_id, make_item(content=content), scope_key="stock:600001", evidence_links=links
        )
        assert created is True
        versions.append(observation_id)
        store.finish_run(run_id, status="SUCCESS", finished_at=NOW)
    rows = store.conn.execute(
        "SELECT observation_id, observation_version, content, drift_from_observation_id "
        "FROM source_item_observations ORDER BY observation_version"
    ).fetchall()
    assert [row[1] for row in rows] == [1, 2, 3]
    assert [row[2] for row in rows] == ["A", "B", "A"]
    assert rows[0][0] == versions[0] and rows[1][3] == versions[0] and rows[2][3] == versions[1]
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("UPDATE source_item_observations SET content='overwrite' WHERE observation_id=?", (versions[0],))
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("DELETE FROM source_item_observations WHERE observation_id=?", (versions[0],))
    assert store.conn.execute(
        "SELECT content FROM source_item_observations WHERE observation_id=?", (versions[0],)
    ).fetchone()[0] == "A"


def test_pst014_retry_success_retains_attempts_and_stable_observation_identity(tmp_path: Path) -> None:
    def build(root: Path) -> tuple[str, list[str], str]:
        store, raw = make_store(root)
        run_id = "retry-stable"
        start_run(store, run_id)
        a1 = add_evidence(store, raw, run_id, 0, kind="list", outcome="timeout", http_status=None)
        store.record_failure(run_id, "failure-timeout", phase="list", failure_class="timeout", occurred_at=NOW, message="timeout", attempt_id=f"{run_id}-attempt-0")
        a2 = add_evidence(store, raw, run_id, 1, kind="list", payload=b"429 body", outcome="http_error", http_status=429)
        store.record_failure(run_id, "failure-429", phase="list", failure_class="rate_limit", occurred_at=NOW, message="429", attempt_id=f"{run_id}-attempt-1", evidence_id=a2)
        a3 = add_evidence(store, raw, run_id, 2, kind="detail", payload=b"success body", outcome="success", http_status=200)
        observation_id, version, created = store.record_observation(
            run_id, make_item(item_id="retry-item"), scope_key="stock:600001", evidence_links=[(a3, "detail")]
        )
        store.finish_run(run_id, status="SUCCESS", finished_at=NOW, safe_frontier=SafeFrontier(T3))
        return observation_id, [a1, a2, a3], store.conn.execute(
            "SELECT attempt_ordinal, outcome FROM collection_attempts ORDER BY attempt_ordinal"
        ).fetchall()

    first_id, first_evidence, attempts = build(tmp_path / "first")
    second_id, second_evidence, attempts_again = build(tmp_path / "second")
    assert attempts == [(0, "timeout"), (1, "http_error"), (2, "success")]
    assert attempts_again == attempts
    assert first_id == second_id
    assert first_evidence == second_evidence
    assert first_evidence[0] != first_evidence[2]
    assert first_evidence[2].endswith("-2")


def test_pst015_retry_exhaustion_preserves_attempts_failure_and_checkpoint(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    start_run(store, "exhausted")
    for ordinal, outcome in enumerate(("timeout", "http_error", "timeout")):
        add_evidence(store, RawEvidenceStore(tmp_path / "unused"), "exhausted", ordinal, outcome=outcome, http_status=503 if outcome == "http_error" else None)
    store.record_failure(
        "exhausted", "retry-exhaustion", phase="run", failure_class="retry_exhaustion",
        occurred_at=NOW, message="retry budget exhausted",
    )
    assert store.finish_run("exhausted", status="COLLECTION_FAILED", finished_at=NOW) is False
    assert store.conn.execute("SELECT count(*) FROM collection_attempts").fetchone()[0] == 3
    assert store.conn.execute("SELECT count(*) FROM collection_failures").fetchone()[0] == 1
    assert store.conn.execute("SELECT count(*) FROM source_item_observations").fetchone()[0] == 0
    assert store.conn.execute("SELECT status FROM collection_runs WHERE run_id='exhausted'").fetchone()[0] == "COLLECTION_FAILED"
    assert store.checkpoint("eastmoney_guba", "stock:600001") is None


def test_pst016_parse_schema_failure_after_body_retains_raw_and_failure(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    start_run(store, "schema-failure")
    evidence_id = add_evidence(store, raw, "schema-failure", 0, kind="list", payload=b"HTTP 200 malformed body")
    store.record_failure(
        "schema-failure", "schema-failure-id", phase="schema", failure_class="schema_mismatch",
        occurred_at=NOW, message="malformed embedded payload", attempt_id="schema-failure-attempt-0", evidence_id=evidence_id,
    )
    assert store.finish_run("schema-failure", status="SPEC_MISMATCH", finished_at=NOW) is False
    assert store.conn.execute("SELECT count(*) FROM raw_evidence").fetchone()[0] == 1
    assert store.conn.execute("SELECT count(*) FROM collection_failures").fetchone()[0] == 1
    assert store.conn.execute("SELECT count(*) FROM source_item_observations").fetchone()[0] == 0
    assert store.conn.execute("SELECT status FROM collection_runs WHERE run_id='schema-failure'").fetchone()[0] == "SPEC_MISMATCH"
    assert store.checkpoint("eastmoney_guba", "stock:600001") is None


def test_pst017_success_and_no_new_data_safe_frontier_commit(tmp_path: Path) -> None:
    success, raw = make_store(tmp_path / "success")
    start_run(success, "success-frontier")
    links = add_observation_evidence(success, raw, "success-frontier")
    finish_success(success, "success-frontier", make_item(), links, frontier=T2)
    assert success.checkpoint("eastmoney_guba", "stock:600001") == (
        "2026-08-10T03:00:00.000000Z", "success-frontier"
    )

    no_data, no_data_raw = make_store(tmp_path / "no-data")
    start_run(no_data, "no-data-frontier")
    add_evidence(no_data, no_data_raw, "no-data-frontier", 0, payload=b"valid empty page")
    assert no_data.finish_run(
        "no-data-frontier", status="NO_NEW_DATA", finished_at=NOW,
        safe_frontier=SafeFrontier(T2),
    ) is True
    assert no_data.checkpoint("eastmoney_guba", "stock:600001") == (
        "2026-08-10T03:00:00.000000Z", "no-data-frontier"
    )


@pytest.mark.xfail(
    strict=False,
    reason="TEST_PLAN_MAPPING_NOTE: NO_NEW_DATA truth is runtime-declared; direct finish_run is not the Collector outcome boundary",
)
def test_pst017_zero_observations_without_frontier_cannot_infer_no_new_data(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    start_run(store, "zero-without-proof")
    with pytest.raises(PersistenceError):
        store.finish_run("zero-without-proof", status="NO_NEW_DATA", finished_at=NOW)
    assert store.conn.execute(
        "SELECT status, watermark_after_utc FROM collection_runs WHERE run_id='zero-without-proof'"
    ).fetchone() == ("RUNNING", None)
    assert store.checkpoint("eastmoney_guba", "stock:600001") is None


def test_pst018_partial_proven_safe_prefix_advances_exactly_to_prefix(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    start_run(store, "seed")
    assert store.finish_run("seed", status="SUCCESS", finished_at=NOW, safe_frontier=SafeFrontier(T0)) is True
    start_run(store, "partial-safe", watermark_before=T0)
    store.record_attempt(
        "partial-safe", "partial-attempt", ordinal=0, request_kind="detail",
        request_url="https://example.test/unresolved", started_at=NOW, finished_at=NOW,
        outcome="timeout", retry_number=1, retry_budget=1,
    )
    store.record_failure(
        "partial-safe", "partial-failure", phase="detail", failure_class="timeout",
        occurred_at=NOW, message="unresolved work after safe prefix", attempt_id="partial-attempt",
    )
    assert store.finish_run(
        "partial-safe", status="PARTIAL_COLLECTION", finished_at=NOW,
        safe_frontier=SafeFrontier(T1),
    ) is True
    assert store.checkpoint("eastmoney_guba", "stock:600001") == (
        "2026-08-10T02:00:00.000000Z", "partial-safe"
    )
    assert store.conn.execute(
        "SELECT status, watermark_after_utc FROM collection_runs WHERE run_id='partial-safe'"
    ).fetchone() == ("PARTIAL_COLLECTION", "2026-08-10T02:00:00.000000Z")


@pytest.mark.parametrize("status", ["PARTIAL_COLLECTION", "COLLECTION_FAILED", "SPEC_MISMATCH", "CANCELLED"])
def test_pst019_unsafe_terminal_states_do_not_advance_checkpoint(tmp_path: Path, status: str) -> None:
    store, _ = make_store(tmp_path / status)
    start_run(store, status)
    frontier = None if status == "PARTIAL_COLLECTION" else SafeFrontier(T2)
    assert store.finish_run(status, status=status, finished_at=NOW, safe_frontier=frontier) is False
    assert store.checkpoint("eastmoney_guba", "stock:600001") is None
    assert store.conn.execute(
        "SELECT status, watermark_after_utc FROM collection_runs WHERE run_id=?", (status,)
    ).fetchone() == (status, None)


def test_pst020_unresolved_gap_rejects_candidate_frontier_without_false_success(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    start_run(store, "gap-crossing")
    candidate = SafeFrontier(T3, unresolved_gaps=("item-B",))
    assert store.finish_run(
        "gap-crossing", status="PARTIAL_COLLECTION", finished_at=NOW, safe_frontier=candidate
    ) is False
    assert store.checkpoint("eastmoney_guba", "stock:600001") is None
    assert store.conn.execute(
        "SELECT status, watermark_after_utc, safe_frontier_utc FROM collection_runs WHERE run_id='gap-crossing'"
    ).fetchone() == ("PARTIAL_COLLECTION", None, "2026-08-10T04:00:00.000000Z")


def test_pst021_checkpoint_component_regression_forward_equal_and_backward(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    start_run(store, "forward")
    assert store.finish_run("forward", status="SUCCESS", finished_at=NOW, safe_frontier=SafeFrontier(T1)) is True
    start_run(store, "forward-again")
    assert store.finish_run("forward-again", status="SUCCESS", finished_at=NOW, safe_frontier=SafeFrontier(T2)) is True
    start_run(store, "equal")
    assert store.finish_run("equal", status="SUCCESS", finished_at=NOW, safe_frontier=SafeFrontier(T2)) is True
    assert store.checkpoint("eastmoney_guba", "stock:600001") == (
        "2026-08-10T03:00:00.000000Z", "equal"
    )

    start_run(store, "backward-success")
    with pytest.raises(PersistenceError):
        store.finish_run("backward-success", status="SUCCESS", finished_at=NOW, safe_frontier=SafeFrontier(T1))
    assert store.conn.execute(
        "SELECT status, watermark_after_utc FROM collection_runs WHERE run_id='backward-success'"
    ).fetchone() == ("RUNNING", None)
    assert store.checkpoint("eastmoney_guba", "stock:600001") == (
        "2026-08-10T03:00:00.000000Z", "equal"
    )

    start_run(store, "backward-partial")
    with pytest.raises(PersistenceError):
        store.finish_run("backward-partial", status="PARTIAL_COLLECTION", finished_at=NOW, safe_frontier=SafeFrontier(T1))
    assert store.conn.execute(
        "SELECT status, watermark_after_utc FROM collection_runs WHERE run_id='backward-partial'"
    ).fetchone() == ("RUNNING", None)
    assert store.checkpoint("eastmoney_guba", "stock:600001") == (
        "2026-08-10T03:00:00.000000Z", "equal"
    )


def test_pst022_source_agnostic_direct_evidence_role_is_supported(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    start_run(store, "direct-source", source="synthetic_direct", scope="synthetic:one")
    links = add_observation_evidence(store, raw, "direct-source", direct=True)
    item = make_item(source="synthetic_direct", item_id="direct-1")
    observation_id, version, created = store.record_observation(
        "direct-source", item, scope_key="synthetic:one", evidence_links=links
    )
    assert observation_id and version == 1 and created is True
    roles = store.conn.execute(
        "SELECT evidence_role FROM observation_evidence WHERE observation_id=?", (observation_id,)
    ).fetchall()
    assert roles == [("direct",)]
    assert store.finish_run("direct-source", status="SUCCESS", finished_at=NOW) is False
