"""The enrich driver's start-up path must survive `set -u` (2026-09-28).

REGRESSION: a bare `$PACE_MODEL` in the RUN_START echo aborted the whole script
under `set -u` (`PACE_MODEL: parameter not set`) before a single request was
made. `DRY_RUN=1` did NOT catch it, because the dry-run branch used to `exit 0`
*before* the RUN_START echo and the flag arrays -- so "it dry-runs clean" proved
nothing about the code that had just been added.

Two things were fixed, and this file tests both:

* the pacing variables are defaulted once, at the top, with `:-`, and referenced
  as plain variables (the same pattern as every other knob in the script);
* `DRY_RUN=1` now exits **after** the preamble and every flag array, so it walks
  the same shell path as a live run up to the first network call.

The test runs the real script with a stock that has no backlog (`000000`) so it
never depends on production data for a count, and with `DRY_RUN=1` so it never
touches the network. It reads `data/collector.db`; nothing is written.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DRIVER = REPO / "scripts" / "ops" / "enrich_all_stocks.sh"
DB = REPO / "data" / "collector.db"

pytestmark = pytest.mark.skipif(
    not DB.exists(), reason="needs the collector database to resolve pending counts"
)


def _dry_run(**env):
    environment = dict(os.environ)
    environment.update({
        "STOCKS": "000000",
        "DRY_RUN": "1",
        "MIN_DELAY": "3.0",
        "MAX_DELAY": "5.0",
    })
    environment.update({k: str(v) for k, v in env.items()})
    return subprocess.run(
        ["/bin/zsh", str(DRIVER)],
        capture_output=True, text=True, env=environment, cwd=str(REPO),
    )


def test_the_startup_path_completes_with_the_pacing_variables_unset():
    """The exact failure: none of PACE_*/READ_* is set in the environment.

    A live run sets none of them either -- they are new knobs, so every existing
    invocation has them unset. Under `set -u` that must still work.
    """
    result = _dry_run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "parameter not set" not in result.stdout + result.stderr
    assert "DRY_RUN_OK" in result.stdout


def test_the_dry_run_walks_past_the_run_start_preamble():
    """The dry run must exercise the code the live path executes.

    This is the assertion that would have caught the bug: it requires the
    RUN_START line (and therefore the flag arrays around it) to have been
    evaluated *before* the dry run exits.
    """
    result = _dry_run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "RUN_START" in result.stdout, result.stdout
    assert "pace_model=longtail" in result.stdout
    assert "read_every=20" in result.stdout


def test_the_pacing_knobs_are_overridable():
    result = _dry_run(PACE_MODEL="uniform", READ_EVERY="0")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "pace_model=uniform" in result.stdout
    assert "read_every=0" in result.stdout


def test_the_browser_identity_is_reused_by_default():
    """The run must NOT arrive as a brand-new visitor every time.

    Observed in the profiles: the source issues `nid18` / `gviem` on
    .eastmoney.com, i.e. a visitor identity, and the old code discarded the whole
    profile after every run (`_fresh_managed_profile_path()`), so each run was a
    stranger. The operator's own Chrome, on the same IP, browses guba without a
    challenge -- which is what points at the browser identity rather than the IP.
    """
    result = _dry_run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "profile_dir=" in result.stdout
    assert "profile_dir=fresh" not in result.stdout
    assert "eastmoney-managed-persistent" in result.stdout


def test_the_identity_can_be_discarded_explicitly():
    """`PROFILE_DIR=` (empty) must mean fresh, not "fall back to the default".

    This uses `${VAR-default}` rather than `${VAR:-default}`: with `:-` an empty
    value counts as unset, so the escape hatch would silently do nothing -- which
    is exactly the bug the first version had.
    """
    result = _dry_run(PROFILE_DIR="")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "profile_dir=fresh" in result.stdout


def _held_profile(tmp_path):
    """A profile directory whose SingletonLock names a live process."""
    profile = tmp_path / "held-profile"
    profile.mkdir()
    # The lock is a symlink to "<host>-<pid>"; point it at this test process,
    # which is certainly alive.
    (profile / "SingletonLock").symlink_to(f"host-{os.getpid()}")
    return profile


def test_dry_run_is_never_blocked_by_a_busy_profile(tmp_path):
    """REGRESSION 2026-09-28: a dry run must be runnable at any time.

    The profile-occupancy guard was first written to `exit 3` from the preamble,
    i.e. BEFORE the dry-run branch -- so `DRY_RUN=1` failed outright on a machine
    where a run happened to be live, which is precisely when an operator wants to
    dry-run. A dry run launches no browser and touches no profile, so it must
    never be gated on occupancy; it should still REPORT it.
    """
    result = _dry_run(ACQ_MODE="managed-chromium", PROFILE_DIR=str(_held_profile(tmp_path)))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "DRY_RUN_OK" in result.stdout
    # ...and the condition is still surfaced, just not fatal.
    assert f"profile_busy={os.getpid()}" in result.stdout


def test_a_live_run_refuses_to_share_a_busy_profile(tmp_path):
    """The other half: a real run must refuse, loudly, naming the holder."""
    environment = {
        "STOCKS": "000000", "ACQ_MODE": "managed-chromium",
        "PROFILE_DIR": str(_held_profile(tmp_path)),
    }
    env = dict(os.environ)
    env.update(environment)
    result = subprocess.run(
        ["/bin/zsh", str(DRIVER)], capture_output=True, text=True, env=env, cwd=str(REPO)
    )

    assert result.returncode == 3, result.stdout + result.stderr
    assert "PROFILE_BUSY" in result.stdout
    assert f"held_by_pid={os.getpid()}" in result.stdout


def test_a_worker_id_gives_the_stream_its_own_profile(tmp_path):
    result = _dry_run(ACQ_MODE="managed-chromium", WORKER_ID="7")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "eastmoney-managed-persistent-7" in result.stdout
    assert "worker=7" in result.stdout
    assert "profile_busy=no" in result.stdout


def test_the_enrich_order_reaches_the_cli_and_is_reported():
    result = _dry_run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "enrich_order=asc" in result.stdout


def test_the_enrich_order_is_overridable():
    result = _dry_run(ENRICH_ORDER="desc")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "enrich_order=desc" in result.stdout


def test_the_dry_run_reports_a_predicted_wall_clock():
    result = _dry_run()

    assert result.returncode == 0, result.stdout + result.stderr
    # The estimate is printed with every assumption it made, plus a cross-check
    # against the ledger, so a wrong prediction is visible rather than trusted.
    assert "DRY_RUN_ETA_MODEL" in result.stdout
    assert "DRY_RUN_ETA_TOTAL" in result.stdout
    assert "DRY_RUN_ETA_CROSSCHECK" in result.stdout
    assert "DRY_RUN_ETA_CAVEAT" in result.stdout


def test_each_stock_appears_once_in_the_dry_run():
    """REGRESSION: the estimate used to list every stock a second time.

    The driver printed `DRY_RUN stock=X pending=N` and the estimate then printed
    `DRY_RUN_ETA stock=X eta=…` -- the same code twice, and `posts_per_min` (a
    property of the config alone) on every one of those lines. One line per stock,
    carrying both numbers, is the whole point of the summary.
    """
    result = _dry_run()

    assert result.returncode == 0, result.stdout + result.stderr
    stock_lines = [l for l in result.stdout.splitlines() if l.startswith("DRY_RUN stock=")]
    assert len(stock_lines) == 1, result.stdout          # STOCKS=000000 -> one stock
    # ...and that single line carries both numbers it is about.
    assert "pending=" in stock_lines[0] and "eta=" in stock_lines[0], stock_lines[0]
    # The code must not appear twice inside the dry-run summary. Scoped to the
    # DRY_RUN lines: it legitimately also appears in PLAN_DRIVER_QUEUE above.
    summary = "\n".join(l for l in result.stdout.splitlines() if l.startswith("DRY_RUN"))
    assert summary.count("000000") == 1, summary
    # No leftover duplicate-line format.
    assert "DRY_RUN_ETA stock=" not in result.stdout
    assert "DRY_RUN_TOTAL_PENDING" not in result.stdout
