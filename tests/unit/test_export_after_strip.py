"""A stripped node must still answer the hub's sync request.

The strip empties `export_journal` (it is a data table) while the hub's cursor for
that node still sits at the pre-strip maximum. The hub then asks for
`after=<old max>`, and `export_page` compared that against a maximum of 0 and
raised `ValueError: export 游标/快照超出已提交序列` -- sync died with
"同步失败" right after a successful strip.

`journal_seq_hint` is the number the strip wrote down for exactly this reason. Up
to that number the journal is empty *by design*, so the honest answer is an empty
page (with the gap declared), not an error. Beyond it, the old error still stands:
that would be real data loss.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "apps" / "http_backfill"
sys.path.insert(0, str(APP))

import core  # noqa: E402
from federation import export_page  # noqa: E402

HINT = 40


def _stripped_node(tmp_path, hint=HINT):
    """A node whose journal was emptied by a strip that recorded `hint`."""
    engine = core.Engine(tmp_path)
    engine.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('journal_seq_hint',?)", (str(hint),))
    engine.db.commit()
    return engine


def test_hub_cursor_at_the_hint_gets_an_empty_page(tmp_path):
    engine = _stripped_node(tmp_path)
    try:
        page = export_page(engine, after=HINT)
    finally:
        engine.close()

    assert page["items"] == []
    assert page["next_after"] == HINT
    assert page["has_more"] is False
    assert page["skipped"] == []


def test_a_lagging_cursor_gets_the_empty_range_declared_as_skipped(tmp_path):
    """merge_page 要求 (after, next_after] 完全由 items+skipped 覆盖，否则报"序列存在间隙"。"""
    engine = _stripped_node(tmp_path)
    try:
        page = export_page(engine, after=20, snapshot=HINT)
    finally:
        engine.close()

    assert page["next_after"] == HINT
    assert page["skipped"] == [[21, HINT]]


def test_a_cursor_past_the_hint_is_still_a_hard_error(tmp_path):
    engine = _stripped_node(tmp_path)
    try:
        with pytest.raises(ValueError):
            export_page(engine, after=HINT + 1)
    finally:
        engine.close()
