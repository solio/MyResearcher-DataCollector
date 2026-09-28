"""End-to-end tests for the off-machine backup script (D-026's open item).

These run the real `scripts/ops/backup_snapshot.sh` against a throwaway database,
because every property worth asserting here lives in the shell/python seam and
not in an importable function: that `VACUUM INTO` produces a readable database,
that retention deletes only what it created, and that the upload path sends
**one file in one direction** and can never mirror or delete anything remote.

The last one is the reason this file exists. A backup tool that can delete
remote data is a worse failure mode than no backup tool, so `sync` must never be
invoked -- and that claim is only worth anything if something fails when it is.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from myresearcher_collector.simple_store import SimplePostStore

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "ops" / "backup_snapshot.sh"
ROW_COUNT = 5


def _seed(db_path: Path, rows: int = ROW_COUNT) -> None:
    """A database with exactly the tables the backup verifies are present."""
    store = SimplePostStore(db_path)
    for index in range(rows):
        store.upsert_post(
            source="eastmoney_guba",
            source_item_id=str(index),
            stock_code="601012",
            title=f"标题{index}" + "x" * 40,
            content="正文" if index % 2 == 0 else None,
            author_id="u",
            author_name="n",
            published_at="2026-08-01T02:00:00.000000Z",
            url=f"https://guba.eastmoney.com/news,601012,{index}.html",
            read_count=0,
            reply_count=0,
            like_count=0,
            forward_count=0,
        )
    store.close()


def _run(db: Path, backup_dir: Path, *args: str, env: dict | None = None):
    environment = dict(os.environ)
    environment.update(
        {
            "DB": str(db),
            "BACKUP_DIR": str(backup_dir),
            "PY": os.environ.get("PY", shutil.which("python3") or "python3"),
        }
    )
    environment.update(env or {})
    return subprocess.run(
        ["/bin/zsh", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=environment,
        cwd=str(REPO),
    )


def test_snapshot_is_a_readable_database_that_verifies(tmp_path):
    db = tmp_path / "collector.db"
    backups = tmp_path / "backups"
    _seed(db)

    result = _run(db, backups)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SNAPSHOT_OK" in result.stdout
    assert f"posts={ROW_COUNT}" in result.stdout
    assert "integrity=ok" in result.stdout
    # Nothing leaves the machine unless a destination was named.
    assert "UPLOAD_SKIPPED" in result.stdout

    snapshots = sorted(backups.glob("collector-*.db"))
    assert len(snapshots) == 1
    # The snapshot is a real database with the rows, not just a file with a size.
    check = sqlite3.connect(f"file:{snapshots[0]}?mode=ro", uri=True)
    assert check.execute("SELECT count(*) FROM posts").fetchone()[0] == ROW_COUNT
    assert check.execute(
        "SELECT count(*) FROM posts WHERE content IS NOT NULL"
    ).fetchone()[0] == 3
    check.close()

    verify = _run(db, backups, "--verify")
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert "VERIFY_OK" in verify.stdout
    assert "manifest=matched" in verify.stdout


def test_a_truncated_snapshot_fails_verification(tmp_path):
    """NEGATIVE CONTROL for the verifier.

    Without this, "VERIFY_OK" would only prove the script can print VERIFY_OK.
    Truncating the newest snapshot must make verification fail loudly -- that is
    the failure mode that otherwise stays invisible until the day it is needed.
    """
    db = tmp_path / "collector.db"
    backups = tmp_path / "backups"
    _seed(db)
    assert _run(db, backups).returncode == 0

    victim = sorted(backups.glob("collector-*.db"))[-1]
    os.truncate(victim, 4096)

    verify = _run(db, backups, "--verify")
    assert verify.returncode != 0
    assert "VERIFY_FAILED" in verify.stdout


def test_retention_keeps_the_newest_and_names_every_deletion(tmp_path):
    db = tmp_path / "collector.db"
    backups = tmp_path / "backups"
    _seed(db)

    for _ in range(3):
        assert _run(db, backups, env={"KEEP": "2"}).returncode == 0

    keep = sorted(backups.glob("collector-*.db"))
    assert len(keep) == 2, [p.name for p in keep]
    # The two survivors are the two most recent runs, and every removal was
    # printed before it happened rather than done silently.
    third = _run(db, backups, env={"KEEP": "2"})
    assert third.stdout.count("RETENTION_DELETE") == 1
    assert len(sorted(backups.glob("collector-*.db"))) == 2


def test_upload_sends_exactly_one_file_and_never_syncs(tmp_path):
    """The safety property: one `upload`, and no `sync` under any name.

    A stub `aliyunpan` records its argv, so this asserts what the script really
    executed instead of trusting the docstring.
    """
    db = tmp_path / "collector.db"
    backups = tmp_path / "backups"
    _seed(db)

    log = tmp_path / "aliyunpan.calls"
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "aliyunpan"
    stub.write_text(
        "#!/bin/zsh\n"
        f'print -r -- "$@" >> {log}\n'
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    result = _run(
        db,
        backups,
        env={
            "ALIYUNPAN_REMOTE": "/备份/myresearcher",
            "ALIYUNPAN_BIN": str(stub),
            "PATH": f"{stub_dir}:{os.environ.get('PATH', '')}",
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "UPLOAD_OK" in result.stdout

    calls = log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1, calls
    verb, uploaded, remote = calls[0].split()
    assert verb == "upload"
    assert uploaded.endswith(".db")
    assert remote == "/备份/myresearcher"
    # `sync` can mirror a remote directory, and with `exclusive` it deletes. The
    # backup path must never be able to do that.
    assert "sync" not in calls[0]


def test_a_missing_database_is_a_loud_failure_not_an_empty_snapshot(tmp_path):
    db = tmp_path / "absent.db"
    backups = tmp_path / "backups"
    result = _run(db, backups)

    assert result.returncode != 0
    assert "BACKUP_FAILED reason=database_missing" in result.stdout
    assert not list(backups.glob("collector-*.db"))
