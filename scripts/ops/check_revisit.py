#!/usr/bin/env python
"""Guard: posts already known-missing must never be re-requested.

Cross-checks the detail-enrichment jsonl log against the sidecar skip ledger
(`detail_enrichment_skips`). If a post that was *already* marked missing before
the run appears in the run's log, a deleted post was visited again -- the exact
regression this check exists to catch.

Two boundaries keep the verdict honest:

* ``--from-line N`` ignores jsonl lines before the Nth, so only this run's
  requests are inspected (historical rows are not re-visits).
* ``--run-start ISO`` ignores ledger entries first seen at/after that instant.
  A post marked *during* the run is a fresh discovery -- its one and only
  request is the request that found it, not a re-visit. Without this the guard
  reports itself: the discovery event sits in the very log window being scanned.

Read-only: this guard never writes to the ledger or the log.

Usage:
    python scripts/ops/check_revisit.py [--from-line N] [--run-start ISO8601]

Exit code: 0 = clean, 2 = revisit detected, 3 = inputs unavailable.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Layout: <repo>/scripts/ops/<this file> -> repo root is two levels up.
REPO = Path(__file__).resolve().parents[2]
DEFAULT_JSONL = REPO / "runtime" / "logs" / "eastmoney-detail-enrichment.jsonl"
DEFAULT_LEDGER = REPO / "data" / "collector.detail_enrichment_skips.db"
SOURCE = "eastmoney_guba"


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def ledger_entries(ledger: Path, source: str) -> dict[str, str]:
    """Return {source_item_id: first_seen_at} for the given source."""
    if not ledger.is_file():
        return {}
    con = sqlite3.connect(f"{ledger.resolve().as_uri()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT source_item_id, first_seen_at FROM detail_enrichment_skips WHERE source=?",
            (source,),
        ).fetchall()
    finally:
        con.close()
    return {str(r[0]): str(r[1]) for r in rows}


def scan(jsonl: Path, from_line: int) -> tuple[int, list[dict]]:
    """Return (rows_scanned, rows) for the jsonl lines after ``from_line``."""
    if not jsonl.is_file():
        return 0, []
    rows: list[dict] = []
    scanned = 0
    with jsonl.open(encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index < from_line:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            scanned += 1
            if str(row.get("source_item_id", "")):
                rows.append(row)
    return scanned, rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--source", default=SOURCE)
    parser.add_argument("--from-line", type=int, default=0)
    parser.add_argument(
        "--run-start",
        default=None,
        help="ISO8601 start of the run; ledger entries first seen at/after this "
        "are treated as discoveries of this run, not pre-existing marks",
    )
    args = parser.parse_args(argv)

    if not args.jsonl.is_file():
        print(f"REVISIT_CHECK unavailable: jsonl not found: {args.jsonl}")
        return 3

    entries = ledger_entries(args.ledger, args.source)
    run_start = parse_ts(args.run_start)
    preexisting = {
        sid for sid, first in entries.items()
        if run_start is None or (parse_ts(first) or datetime.min.replace(tzinfo=timezone.utc)) < run_start
    }
    discovered_now = len(entries) - len(preexisting)

    scanned, rows = scan(args.jsonl, args.from_line)
    hits = [row for row in rows if str(row.get("source_item_id", "")) in preexisting]

    print(
        f"REVISIT_CHECK ledger_marked={len(entries)} "
        f"preexisting_marks={len(preexisting)} discovered_this_run={discovered_now} "
        f"jsonl_rows_scanned={scanned}"
    )
    if not hits:
        print("REVISIT_CHECK OK: no already-marked post was re-requested")
        return 0

    print(f"REVISIT_CHECK VIOLATION: {len(hits)} re-request(s) of already-marked posts")
    for row in hits[:20]:
        print(
            "  ",
            row.get("timestamp"),
            row.get("source_item_id"),
            row.get("result"),
            row.get("failure_reason", ""),
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
