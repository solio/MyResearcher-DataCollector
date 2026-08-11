"""Offline persistence integration for the Xueqiu source boundary."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from myresearcher_collector.integration import execute_and_persist_xueqiu_collection
from myresearcher_collector.sources.xueqiu import CollectorConfig
from myresearcher_collector.storage import RawEvidenceStore, SQLitePersistence


FIXTURES = Path(__file__).parents[1] / "fixtures" / "xueqiu"
NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)


class FixtureTransport:
    def __init__(self, names: list[str]) -> None:
        self.names = list(names)
        self.calls: list[str] = []

    def get(self, url: str, *, timeout: float):
        del timeout
        self.calls.append(url)
        query = parse_qs(urlparse(url).query)
        expected_page = int(query["page"][0])
        name = self.names.pop(0)
        body = (FIXTURES / name).read_bytes()
        actual_page = json.loads(body)["page"]
        assert actual_page == expected_page or expected_page == 1
        from myresearcher_collector.sources.xueqiu import XueqiuResponse
        return XueqiuResponse(
            200, body, {"content-type": "application/json"},
            "https://xueqiu.com/query/v1/symbol/search/status.json",
        )


def test_xueqiu_reuses_raw_evidence_and_per_stock_checkpoint(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    config = CollectorConfig(max_pages=2, min_interval_seconds=3.0, base_backoff_seconds=0)
    first_transport = FixtureTransport(["page_1.json", "page_2.json"])
    first = execute_and_persist_xueqiu_collection(
        db_path=data_dir / "collector.db",
        raw_data_dir=data_dir,
        stock_code="600519",
        transport=first_transport,
        run_id="xq-bootstrap",
        collector_config=config,
        clock=lambda: NOW,
        sleep_fn=lambda _: None,
        max_pages=2,
    )
    assert first.result.status.value == "SUCCESS"
    store = SQLitePersistence(data_dir / "collector.db", RawEvidenceStore(data_dir, source="xueqiu"))
    try:
        assert store.checkpoint("xueqiu", "stock:600519") is not None
        assert store.conn.execute("SELECT count(*) FROM raw_evidence WHERE run_id='xq-bootstrap'").fetchone()[0] == 2
        assert store.conn.execute("SELECT count(*) FROM source_item_observations WHERE source='xueqiu'").fetchone()[0] == 4
        assert store.conn.execute("SELECT count(*) FROM observation_scopes WHERE scope_key='stock:600519'").fetchone()[0] == 4
    finally:
        store.close()

    second_transport = FixtureTransport(["page_1.json"])
    second = execute_and_persist_xueqiu_collection(
        db_path=data_dir / "collector.db",
        raw_data_dir=data_dir,
        stock_code="600519",
        transport=second_transport,
        run_id="xq-incremental",
        collector_config=CollectorConfig(max_pages=3, min_interval_seconds=3.0),
        clock=lambda: NOW,
        sleep_fn=lambda _: None,
        max_pages=3,
    )
    assert second.result.status.value == "NO_NEW_DATA"
    assert len(second_transport.calls) == 1

