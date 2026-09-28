"""Enrich order: which end of a stock's backlog gets fetched first.

Before this knob the order was implicit -- `ORDER BY published_at` in the legacy
query and `sorted(key=published_at)` on the canonical path, both ASC, i.e. oldest
first, and nothing recorded it. Two properties matter:

* `desc` really does flip it (not silently ignored);
* `asc` is byte-identical to what ran before, so it stays usable as the control
  arm for any later "does order matter" comparison.
"""

from __future__ import annotations

import pytest

from myresearcher_collector.detail_enrichment import (
    _legacy_candidates,
    execute_detail_enrichment,
)
from myresearcher_collector.simple_store import SimplePostStore

# Distinct, out-of-order insertion: proves the result is sorted, not just echoed.
SEED = [
    ("2001", "2026-08-03T02:00:00.000000Z"),
    ("2002", "2026-08-01T02:00:00.000000Z"),
    ("2003", "2026-08-02T02:00:00.000000Z"),
]


def _seed(tmp_path):
    store = SimplePostStore(tmp_path / "collector.db")
    for item_id, published in SEED:
        store.upsert_post(
            source="eastmoney_guba", source_item_id=item_id, stock_code="601012",
            title="x" * 45, content=None, author_id="u", author_name="n",
            published_at=published,
            url=f"https://guba.eastmoney.com/news,601012,{item_id}.html",
            read_count=0, reply_count=0, like_count=0, forward_count=0,
        )
    return store


def test_asc_is_oldest_first_and_matches_the_historical_order(tmp_path):
    store = _seed(tmp_path)
    try:
        got = [c.source_item_id for c in _legacy_candidates(
            store, "601012", include_short_titles=False, order="asc"
        )]
    finally:
        store.close()

    assert got == ["2002", "2003", "2001"], "asc must be oldest published_at first"


def test_desc_is_newest_first(tmp_path):
    store = _seed(tmp_path)
    try:
        got = [c.source_item_id for c in _legacy_candidates(
            store, "601012", include_short_titles=False, order="desc"
        )]
    finally:
        store.close()

    assert got == ["2001", "2003", "2002"], "desc must be newest published_at first"


def test_the_default_is_asc(tmp_path):
    """Omitting the argument must not change behaviour for existing callers."""
    store = _seed(tmp_path)
    try:
        default = [c.source_item_id for c in _legacy_candidates(
            store, "601012", include_short_titles=False
        )]
    finally:
        store.close()

    assert default == ["2002", "2003", "2001"]


def test_an_unknown_order_is_refused(tmp_path):
    """The value reaches a SQL ORDER BY clause, so it must never be free-form."""
    store = _seed(tmp_path)
    store.close()
    with pytest.raises(ValueError, match="enrich_order"):
        execute_detail_enrichment(
            db_path=tmp_path / "collector.db", stock_code="601012",
            transport=object(), log_path=tmp_path / "run.jsonl",
            enrich_order="desc; DROP TABLE posts",
        )
