from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from myresearcher_collector.integration import (
    execute_and_persist_backfill_collection,
    execute_and_persist_collection,
)
from myresearcher_collector.models import CollectionStatus
from myresearcher_collector.sources.eastmoney_guba import CollectorConfig, EastmoneyGubaCollector, HttpResponse
from myresearcher_collector.storage import RawEvidenceStore, SQLitePersistence


FIXTURES = Path(__file__).parents[1] / "fixtures" / "eastmoney_guba"


class MappingTransport:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str, *, timeout: float) -> HttpResponse:
        del timeout
        self.calls.append(url)
        if ",f.html" in url:
            return HttpResponse(200, (FIXTURES / "list_page_1.html").read_bytes(), {})
        if ",f_2.html" in url:
            return HttpResponse(200, (FIXTURES / "list_page_2.html").read_bytes(), {})
        if ",f_3.html" in url:
            return HttpResponse(200, (FIXTURES / "empty_page.html").read_bytes(), {})
        item_id = url.split(",")[-1].split(".")[0]
        return HttpResponse(200, (FIXTURES / f"detail_{item_id}.html").read_bytes(), {})


def config() -> CollectorConfig:
    return CollectorConfig(max_pages=3, min_interval_seconds=2.5, base_backoff_seconds=0)


def test_backfill_fresh_checkpoint_stays_null_and_forward_bootstrap_remains_pending(tmp_path: Path) -> None:
    transport = MappingTransport()
    execution = execute_and_persist_backfill_collection(
        db_path=tmp_path / "collector.db", raw_data_dir=tmp_path / "data",
        stock_code="600001",
        from_time=datetime(2026, 8, 9, tzinfo=timezone.utc),
        to_time=datetime(2026, 8, 11, tzinfo=timezone.utc),
        transport=transport, collector_config=config(), clock=lambda: datetime(2026, 8, 11, tzinfo=timezone.utc),
        sleep_fn=lambda _: None, max_pages=3,
    )
    assert execution.execution.result.status is CollectionStatus.SUCCESS
    assert execution.checkpoint_before is None
    assert execution.checkpoint_after is None
    store = SQLitePersistence(tmp_path / "collector.db", RawEvidenceStore(tmp_path / "data", source="eastmoney_guba"))
    try:
        assert store.checkpoint("eastmoney_guba", "stock:600001") is None
    finally:
        store.close()


def test_backfill_preserves_existing_forward_checkpoint_and_is_idempotent(tmp_path: Path) -> None:
    first = execute_and_persist_collection(
        db_path=tmp_path / "collector.db", raw_data_dir=tmp_path / "data",
        stock_code="600001", transport=MappingTransport(), run_id="forward",
        collector_config=config(), clock=lambda: datetime(2026, 8, 11, tzinfo=timezone.utc),
        sleep_fn=lambda _: None, max_pages=3,
    )
    assert first.result.status is CollectionStatus.SUCCESS
    store = SQLitePersistence(tmp_path / "collector.db", RawEvidenceStore(tmp_path / "data", source="eastmoney_guba"))
    try:
        checkpoint_before = store.checkpoint("eastmoney_guba", "stock:600001")
    finally:
        store.close()

    backfill = execute_and_persist_backfill_collection(
        db_path=tmp_path / "collector.db", raw_data_dir=tmp_path / "data",
        stock_code="600001",
        from_time=datetime(2026, 8, 9, tzinfo=timezone.utc),
        to_time=datetime(2026, 8, 11, tzinfo=timezone.utc),
        transport=MappingTransport(), run_id="backfill-1", collector_config=config(),
        clock=lambda: datetime(2026, 8, 11, tzinfo=timezone.utc), sleep_fn=lambda _: None, max_pages=3,
    )
    assert backfill.checkpoint_after == checkpoint_before[0]
    assert backfill.records_new == 0
    assert backfill.records_existing > 0
    repeated = execute_and_persist_backfill_collection(
        db_path=tmp_path / "collector.db", raw_data_dir=tmp_path / "data",
        stock_code="600001",
        from_time=datetime(2026, 8, 9, tzinfo=timezone.utc),
        to_time=datetime(2026, 8, 11, tzinfo=timezone.utc),
        transport=MappingTransport(), run_id="backfill-2", collector_config=config(),
        clock=lambda: datetime(2026, 8, 11, tzinfo=timezone.utc), sleep_fn=lambda _: None, max_pages=3,
    )
    assert repeated.records_new == 0
    assert repeated.records_versioned == 0
    store = SQLitePersistence(tmp_path / "collector.db", RawEvidenceStore(tmp_path / "data", source="eastmoney_guba"))
    try:
        assert store.checkpoint("eastmoney_guba", "stock:600001") == checkpoint_before
        assert store.conn.execute("SELECT count(*) FROM source_item_observations").fetchone()[0] == 2
    finally:
        store.close()


def test_backfill_detail_schema_mismatch_persists_evidence_and_failure(tmp_path: Path) -> None:
    class SchemaMismatchTransport:
        def get(self, url: str, *, timeout: float) -> HttpResponse:
            del timeout
            if ",f.html" in url:
                from tests.unit.test_backfill import synthetic_page
                value = synthetic_page(("1001", "2026-08-10 10:00:00"))
                return value
            return HttpResponse(200, b"<script>var post_article={};</script>", {})

    execution = execute_and_persist_backfill_collection(
        db_path=tmp_path / "collector.db", raw_data_dir=tmp_path / "data",
        stock_code="600001", from_time=datetime(2026, 8, 8, tzinfo=timezone.utc),
        to_time=datetime(2026, 8, 11, tzinfo=timezone.utc), transport=SchemaMismatchTransport(),
        run_id="backfill-schema-mismatch", collector_config=config(),
        clock=lambda: datetime(2026, 8, 11, tzinfo=timezone.utc), sleep_fn=lambda _: None, max_pages=1,
    )
    assert execution.execution.result.status is CollectionStatus.SPEC_MISMATCH
    assert execution.execution.result.stop_reason == "detail_schema_mismatch"
    store = SQLitePersistence(tmp_path / "collector.db", RawEvidenceStore(tmp_path / "data", source="eastmoney_guba"))
    try:
        assert store.conn.execute("SELECT count(*) FROM raw_evidence WHERE run_id=?", (execution.run_id,)).fetchone()[0] == 2
        assert store.conn.execute("SELECT count(*) FROM collection_failures WHERE run_id=?", (execution.run_id,)).fetchone()[0] == 1
        assert store.checkpoint("eastmoney_guba", "stock:600001") is None
    finally:
        store.close()
