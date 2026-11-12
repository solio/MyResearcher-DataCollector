"""Regression for the 2026-11-11 daily-enrich age window.

The two fixture rows are VERBATIM copies of real rows in ``data/collector.db``,
exported on 2026-11-11 with::

    source='eastmoney_guba' AND content IS NULL
    AND length(trim(title)) >= 40 AND url IS NOT NULL
    ORDER BY published_at ASC|DESC LIMIT 1

All 15 columns are kept, including the NULL ``like_count`` and the exact
``+08:00`` timestamp format. A synthetic "reasonable" post would not prove
anything about the production predicate -- the whole point of this file is to
run the real shape of a candidate through the new filter.

Controls, in the order the assertions run:
  * NO window -> both rows are candidates (proves the fixture really enters
    the candidate set, so a later "0 candidates" is not a false green)
  * 30-day window -> only the recent row survives
  * ``ENRICH_MAX_AGE_DAYS=0`` -> the plan goes back to the full backlog
"""

import importlib.util
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from myresearcher_collector.detail_enrichment import (
    DEFAULT_ENRICH_MAX_AGE_DAYS,
    ENRICH_AGE_ENV,
    _legacy_candidates,
    enrich_published_from,
    resolve_enrich_max_age_days,
)
from myresearcher_collector.simple_store import SimplePostStore

CST = timezone(timedelta(hours=8))
FIXED_NOW = datetime(2026, 11, 11, 9, 30, tzinfo=CST)
FIXED_CUTOFF = "2026-10-12T09:30:00+08:00"

CREATE_POSTS = """
CREATE TABLE posts (
    source TEXT, source_item_id TEXT, stock_code TEXT, title TEXT, content TEXT,
    author_id TEXT, author_name TEXT, published_at TEXT, url TEXT,
    read_count INTEGER, reply_count INTEGER, like_count INTEGER,
    forward_count INTEGER, created_at TEXT, updated_at TEXT)
"""
COLUMNS = (
    "source", "source_item_id", "stock_code", "title", "content",
    "author_id", "author_name", "published_at", "url", "read_count",
    "reply_count", "like_count", "forward_count", "created_at", "updated_at",
)
INSERT = f"INSERT INTO posts ({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))})"

# Oldest eligible row: 2024-03-01, stock 601012.
OLD_ROW = (
    "eastmoney_guba", "1403626271", "601012",
    "隆基绿能:隆基绿能:投资者关系活动记录表(2024年1月29日-2024年2月2",
    None, "6047113638506594", "隆基绿能资讯", "2024-03-01T00:00:00+08:00",
    "https://guba.eastmoney.com/news,601012,1403626271.html",
    88, 0, None, 0, "2026-11-02T01:06:19.888581Z", "2026-11-02T01:06:19.888581Z",
)
# Newest eligible row: 2026-11-11, stock 002463.
NEW_ROW = (
    "eastmoney_guba", "1794482595", "002463",
    "日韩今天没咋跌啊，看来达子出轨这事消化完了。。达子也跌了30%。。利空落地，看修",
    None, "4624125297363220", "凝神的终峤", "2026-11-11T08:24:50+08:00",
    "https://guba.eastmoney.com/news,600183,1794482595.html",
    44, 0, None, 0, "2026-11-11T01:03:31.139615Z", "2026-11-11T01:03:31.139615Z",
)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "collector.db"
    con = sqlite3.connect(path)
    try:
        con.execute(CREATE_POSTS)
        con.executemany(INSERT, (OLD_ROW, NEW_ROW))
        con.commit()
    finally:
        con.close()
    return path


def _ids(db, code, **kwargs):
    store = SimplePostStore(db)
    return [
        c.source_item_id
        for c in _legacy_candidates(store, code, include_short_titles=False, **kwargs)
    ]


def _plan_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "enrich_plan.py"
    spec = importlib.util.spec_from_file_location("_enrich_plan_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_control_no_window_keeps_both_real_rows(db):
    """Negative control: without the fix, BOTH rows are candidates."""
    assert _ids(db, "601012") == ["1403626271"]
    assert _ids(db, "002463") == ["1794482595"]


def test_cutoff_uses_the_stored_timestamp_format():
    assert enrich_published_from(30, now=FIXED_NOW) == FIXED_CUTOFF
    assert enrich_published_from(0, now=FIXED_NOW) is None
    assert enrich_published_from(None, now=FIXED_NOW) is None


def test_window_excludes_only_the_old_post(db):
    assert _ids(db, "601012", published_from=FIXED_CUTOFF) == []
    assert _ids(db, "002463", published_from=FIXED_CUTOFF) == ["1794482595"]
    # Explicit rollback: zero days must be byte-identical to "no window".
    assert _ids(db, "601012", published_from=enrich_published_from(0)) == _ids(db, "601012")


def test_resolve_window_env_and_override(monkeypatch):
    monkeypatch.delenv(ENRICH_AGE_ENV, raising=False)
    assert DEFAULT_ENRICH_MAX_AGE_DAYS == 30
    assert resolve_enrich_max_age_days() == 30
    monkeypatch.setenv(ENRICH_AGE_ENV, "7")
    assert resolve_enrich_max_age_days() == 7
    assert resolve_enrich_max_age_days(14) == 14  # explicit value wins
    monkeypatch.setenv(ENRICH_AGE_ENV, "0")
    assert resolve_enrich_max_age_days() == 0
    monkeypatch.setenv(ENRICH_AGE_ENV, "not-an-int")
    with pytest.raises(ValueError):
        resolve_enrich_max_age_days()


def test_plan_and_collector_agree_on_scope(db, monkeypatch):
    """The plan must not report a backlog the collector refuses to fetch."""
    ep = _plan_module()
    # Freeze the cutoff so this test does not depend on the day it is run.
    monkeypatch.setattr(
        ep, "enrich_published_from",
        lambda days: None if days == 0 else FIXED_CUTOFF,
    )
    monkeypatch.delenv(ENRICH_AGE_ENV, raising=False)
    ep.age_predicate.cache_clear()
    try:
        assert dict(ep.backlog(db)) == {"002463": 1}
        # Roll back the change: the old post must reappear in the plan.
        monkeypatch.setenv(ENRICH_AGE_ENV, "0")
        ep.age_predicate.cache_clear()
        assert dict(ep.backlog(db)) == {"601012": 1, "002463": 1}
    finally:
        ep.age_predicate.cache_clear()
