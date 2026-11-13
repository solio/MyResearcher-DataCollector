#!/usr/bin/env python3
"""CLI for the collector-data strip (see apps/http_backfill/collector_clean.py).

    python3 scripts/ops/collector_clean.py check   --data /data   # 只判定，不改库
    python3 scripts/ops/collector_clean.py state   --data /data
    python3 scripts/ops/collector_clean.py export  --data /data
    python3 scripts/ops/collector_clean.py strip   --data /data --stamp 20261112
    python3 scripts/ops/collector_clean.py verify  --data /data --stamp 20261112
    python3 scripts/ops/collector_clean.py cleanup --data /data --stamp 20261112

`strip` refuses to run unless an export exists that is newer than the last write and whose
counts still match the database. It never deletes the old file: it moves it to
`tmp/clean-<stamp>/` and leaves a control-only database behind.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "http_backfill"))
import collector_clean as cc  # noqa: E402


def show(result):
    for check in result.get("checks", []):
        print(("PASS  " if check["ok"] else "FAIL  ") + check["name"]
              + (("  | " + str(check["detail"])) if check.get("detail") else ""))
    if result.get("stage"):
        print("stage:", result["stage"])
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["state", "export", "strip", "verify", "cleanup", "check"])
    parser.add_argument("--data", default="/data")
    parser.add_argument("--stamp", default="")
    args = parser.parse_args()

    if args.command == "state":
        print(json.dumps(cc.state(args.data), ensure_ascii=False, indent=2))
        sys.exit(0)
    if args.command == "export":
        record = cc.export_all(args.data, args.stamp)
        print(json.dumps(record, ensure_ascii=False, indent=2))
        sys.exit(0)
    if args.command == "check":
        # Same work as `strip` -- including the copy and the schema comparison -- but it stops
        # before the swap. Prints the exact check that would refuse, and changes nothing.
        sys.exit(show(cc.strip(args.data, args.stamp or "", dry_run=True)))
    if args.command == "strip":
        sys.exit(show(cc.strip(args.data, args.stamp or "")))
    if args.command == "verify":
        sys.exit(show(cc.verify(args.data, args.stamp)))
    sys.exit(show(cc.cleanup(args.data, args.stamp)))
