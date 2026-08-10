"""Offline implementation tests for the Phase 2 local persistence boundary."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from myresearcher_collector.models import GubaSourceItem
from myresearcher_collector.sources.eastmoney_guba.parser import parse_detail_page, parse_list_page
from myresearcher_collector.storage import (
    PersistenceError,
    RawEvidenceStore,
    RawStoreError,
    SafeFrontier,
    SchemaError,
    SQLitePersistence,
    connect_database,
)


FIXTURES = Path(__file__).parents[1] / "fixtures" / "eastmoney_guba"
NOW = datetime(2026, 8, 10, 3, 0, tzinfo=timezone.utc)


def item(*, item_id: str = "1001", content: str = "synthetic body one") -> GubaSourceItem:
    row = parse_list_page((FIXTURES / "list_page_1.html").read_text(), "600001").rows[0]
    detail = parse_detail_page((FIXTURES / "detail_1001.html").read_text())
    return GubaSourceItem(
        source="eastmoney_guba",
        schema_version="eastmoney_guba.raw.v1",
        source_item_id=item_id,
        requested_bar_code="600001",
        canonical_bar_code="600001",
        canonical_bar_name="Synthetic Bar",
        author_id="9001",
        author_name="author-one",
        title="synthetic post one",
        content=content,
        published_at=detail.published_at,
        last_updated_at=detail.last_updated_at,
        display_time=detail.display_time,
        url=row.url.replace("1001", item_id),
        post_type=0,
        post_state=0,
        post_top_status=0,
        read_count=0,
        reply_count=0,
        like_count=0,
        forward_count=0,
        source_post_id=None,
        collected_at=NOW,
        source_times_raw=detail.source_times_raw,
        source_metadata={"extra": {"synthetic": True}},
        raw_ref={},
    )


def make_store(tmp_path: Path) -> tuple[SQLitePersistence, RawEvidenceStore]:
    raw = RawEvidenceStore(tmp_path)
    return SQLitePersistence(tmp_path / "collector.db", raw), raw


def start_run(store: SQLitePersistence, run_id: str = "run-1", scope: str = "stock:600001") -> None:
    store.start_run(
        run_id,
        "eastmoney_guba",
        scope,
        started_at=NOW,
        collector_version="collector.v1",
        parser_version="parser.v1",
        schema_version="eastmoney_guba.raw.v1",
    )


def evidence(store: SQLitePersistence, raw: RawEvidenceStore, run_id: str = "run-1", *, ordinal: int = 0, kind: str = "list") -> str:
    attempt_id = f"{run_id}-attempt-{ordinal}"
    evidence_id = f"{run_id}-evidence-{ordinal}"
    url = "https://guba.eastmoney.com/list,600001,f.html" if kind == "list" else "https://guba.eastmoney.com/news,600001,1001.html"
    store.record_attempt(
        run_id,
        attempt_id,
        ordinal=ordinal,
        request_kind=kind,
        request_url=url,
        started_at=NOW,
        finished_at=NOW,
        outcome="success",
        retry_number=1,
        retry_budget=3,
    )
    published = raw.publish(run_id, ordinal, f"raw-{run_id}-{ordinal}".encode())
    store.record_raw_evidence(
        run_id,
        attempt_id,
        evidence_id,
        published,
        evidence_kind=kind,
        request_url=url,
        final_url=url,
        fetched_at=NOW,
        http_status=200,
        content_type="text/html",
    )
    return evidence_id


def test_fresh_reopen_foreign_keys_and_schema_drift_rejection(tmp_path: Path) -> None:
    db = tmp_path / "collector.db"
    conn = connect_database(db)
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()
    connect_database(db).close()

    drift = sqlite3.connect(db)
    drift.execute("ALTER TABLE collection_runs ADD COLUMN drift_marker TEXT")
    drift.commit()
    drift.close()
    with pytest.raises(SchemaError):
        connect_database(db)


def test_unknown_existing_database_is_rejected(tmp_path: Path) -> None:
    db = tmp_path / "unknown.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE someone_elses_table (value TEXT)")
    conn.commit()
    conn.close()
    with pytest.raises(SchemaError):
        connect_database(db)


def test_sql_shape_rejects_invalid_timestamp_and_negative_numeric(tmp_path: Path) -> None:
    conn = connect_database(tmp_path / "collector.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO collection_runs(run_id,source,scope_key,status,started_at_utc,
               collector_version,parser_version,schema_version,counters_json)
               VALUES ('r','s','scope','RUNNING','not-a-time','c','p','s','{}')"""
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO collection_runs(run_id,source,scope_key,status,started_at_utc,
               collector_version,parser_version,schema_version,counters_json)
               VALUES ('r','s','scope','RUNNING','2026-08-10T03:00:00.000000Z','c','p','s','{}')"""
        )
        conn.execute(
            """INSERT INTO collection_attempts(attempt_id,run_id,attempt_ordinal,request_kind,
               request_url,started_at_utc,outcome,retry_number,retry_budget)
               VALUES ('a','r',0,'list','https://x','2026-08-10T03:00:00.000000Z','success',0,3)"""
        )


def test_raw_publish_reuses_bytes_and_keeps_separate_lineage(tmp_path: Path) -> None:
    raw = RawEvidenceStore(tmp_path)
    first = raw.publish("run-1", 0, b"same")
    second = raw.publish("run-1", 1, b"same")
    assert first.relative_path == second.relative_path
    assert len(list((tmp_path / "raw" / ".tmp").glob("*.partial"))) == 0


def test_raw_temp_write_failure_creates_no_publishable_reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = RawEvidenceStore(tmp_path)
    original_open = Path.open

    def fail_partial(path: Path, *args: object, **kwargs: object):
        if path.name.endswith(".partial"):
            raise OSError("synthetic temp write failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_partial)
    with pytest.raises(OSError):
        raw.publish("run-1", 0, b"failure")
    assert not list((tmp_path / "raw" / ".tmp").glob("*.partial"))


def test_raw_collision_and_missing_reference_fail_closed(tmp_path: Path) -> None:
    raw = RawEvidenceStore(tmp_path)
    published = raw.publish("run-1", 0, b"correct")
    published.absolute_path.write_bytes(b"wrong")
    with pytest.raises(RawStoreError):
        raw.publish("run-1", 1, b"correct")

    published.absolute_path.write_bytes(b"correct")
    store = SQLitePersistence(tmp_path / "collector.db", raw)
    start_run(store)
    eid = evidence(store, raw)
    referenced = store.conn.execute("SELECT filesystem_path FROM raw_evidence WHERE evidence_id=?", (eid,)).fetchone()[0]
    (tmp_path / referenced).unlink()
    with pytest.raises(PersistenceError):
        store.verify_evidence(eid)


def test_orphan_after_sqlite_reference_failure_is_not_success(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    start_run(store)
    published = raw.publish("run-1", 0, b"orphan")
    with pytest.raises(PersistenceError):
        store.record_raw_evidence(
            "run-1", "missing-attempt", "evidence", published,
            evidence_kind="list", request_url="https://x", final_url=None,
            fetched_at=NOW, http_status=200, content_type="text/html",
        )
    assert store.conn.execute("SELECT count(*) FROM raw_evidence").fetchone()[0] == 0
    assert published.absolute_path.exists()


def test_observation_idempotency_versioning_scope_and_immutability(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    start_run(store)
    list_evidence = evidence(store, raw, ordinal=0, kind="list")
    detail_evidence = evidence(store, raw, ordinal=1, kind="detail")
    first = item()
    one = store.record_observation("run-1", first, scope_key="stock:600001", evidence_links=[(list_evidence, "list"), (detail_evidence, "detail")])
    same = store.record_observation("run-1", first, scope_key="stock:600002", evidence_links=[(list_evidence, "list")])
    changed = store.record_observation("run-1", replace(first, content="changed"), scope_key="stock:600001", evidence_links=[(list_evidence, "list"), (detail_evidence, "detail")])
    assert one[1:] == (1, True)
    assert same[0:2] == (one[0], 1) and same[2] is False
    assert changed[1:] == (2, True)
    assert store.conn.execute("SELECT count(*) FROM source_item_observations").fetchone()[0] == 2
    assert store.conn.execute("SELECT count(*) FROM observation_scopes").fetchone()[0] == 3
    assert store.conn.execute("SELECT drift_from_observation_id FROM source_item_observations WHERE observation_version=2").fetchone()[0] == one[0]
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("UPDATE source_item_observations SET title='overwrite' WHERE observation_id=?", (one[0],))
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("DELETE FROM source_item_observations WHERE observation_id=?", (one[0],))


def test_lineage_and_physical_dedup_keep_two_evidence_rows(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    start_run(store)
    list_evidence = evidence(store, raw, ordinal=0, kind="list")
    detail_evidence = evidence(store, raw, ordinal=1, kind="detail")
    store.record_observation("run-1", item(), scope_key="stock:600001", evidence_links=[(list_evidence, "list"), (detail_evidence, "detail")])
    assert store.conn.execute("SELECT count(*) FROM raw_evidence").fetchone()[0] == 2
    assert store.conn.execute("SELECT count(*) FROM observation_evidence").fetchone()[0] == 2
    assert store.conn.execute("SELECT count(*) FROM collection_attempts").fetchone()[0] == 2


def test_evidence_roles_are_source_agnostic_single_direct_support_is_allowed(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    start_run(store)
    direct = evidence(store, raw, ordinal=0, kind="other_approved")
    observation_id, version, created = store.record_observation(
        "run-1", item(), scope_key="stock:600001", evidence_links=[(direct, "direct")]
    )
    assert observation_id and version == 1 and created is True
    assert store.conn.execute("SELECT evidence_role FROM observation_evidence").fetchone()[0] == "direct"


def test_grouped_transaction_rolls_back_metadata_and_never_reports_success(tmp_path: Path) -> None:
    store, raw = make_store(tmp_path)
    start_run(store)
    with pytest.raises(PersistenceError):
        with store.transaction():
            attempt_id = "run-1-attempt-0"
            store.record_attempt(
                "run-1", attempt_id, ordinal=0, request_kind="list",
                request_url="https://x", started_at=NOW, finished_at=NOW,
                outcome="success", retry_number=1, retry_budget=1,
            )
            store.record_observation("run-1", item(), scope_key="stock:600001", evidence_links=[("missing", "direct")])
    assert store.conn.execute("SELECT count(*) FROM collection_attempts").fetchone()[0] == 0
    assert store.conn.execute("SELECT status FROM collection_runs WHERE run_id='run-1'").fetchone()[0] == "RUNNING"


@pytest.mark.parametrize("status", ["SUCCESS", "NO_NEW_DATA", "PARTIAL_COLLECTION"])
def test_safe_frontier_commits_for_success_no_data_and_partial(tmp_path: Path, status: str) -> None:
    store, _ = make_store(tmp_path)
    start_run(store)
    assert store.finish_run("run-1", status=status, finished_at=NOW, safe_frontier=SafeFrontier(NOW)) is True
    assert store.checkpoint("eastmoney_guba", "stock:600001") == ("2026-08-10T03:00:00.000000Z", "run-1")


def test_checkpoint_allows_forward_advance(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    t1 = datetime(2026, 8, 10, 1, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 8, 10, 2, 0, tzinfo=timezone.utc)
    start_run(store, "run-1")
    assert store.finish_run("run-1", status="SUCCESS", finished_at=NOW, safe_frontier=SafeFrontier(t1)) is True
    start_run(store, "run-2")
    assert store.finish_run("run-2", status="SUCCESS", finished_at=NOW, safe_frontier=SafeFrontier(t2)) is True
    assert store.checkpoint("eastmoney_guba", "stock:600001") == ("2026-08-10T02:00:00.000000Z", "run-2")


def test_checkpoint_equal_frontier_is_idempotent(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    t1 = datetime(2026, 8, 10, 1, 0, tzinfo=timezone.utc)
    start_run(store, "run-1")
    assert store.finish_run("run-1", status="SUCCESS", finished_at=NOW, safe_frontier=SafeFrontier(t1)) is True
    start_run(store, "run-2")
    assert store.finish_run("run-2", status="SUCCESS", finished_at=NOW, safe_frontier=SafeFrontier(t1)) is True
    assert store.checkpoint("eastmoney_guba", "stock:600001") == ("2026-08-10T01:00:00.000000Z", "run-2")


def test_checkpoint_regression_fails_closed_and_keeps_run_running(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    t1 = datetime(2026, 8, 10, 1, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 8, 10, 2, 0, tzinfo=timezone.utc)
    start_run(store, "run-1")
    assert store.finish_run("run-1", status="SUCCESS", finished_at=NOW, safe_frontier=SafeFrontier(t2)) is True
    start_run(store, "run-2")
    with pytest.raises(PersistenceError, match="checkpoint frontier regression"):
        store.finish_run("run-2", status="SUCCESS", finished_at=NOW, safe_frontier=SafeFrontier(t1))
    assert store.checkpoint("eastmoney_guba", "stock:600001") == ("2026-08-10T02:00:00.000000Z", "run-1")
    assert store.conn.execute("SELECT status, watermark_after_utc FROM collection_runs WHERE run_id='run-2'").fetchone() == ("RUNNING", None)


def test_partial_checkpoint_regression_fails_closed(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    t1 = datetime(2026, 8, 10, 1, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 8, 10, 2, 0, tzinfo=timezone.utc)
    start_run(store, "run-1")
    assert store.finish_run("run-1", status="PARTIAL_COLLECTION", finished_at=NOW, safe_frontier=SafeFrontier(t2)) is True
    start_run(store, "run-2")
    with pytest.raises(PersistenceError, match="checkpoint frontier regression"):
        store.finish_run("run-2", status="PARTIAL_COLLECTION", finished_at=NOW, safe_frontier=SafeFrontier(t1))
    assert store.checkpoint("eastmoney_guba", "stock:600001") == ("2026-08-10T02:00:00.000000Z", "run-1")
    assert store.conn.execute("SELECT status FROM collection_runs WHERE run_id='run-2'").fetchone()[0] == "RUNNING"


def test_partial_without_frontier_and_unresolved_gap_do_not_advance(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    start_run(store)
    assert store.finish_run("run-1", status="PARTIAL_COLLECTION", finished_at=NOW) is False
    assert store.checkpoint("eastmoney_guba", "stock:600001") is None

    store2, _ = make_store(tmp_path / "second")
    start_run(store2)
    frontier = SafeFrontier(NOW, unresolved_gaps=("eligible item",))
    assert store2.finish_run("run-1", status="PARTIAL_COLLECTION", finished_at=NOW, safe_frontier=frontier) is False
    assert store2.checkpoint("eastmoney_guba", "stock:600001") is None


@pytest.mark.parametrize("status", ["COLLECTION_FAILED", "SPEC_MISMATCH", "CANCELLED"])
def test_unsafe_terminal_status_does_not_advance_checkpoint(tmp_path: Path, status: str) -> None:
    store, _ = make_store(tmp_path)
    start_run(store)
    assert store.finish_run("run-1", status=status, finished_at=NOW, safe_frontier=SafeFrontier(NOW)) is False
    assert store.checkpoint("eastmoney_guba", "stock:600001") is None
