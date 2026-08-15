from datetime import datetime, timezone
import sqlite3

from myresearcher_collector.integration import execute_and_persist_simple_backfill_collection
from myresearcher_collector.sources.eastmoney_guba import CollectorConfig
from tests.unit.test_backfill import MappingTransport, synthetic_page
from myresearcher_collector.sources.eastmoney_guba.collector import EastmoneyGubaCollector


def test_simple_list_backfill_writes_posts_only_and_resumes(tmp_path):
    stock = "600001"
    p1 = EastmoneyGubaCollector.list_url(stock, 1)
    p2 = EastmoneyGubaCollector.list_url(stock, 2)
    routes = {p1: synthetic_page(("1001", "2026-08-10 10:00:00")), p2: synthetic_page()}
    result = execute_and_persist_simple_backfill_collection(
        db_path=tmp_path / "collector.db", stock_code=stock,
        from_time=datetime(2026, 8, 9, tzinfo=timezone.utc),
        to_time=datetime(2026, 8, 11, tzinfo=timezone.utc),
        transport=MappingTransport(routes),
        collector_config=CollectorConfig(max_pages=3, min_interval_seconds=2.5),
        sleep_fn=lambda _: None,
    )
    assert result.execution.result.items[0].content.startswith("post 1001")
    conn = sqlite3.connect(tmp_path / "collector.db")
    try:
        assert conn.execute("select count(*) from posts").fetchone()[0] == 1
        assert conn.execute("select content from posts").fetchone()[0] is None
        tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
        assert tables == {"posts", "backfill_resume", "backfill_coverage", "backfill_page_anchors"}
        assert conn.execute("select page_no, page_size from backfill_page_anchors").fetchone() == (1, 1)
        assert conn.execute("select count(*) from backfill_resume").fetchone()[0] == 0
        assert conn.execute("select covered_from, covered_to from backfill_coverage").fetchone() == (
            "2026-08-09T00:00:00Z", "2026-08-11T00:00:00Z",
        )
    finally:
        conn.close()
