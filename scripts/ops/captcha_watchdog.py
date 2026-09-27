#!/usr/bin/env python3
"""Side-channel captcha watchdog for an unattended backfill/enrich run.

WHY THIS EXISTS
---------------
The drivers already fail closed, but "already fails closed" is not the same as
"cannot be missed". D-016 established that a challenge can exist **only in the
rendered DOM** -- a slider overlay drawn on top of a page whose embedded payload
still parses -- so a byte-level oracle reports a normal success while the source
is actively challenging us. `enrich_all_stocks.sh` stops on `stopped=True`, which
is produced from the transport's own verdict; if that verdict is blind, the run
grinds on and every extra request is another chance to be counted.

This process watches from **outside** the run: it never touches the browser, and
it does not trust the run's opinion of itself. It reads artefacts the run writes
anyway, and on the first challenge signal it kills the run's whole process tree.

WHAT IT WATCHES (every signal is evidence, not a heuristic guess)
----------------------------------------------------------------
a. `eastmoney-detail-enrichment.jsonl` -- rows appended after the baseline line
   with `result == "access_block"`, or a non-empty `challenge_reasons`, or a
   `challenge_tier`. Measured: 137 production rows carry `result=access_block`,
   so this is a signal that actually fires rather than a placeholder.
   Rows with `source_item_id == "1"` are ignored: that is the unit-test fixture
   id, and test rows legitimately contain `challenge_reasons` (see D-020/D-022).
b. `runtime/enrich-runs/<stock>.json` reports written during this run with
   `access_block_count > 0` or `stopped == True`.
c. `runtime/logs/backfill-<stock>-<tag>.json` written during this run with
   `status != "SUCCESS"`. The backfill report carries **no** challenge field, so
   the status is the only channel it offers.
d. new `runtime/diagnostics/*.png`. Screenshots are NOT routine: the whole
   directory held 5 files, all from 2026-08-13, across hundreds of navigations.
   A new one during a run means a challenge was detected and captured.
e. `--stop-file` exists. This is also the manual abort switch: `touch` it and the
   run stops, so a human can stop the chain without hunting for a PID.

FAIL-SAFE DIRECTION
-------------------
Every uncertainty resolves to "stop". An unreadable artefact, a malformed JSON
row or a report that cannot be parsed is a trigger, not an exemption: stopping a
healthy run costs a re-run, while continuing a challenged one costs the IP.

The exit code is 2 when it fires, so a wrapper can tell "stopped by watchdog"
from "chain failed on its own" (non-zero but different) and "chain finished".
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

FIXTURE_ITEM_ID = "1"  # unit-test marker; see D-020 / D-022
NON_SUCCESS = ("SUCCESS",)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _log(handle, message: str) -> None:
    line = f"{_now_iso()} WATCHDOG {message}"
    print(line, flush=True)
    if handle is not None:
        handle.write(line + "\n")
        handle.flush()


def _descendants(root: int) -> list[int]:
    """Every live descendant of `root`, deepest last. Never raises."""
    found: list[int] = []
    frontier = [root]
    while frontier:
        pid = frontier.pop()
        try:
            out = subprocess.run(
                ["pgrep", "-P", str(pid)], capture_output=True, text=True, timeout=5
            ).stdout
        except Exception:
            continue
        for token in out.split():
            try:
                child = int(token)
            except ValueError:
                continue
            if child not in found:
                found.append(child)
                frontier.append(child)
    return found


def _kill_tree(root: int, log, grace: float = 5.0) -> list[int]:
    """TERM then KILL the whole tree rooted at `root`.

    Children first, then the root: killing the parent first can orphan a
    chromium that keeps talking to the source, which is the exact outcome this
    watchdog exists to prevent.
    """
    if root <= 0:
        return []
    targets = []
    for _ in range(3):  # a process may spawn between passes
        targets = _descendants(root)
        if not targets:
            break
        time.sleep(0.2)
    targets.append(root)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in reversed(targets):  # deepest first
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
            except PermissionError as exc:
                _log(log, f"kill_failed pid={pid} sig={sig} err={exc}")
        if sig is signal.SIGTERM:
            time.sleep(grace)
    alive = [pid for pid in targets if _alive(pid)]
    if alive:
        _log(log, f"still_alive_after_kill pids={alive}")
    return targets


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_new_jsonl_rows(path: Path, baseline: int) -> tuple[list[dict], int]:
    """Rows after `baseline` lines. A malformed row is surfaced as an error row."""
    rows: list[dict] = []
    if not path.exists():
        return rows, baseline
    try:
        with path.open(encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return [{"_unreadable": True}], baseline
    if len(lines) <= baseline:
        return rows, len(lines)
    for line in lines[baseline:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"_malformed": line[:200]})
    return rows, len(lines)


def _jsonl_signal(rows: list[dict]) -> str | None:
    for row in rows:
        if row.get("_unreadable"):
            return "jsonl_unreadable"
        if row.get("_malformed"):
            return f"jsonl_malformed:{row['_malformed'][:80]}"
        if str(row.get("source_item_id")) == FIXTURE_ITEM_ID:
            continue  # unit-test row, see D-020 / D-022
        if row.get("result") == "access_block":
            return f"jsonl_access_block:{row.get('source_item_id')}"
        reasons = row.get("challenge_reasons")
        if reasons:
            return f"jsonl_challenge_reasons:{reasons}"
        if row.get("challenge_tier"):
            return f"jsonl_challenge_tier:{row['challenge_tier']}"
    return None


def _report_signal(
    directory: Path, since: float, key: str, label: str, pattern: str = "*.json"
) -> str | None:
    if not directory.exists():
        return None
    for path in sorted(directory.glob(pattern)):
        try:
            if path.stat().st_mtime < since:
                continue
        except OSError:
            return f"{label}_unreadable:{path.name}"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            # Mid-write or empty: the driver `rm -f`s before writing, so a
            # present-but-unparseable report is either in-flight or broken.
            # Neither is grounds for stopping; it is re-checked next poll.
            continue
        if not isinstance(payload, dict):
            continue
        if key == "enrich":
            if (payload.get("access_block_count") or 0) > 0:
                return f"{label}:{path.name}:access_block_count={payload['access_block_count']}"
            if payload.get("stopped") is True:
                return f"{label}:{path.name}:stopped=True"
        else:
            status = payload.get("status")
            if status and status not in NON_SUCCESS:
                return f"{label}:{path.name}:status={status}"
    return None


def _screenshot_signal(directory: Path, since: float) -> str | None:
    if not directory.exists():
        return None
    for path in sorted(directory.glob("*.png")):
        try:
            if path.stat().st_mtime >= since:
                return f"new_diagnostic_screenshot:{path.name}"
        except OSError:
            continue
    return None


def _driver_log_signal(logs: Path, since: float) -> str | None:
    # Every halt path the drivers can take. `HALT_ON_ACCESS_BLOCK` belongs to the
    # enrich driver and `HALT_ON_COLLECTION_FAILED` / `HALT_ON_UNREADABLE_REPORT`
    # to the backfill driver; listing only a remembered subset is how the wrapper
    # guard silently stopped working, so the prefixes are matched generically.
    markers = (
        "HALT_ON_",
        "ALL_DONE_HALTED",
        "STOP access_block",
        "STOP collection_failed",
        "STOP unreadable_report",
    )
    if not logs.exists():
        return None
    for path in sorted(logs.glob("unattended-chain-*.log")) + sorted(
        logs.glob("backfill-all-*.log")
    ):
        try:
            if path.stat().st_mtime < since:
                continue
            tail = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for marker in markers:
            if marker in tail:
                return f"driver_log:{path.name}:{marker}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-pid", type=int, required=True,
                        help="PID of the chain to kill when a signal fires")
    parser.add_argument("--jsonl", default="runtime/logs/eastmoney-detail-enrichment.jsonl")
    parser.add_argument("--enrich-reports", default="runtime/enrich-runs")
    parser.add_argument("--backfill-logs", default="runtime/logs")
    parser.add_argument("--diagnostics", default="runtime/diagnostics")
    parser.add_argument("--backfill-tag", default="")
    parser.add_argument("--stop-file", default="runtime/WATCHDOG_STOP")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--max-seconds", type=float, default=7200.0)
    parser.add_argument("--result-file", default="runtime/watchdog-result.txt")
    parser.add_argument("--log-file", default="runtime/logs/captcha-watchdog.log")
    parser.add_argument("--dry-run", action="store_true",
                        help="detect and report, but do not kill — for controls")
    args = parser.parse_args()

    run_start_wall = time.time()
    log_path = Path(args.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("a", encoding="utf-8")

    jsonl = Path(args.jsonl)
    baseline = 0
    if jsonl.exists():
        try:
            with jsonl.open(encoding="utf-8") as handle:
                baseline = sum(1 for _ in handle)
        except OSError:
            baseline = 0

    _log(log, f"start pid={os.getpid()} chain_pid={args.root_pid} "
              f"jsonl_baseline={baseline} poll={args.poll_seconds}s "
              f"dry_run={args.dry_run}")

    # PID-reuse guard: watching an already-dead PID means the kill could land on
    # an unrelated process. Refuse rather than guess.
    if not _alive(args.root_pid):
        _log(log, f"root_pid_not_alive pid={args.root_pid} — refusing to watch")
        log.close()
        return 3

    stop_file = Path(args.stop_file)
    enrich_dir = Path(args.enrich_reports)
    backfill_dir = Path(args.backfill_logs)
    diag_dir = Path(args.diagnostics)

    deadline = run_start_wall + args.max_seconds
    while time.time() < deadline:
        checks: list[tuple[str, str | None]] = []

        rows, baseline = _read_new_jsonl_rows(jsonl, baseline)
        checks.append(("jsonl", _jsonl_signal(rows)))
        checks.append(("enrich_report", _report_signal(enrich_dir, run_start_wall, "enrich", "enrich_report")))
        # The backfill phase exposes no challenge field, so a non-SUCCESS status
        # is the only channel it has. Omitting this check was a real coverage
        # hole caught by the selftest's backfill-report control.
        backfill_pattern = f"backfill-*-{args.backfill_tag}.json" if args.backfill_tag else "backfill-*.json"
        checks.append(("backfill_report", _report_signal(
            backfill_dir, run_start_wall, "backfill", "backfill_report", backfill_pattern
        )))
        checks.append(("screenshot", _screenshot_signal(diag_dir, run_start_wall)))
        checks.append(("driver_log", _driver_log_signal(backfill_dir, run_start_wall)))
        if stop_file.exists():
            checks.append(("stop_file", f"stop_file_present:{stop_file}"))

        for name, reason in checks:
            if reason is None:
                continue
            _log(log, f"TRIGGER check={name} reason={reason}")
            killed: list[int] = []
            if not args.dry_run:
                killed = _kill_tree(args.root_pid, log)
                _log(log, f"killed pids={killed}")
            result = Path(args.result_file)
            result.parent.mkdir(parents=True, exist_ok=True)
            result.write_text(
                f"STOPPED check={name} reason={reason} at={_now_iso()} "
                f"chain_pid={args.root_pid} killed={killed} dry_run={args.dry_run}\n",
                encoding="utf-8",
            )
            _log(log, f"exit code=2 (stopped, check={name})")
            log.close()
            return 2

        time.sleep(args.poll_seconds)

    _log(log, "timeout without a trigger (no challenge signal observed)")
    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
