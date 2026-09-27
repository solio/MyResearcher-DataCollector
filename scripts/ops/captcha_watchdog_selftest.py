#!/usr/bin/env python3
"""Controls for captcha_watchdog.py — run this BEFORE trusting the watchdog.

A watchdog is an instrument, and an uncalibrated instrument is worse than none:
one that never fires looks exactly like "no captcha ever happened", and one that
always fires looks like a paranoid script nobody will use. So this exercises both
directions against a **dummy chain** (never the real drivers, so the controls cost
zero requests against the source):

  * negative: with no signal present the watchdog must stay quiet AND leave the
    dummy chain running, for several poll intervals.
  * positive: for each signal class, exactly one injected signal must stop the
    watchdog within one poll, and the dummy chain -- including its **child** --
    must be observably gone afterwards.

The child-process assertion is the one that matters: killing only the parent
orphans a browser that keeps talking to the source, which is the specific failure
this watchdog exists to prevent. A version that passes the "watchdog exited"
assertion while leaving the child alive would be a false green.

Usage:  python3 scripts/ops/captcha_watchdog_selftest.py
Exit 0 when every control behaves; 1 otherwise.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WATCHDOG = REPO / "scripts" / "ops" / "captcha_watchdog.py"
PY = sys.executable
POLL = 0.5
FIRE_DEADLINE = 12.0


class Case:
    def __init__(self, name: str, expect_fire: bool, inject=None, prepare=None):
        self.name = name
        self.expect_fire = expect_fire
        self.inject = inject
        self.prepare = prepare


def _start_dummy_chain() -> tuple[subprocess.Popen, int]:
    """A parent shell with one sleeping child. Returns (parent_proc, child_pid).

    The Popen handle is kept because the parent is this process's own child: a
    killed-but-unreaped child is a **zombie**, and `os.kill(pid, 0)` succeeds on
    zombies, so probing aliveness by signal would report "still running" for a
    process the watchdog already killed. Reaping through the handle is the only
    honest check.
    """
    proc = subprocess.Popen(["sh", "-c", "sleep 600 & wait"])
    parent = proc.pid
    child = 0
    for _ in range(40):
        out = subprocess.run(
            ["pgrep", "-P", str(parent)], capture_output=True, text=True
        ).stdout.split()
        if out:
            child = int(out[0])
            break
        time.sleep(0.05)
    return proc, child


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _reap(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run_case(case: Case, tmp: Path) -> tuple[bool, str]:
    logs = tmp / "logs"
    enrich = tmp / "enrich-runs"
    diag = tmp / "diagnostics"
    for d in (logs, enrich, diag):
        d.mkdir(parents=True, exist_ok=True)
    jsonl = logs / "enrichment.jsonl"
    jsonl.write_text(
        json.dumps({"result": "success", "source_item_id": "1774619784"}) + "\n"
        + json.dumps({"result": "success", "source_item_id": "1774637253"}) + "\n",
        encoding="utf-8",
    )
    stop_file = tmp / "WATCHDOG_STOP"
    result_file = tmp / "watchdog-result.txt"

    if case.prepare:
        case.prepare(tmp, logs, enrich, diag)

    parent_proc, child = _start_dummy_chain()
    parent = parent_proc.pid
    wd = subprocess.Popen(
        [
            PY, str(WATCHDOG),
            "--root-pid", str(parent),
            "--jsonl", str(jsonl),
            "--enrich-reports", str(enrich),
            "--backfill-logs", str(logs),
            "--diagnostics", str(diag),
            "--stop-file", str(stop_file),
            "--poll-seconds", str(POLL),
            "--max-seconds", "40",
            "--result-file", str(result_file),
            "--log-file", str(logs / "watchdog.log"),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    time.sleep(1.0)  # let it take its baseline
    if case.inject:
        case.inject(tmp, logs, enrich, diag, stop_file)

    fired = False
    deadline = time.time() + FIRE_DEADLINE
    while time.time() < deadline:
        if wd.poll() is not None:
            fired = wd.returncode == 2
            break
        time.sleep(0.2)

    # Give the negative case the full window before calling it quiet.
    if not fired and wd.poll() is None:
        time.sleep(1.5)

    # Reap through the handle before judging: a killed-but-unreaped child is a
    # zombie, and signal-probing a zombie falsely reports it as running.
    parent_exited = False
    try:
        parent_proc.wait(timeout=3)
        parent_exited = True
    except subprocess.TimeoutExpired:
        parent_exited = False
    parent_alive, child_alive = (not parent_exited), _alive(child)
    reason = ""
    if result_file.exists():
        reason = result_file.read_text(encoding="utf-8").strip()

    if wd.poll() is None:
        wd.terminate()
        try:
            wd.wait(timeout=5)
        except subprocess.TimeoutExpired:
            wd.kill()

    if not parent_exited:
        _reap(parent)
        try:
            parent_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
    _reap(child)

    problems = []
    if fired != case.expect_fire:
        problems.append(f"fired={fired} expected={case.expect_fire}")
    if case.expect_fire and (parent_alive or child_alive):
        problems.append(f"chain_survived parent_alive={parent_alive} child_alive={child_alive}")
    if not case.expect_fire and not (parent_alive and child_alive):
        problems.append(f"chain_killed_without_signal parent_alive={parent_alive} child_alive={child_alive}")
    return (not problems), "; ".join(problems) + (f" [{reason}]" if reason else "")


# ---- injections -------------------------------------------------------------

def inj_jsonl_access_block(tmp, logs, enrich, diag, stop_file):
    with (logs / "enrichment.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"result": "access_block", "source_item_id": "1774999999"}) + "\n")


def inj_jsonl_challenge_reasons(tmp, logs, enrich, diag, stop_file):
    with (logs / "enrichment.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "result": "success", "source_item_id": "1774888888",
            "challenge_reasons": ["visible_text:滑块", "visible_text:拼图"],
            "challenge_tier": "text_only",
        }) + "\n")


def inj_jsonl_fixture_row(tmp, logs, enrich, diag, stop_file):
    """The unit-test marker must NOT trigger: tests legitimately carry these."""
    with (logs / "enrichment.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "result": "access_block", "source_item_id": "1",
            "challenge_reasons": ["visible_text:滑块"],
        }) + "\n")


def inj_enrich_report(tmp, logs, enrich, diag, stop_file):
    (enrich / "601012.json").write_text(
        json.dumps({"access_block_count": 1, "stopped": True}), encoding="utf-8"
    )


def inj_backfill_report(tmp, logs, enrich, diag, stop_file):
    (logs / "backfill-601012-20260924.json").write_text(
        json.dumps({"status": "COLLECTION_FAILED", "stop_reason": "challenge"}), encoding="utf-8"
    )


def inj_screenshot(tmp, logs, enrich, diag, stop_file):
    (diag / "eastmoney-20260924T120000.png").write_bytes(b"\x89PNG\r\n\x1a\n")


def inj_stop_file(tmp, logs, enrich, diag, stop_file):
    stop_file.write_text("manual abort\n", encoding="utf-8")


def inj_driver_log(tmp, logs, enrich, diag, stop_file):
    (logs / "backfill-all-20260924T120000Z.log").write_text(
        "HALT_ON_ACCESS_BLOCK stock=601012 at=2026-09-24T12:00:00Z\n", encoding="utf-8"
    )


def inj_driver_log_collection_failed(tmp, logs, enrich, diag, stop_file):
    """The marker the chain wrapper actually missed on 2026-09-24.

    The wrapper grepped `HALT_ON_ACCESS_BLOCK` -- the *enrich* driver's marker,
    absent from the backfill driver -- so a halted backfill did not stop the
    enrich leg. This control pins the real backfill marker so neither the
    watchdog nor a future wrapper can regress to a partial marker set.
    """
    (logs / "backfill-all-20260924T120001Z.log").write_text(
        "STOP collection_failed\n"
        "HALT_ON_COLLECTION_FAILED stock=601012 exit=1 at=2026-09-24T12:00:01Z\n"
        "ALL_DONE_HALTED 2026-09-24T12:00:01Z\n",
        encoding="utf-8",
    )


CASES = [
    Case("negative_no_signal", expect_fire=False),
    Case("positive_jsonl_access_block", True, inj_jsonl_access_block),
    Case("negative_jsonl_fixture_row_ignored", False, inj_jsonl_fixture_row),
    Case("positive_jsonl_challenge_reasons", True, inj_jsonl_challenge_reasons),
    Case("positive_enrich_report_access_block", True, inj_enrich_report),
    Case("positive_backfill_report_non_success", True, inj_backfill_report),
    Case("positive_new_diagnostic_screenshot", True, inj_screenshot),
    Case("positive_driver_log_halt", True, inj_driver_log),
    Case("positive_driver_log_collection_failed", True, inj_driver_log_collection_failed),
    Case("positive_manual_stop_file", True, inj_stop_file),
]


def main() -> int:
    if not WATCHDOG.exists():
        print(f"watchdog not found: {WATCHDOG}")
        return 1
    failures = 0
    print(f"watchdog selftest — {len(CASES)} controls, poll={POLL}s\n")
    for case in CASES:
        tmp = Path(tempfile.mkdtemp(prefix="wd-selftest-"))
        try:
            ok, detail = run_case(case, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        verdict = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        expect = "should fire" if case.expect_fire else "should stay quiet"
        print(f"  [{verdict}] {case.name} ({expect})")
        if detail:
            print(f"         {detail}")
    print()
    if failures:
        print(f"{failures} control(s) FAILED — do not trust the watchdog")
        return 1
    print("all controls passed: the watchdog fires on every challenge signal and "
          "stays quiet otherwise, and it kills the whole chain when it fires")
    return 0


if __name__ == "__main__":
    sys.exit(main())
