"""Tests for the browser-profile retention script.

The property that matters most is a NEGATIVE one: it must never touch the
sibling profile sets that share the parent directory. `eastmoney-chrome` is the
persistent profile `chrome-clean` reuses and `xueqiu-dedicated` belongs to a
different source entirely, so a retention glob one level too wide would destroy
a profile that is still in use. That claim is only worth something if a test
fails when the glob is widened, which is what the control below does.

`TRASH_DIR` is a parameter precisely so these tests never touch the real
`~/.Trash`.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "ops" / "prune_browser_profiles.sh"

# Same shape the transport generates: `%Y%m%d-%H%M%S-%f`.
NAMES = [
    "20260813-062147-960743",
    "20260820-101010-000001",
    "20260927-235959-999999",
    "20260928-011500-906338",
]


def _make_profiles(root: Path, set_name: str, names: list[str]) -> Path:
    target = root / set_name
    target.mkdir(parents=True, exist_ok=True)
    for index, name in enumerate(names):
        (target / name).mkdir()
        (target / name / "payload.bin").write_bytes(b"x" * (index + 1))
    return target


def _run(root: Path, trash: Path, **env):
    environment = dict(os.environ)
    environment.update(
        {
            "PROFILES_ROOT": str(root),
            "TRASH_DIR": str(trash),
            "PY": os.environ.get("PY", "python3"),
        }
    )
    environment.update({k: str(v) for k, v in env.items()})
    return subprocess.run(
        ["/bin/zsh", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=environment,
        cwd=str(REPO),
    )


def test_dry_run_is_the_default_and_moves_nothing(tmp_path):
    root = tmp_path / "browser-profiles"
    target = _make_profiles(root, "eastmoney-managed", NAMES)
    trash = tmp_path / "trash"
    trash.mkdir()

    result = _run(root, trash, KEEP=1)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PRUNE_DRY_RUN_OK" in result.stdout
    assert "nothing was moved" in result.stdout
    assert len(list(target.iterdir())) == len(NAMES)
    assert list(trash.iterdir()) == []


def test_keeps_the_newest_and_moves_only_the_named_set(tmp_path):
    root = tmp_path / "browser-profiles"
    target = _make_profiles(root, "eastmoney-managed", NAMES)
    # The siblings that share the parent: must come out byte-for-byte unchanged.
    # `eastmoney-managed-persistent` is in here deliberately: it holds the
    # accumulated browser identity (`nid18`/`gviem`) that runs are supposed to
    # keep, so pruning it would throw away everything the requests bought.
    _make_profiles(root, "eastmoney-chrome", ["keep-me-1", "keep-me-2"])
    _make_profiles(root, "eastmoney-managed-persistent", ["identity-must-survive"])
    _make_profiles(root, "xueqiu-dedicated", ["keep-me-too"])
    trash = tmp_path / "trash"
    trash.mkdir()

    result = _run(root, trash, KEEP=2, DRY_RUN=0)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PRUNE_DONE moved=2 remaining=2" in result.stdout
    survivors = sorted(p.name for p in target.iterdir() if p.is_dir())
    assert survivors == sorted(NAMES[-2:])
    moved = sorted(p.name for p in trash.iterdir())
    assert moved == sorted(NAMES[:2])

    # CONTROL: the two sibling sets are exactly as they were.
    assert sorted(p.name for p in (root / "eastmoney-chrome").iterdir()) == [
        "keep-me-1",
        "keep-me-2",
    ]
    assert [p.name for p in (root / "xueqiu-dedicated").iterdir()] == ["keep-me-too"]
    assert [p.name for p in (root / "eastmoney-managed-persistent").iterdir()] == [
        "identity-must-survive"
    ]

    # A manifest is written so "what did it take" is answerable afterwards.
    manifests = list(target.glob(".trash-manifest-*.jsonl"))
    assert len(manifests) == 1
    assert len(manifests[0].read_text(encoding="utf-8").strip().splitlines()) == 2


def test_a_second_run_is_a_no_op(tmp_path):
    """Idempotent: retention must not report work it has already done."""
    root = tmp_path / "browser-profiles"
    _make_profiles(root, "eastmoney-managed", NAMES)
    trash = tmp_path / "trash"
    trash.mkdir()

    assert _run(root, trash, KEEP=2, DRY_RUN=0).returncode == 0
    again = _run(root, trash, KEEP=2, DRY_RUN=0)

    assert again.returncode == 0
    assert "PRUNE_NOTHING_TO_DO" in again.stdout


def test_entries_that_are_not_profiles_are_left_alone(tmp_path):
    """A stray file or a hand-made directory must not be swept up.

    The retention name pattern is the only thing standing between "our
    timestamped run directories" and "whatever else ended up in there".
    """
    root = tmp_path / "browser-profiles"
    target = _make_profiles(root, "eastmoney-managed", NAMES)
    (target / "notes.txt").write_text("not a profile", encoding="utf-8")
    (target / "manual-backup").mkdir()
    trash = tmp_path / "trash"
    trash.mkdir()

    result = _run(root, trash, KEEP=1, DRY_RUN=0)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (target / "notes.txt").exists()
    assert (target / "manual-backup").exists()
    # 4 named profiles, keep 1 -> exactly 3 moved; the two others stay put.
    assert "PRUNE_DONE moved=3 remaining=1" in result.stdout


def test_a_missing_profile_set_is_a_loud_failure(tmp_path):
    root = tmp_path / "browser-profiles"
    root.mkdir()
    trash = tmp_path / "trash"
    trash.mkdir()

    result = _run(root, trash, DRY_RUN=0)

    assert result.returncode != 0
    assert "PRUNE_FAILED reason=profile_set_missing" in result.stdout


def test_a_bad_batch_size_is_refused_before_anything_moves(tmp_path):
    root = tmp_path / "browser-profiles"
    _make_profiles(root, "eastmoney-managed", NAMES)
    trash = tmp_path / "trash"
    trash.mkdir()

    result = _run(root, trash, BATCH="ten", DRY_RUN=0)

    assert result.returncode != 0
    assert "PRUNE_FAILED reason=bad_BATCH" in result.stdout
    assert len(list(trash.iterdir())) == 0
