"""Tester-owned Stage B checks for repeated pages and historical drift."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from myresearcher_collector.integration import execute_and_persist_xueqiu_collection
from myresearcher_collector.sources.xueqiu import CollectorConfig, XueqiuResponse
from myresearcher_collector.storage import RawEvidenceStore, SQLitePersistence


FIXTURES = Path(__file__).parents[1] / "fixtures" / "xueqiu"
NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)
CHECKPOINT_T0 = "2023-11-14T22:13:20.000000Z"


class _PayloadTransport:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self.payloads = iter(payloads)

    def get(self, url: str, *, timeout: float) -> XueqiuResponse:
        del url, timeout
        return XueqiuResponse(
            200,
            json.dumps(next(self.payloads), ensure_ascii=False).encode("utf-8"),
            {"content-type": "application/json"},
            "https://xueqiu.com/query/v1/symbol/search/status.json",
        )


def _fixture_payload(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text())


def _run(tmp_path: Path, run_id: str, payloads: list[dict[str, object]], pages: int):
    return execute_and_persist_xueqiu_collection(
        db_path=tmp_path / "collector.db",
        raw_data_dir=tmp_path / "raw",
        stock_code="600519",
        transport=_PayloadTransport(payloads),
        run_id=run_id,
        collector_config=CollectorConfig(max_pages=pages, min_interval_seconds=3.0),
        clock=lambda: NOW,
        sleep_fn=lambda _: None,
        max_pages=pages,
    )


def _checkpoint(tmp_path: Path) -> tuple[str, str]:
    store = SQLitePersistence(
        tmp_path / "collector.db",
        RawEvidenceStore(tmp_path / "raw", source="xueqiu"),
    )
    try:
        value = store.checkpoint("xueqiu", "stock:600519")
        assert value is not None
        return value
    finally:
        store.close()


def test_reviewer_xq023_repeated_new_page_is_partial_and_checkpoint_stays_put(
    tmp_path: Path,
) -> None:
    seed = _run(tmp_path, "reviewer-xq023-seed", [_fixture_payload("page_1.json"), _fixture_payload("page_2.json")], 2)
    assert seed.result.status.value == "SUCCESS"

    page_one = {
        "list": [
            {"id": 930101, "description": "reviewer A", "title": "A", "created_at": 1801000000000,
             "target": "https://xueqiu.com/930101", "user": {"id": 830101, "screen_name": "a"},
             "fav_count": 1, "reply_count": 1, "retweet_count": 1},
            {"id": 930102, "description": "reviewer B", "title": "B", "created_at": 1800999000000,
             "target": "https://xueqiu.com/930102", "user": {"id": 830102, "screen_name": "b"},
             "fav_count": 2, "reply_count": 2, "retweet_count": 2},
        ],
        "count": 2,
        "maxPage": 100,
        "page": 1,
    }
    page_two = dict(page_one)
    page_two["page"] = 2
    result = _run(tmp_path, "reviewer-xq023-repeat", [page_one, page_two], 3)

    assert result.result.status.value == "PARTIAL_COLLECTION"
    assert result.result.stop_reason == "pagination_failure"
    assert result.result.safe_frontier is None
    assert _checkpoint(tmp_path)[0] == CHECKPOINT_T0


def test_reviewer_xq024_drift_versions_once_without_advancing_checkpoint(tmp_path: Path) -> None:
    seed = _run(tmp_path, "reviewer-xq024-seed", [_fixture_payload("page_1.json"), _fixture_payload("page_2.json")], 2)
    assert seed.result.status.value == "SUCCESS"

    changed = _fixture_payload("page_1.json")
    item = changed["list"][0]
    assert isinstance(item, dict)
    item["description"] = "reviewer-observed historical drift"
    item["reply_count"] = 77

    first = _run(tmp_path, "reviewer-xq024-drift", [changed], 1)
    second = _run(tmp_path, "reviewer-xq024-repeat", [changed], 1)
    assert first.result.status.value == "SUCCESS"
    assert second.result.status.value == "NO_NEW_DATA"
    assert _checkpoint(tmp_path)[0] == CHECKPOINT_T0

    store = SQLitePersistence(
        tmp_path / "collector.db",
        RawEvidenceStore(tmp_path / "raw", source="xueqiu"),
    )
    try:
        rows = store.conn.execute(
            """SELECT observation_version, content, reply_count
               FROM source_item_observations
               WHERE source='xueqiu' AND source_item_id='910001'
               ORDER BY observation_version"""
        ).fetchall()
        assert rows == [
            (1, "<p>synthetic body one 😀</p>", 2),
            (2, "reviewer-observed historical drift", 77),
        ]
    finally:
        store.close()
