"""Tester-owned independent acceptance for raw evidence retention v0.1."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from myresearcher_collector.cli.main import main
from myresearcher_collector.models import XueqiuSourceItem
from myresearcher_collector.storage import (
    PersistenceError,
    RawBodyPurged,
    RawEvidenceStore,
    SafeFrontier,
    SQLitePersistence,
    connect_database,
    purge_raw_bodies,
)


UTC = timezone.utc
NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
OLD = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
YOUNG = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
HISTORICAL_SCHEMA_COMMIT = "580150a24944c01992dadbac3d49fdb372871b96"


def _store(tmp_path: Path, source: str = "xueqiu") -> tuple[SQLitePersistence, RawEvidenceStore]:
    raw = RawEvidenceStore(tmp_path, source=source)
    return SQLitePersistence(tmp_path / "collector.db", raw), raw


def _item(content: str, collected_at: datetime = OLD) -> XueqiuSourceItem:
    return XueqiuSourceItem(
        source="xueqiu", schema_version="xueqiu.raw.v1", source_item_id="retention-item",
        requested_bar_code="600519", canonical_bar_code="600519", canonical_bar_name="Synthetic",
        author_id="author", author_name="Author", title="Title", content=content,
        published_at=OLD, last_updated_at=None, display_time=None, url="https://example.test/item",
        post_type=0, post_state=0, post_top_status=0, read_count=0, reply_count=0,
        like_count=0, forward_count=0, source_post_id=None, collected_at=collected_at,
        source_times_raw={}, source_metadata={"fixture": "retention"}, raw_ref={},
    )


def _evidence(
    store: SQLitePersistence,
    raw: RawEvidenceStore,
    run_id: str,
    payload: bytes,
    fetched_at: datetime,
    *,
    status: str = "SUCCESS",
    failure_class: str | None = None,
    observation: bool = False,
) -> tuple[str, str, Path]:
    store.start_run(run_id, raw.source, "scope:retention", started_at=fetched_at,
                    collector_version="retention.test", parser_version="retention.test",
                    schema_version="xueqiu.raw.v1")
    attempt_id = f"{run_id}-attempt"
    evidence_id = f"{run_id}-evidence"
    request_url = f"https://example.test/request/{run_id}"
    store.record_attempt(run_id, attempt_id, ordinal=0, request_kind="list",
                         request_url=request_url, started_at=fetched_at, finished_at=fetched_at,
                         outcome="success", retry_number=1, retry_budget=1, http_status=200)
    published = raw.publish(run_id, 0, payload)
    store.record_raw_evidence(run_id, attempt_id, evidence_id, published, evidence_kind="list",
                              request_url=request_url, final_url=f"{request_url}/final",
                              fetched_at=fetched_at, http_status=200, content_type="application/json")
    if observation:
        store.record_observation(run_id, _item("retained observation", fetched_at),
                                 scope_key="scope:retention", evidence_links=[(evidence_id, "list")])
    if failure_class:
        store.record_failure(run_id, f"{run_id}-failure", phase="collect",
                             failure_class=failure_class, occurred_at=fetched_at,
                             message="independent retention fixture", evidence_id=evidence_id,
                             attempt_id=attempt_id)
    if status != "RUNNING":
        store.finish_run(run_id, status=status, finished_at=fetched_at)
    return evidence_id, published.sha256, published.absolute_path


def _purge(store: SQLitePersistence, tmp_path: Path, **kwargs):
    store.close()
    return purge_raw_bodies(db_path=tmp_path / "collector.db", raw_data_dir=tmp_path, now=NOW, **kwargs)


def _state(tmp_path: Path, digest: str) -> str:
    conn = sqlite3.connect(tmp_path / "collector.db")
    try:
        return conn.execute("SELECT body_state FROM raw_body_state WHERE source='xueqiu' AND content_sha256=?", (digest,)).fetchone()[0]
    finally:
        conn.close()


def test_ret001_metadata_lineage_survives_body_purge(tmp_path: Path) -> None:
    store, raw = _store(tmp_path)
    evidence_id, digest, body = _evidence(store, raw, "ret001", b"permanent-body", OLD, observation=True)
    result = _purge(store, tmp_path, dry_run=False, confirm=True)
    assert result.purged_objects == 1
    assert not body.exists()
    assert _state(tmp_path, digest) == "PURGED"
    conn = sqlite3.connect(tmp_path / "collector.db")
    try:
        metadata = conn.execute(
            "SELECT request_url, final_url, content_sha256, byte_size FROM raw_evidence WHERE evidence_id=?",
            (evidence_id,),
        ).fetchone()
        assert metadata == (
            "https://example.test/request/ret001",
            "https://example.test/request/ret001/final",
            digest,
            len(b"permanent-body"),
        )
        assert conn.execute("SELECT count(*) FROM observation_evidence").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM source_item_observations").fetchone()[0] == 1
    finally:
        conn.close()


def test_ret002_young_body_is_present_and_retained(tmp_path: Path) -> None:
    store, raw = _store(tmp_path)
    _, digest, body = _evidence(store, raw, "ret002", b"young-body", YOUNG)
    result = _purge(store, tmp_path, dry_run=False, confirm=True)
    assert result.retained_recent == 1
    assert result.purged_objects == 0
    assert _state(tmp_path, digest) == "PRESENT"
    assert body.exists()


def test_ret003_ret004_shared_sha_requires_all_references_to_expire(tmp_path: Path) -> None:
    store, raw = _store(tmp_path)
    _, digest, body = _evidence(store, raw, "ret003-old", b"shared-body", OLD)
    _evidence(store, raw, "ret003-young", b"shared-body", YOUNG)
    mixed = _purge(store, tmp_path, dry_run=False, confirm=True)
    assert mixed.purged_objects == 0
    assert body.exists() and _state(tmp_path, digest) == "PRESENT"

    store2, raw2 = _store(tmp_path / "all-old")
    _, digest2, body2 = _evidence(store2, raw2, "ret004-a", b"shared-body", OLD)
    _evidence(store2, raw2, "ret004-b", b"shared-body", OLD)
    all_old = _purge(store2, tmp_path / "all-old", dry_run=False, confirm=True)
    assert all_old.eligible_objects == all_old.purged_objects == 1
    assert not body2.exists() and _state(tmp_path / "all-old", digest2) == "PURGED"
    conn = sqlite3.connect(tmp_path / "all-old" / "collector.db")
    try:
        assert conn.execute("SELECT count(*) FROM raw_evidence WHERE content_sha256=?", (digest2,)).fetchone()[0] == 2
    finally:
        conn.close()


@pytest.mark.parametrize("failure_class,status", [("COLLECTION_FAILED", "COLLECTION_FAILED"), ("SPEC_MISMATCH", "SPEC_MISMATCH")])
def test_ret005_ret006_failure_holds_keep_old_body(tmp_path: Path, failure_class: str, status: str) -> None:
    store, raw = _store(tmp_path)
    _, digest, body = _evidence(store, raw, f"ret-{failure_class}", b"failure-body", OLD,
                                 status=status, failure_class=failure_class)
    result = _purge(store, tmp_path, dry_run=False, confirm=True)
    assert result.retained_failure == 1 and result.purged_objects == 0
    assert _state(tmp_path, digest) == "PRESENT" and body.exists()


def test_ret007_ret008_ret009_purged_missing_and_republish_semantics(tmp_path: Path) -> None:
    store, raw = _store(tmp_path)
    evidence_id, digest, body = _evidence(store, raw, "ret007", b"purge-me", OLD)
    _purge(store, tmp_path, dry_run=False, confirm=True)
    reopened = SQLitePersistence(tmp_path / "collector.db", raw)
    try:
        with pytest.raises(RawBodyPurged):
            reopened.verify_evidence(evidence_id)
        # A republish of the same SHA restores PRESENT and keeps old metadata.
        _evidence(reopened, raw, "ret009", b"purge-me", NOW)
        assert _state(tmp_path, digest) == "PRESENT" and body.exists()
        assert reopened.conn.execute("SELECT count(*) FROM raw_evidence WHERE content_sha256=?", (digest,)).fetchone()[0] == 2
    finally:
        reopened.close()

    store2, raw2 = _store(tmp_path / "missing")
    evidence_id2, digest2, body2 = _evidence(store2, raw2, "ret008", b"missing", OLD)
    body2.unlink()
    result = _purge(store2, tmp_path / "missing", dry_run=False, confirm=True)
    assert result.purged_objects == 0 and any("unexpectedly missing" in e for e in result.errors)
    reopened2 = SQLitePersistence(tmp_path / "missing" / "collector.db", raw2)
    try:
        with pytest.raises(PersistenceError):
            reopened2.verify_evidence(evidence_id2)
        assert _state(tmp_path / "missing", digest2) == "PRESENT"
    finally:
        reopened2.close()


def test_ret010_mixed_dry_run_is_strictly_read_only(tmp_path: Path) -> None:
    store, raw = _store(tmp_path)
    _, digest_a, body_a = _evidence(store, raw, "ret010-a", b"A", OLD)
    _, digest_b, body_b = _evidence(store, raw, "ret010-b", b"B", OLD)
    _, digest_c, body_c = _evidence(store, raw, "ret010-c", b"C", OLD)
    _, digest_d, body_d = _evidence(store, raw, "ret010-d", b"D", YOUNG)
    _purge(store, tmp_path, dry_run=False, confirm=True)  # first purge C later restored below
    # Recreate the mixed fixture from the persisted states without changing DB metadata.
    conn = sqlite3.connect(tmp_path / "collector.db")
    conn.execute("UPDATE raw_body_state SET body_state='PRESENT', purged_at_utc=NULL WHERE content_sha256=?", (digest_a,))
    conn.execute("UPDATE raw_body_state SET body_state='PRESENT', purged_at_utc=NULL WHERE content_sha256=?", (digest_b,))
    conn.execute("UPDATE raw_body_state SET body_state='PURGED' WHERE content_sha256=?", (digest_c,))
    conn.commit(); conn.close()
    body_a.write_bytes(b"A")
    body_b = raw.publish("ret010-b-republish", 0, b"B").absolute_path
    body_b.rename(body_b.with_name(body_b.name + ".purging"))
    body_c.with_name(body_c.name + ".purging").write_bytes(b"C")
    before_files = sorted((str(p.relative_to(tmp_path)), p.read_bytes()) for p in (tmp_path / "raw").rglob("*") if p.is_file())
    conn = sqlite3.connect(tmp_path / "collector.db")
    before_db = {
        "state": conn.execute("SELECT * FROM raw_body_state ORDER BY source, content_sha256").fetchall(),
        "evidence": conn.execute("SELECT * FROM raw_evidence ORDER BY evidence_id").fetchall(),
        "links": conn.execute("SELECT * FROM observation_evidence ORDER BY observation_id, evidence_id").fetchall(),
    }
    conn.close()
    result = purge_raw_bodies(db_path=tmp_path / "collector.db", raw_data_dir=tmp_path, now=NOW, dry_run=True, confirm=False)
    assert result.eligible_objects == 1 and result.purged_objects == 0
    assert result.recovery_required == 1 and result.cleanup_required == 1
    assert body_a.exists() and body_b.with_name(body_b.name + ".purging").exists()
    assert body_c.with_name(body_c.name + ".purging").exists() and body_d.exists()
    after_files = sorted((str(p.relative_to(tmp_path)), p.read_bytes()) for p in (tmp_path / "raw").rglob("*") if p.is_file())
    conn = sqlite3.connect(tmp_path / "collector.db")
    try:
        after_db = {
            "state": conn.execute("SELECT * FROM raw_body_state ORDER BY source, content_sha256").fetchall(),
            "evidence": conn.execute("SELECT * FROM raw_evidence ORDER BY evidence_id").fetchall(),
            "links": conn.execute("SELECT * FROM observation_evidence ORDER BY observation_id, evidence_id").fetchall(),
        }
    finally:
        conn.close()
    assert after_files == before_files and after_db == before_db


def test_ret012_interrupted_purge_recovery_and_db_failure_restore(tmp_path: Path) -> None:
    store, raw = _store(tmp_path)
    _, digest, body = _evidence(store, raw, "ret012a", b"atomic", OLD)
    conn = sqlite3.connect(tmp_path / "collector.db")
    conn.execute("""CREATE TRIGGER fail_retention_update BEFORE UPDATE OF body_state ON raw_body_state BEGIN SELECT RAISE(ABORT, 'fault'); END""")
    conn.commit(); conn.close()
    result = _purge(store, tmp_path, dry_run=False, confirm=True)
    assert result.purged_objects == 0 and _state(tmp_path, digest) == "PRESENT"
    assert body.exists() and not body.with_name(body.name + ".purging").exists()

    store2, raw2 = _store(tmp_path / "recover")
    _, digest2, body2 = _evidence(store2, raw2, "ret012b", b"recover", OLD)
    tombstone2 = body2.with_name(body2.name + ".purging")
    body2.rename(tombstone2)
    dry = _purge(store2, tmp_path / "recover", dry_run=True, confirm=False)
    assert dry.recovery_required == 1 and tombstone2.exists() and not body2.exists()
    confirm = purge_raw_bodies(db_path=tmp_path / "recover" / "collector.db", raw_data_dir=tmp_path / "recover", now=NOW, dry_run=False, confirm=True)
    assert confirm.errors == () and body2.exists() and not tombstone2.exists() and _state(tmp_path / "recover", digest2) == "PRESENT"


def test_ret012c_purged_tombstone_cleanup_and_ret014_running_hold(tmp_path: Path) -> None:
    store, raw = _store(tmp_path)
    _, digest, body = _evidence(store, raw, "ret012c", b"cleanup", OLD)
    _purge(store, tmp_path, dry_run=False, confirm=True)
    tombstone = body.with_name(body.name + ".purging")
    tombstone.write_bytes(b"stale")
    dry = purge_raw_bodies(db_path=tmp_path / "collector.db", raw_data_dir=tmp_path, now=NOW, dry_run=True, confirm=False)
    assert dry.cleanup_required == 1 and tombstone.exists()
    purge_raw_bodies(db_path=tmp_path / "collector.db", raw_data_dir=tmp_path, now=NOW, dry_run=False, confirm=True)
    assert not tombstone.exists() and _state(tmp_path, digest) == "PURGED"

    store2, raw2 = _store(tmp_path / "running")
    _, digest2, body2 = _evidence(store2, raw2, "ret014", b"running", OLD, status="RUNNING")
    held = _purge(store2, tmp_path / "running", dry_run=False, confirm=True)
    assert held.retained_recent == 1 and body2.exists() and _state(tmp_path / "running", digest2) == "PRESENT"


def test_ret013_source_identity_fails_closed_before_metadata_insert(tmp_path: Path) -> None:
    store, xueqiu_raw = _store(tmp_path, source="xueqiu")
    eastmoney_raw = RawEvidenceStore(tmp_path, source="eastmoney_guba")
    store.start_run("ret013", "xueqiu", "scope:retention", started_at=OLD,
                    collector_version="c", parser_version="p", schema_version="xueqiu.raw.v1")
    attempt = "ret013-attempt"
    store.record_attempt("ret013", attempt, ordinal=0, request_kind="list", request_url="https://example.test/ret013",
                         started_at=OLD, finished_at=OLD, outcome="success", retry_number=1, retry_budget=1, http_status=200)
    wrong = eastmoney_raw.publish("ret013", 0, b"wrong-source")
    with pytest.raises(PersistenceError, match="source"):
        store.record_raw_evidence("ret013", attempt, "ret013-evidence", wrong, evidence_kind="list",
                                  request_url="https://example.test/ret013", final_url=None, fetched_at=OLD,
                                  http_status=200, content_type="application/json")
    assert store.conn.execute("SELECT count(*) FROM raw_evidence").fetchone()[0] == 0
    assert store.conn.execute("SELECT count(*) FROM raw_body_state").fetchone()[0] == 0
    correct = xueqiu_raw.publish("ret013", 1, b"correct-source")
    store.record_attempt("ret013", "ret013-attempt-2", ordinal=1, request_kind="list", request_url="https://example.test/ret013/2",
                         started_at=OLD, finished_at=OLD, outcome="success", retry_number=1, retry_budget=1, http_status=200)
    store.record_raw_evidence("ret013", "ret013-attempt-2", "ret013-evidence-2", correct, evidence_kind="list",
                              request_url="https://example.test/ret013/2", final_url=None, fetched_at=OLD,
                              http_status=200, content_type="application/json")
    assert store.conn.execute("SELECT count(*) FROM raw_body_state").fetchone()[0] == 1
    store.close()


def test_ret011_real_historical_v1_migrates_in_place_and_preserves_rows(tmp_path: Path) -> None:
    """Build the schema from the pre-retention production commit, not a downgrade."""
    source = subprocess.check_output(
        ["git", "show", f"{HISTORICAL_SCHEMA_COMMIT}:src/myresearcher_collector/storage/schema.py"],
        text=True,
    )
    namespace: dict[str, object] = {}
    exec(compile(source, "historical-schema.py", "exec"), namespace)
    assert namespace["SCHEMA_VERSION"] == 1
    migration_sql = namespace["MIGRATION_SQL"]
    checksum = namespace["MIGRATION_CHECKSUM"]
    assert isinstance(migration_sql, str) and isinstance(checksum, str)

    data_dir = tmp_path / "historical-v1"
    data_dir.mkdir()
    raw = RawEvidenceStore(data_dir, source="xueqiu")
    published = raw.publish("v1-run", 0, b"historical-v1-body")
    db_path = data_dir / "collector.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(migration_sql)
    conn.execute("PRAGMA user_version=1")
    now = "2026-08-01T12:00:00.000000Z"
    conn.execute("INSERT INTO schema_migrations(version, applied_at_utc, checksum) VALUES(?,?,?)", (1, now, checksum))
    conn.execute("INSERT INTO collection_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", ("v1-run", "xueqiu", "scope:retention", "SUCCESS", now, now, "c", "p", "xueqiu.raw.v1", None, now, now, "{}"))
    conn.execute("INSERT INTO collection_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("v1-attempt", "v1-run", 0, "list", "https://example.test/v1", now, now, "success", 200, None, 1, 1, None, None))
    conn.execute("INSERT INTO raw_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", ("v1-evidence", "v1-run", "v1-attempt", "list", "https://example.test/v1", "https://example.test/v1/final", now, 200, "application/json", published.sha256, published.byte_size, published.relative_path, "raw.v1"))
    conn.execute("INSERT INTO source_item_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("v1-observation", "xueqiu", "v1-item", 1, now, now, None, None, "a", "A", "title", "body", hashlib.sha256(b"body").hexdigest(), "https://example.test/item", "600519", "Synthetic", 0, 0, 0, 0, 0, 0, 0, "{}", "{}", hashlib.sha256(b"facts").hexdigest(), "xueqiu.raw.v1", "c", "p", None))
    conn.execute("INSERT INTO observation_evidence VALUES(?,?,?)", ("v1-observation", "v1-evidence", "list"))
    conn.execute("INSERT INTO observation_scopes VALUES(?,?,?)", ("v1-observation", "scope:retention", "600519"))
    conn.execute("INSERT INTO collector_checkpoints VALUES(?,?,?, ?,?)", ("xueqiu", "scope:retention", now, "v1-run", now))
    conn.commit(); conn.close()

    migrated = connect_database(db_path)
    try:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == 2
        for table in ("collection_runs", "collection_attempts", "raw_evidence", "source_item_observations", "observation_evidence", "collector_checkpoints"):
            assert migrated.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1
        assert migrated.execute("SELECT watermark_utc FROM collector_checkpoints").fetchone()[0] == now
        assert migrated.execute("SELECT body_state FROM raw_body_state WHERE source='xueqiu' AND content_sha256=?", (published.sha256,)).fetchone()[0] == "PRESENT"
        assert published.absolute_path.exists()
    finally:
        migrated.close()


def test_retention_cli_default_is_read_only_and_confirm_is_required(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store, raw = _store(tmp_path)
    _, digest, body = _evidence(store, raw, "ret-cli", b"cli-body", OLD)
    store.close()
    assert main(["raw-retention", "--data-dir", str(tmp_path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["eligible_objects"] == 1 and output["purged_objects"] == 0 and output["physical_bytes_purged"] == 0
    assert body.exists() and _state(tmp_path, digest) == "PRESENT"
    assert main(["raw-retention", "--data-dir", str(tmp_path), "--confirm"]) == 0
    json.loads(capsys.readouterr().out)
    assert not body.exists() and _state(tmp_path, digest) == "PURGED"
