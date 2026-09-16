#!/usr/bin/env python
"""Single source of truth for detail-enrichment backlog accounting and for the
two-stream split.

Why this file exists
--------------------
`enrich_all_stocks.sh` used to carry a hardcoded 16-stock array labelled
"Ordered by original pending count (desc)", frozen on 2026-09-10. Two problems
fell out of that on 2026-09-16:

1. The ordering went stale. The driver always started on 601888 even after
   601012 had become the largest backlog, so "why is the work concentrated on
   601888" was an artifact of the frozen queue, not of the data.
2. `enrich_tail_worker.sh` walked the *same* list from the tail and excluded only
   the stock the driver was *currently* holding. The stock the driver was about
   to take next stayed unreserved, so both streams converged on 002028 and
   8 of its candidates were requested twice. "Exclude what the driver holds"
   cannot be made to work while both streams share one queue.

The fix is a plan computed once, by the driver, before either stream starts:
each stream gets its own queue and neither ever reads the other's stocks.

Usage
-----
    enrich_plan.py list                        # "<stock>\t<pending>", pending desc
    enrich_plan.py pending <stock>             # pending count for one stock
    enrich_plan.py write-plan <outdir> [--split]
    enrich_plan.py pick <driver|tail> [plan]   # "<stock> <pending>", smallest first

`plan.txt` is a single file, replaced atomically. **Without `--split` every stock
is assigned to `driver`**, so a single-stream run covers the whole backlog and
`enrich_tail_worker.sh` finds an empty queue and exits `TAIL_DONE` immediately:

    driver\t002648\t86
    driver\t002028\t4
    driver\t600312\t50
    driver\t300666\t42

With `--split`, the same backlog is divided by LPT between the two streams:

    driver\t002648\t86
    driver\t002028\t4
    tail\t600312\t50
    tail\t300666\t42

`--split` is opt-in on purpose. The driver cannot know whether a tail worker will
ever show up, so a default split would let `ALL_DONE` mean "done with *my* half"
while the other half sat untouched — a silent under-run. Default (=single) keeps
`ALL_DONE` meaning "the whole backlog is exhausted".

One file rather than two queue files: the driver republishes the plan whenever it
restarts (`mop_up.sh` re-runs it in a loop), and a reader that saw a fresh tail
list next to a stale driver list could pick an overlapping stock. A single atomic
replace cannot be torn.

Eligibility mirrors the collector's own rule (trimmed list-title length >= 40,
`content IS NULL`) minus ids already recorded in the sidecar skip ledger, i.e.
exactly the same predicate the drivers use for their `pending=` count. The
database is opened read-only.

The split is a longest-processing-time-first (LPT) assignment: stocks are handed
to the currently lighter stream, largest backlog first. That balances the two
sides by *pending rows*, not by number of stocks (the counts are wildly uneven —
one stock can hold half the backlog: 86 of 182 above), and it keeps the largest
single job on the driver, which is the fail-closed stream.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DB = REPO / "data" / "collector.db"
LEDGER = REPO / "data" / "collector.detail_enrichment_skips.db"

SOURCE = "eastmoney_guba"
MIN_TITLE_LEN = 40


def _connect() -> sqlite3.Connection:
    """Read-only connection with the skip ledger attached when it is readable.

    A broken/absent ledger degrades to "no exclusions" rather than failing the
    plan, matching the drivers' own behaviour. That trades a possible extra
    request for never blocking the run; the ledger is a best-effort sidecar.
    """
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    if not LEDGER.exists():
        return con
    try:
        con.execute(f"ATTACH DATABASE 'file:{LEDGER}?mode=ro' AS skips")
        present = con.execute(
            "SELECT COUNT(*) FROM skips.sqlite_master"
            " WHERE type='table' AND name='detail_enrichment_skips'"
        ).fetchone()[0]
        if not present:
            con.execute("DETACH DATABASE skips")
    except sqlite3.Error:
        pass
    return con


def _exclusion(con: sqlite3.Connection) -> str:
    # PRAGMA database_list rows are (seq, name, file).
    has_ledger = any(
        row[1] == "skips" for row in con.execute("PRAGMA database_list").fetchall()
    )
    if not has_ledger:
        return ""
    return (
        " AND p.source_item_id NOT IN ("
        "SELECT source_item_id FROM skips.detail_enrichment_skips"
        f" WHERE source='{SOURCE}')"
    )


def pending_for(codes: list[str]) -> list[tuple[str, int]]:
    """[(stock_code, pending)] for the given codes, skipping zero-pending ones."""
    if not codes:
        return []
    con = _connect()
    try:
        exclude = _exclusion(con)
        placeholders = ",".join("?" * len(codes))
        rows = con.execute(
            "SELECT p.stock_code, COUNT(*) AS n FROM posts p"
            " WHERE p.source = ?"
            f" AND p.stock_code IN ({placeholders})"
            " AND LENGTH(TRIM(COALESCE(p.title, ''))) >= ?"
            " AND p.content IS NULL"
            f"{exclude}"
            " GROUP BY p.stock_code"
            " HAVING COUNT(*) > 0",
            (SOURCE, *codes, MIN_TITLE_LEN),
        ).fetchall()
    finally:
        con.close()
    return [(str(code), int(n)) for code, n in rows]


def plan_rows(path: Path) -> list[tuple[str, str, int]]:
    """Parse plan.txt into [(role, stock, pending)]; pending is advisory only."""
    out: list[tuple[str, str, int]] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2 or not parts[1]:
            continue
        try:
            n = int(parts[2]) if len(parts) > 2 else 0
        except ValueError:
            n = 0
        out.append((parts[0], parts[1], n))
    return out


def queue_for(path: Path, role: str) -> list[str]:
    """Stock codes owned by `role`, in published order."""
    return [code for r, code, _ in plan_rows(path) if r == role]


def backlog() -> list[tuple[str, int]]:
    """[(stock_code, pending)] for every stock with pending work, desc by size."""
    con = _connect()
    try:
        exclude = _exclusion(con)
        rows = con.execute(
            "SELECT p.stock_code, COUNT(*) AS n FROM posts p"
            " WHERE p.source = ?"
            " AND LENGTH(TRIM(COALESCE(p.title, ''))) >= ?"
            " AND p.content IS NULL"
            " AND p.stock_code IS NOT NULL AND p.stock_code <> ''"
            f"{exclude}"
            " GROUP BY p.stock_code"
            " HAVING COUNT(*) > 0"
            " ORDER BY n DESC, p.stock_code ASC",
            (SOURCE, MIN_TITLE_LEN),
        ).fetchall()
    finally:
        con.close()
    return [(str(code), int(n)) for code, n in rows]


def split(work: list[tuple[str, int]]) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """Greedy LPT: largest backlog first, always onto the currently lighter side.

    Returns (driver_side, tail_side). The driver side is re-sorted by size desc
    afterwards so the fail-closed stream still starts on its biggest job.
    """
    driver: list[tuple[str, int]] = []
    tail: list[tuple[str, int]] = []
    driver_load = tail_load = 0
    for code, n in work:                      # `work` is already desc by size
        if driver_load <= tail_load:
            driver.append((code, n))
            driver_load += n
        else:
            tail.append((code, n))
            tail_load += n
    driver.sort(key=lambda item: (-item[1], item[0]))
    tail.sort(key=lambda item: (-item[1], item[0]))
    return driver, tail


def cmd_list() -> int:
    for code, n in backlog():
        print(f"{code}\t{n}")
    return 0


def cmd_pending(stock: str) -> int:
    """Print the pending count for one stock. 0 when it has no work left."""
    work = pending_for([stock])
    print(work[0][1] if work else 0)
    return 0


def cmd_pick(role: str, plan: Path) -> int:
    """Print "<stock> <pending>" for the smallest unclaimed backlog for `role`.

    Used by enrich_tail_worker.sh, which rolls smallest-first through its own
    queue. Nothing is printed when that queue has no pending work left.
    """
    codes = queue_for(plan, role)
    work = pending_for(codes)
    if not work:
        return 0
    work.sort()
    print(f"{work[0][0]} {work[0][1]}")
    return 0


def cmd_write_plan(outdir: Path, do_split: bool) -> int:
    work = backlog()
    if do_split:
        driver, tail = split(work)
        mode = "split"
    else:
        # Single-stream: this process owns everything, so an ALL_DONE means the
        # whole backlog really is exhausted.
        driver, tail = list(work), []
        mode = "single"
    outdir.mkdir(parents=True, exist_ok=True)
    body = "".join(
        f"{role}\t{code}\t{n}\n"
        for role, side in (("driver", driver), ("tail", tail))
        for code, n in side
    )
    # Write-then-rename: a reader either sees the whole previous plan or the
    # whole new one, never a half-written mix of the two.
    fd, tmp = tempfile.mkstemp(dir=outdir, prefix=".plan.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.replace(tmp, outdir / "plan.txt")
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    total = sum(n for _, n in work)
    print(
        f"PLAN_MODE {mode}"
        f" stocks={len(work)} total_pending={total}"
        f" driver_stocks={len(driver)} driver_pending={sum(n for _, n in driver)}"
        f" tail_stocks={len(tail)} tail_pending={sum(n for _, n in tail)}"
    )
    for label, side in (("PLAN_DRIVER", driver), ("PLAN_TAIL", tail)):
        if not side:
            print(f"{label} (none)")
        for code, n in side:
            print(f"{label} stock={code} pending={n}")
    return 0


def main(argv: list[str]) -> int:
    args = argv[1:]
    if args[:1] == ["list"]:
        return cmd_list()
    if args[:1] == ["pending"] and len(args) >= 2:
        return cmd_pending(args[1])
    if args[:1] == ["write-plan"] and len(args) >= 2:
        rest = args[2:]
        if rest and rest[0] not in ("--split", "--single"):
            print(f"unknown flag: {rest[0]}", file=sys.stderr)
            return 2
        return cmd_write_plan(Path(args[1]), do_split=(rest[:1] == ["--split"]))
    if args[:1] == ["pick"] and len(args) >= 2:
        plan = Path(args[2]) if len(args) >= 3 else REPO / "runtime/enrich-runs/plan.txt"
        return cmd_pick(args[1], plan)
    print(__doc__.strip().splitlines()[0], file=sys.stderr)
    print(
        "usage: enrich_plan.py list"
        " | pending <stock>"
        " | write-plan <outdir> [--split]"
        " | pick <driver|tail> [plan.txt]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
