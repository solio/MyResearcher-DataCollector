"""Offline acceptance coverage for raw evidence retention and migration."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from myresearcher_collector.storage import (
    PersistenceError,
    RawBodyPurged,
    RawEvidenceStore,
    SafeFrontier,
    SQLitePersistence,
    connect_database,
    purge_raw_bodies,
)
from myresearcher_collector.cli.main import main
from myresearcher_collector.models import XueqiuSourceItem


UTC = timezone.utc
NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
OLD = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
YOUNG = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def make_store(tmp_path: Path, source: str = "xueqiu") -> tuple[SQLitePersistence, RawEvidenceStore]:
    raw = RawEvidenceStore(tmp_path, source=source)
    return SQLitePersistence(tmp_path / "collector.db", raw), raw


def add_evidence(
    store: SQLitePersistence,
    raw: RawEvidenceStore,
    run_id: str,
    payload: bytes,
    fetched_at: datetime,
    *,
    status: str = "SUCCESS",
    failure: bool = False,
) -> tuple[str, str]:
    store.start_run(
        run_id,
        raw.source,
        "scope:test",
        started_at=fetched_at,
        collector_version="collector.test",
        parser_version="parser.test",
        schema_version="xueqiu.raw.v1",
    )
    evidence_id, digest = record_evidence(store, raw, run_id, payload, fetched_at)
    if failure:
        store.record_failure(
            run_id,
            f"{run_id}-failure",
            phase="collection",
            failure_class="SPEC_MISMATCH" if status == "SPEC_MISMATCH" else "COLLECTION_FAILED",
            occurred_at=fetched_at,
            message="retention acceptance failure",
            evidence_id=evidence_id,
        )
    store.finish_run(run_id, status=status, finished_at=fetched_at)
    return evidence_id, digest


def record_evidence(
    store: SQLitePersistence,
    raw: RawEvidenceStore,
    run_id: str,
    payload: bytes,
    fetched_at: datetime,
) -> tuple[str, str]:
    attempt_id = f"{run_id}-attempt"
    evidence_id = f"{run_id}-evidence"
    url = f"https://example.test/{run_id}"
    store.record_attempt(
        run_id,
        attempt_id,
        ordinal=0,
        request_kind="list",
        request_url=url,
        started_at=fetched_at,
        finished_at=fetched_at,
        outcome="success",
        retry_number=1,
        retry_budget=1,
        http_status=200,
    )
    published = raw.publish(run_id, 0, payload)
    store.record_raw_evidence(
        run_id,
        attempt_id,
        evidence_id,
        published,
        evidence_kind="list",
        request_url=url,
        final_url=url,
        fetched_at=fetched_at,
        http_status=200,
        content_type="application/json",
    )
    return evidence_id, published.sha256


def purge(store: SQLitePersistence, tmp_path: Path, **kwargs):
    store.close()
    return purge_raw_bodies(
        db_path=tmp_path / "collector.db",
        raw_data_dir=tmp_path,
        now=NOW,
        **kwargs,
    )


def state(tmp_path: Path, source: str, digest: str) -> str:
    conn = sqlite3.connect(tmp_path / "collector.db")
    try:
        return conn.execute(
            "SELECT body_state FROM raw_body_state WHERE source=? AND content_sha256=?",
            (source, digest),
        ).fetchone()[0]
    finally:
        conn.close()


def test_ret001_old_normal_body_purges_but_lineage_remains(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    evidence_id, digest = add_evidence(store, raw, "old", b"old-body", OLD)
    result = purge(store, tmp_path, dry_run=False, confirm=True)
    assert result.purged_objects == 1
    assert result.errors == ()
    assert not (tmp_path / "raw" / "xueqiu" / f"{digest}.body").exists()
    assert state(tmp_path, "xueqiu", digest) == "PURGED"
    conn = sqlite3.connect(tmp_path / "collector.db")
    assert conn.execute("SELECT count(*) FROM raw_evidence WHERE evidence_id=?", (evidence_id,)).fetchone()[0] == 1
    conn.close()


def test_ret002_young_body_is_retained(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    _, digest = add_evidence(store, raw, "young", b"young-body", YOUNG)
    result = purge(store, tmp_path, dry_run=False, confirm=True)
    assert result.purged_objects == 0
    assert result.retained_recent == 1
    assert state(tmp_path, "xueqiu", digest) == "PRESENT"


def test_ret003_mixed_old_and_young_references_are_retained(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    _, digest = add_evidence(store, raw, "mixed-old", b"same-body", OLD)
    add_evidence(store, raw, "mixed-young", b"same-body", YOUNG)
    result = purge(store, tmp_path, dry_run=False, confirm=True)
    assert result.purged_objects == 0
    assert result.retained_recent == 1
    assert state(tmp_path, "xueqiu", digest) == "PRESENT"


def test_ret004_all_old_references_delete_one_physical_object(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    _, digest = add_evidence(store, raw, "old-a", b"shared", OLD)
    add_evidence(store, raw, "old-b", b"shared", OLD)
    result = purge(store, tmp_path, dry_run=False, confirm=True)
    assert result.eligible_objects == result.purged_objects == 1
    conn = sqlite3.connect(tmp_path / "collector.db")
    assert conn.execute("SELECT count(*) FROM raw_evidence WHERE content_sha256=?", (digest,)).fetchone()[0] == 2
    conn.close()


@pytest.mark.parametrize("status", ["COLLECTION_FAILED", "SPEC_MISMATCH"])
def test_ret005_ret006_failure_and_spec_evidence_are_never_auto_purged(tmp_path: Path, status: str) -> None:
    store, raw = make_store(tmp_path)
    _, digest = add_evidence(store, raw, status.lower(), b"failure-body", OLD, status=status, failure=True)
    result = purge(store, tmp_path, dry_run=False, confirm=True)
    assert result.purged_objects == 0
    assert result.retained_failure == 1
    assert state(tmp_path, "xueqiu", digest) == "PRESENT"


def test_ret007_purged_body_has_explicit_semantic_error(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    evidence_id, _ = add_evidence(store, raw, "purged", b"purged-body", OLD)
    purge(store, tmp_path, dry_run=False, confirm=True)
    reopened = SQLitePersistence(tmp_path / "collector.db", raw)
    with pytest.raises(RawBodyPurged):
        reopened.verify_evidence(evidence_id)
    reopened.close()


def test_ret008_missing_present_body_is_integrity_error_and_not_purged(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    evidence_id, digest = add_evidence(store, raw, "missing", b"missing-body", OLD)
    (tmp_path / "raw" / "xueqiu" / f"{digest}.body").unlink()
    result = purge(store, tmp_path, dry_run=False, confirm=True)
    assert result.purged_objects == 0
    assert any("unexpectedly missing" in error for error in result.errors)
    reopened = SQLitePersistence(tmp_path / "collector.db", raw)
    with pytest.raises(PersistenceError):
        reopened.verify_evidence(evidence_id)
    reopened.close()


def test_ret009_republish_after_purge_restores_present_state(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    _, digest = add_evidence(store, raw, "first", b"republish", OLD)
    purge(store, tmp_path, dry_run=False, confirm=True)
    reopened = SQLitePersistence(tmp_path / "collector.db", raw)
    evidence_id, attempt_id = "second-evidence", "second-attempt"
    reopened.start_run("second", "xueqiu", "scope:test", started_at=NOW, collector_version="c", parser_version="p", schema_version="xueqiu.raw.v1")
    reopened.record_attempt("second", attempt_id, ordinal=0, request_kind="list", request_url="https://example.test/second", started_at=NOW, finished_at=NOW, outcome="success", retry_number=1, retry_budget=1, http_status=200)
    published = raw.publish("second", 0, b"republish")
    reopened.record_raw_evidence("second", attempt_id, evidence_id, published, evidence_kind="list", request_url="https://example.test/second", final_url=None, fetched_at=NOW, http_status=200, content_type="application/json")
    reopened.finish_run("second", status="SUCCESS", finished_at=NOW)
    assert published.sha256 == digest
    assert state(tmp_path, "xueqiu", digest) == "PRESENT"
    assert reopened.verify_evidence(evidence_id).exists()
    reopened.close()


def test_ret010_dry_run_reports_without_mutating(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store, raw = make_store(tmp_path)
    _, digest = add_evidence(store, raw, "dry", b"dry-body", OLD)
    store.close()
    assert main(["raw-retention", "--data-dir", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert '"eligible_objects": 1' in output
    assert '"purged_objects": 0' in output
    assert state(tmp_path, "xueqiu", digest) == "PRESENT"
    assert (tmp_path / "raw" / "xueqiu" / f"{digest}.body").exists()


def _item(content: str, *, collected_at: datetime) -> XueqiuSourceItem:
    return XueqiuSourceItem(
        source="xueqiu",
        schema_version="xueqiu.raw.v1",
        source_item_id="historical-A",
        requested_bar_code="600001",
        canonical_bar_code="600001",
        canonical_bar_name="Synthetic",
        author_id="author",
        author_name="Author",
        title="Title",
        content=content,
        published_at=OLD,
        last_updated_at=None,
        display_time=None,
        url="https://xueqiu.test/A",
        post_type=0,
        post_state=0,
        post_top_status=0,
        read_count=0,
        reply_count=0,
        like_count=0,
        forward_count=0,
        source_post_id=None,
        collected_at=collected_at,
        source_times_raw={},
        source_metadata={"reply_count_raw": 0},
        raw_ref={},
    )


def test_ret024_observed_historical_drift_is_versioned_and_equivalent_is_deduped(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)

    # First observation is historical relative to the committed checkpoint.
    store.start_run("history-1", "xueqiu", "scope:test", started_at=OLD, collector_version="c", parser_version="p", schema_version="xueqiu.raw.v1")
    evidence_id, _ = record_evidence(store, raw, "history-1", b"historical-body", OLD)
    created = store.record_observation("history-1", _item("original", collected_at=OLD), scope_key="scope:test", evidence_links=[(evidence_id, "list")])
    store.finish_run("history-1", status="SUCCESS", finished_at=OLD)
    assert created[1:] == (1, True)

    # A normal scan observes the same historical ID with changed mutable facts.
    store.start_run("history-2", "xueqiu", "scope:test", started_at=YOUNG, collector_version="c", parser_version="p", schema_version="xueqiu.raw.v1")
    evidence_id_2, _ = record_evidence(store, raw, "history-2", b"historical-body-2", YOUNG)
    drift = store.record_observation("history-2", _item("changed", collected_at=YOUNG), scope_key="scope:test", evidence_links=[(evidence_id_2, "list")])
    store.finish_run("history-2", status="SUCCESS", finished_at=YOUNG)
    assert drift[1:] == (2, True)

    # Equivalent historical observation links evidence without a new version.
    store.start_run("history-3", "xueqiu", "scope:test", started_at=NOW, collector_version="c", parser_version="p", schema_version="xueqiu.raw.v1")
    evidence_id_3, _ = record_evidence(store, raw, "history-3", b"historical-body-2", NOW)
    same = store.record_observation("history-3", _item("changed", collected_at=YOUNG), scope_key="scope:test", evidence_links=[(evidence_id_3, "list")])
    store.finish_run("history-3", status="SUCCESS", finished_at=NOW)
    assert same[1:] == (2, False)

    conn = sqlite3.connect(tmp_path / "collector.db")
    assert conn.execute("SELECT count(*) FROM source_item_observations WHERE source_item_id='historical-A'").fetchone()[0] == 2
    assert conn.execute("SELECT max(observation_version) FROM source_item_observations WHERE source_item_id='historical-A'").fetchone()[0] == 2
    conn.close()


def test_ret011_v1_fixture_migrates_in_place_and_initializes_present_state(tmp_path: Path) -> None:
    db_path = tmp_path / "collector.db"
    store, raw = make_store(tmp_path)
    store.start_run("v1-run", "xueqiu", "scope:test", started_at=OLD, collector_version="c", parser_version="p", schema_version="xueqiu.raw.v1")
    evidence_id, digest = record_evidence(store, raw, "v1-run", b"v1-body", OLD)
    store.record_observation("v1-run", _item("v1-observation", collected_at=OLD), scope_key="scope:test", evidence_links=[(evidence_id, "list")])
    store.finish_run("v1-run", status="SUCCESS", finished_at=OLD, safe_frontier=SafeFrontier(OLD))
    store.close()

    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE raw_body_state")
    conn.execute("DELETE FROM schema_migrations WHERE version=2")
    conn.execute("PRAGMA user_version=1")
    conn.commit()
    conn.close()

    migrated = connect_database(db_path)
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == 2
    assert migrated.execute("SELECT count(*) FROM raw_evidence").fetchone()[0] == 1
    assert migrated.execute("SELECT count(*) FROM source_item_observations").fetchone()[0] == 1
    assert migrated.execute("SELECT count(*) FROM collector_checkpoints").fetchone()[0] == 1
    assert migrated.execute("SELECT body_state FROM raw_body_state WHERE source='xueqiu' AND content_sha256=?", (digest,)).fetchone()[0] == "PRESENT"
    migrated.close()


def test_ret012a_db_state_failure_restores_body_from_purging_tombstone(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    _, digest = add_evidence(store, raw, "fault", b"fault-body", OLD)
    conn = sqlite3.connect(tmp_path / "collector.db")
    conn.execute(
        """CREATE TRIGGER injected_retention_failure
           BEFORE UPDATE OF body_state ON raw_body_state
           BEGIN SELECT RAISE(ABORT, 'injected state failure'); END"""
    )
    conn.commit()
    conn.close()

    result = purge(store, tmp_path, dry_run=False, confirm=True)
    body = tmp_path / "raw" / "xueqiu" / f"{digest}.body"
    assert result.purged_objects == 0
    assert any("state update failed" in error for error in result.errors)
    assert state(tmp_path, "xueqiu", digest) == "PRESENT"
    assert body.exists()
    assert not body.with_name(body.name + ".purging").exists()


def test_ret012b_interrupted_precommit_purge_recovers_present_body(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    _, digest = add_evidence(store, raw, "recover-present", b"recover-body", OLD)
    body = tmp_path / "raw" / "xueqiu" / f"{digest}.body"
    tombstone = body.with_name(body.name + ".purging")
    body.rename(tombstone)

    result = purge(store, tmp_path, dry_run=True, confirm=False)
    assert result.errors == ()
    assert state(tmp_path, "xueqiu", digest) == "PRESENT"
    assert body.exists()
    assert not tombstone.exists()


def test_ret012c_committed_purge_cleans_leftover_tombstone(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    _, digest = add_evidence(store, raw, "recover-purged", b"purged-body", OLD)
    purge(store, tmp_path, dry_run=False, confirm=True)
    body = tmp_path / "raw" / "xueqiu" / f"{digest}.body"
    tombstone = body.with_name(body.name + ".purging")
    tombstone.write_bytes(b"leftover tombstone")

    result = purge_raw_bodies(db_path=tmp_path / "collector.db", raw_data_dir=tmp_path, now=NOW, dry_run=True, confirm=False)
    assert result.errors == ()
    assert state(tmp_path, "xueqiu", digest) == "PURGED"
    assert not tombstone.exists()


def test_ret013_published_source_must_match_run_source(tmp_path: Path) -> None:
    store, xueqiu_raw = make_store(tmp_path, source="xueqiu")
    eastmoney_raw = RawEvidenceStore(tmp_path, source="eastmoney_guba")
    store.start_run("source-mismatch", "xueqiu", "scope:test", started_at=OLD, collector_version="c", parser_version="p", schema_version="xueqiu.raw.v1")
    store.record_attempt("source-mismatch", "mismatch-attempt", ordinal=0, request_kind="list", request_url="https://example.test/mismatch", started_at=OLD, finished_at=OLD, outcome="success", retry_number=1, retry_budget=1, http_status=200)
    wrong = eastmoney_raw.publish("source-mismatch", 0, b"wrong-source")
    with pytest.raises(PersistenceError, match="source"):
        store.record_raw_evidence("source-mismatch", "mismatch-attempt", "mismatch-evidence", wrong, evidence_kind="list", request_url="https://example.test/mismatch", final_url=None, fetched_at=OLD, http_status=200, content_type="application/json")
    assert store.conn.execute("SELECT count(*) FROM raw_evidence").fetchone()[0] == 0
    assert store.conn.execute("SELECT count(*) FROM raw_body_state").fetchone()[0] == 0

    store.record_attempt("source-mismatch", "correct-attempt", ordinal=1, request_kind="list", request_url="https://example.test/correct", started_at=OLD, finished_at=OLD, outcome="success", retry_number=1, retry_budget=1, http_status=200)
    correct = xueqiu_raw.publish("source-mismatch", 1, b"correct-source")
    store.record_raw_evidence("source-mismatch", "correct-attempt", "correct-evidence", correct, evidence_kind="list", request_url="https://example.test/correct", final_url=None, fetched_at=OLD, http_status=200, content_type="application/json")
    assert store.conn.execute("SELECT count(*) FROM raw_evidence").fetchone()[0] == 1
    assert store.conn.execute("SELECT count(*) FROM raw_body_state").fetchone()[0] == 1
    store.close()
