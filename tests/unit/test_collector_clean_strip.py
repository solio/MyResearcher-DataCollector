"""The "数据剥离" path had no test at all, which is how it shipped broken.

`strip()` failed on every click from 2026-11-13 09:17 (43bf726): it referenced
`old_db`, a name that exists only as `_copy_control`'s parameter, so the console
answered 500 {"error": "NameError: ..."} and nothing was ever stripped. The
failure happened *after* `tmp.mkdir()` and `VACUUM INTO`, so every attempt also
left a full-size copy of the database under `tmp/clean-*` -- invisible to
`leftovers()` (it only counts directories carrying `marker.json`) and therefore
invisible to `cleanup`, which will never reclaim it.

Both halves are pinned here:

* a successful strip swaps the database, keeps the control rows, and keeps the
  original as the rollback point;
* a failed strip leaves no directory behind and does not touch the live
  database -- except when the original has already been moved into `tmp`, where
  deleting the directory would destroy the only copy.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "apps" / "http_backfill"
sys.path.insert(0, str(APP))

import collector_clean  # noqa: E402
import core  # noqa: E402

STAMP = "20261113-223000"


def _seed(data: Path) -> None:
    """A real data directory: `Engine` creates the 24-table schema the strip copies."""
    engine = core.Engine(data)
    try:
        engine.db.execute("INSERT INTO events(job,created,kind,message) VALUES(NULL,0,'test','before strip')")
        engine.db.commit()
    finally:
        engine.close()


def test_strip_swaps_the_database_and_keeps_control_rows(tmp_path):
    _seed(tmp_path)
    collector_clean.export_all(tmp_path)

    result = collector_clean.strip(tmp_path, STAMP, exported=True)

    assert result["ok"] is True, result["checks"]
    assert result["stage"] == "detached"
    # The old database becomes the rollback point; `verify`/`cleanup` read this layout.
    assert (tmp_path / "tmp" / f"clean-{STAMP}" / "collector.db").is_file()
    assert (tmp_path / "clean-current.json").is_file()

    db = sqlite3.connect(tmp_path / "collector.db")
    try:
        assert db.execute("SELECT count(*) FROM posts").fetchone()[0] == 0
        # Control rows survive row-for-row; the strip appends its own event on top.
        assert db.execute("SELECT count(*) FROM events WHERE message='before strip'").fetchone()[0] == 1
    finally:
        db.close()
    assert collector_clean.state(tmp_path)["stage"] == "detached"


def test_failed_strip_leaves_no_copy_and_keeps_the_live_database(tmp_path, monkeypatch):
    _seed(tmp_path)
    collector_clean.export_all(tmp_path)
    before = (tmp_path / "collector.db").stat().st_size

    def boom(old_db, new_db):
        raise RuntimeError("copy failed")

    monkeypatch.setattr(collector_clean, "_copy_control", boom)

    with pytest.raises(RuntimeError):
        collector_clean.strip(tmp_path, STAMP)

    assert not (tmp_path / "tmp" / f"clean-{STAMP}").exists(), "失败后不允许留下整库副本"
    assert (tmp_path / "collector.db").stat().st_size == before
    assert collector_clean.state(tmp_path)["stage"] == "paused"


def test_guard_keeps_the_original_when_the_swap_itself_fails(tmp_path, monkeypatch):
    """After the first replace, `tmp/clean-<stamp>/collector.db` IS the only copy."""
    _seed(tmp_path)
    collector_clean.export_all(tmp_path)
    real_replace = collector_clean.os.replace
    calls = {"n": 0}

    def flaky(source, target):
        calls["n"] += 1
        if calls["n"] == 2:  # 1st: original into tmp; 2nd: new database into place
            raise OSError("swap failed")
        return real_replace(source, target)

    monkeypatch.setattr(collector_clean.os, "replace", flaky)

    with pytest.raises(OSError):
        collector_clean.strip(tmp_path, STAMP)

    assert (tmp_path / "tmp" / f"clean-{STAMP}" / "collector.db").is_file()


def test_pending_details_block_the_strip_unless_the_caller_says_so(tmp_path):
    """The console's second confirmation is what sends `allow_missing_details`.

    bd29208 turned "待补详情" from a note into a hard refusal, and nothing in the UI
    ever sent the override, so a node with an un-fetchable backlog could never be
    stripped at all -- the button just answered 409. The API contract pinned here is
    the one the two-step dialog relies on: refuse first, proceed only on an explicit
    `allow_missing_details: true`.
    """
    _seed(tmp_path)
    db = sqlite3.connect(tmp_path / "collector.db")
    try:
        db.execute("INSERT INTO tasks(job,kind,stock,status) VALUES(1,'detail','300604','pending')")
        db.commit()
    finally:
        db.close()
    collector_clean.export_all(tmp_path)

    refused = collector_clean.strip(tmp_path, STAMP)

    assert refused["ok"] is False
    names = [check["name"] for check in refused["checks"] if not check["ok"]]
    assert any("待补详情" in name for name in names), names
    assert not (tmp_path / "tmp" / f"clean-{STAMP}").exists()

    allowed = collector_clean.strip(tmp_path, STAMP, allow_missing_details=True)

    assert allowed["ok"] is True, allowed["checks"]
    assert allowed["stage"] == "detached"


def test_check_mode_reports_without_changing_anything(tmp_path):
    """`check` is the instrument: same verdict, no swap.

    Without it the only way to learn why a node refuses is to click the button and
    read the console, which is exactly how "点了没反应" went unexplained.
    """
    _seed(tmp_path)
    db = sqlite3.connect(tmp_path / "collector.db")
    try:
        db.execute("INSERT INTO tasks(job,kind,stock,status) VALUES(1,'detail','300604','pending')")
        db.commit()
    finally:
        db.close()
    collector_clean.export_all(tmp_path)
    before = (tmp_path / "collector.db").stat().st_size

    result = collector_clean.strip(tmp_path, STAMP, dry_run=True)

    assert result["ok"] is False
    assert any("待补详情" in check["name"] for check in result["checks"] if not check["ok"])
    assert not (tmp_path / "tmp" / f"clean-{STAMP}").exists()
    assert (tmp_path / "collector.db").stat().st_size == before
    assert not (tmp_path / "clean-current.json").exists()


def _strip_clean(tmp_path, stamp=STAMP):
    _seed(tmp_path)
    db = sqlite3.connect(tmp_path / "collector.db")
    try:
        db.execute("INSERT INTO meta(key,value) VALUES('keep_me','42')")
        db.execute("INSERT INTO tasks(job,kind,stock,status) VALUES(1,'detail','300604','pending')")
        db.commit()
    finally:
        db.close()
    collector_clean.export_all(tmp_path)
    assert collector_clean.strip(tmp_path, stamp, allow_missing_details=True)["ok"] is True


def test_verify_ignores_the_writes_the_live_engine_makes_after_the_swap(tmp_path):
    """`meta` gaining runtime keys is normal operation, not data loss.

    The old check compared the live database against a digest taken at strip time and refused
    forever with "meta before=38 after=40" -- the state a healthy strip always reaches, because
    the console keeps running after the swap.
    """
    _strip_clean(tmp_path)
    db = sqlite3.connect(tmp_path / "collector.db")
    try:
        for key in ("storage_sync_at", "clean_shutdown"):
            db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, "1"))
        db.execute("INSERT INTO tasks(job,kind,stock,status) VALUES(1,'list','300604','pending')")
        db.commit()
    finally:
        db.close()

    result = collector_clean.verify(tmp_path, STAMP)

    assert result["ok"] is True, result["checks"]
    assert result["stage"] == "verified"


def test_verify_still_catches_control_data_that_disappeared(tmp_path):
    _strip_clean(tmp_path)
    db = sqlite3.connect(tmp_path / "collector.db")
    try:
        db.execute("DELETE FROM meta WHERE key='keep_me'")
        db.commit()
    finally:
        db.close()

    result = collector_clean.verify(tmp_path, STAMP)

    assert result["ok"] is False
    assert any("丢键" in str(check["detail"]) for check in result["checks"] if not check["ok"])


def test_a_deleted_copy_still_leaves_a_reclaimable_record(tmp_path):
    """The copy can vanish (manual rm, disk cleanup) while `clean-current.json` stays.

    Without this path the node sat at "detached" forever: verify answered with a bare path,
    cleanup refused "存在可清理的副本", and all three buttons were dead.
    """
    _seed(tmp_path)
    collector_clean.export_all(tmp_path)
    assert collector_clean.strip(tmp_path, STAMP)["ok"] is True

    shutil.rmtree(tmp_path / "tmp" / f"clean-{STAMP}")

    assert collector_clean.state(tmp_path)["stage"] == "leftovers"
    result = collector_clean.cleanup(tmp_path)

    assert result["ok"] is True, result["checks"]
    assert not (tmp_path / "clean-current.json").exists()
    assert collector_clean.state(tmp_path)["stage"] == "paused"
