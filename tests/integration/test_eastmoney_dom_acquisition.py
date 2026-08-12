"""DOM acquisition through production parser, RawEvidence and SQLite."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from myresearcher_collector.integration import execute_and_persist_backfill_collection
from myresearcher_collector.models import CollectionStatus
from myresearcher_collector.sources.eastmoney_guba import (
    AcquiredDocument,
    BROWSER_DOM_SNAPSHOT,
    CollectorConfig,
    EastmoneyGubaCollector,
    ExistingChromeAcquisitionError,
)
from myresearcher_collector.storage import RawEvidenceStore, SQLitePersistence


FIXTURES = Path(__file__).parents[1] / "fixtures" / "eastmoney_guba"
NOW = datetime(2026, 8, 12, 4, 0, tzinfo=timezone.utc)


class DomMappingTransport:
    def __init__(self) -> None:
        self.payloads: dict[str, bytes] = {}

    def get(self, url: str, *, timeout: float) -> AcquiredDocument:
        del timeout
        if ",f.html" in url:
            payload = (FIXTURES / "list_page_1.html").read_bytes()
        elif ",f_2.html" in url:
            payload = (FIXTURES / "empty_page.html").read_bytes()
        else:
            item_id = url.split(",")[-1].split(".")[0]
            payload = (FIXTURES / f"detail_{item_id}.html").read_bytes()
        self.payloads[url] = payload
        return AcquiredDocument(
            payload=payload,
            request_url=url,
            observed_url=url,
            capture_method=BROWSER_DOM_SNAPSHOT,
            fetched_at=NOW,
        )


def run(tmp_path: Path, run_id: str):
    transport = DomMappingTransport()
    result = execute_and_persist_backfill_collection(
        db_path=tmp_path / "collector.db",
        raw_data_dir=tmp_path / "data",
        stock_code="600001",
        from_time=datetime(2026, 8, 9, tzinfo=timezone.utc),
        to_time=datetime(2026, 8, 11, tzinfo=timezone.utc),
        transport=transport,
        run_id=run_id,
        collector_config=CollectorConfig(
            max_pages=2,
            min_interval_seconds=2.5,
            base_backoff_seconds=0,
        ),
        clock=lambda: NOW,
        sleep_fn=lambda _seconds: None,
        max_pages=2,
    )
    return result, transport


def test_dom_acquisition_uses_production_parser_and_persists_exact_consumed_bytes(tmp_path: Path) -> None:
    execution, transport = run(tmp_path, "dom-backfill-1")
    assert execution.execution.result.status is CollectionStatus.SUCCESS
    assert execution.records_new == 1
    assert execution.checkpoint_before == execution.checkpoint_after is None

    store = SQLitePersistence(
        tmp_path / "collector.db",
        RawEvidenceStore(tmp_path / "data", source="eastmoney_guba"),
    )
    try:
        rows = store.conn.execute(
            """SELECT evidence_id, request_url, evidence_kind, http_status,
                      content_type, final_url, filesystem_path, content_sha256
                 FROM raw_evidence WHERE run_id=? ORDER BY evidence_id""",
            (execution.run_id,),
        ).fetchall()
        assert len(rows) == len(transport.payloads)
        assert all(row[2].endswith(":browser_dom_snapshot") for row in rows)
        assert all(row[3] is None and row[4] is None for row in rows)
        assert all(row[5] == row[1] for row in rows)
        for row in rows:
            persisted = (tmp_path / "data" / row[6]).read_bytes()
            assert persisted == transport.payloads[row[1]]
            assert hashlib.sha256(persisted).hexdigest() == row[7]
        assert store.conn.execute("SELECT count(*) FROM observation_evidence").fetchone()[0] == 2
    finally:
        store.close()


def test_dom_backfill_rerun_is_idempotent_and_checkpoint_does_not_move(tmp_path: Path) -> None:
    first, _ = run(tmp_path, "dom-backfill-first")
    second, _ = run(tmp_path, "dom-backfill-second")
    assert first.records_new == 1
    assert second.records_new == 0
    assert second.records_existing == 1
    assert second.records_versioned == 0
    assert second.checkpoint_before == second.checkpoint_after is None

    store = SQLitePersistence(
        tmp_path / "collector.db",
        RawEvidenceStore(tmp_path / "data", source="eastmoney_guba"),
    )
    try:
        assert store.conn.execute(
            "SELECT count(*) FROM source_item_observations"
        ).fetchone()[0] == 1
    finally:
        store.close()


def test_dom_access_block_is_not_empty_success(tmp_path: Path) -> None:
    blocked = (FIXTURES / "verification_page.html").read_bytes()

    class BlockedTransport:
        def get(self, url: str, *, timeout: float) -> AcquiredDocument:
            del timeout
            return AcquiredDocument(
                payload=blocked,
                request_url=url,
                observed_url=url,
                capture_method=BROWSER_DOM_SNAPSHOT,
                fetched_at=NOW,
            )

    collector = EastmoneyGubaCollector(
        BlockedTransport(),
        config=CollectorConfig(
            max_pages=1,
            max_attempts=1,
            access_block_attempts=1,
            min_interval_seconds=2.5,
            base_backoff_seconds=0,
        ),
        sleep_fn=lambda _seconds: None,
    )
    result = collector.collect_backfill(
        "600001",
        from_time=datetime(2026, 8, 9, tzinfo=timezone.utc),
        to_time=datetime(2026, 8, 11, tzinfo=timezone.utc),
        max_pages=1,
    ).result
    assert result.status is CollectionStatus.COLLECTION_FAILED
    assert result.failures == ["page 1: access_block"]


def test_dom_navigation_failure_keeps_explicit_attempt_taxonomy(tmp_path: Path) -> None:
    class NavigationFailureTransport:
        capture_method = BROWSER_DOM_SNAPSHOT

        def get(self, url: str, *, timeout: float):
            del url, timeout
            raise ExistingChromeAcquisitionError(
                "navigation_failure", "Chrome tab navigation timed out"
            )

    execution = execute_and_persist_backfill_collection(
        db_path=tmp_path / "collector.db",
        raw_data_dir=tmp_path / "data",
        stock_code="600001",
        from_time=datetime(2026, 8, 9, tzinfo=timezone.utc),
        to_time=datetime(2026, 8, 11, tzinfo=timezone.utc),
        transport=NavigationFailureTransport(),
        run_id="dom-navigation-failure",
        collector_config=CollectorConfig(
            max_pages=1,
            max_attempts=1,
            min_interval_seconds=2.5,
            base_backoff_seconds=0,
        ),
        clock=lambda: NOW,
        sleep_fn=lambda _seconds: None,
        max_pages=1,
    )
    assert execution.execution.result.status is CollectionStatus.COLLECTION_FAILED
    store = SQLitePersistence(
        tmp_path / "collector.db",
        RawEvidenceStore(tmp_path / "data", source="eastmoney_guba"),
    )
    try:
        assert store.conn.execute(
            "SELECT outcome, error_class FROM collection_attempts WHERE run_id=?",
            (execution.run_id,),
        ).fetchone() == ("transport_error", "navigation_failure")
        assert store.conn.execute(
            "SELECT count(*) FROM raw_evidence WHERE run_id=?", (execution.run_id,)
        ).fetchone()[0] == 0
    finally:
        store.close()
