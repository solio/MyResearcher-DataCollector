"""Minimal CLI boundary for the approved Eastmoney Guba source."""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Callable

from myresearcher_collector.integration import execute_and_persist_collection
from myresearcher_collector.batch import (
    BatchConfigError,
    execute_batch_collection,
    load_targets,
    make_batch_plan,
)
from myresearcher_collector.models import CollectionStatus
from myresearcher_collector.run_report import summarize_run
from myresearcher_collector.sources.eastmoney_guba import (
    CollectorConfig,
    EastmoneyGubaCollector,
    UrllibTransport,
)
from myresearcher_collector.sources.eastmoney_guba.collector import Transport


DEFAULT_USER_AGENT = "MyResearcher-DataCollector/eastmoney_guba-live-smoke"


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, CollectionStatus):
        return value.value
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="myresearcher-collector")
    subparsers = parser.add_subparsers(dest="command", required=True)
    guba = subparsers.add_parser("eastmoney-guba", help="collect standard Eastmoney Guba posts")
    guba.add_argument("stock_code", help="six-digit A-share stock code")
    guba.add_argument("--max-pages", type=int, default=2)
    guba.add_argument("--timeout", type=float, default=20.0)

    live = subparsers.add_parser(
        "eastmoney-guba-live-smoke",
        help="run one explicitly confirmed, persisted Eastmoney Guba live smoke",
    )
    live.add_argument("stock_code", help="six-digit A-share stock code")
    live.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="new or empty local directory for collector.db and raw evidence",
    )
    live.add_argument("--max-pages", type=int, choices=(1, 2), default=1)
    live.add_argument("--timeout", type=float, default=20.0)
    live.add_argument("--min-interval", type=float, default=3.0)
    live.add_argument(
        "--user-agent",
        default=os.environ.get("MYRESEARCHER_EASTMONEY_USER_AGENT", DEFAULT_USER_AGENT),
        help="descriptive non-secret User-Agent; environment fallback is supported",
    )
    live_mode = live.add_mutually_exclusive_group(required=True)
    live_mode.add_argument(
        "--plan-only",
        action="store_true",
        help="print the bounded plan without constructing a network transport",
    )
    live_mode.add_argument(
        "--confirm-live",
        action="store_true",
        help="acknowledge that this command will perform real HTTPS GET requests",
    )

    inspect_run = subparsers.add_parser(
        "inspect-run", help="read a persisted run summary without modifying storage"
    )
    inspect_run.add_argument("--data-dir", type=Path, required=True)
    inspect_run.add_argument("--run-id", default=None, help="default: latest persisted run")

    batch = subparsers.add_parser(
        "collect-batch",
        help="collect a static target set sequentially through the single-stock boundary",
    )
    batch.add_argument("--targets", type=Path, required=True, help="JSON target config")
    batch.add_argument("--data-dir", type=Path, required=True)
    batch.add_argument("--max-pages", type=int, default=2)
    batch.add_argument("--timeout", type=float, default=20.0)
    batch_mode = batch.add_mutually_exclusive_group(required=True)
    batch_mode.add_argument(
        "--plan-only", action="store_true",
        help="print the sequential plan without constructing a transport",
    )
    batch_mode.add_argument(
        "--confirm-live", action="store_true",
        help="explicitly allow real sequential HTTPS requests",
    )
    return parser


def _validated_live_settings(args: argparse.Namespace) -> tuple[Path, str]:
    if len(args.stock_code) != 6 or not args.stock_code.isdigit():
        raise ValueError("stock_code must be six decimal digits")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    if not math.isfinite(args.min_interval) or args.min_interval < 3.0:
        raise ValueError("live smoke min_interval must be at least 3.0 seconds")
    user_agent = args.user_agent.strip()
    if not user_agent:
        raise ValueError("user_agent must be non-empty")
    return args.data_dir.expanduser().resolve(), user_agent


def _data_dir_state(data_dir: Path) -> str:
    if not data_dir.exists():
        return "absent"
    if not data_dir.is_dir():
        return "not_a_directory"
    return "empty" if next(data_dir.iterdir(), None) is None else "nonempty"


def live_smoke_plan(args: argparse.Namespace) -> dict[str, object]:
    """Describe the future execution without constructing a transport."""
    data_dir, _ = _validated_live_settings(args)
    state = _data_dir_state(data_dir)
    return {
        "mode": "PLAN_ONLY",
        "network_execution": False,
        "source": "eastmoney_guba",
        "source_access": "HTTPS_GET_ONLY",
        "stock_code": args.stock_code,
        "max_pages": args.max_pages,
        "timeout_seconds": args.timeout,
        "min_interval_seconds": args.min_interval,
        "data_dir": str(data_dir),
        "data_dir_state": state,
        "data_dir_ready": state in {"absent", "empty"},
        "sqlite_location": str(data_dir / "collector.db"),
        "raw_evidence_location": str(data_dir / "raw" / "eastmoney_guba"),
        "secrets_required": "NONE",
    }


def execute_live_smoke(
    args: argparse.Namespace,
    *,
    transport: Transport | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    run_id: str | None = None,
) -> dict[str, object]:
    """Execute the existing Collector/persistence chain; tests inject transport."""
    data_dir, user_agent = _validated_live_settings(args)
    state = _data_dir_state(data_dir)
    if state not in {"absent", "empty"}:
        raise ValueError("live smoke data_dir must be a new or empty directory")
    run_id = run_id or uuid.uuid4().hex
    execute_and_persist_collection(
        db_path=data_dir / "collector.db",
        raw_data_dir=data_dir,
        stock_code=args.stock_code,
        transport=(
            transport
            if transport is not None
            else UrllibTransport(user_agent=user_agent)
        ),
        run_id=run_id,
        collector_config=CollectorConfig(
            max_pages=args.max_pages,
            timeout_seconds=args.timeout,
            min_interval_seconds=args.min_interval,
        ),
        sleep_fn=sleep_fn,
        max_pages=args.max_pages,
    )
    return summarize_run(
        db_path=data_dir / "collector.db",
        raw_data_dir=data_dir,
        run_id=run_id,
    )


def batch_plan(args: argparse.Namespace) -> dict[str, object]:
    targets = load_targets(args.targets.expanduser().resolve())
    return make_batch_plan(targets, args.data_dir).as_dict()


def execute_batch_cli(args: argparse.Namespace) -> dict[str, object]:
    targets = load_targets(args.targets.expanduser().resolve())
    summary = execute_batch_collection(
        targets,
        data_root=args.data_dir.expanduser().resolve(),
        collector_config=CollectorConfig(max_pages=args.max_pages, timeout_seconds=args.timeout),
    )
    return summary.as_dict()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "collect-batch":
        try:
            if args.plan_only:
                print(json.dumps(batch_plan(args), ensure_ascii=False, indent=2))
                return 0
            summary = execute_batch_cli(args)
        except (BatchConfigError, LookupError, OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            print(f"batch error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if summary["stop_reason"] is not None:
            return 2
        return 0 if summary["targets_failed"] == 0 and summary["targets_partial"] == 0 else 1

    if args.command == "eastmoney-guba-live-smoke":
        try:
            if args.plan_only:
                print(json.dumps(live_smoke_plan(args), ensure_ascii=False, indent=2))
                return 0
            summary = execute_live_smoke(args)
        except (LookupError, OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            print(f"live smoke error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["status"] in ("SUCCESS", "NO_NEW_DATA") else 1

    if args.command == "inspect-run":
        data_dir = args.data_dir.expanduser().resolve()
        try:
            summary = summarize_run(
                db_path=data_dir / "collector.db",
                raw_data_dir=data_dir,
                run_id=args.run_id,
            )
        except (LookupError, OSError, ValueError, sqlite3.Error) as exc:
            print(f"inspect-run error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if args.command != "eastmoney-guba":
        return 2
    try:
        result = EastmoneyGubaCollector(
            config=CollectorConfig(max_pages=args.max_pages, timeout_seconds=args.timeout)
        ).collect(args.stock_code)
    except ValueError as exc:
        print(f"invalid request: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(asdict(result), ensure_ascii=False, default=_json_default, indent=2))
    return 0 if result.status in (CollectionStatus.SUCCESS, CollectionStatus.NO_NEW_DATA) else 1


if __name__ == "__main__":
    raise SystemExit(main())
