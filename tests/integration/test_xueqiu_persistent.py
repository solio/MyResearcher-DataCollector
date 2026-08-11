"""Offline persistence integration for the Xueqiu source boundary."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from myresearcher_collector.integration import execute_and_persist_xueqiu_collection
from myresearcher_collector.sources.xueqiu import CollectorConfig
from myresearcher_collector.sources.xueqiu import XueqiuResponse
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


def test_xq021_incremental_boundary_advances_checkpoint_to_new_frontier(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    config = CollectorConfig(max_pages=2, min_interval_seconds=3.0, base_backoff_seconds=0)
    seed = execute_and_persist_xueqiu_collection(
        db_path=data_dir / "collector.db",
        raw_data_dir=data_dir,
        stock_code="600519",
        transport=FixtureTransport(["page_1.json", "page_2.json"]),
        run_id="xq021-seed",
        collector_config=config,
        clock=lambda: NOW,
        sleep_fn=lambda _: None,
        max_pages=2,
    )
    assert seed.result.status.value == "SUCCESS"
    new_page = {
        "list": [
            {
                "id": 920001,
                "description": "newer item",
                "title": "newer title",
                "created_at": 1800000000000,
                "target": "https://xueqiu.com/920001",
                "user": {"id": 820001, "screen_name": "new-author"},
                "fav_count": 1, "reply_count": 1, "retweet_count": 1,
            },
            {
                "id": 910001,
                "description": "synthetic body one 😀",
                "title": "synthetic title one",
                "created_at": 1700000000000,
                "target": "https://xueqiu.com/910001",
                "user": {"id": 810001, "screen_name": "synthetic-author-1"},
                "fav_count": 1, "reply_count": 2, "retweet_count": 3,
            },
        ],
        "count": 2, "maxPage": 2, "page": 1,
    }
    known_page = json.loads((FIXTURES / "page_1.json").read_text())
    known_page["list"] = known_page["list"][:2]
    known_page["page"] = 2

    class JsonTransport:
        def __init__(self, values: list[dict[str, object]]) -> None:
            self.values = iter(values)

        def get(self, url: str, *, timeout: float):
            del url, timeout
            body = json.dumps(next(self.values)).encode()
            return XueqiuResponse(
                200, body, {"content-type": "application/json"},
                "https://xueqiu.com/query/v1/symbol/search/status.json",
            )

    second = execute_and_persist_xueqiu_collection(
        db_path=data_dir / "collector.db",
        raw_data_dir=data_dir,
        stock_code="600519",
        transport=JsonTransport([new_page, known_page]),
        run_id="xq021-incremental",
        collector_config=CollectorConfig(max_pages=3, min_interval_seconds=3.0),
        clock=lambda: NOW,
        sleep_fn=lambda _: None,
        max_pages=3,
    )
    assert second.result.status.value == "SUCCESS"
    frontier = second.result.safe_frontier
    assert frontier == datetime.fromtimestamp(1800000000, tz=timezone.utc)

    store = SQLitePersistence(data_dir / "collector.db", RawEvidenceStore(data_dir, source="xueqiu"))
    try:
        before = store.conn.execute(
            "SELECT watermark_before_utc, watermark_after_utc FROM collection_runs WHERE run_id='xq021-incremental'"
        ).fetchone()
        checkpoint = store.checkpoint("xueqiu", "stock:600519")
        assert before[0] == "2023-11-14T22:13:20.000000Z"
        assert before[1] == "2027-01-15T08:00:00.000000Z"
        assert checkpoint[0] == before[1]
    finally:
        store.close()
