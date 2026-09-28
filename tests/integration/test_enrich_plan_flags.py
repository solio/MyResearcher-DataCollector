"""`write-plan --no-per-stock`: the plan must not re-list what the caller prints.

`PLAN_DRIVER stock=… pending=…` duplicated a list that exists twice already: the
live driver prints `===== stock=… pending=… =====` before each stock, and the dry
run prints `DRY_RUN stock=… pending=… eta=…`. The plan's own `PLAN_MODE` line keeps
the totals, so suppressing the rows loses no information -- only the repetition.

The plan is written into a temporary directory here, so the test never touches
`runtime/enrich-runs/plan.txt`.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PLANNER = REPO / "scripts" / "ops" / "enrich_plan.py"
DB = REPO / "data" / "collector.db"

pytestmark = pytest.mark.skipif(
    not DB.exists(), reason="write-plan reads the collector database for the backlog"
)


def _write_plan(tmp_path, *flags):
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    return subprocess.run(
        ["python3", str(PLANNER), "write-plan", str(tmp_path), *flags],
        capture_output=True, text=True, env=env, cwd=str(REPO),
    )


def test_the_default_still_lists_every_stock(tmp_path):
    """Existing callers must not change: the rows are only dropped on request."""
    result = _write_plan(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PLAN_MODE" in result.stdout
    assert "PLAN_DRIVER stock=" in result.stdout or "PLAN_DRIVER (none)" in result.stdout
    # ...and the plan file is still written.
    assert (tmp_path / "plan.txt").exists()


def test_no_per_stock_drops_the_rows_but_keeps_the_summary(tmp_path):
    result = _write_plan(tmp_path, "--no-per-stock")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PLAN_DRIVER stock=" not in result.stdout
    assert "PLAN_TAIL stock=" not in result.stdout
    assert "PLAN_MODE" in result.stdout, "the totals must survive"
    assert (tmp_path / "plan.txt").exists()


def test_an_unknown_flag_is_still_refused(tmp_path):
    result = _write_plan(tmp_path, "--nonsense")

    assert result.returncode == 2
    assert "unknown flag" in result.stderr


def test_the_driver_asks_for_the_compact_form():
    """The linkage, asserted on the script: both write-plan calls pass the flag."""
    script = (REPO / "scripts" / "ops" / "enrich_all_stocks.sh").read_text(encoding="utf-8")

    calls = [l for l in script.splitlines() if "plan write-plan" in l and "||" in l]
    assert calls, "expected the driver to call write-plan"
    assert all("--no-per-stock" in line for line in calls), calls
