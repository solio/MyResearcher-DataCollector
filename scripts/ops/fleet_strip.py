#!/usr/bin/env python3
"""CLI wrapper for the ledger strip state machine (see apps/http_backfill/fleet_strip.py)."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "http_backfill"))
import fleet_strip  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["status", "state", "export", "detach", "verify", "cleanup"])
    parser.add_argument("--data", default="/data")
    parser.add_argument("--stamp", default="")
    parser.add_argument("--export", default="")
    parser.add_argument("--offline-path", default="")
    args = parser.parse_args()
    if args.command in ("status", "state"):
        print(fleet_strip.state(args.data))
        sys.exit(0)
    result = fleet_strip.run(args.data, args.command, args.stamp)
    for check in result["checks"]:
        print(("PASS  " if check["ok"] else "FAIL  ") + check["name"] + (("  | " + check["detail"]) if check["detail"] else ""))
    if not result["ok"]:
        print("error:", result.get("error") or "见上方 FAIL")
    print("stage:", result.get("stage_after"))
    sys.exit(0 if result["ok"] else 1)
