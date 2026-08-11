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

from myresearcher_collector.integration import (
    execute_and_persist_collection,
    execute_and_persist_xueqiu_collection,
)
from myresearcher_collector.batch import (
    BatchConfigError,
    execute_batch_collection,
    load_targets,
    make_batch_plan,
)
from myresearcher_collector.models import CollectionStatus
from myresearcher_collector.run_report import summarize_run
from myresearcher_collector.sources.eastmoney_guba import (
    BOOTSTRAP_MIN_PAGES,
    CollectorConfig,
    EastmoneyGubaCollector,
    UrllibTransport,
)
from myresearcher_collector.sources.eastmoney_guba.collector import Transport
from myresearcher_collector.sources.xueqiu import (
    CollectorConfig as XueqiuCollectorConfig,
    XUEQIU_BOOTSTRAP_MIN_PAGES,
    symbol_for,
)
from myresearcher_collector.storage import RAW_BODY_RETENTION_DAYS, purge_raw_bodies


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
    guba.add_argument("--max-pages", type=int, default=BOOTSTRAP_MIN_PAGES)
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

    persistent = subparsers.add_parser(
        "eastmoney-guba-persistent",
        help="run one persistent Eastmoney scope through bootstrap or incremental collection",
    )
    persistent.add_argument("stock_code", help="six-digit A-share stock code")
    persistent.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="persistent directory for collector.db and raw evidence",
    )
    persistent.add_argument("--max-pages", type=int, default=BOOTSTRAP_MIN_PAGES)
    persistent.add_argument("--timeout", type=float, default=20.0)
    persistent.add_argument("--min-interval", type=float, default=3.0)
    persistent.add_argument(
        "--user-agent",
        default=os.environ.get("MYRESEARCHER_EASTMONEY_USER_AGENT", DEFAULT_USER_AGENT),
        help="descriptive non-secret User-Agent; environment fallback is supported",
    )
    persistent_mode = persistent.add_mutually_exclusive_group(required=True)
    persistent_mode.add_argument(
        "--plan-only",
        action="store_true",
        help="inspect the persistent mode without constructing a network transport",
    )
    persistent_mode.add_argument(
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
    batch.add_argument("--max-pages", type=int, default=BOOTSTRAP_MIN_PAGES)
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
    xueqiu = subparsers.add_parser(
        "xueqiu",
        help="collect Xueqiu top-level A-share discussions through a browser-managed session",
    )
    xueqiu.add_argument("stock_code", help="six-digit A-share stock code")
    xueqiu.add_argument("--data-dir", type=Path, required=True)
    xueqiu.add_argument("--max-pages", type=int, default=XUEQIU_BOOTSTRAP_MIN_PAGES)
    xueqiu.add_argument("--timeout", type=float, default=20.0)
    xueqiu.add_argument("--min-interval", type=float, default=3.0)
    xueqiu_mode = xueqiu.add_mutually_exclusive_group(required=True)
    xueqiu_mode.add_argument("--plan-only", action="store_true")
    xueqiu_mode.add_argument(
        "--confirm-live",
        action="store_true",
        help="acknowledge a future browser-managed live run",
    )
    retention = subparsers.add_parser(
        "raw-retention",
        help="report or explicitly purge expired local raw response bodies",
    )
    retention.add_argument("--data-dir", type=Path, required=True)
    retention.add_argument("--retention-days", type=int, default=RAW_BODY_RETENTION_DAYS)
    retention.add_argument("--dry-run", action="store_true", help="report eligible bodies without deleting")
    retention.add_argument("--confirm", action="store_true", help="confirm physical body deletion")
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


def _persistent_mode(data_dir: Path, stock_code: str) -> tuple[str, str | None]:
    db_path = data_dir / "collector.db"
    if not db_path.exists():
        return "BOOTSTRAP_PENDING", None
    if not db_path.is_file():
        raise ValueError("persistent collector.db path must be a file")
    connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            """SELECT watermark_utc FROM collector_checkpoints
               WHERE source=? AND scope_key=?""",
            ("eastmoney_guba", f"stock:{stock_code}"),
        ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError("persistent collector.db does not have the approved schema") from exc
    finally:
        connection.close()
    if row is None or row[0] is None:
        return "BOOTSTRAP_PENDING", None
    return "INCREMENTAL", str(row[0])


def _validated_persistent_settings(
    args: argparse.Namespace,
) -> tuple[Path, str, str, str | None]:
    data_dir, user_agent = _validated_live_settings(args)
    state = _data_dir_state(data_dir)
    if state == "not_a_directory":
        raise ValueError("persistent data_dir must be absent or a directory")
    mode, checkpoint = _persistent_mode(data_dir, args.stock_code)
    if mode == "BOOTSTRAP_PENDING" and args.max_pages < BOOTSTRAP_MIN_PAGES:
        raise ValueError(
            f"bootstrap requires max_pages >= {BOOTSTRAP_MIN_PAGES}"
        )
    return data_dir, user_agent, mode, checkpoint


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


def persistent_run_plan(args: argparse.Namespace) -> dict[str, object]:
    """Describe bootstrap/incremental mode without mutating persistent state."""
    data_dir, _, mode, checkpoint = _validated_persistent_settings(args)
    return {
        "mode": "PLAN_ONLY",
        "network_execution": False,
        "source": "eastmoney_guba",
        "stock_code": args.stock_code,
        "collection_mode": mode,
        "checkpoint": checkpoint,
        "max_pages": args.max_pages,
        "bootstrap_min_pages": BOOTSTRAP_MIN_PAGES,
        "data_dir": str(data_dir),
        "sqlite_location": str(data_dir / "collector.db"),
        "raw_evidence_location": str(data_dir / "raw" / "eastmoney_guba"),
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
        bootstrap_if_no_checkpoint=False,
    )
    return summarize_run(
        db_path=data_dir / "collector.db",
        raw_data_dir=data_dir,
        run_id=run_id,
    )


def execute_persistent_run(
    args: argparse.Namespace,
    *,
    transport: Transport | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    run_id: str | None = None,
) -> dict[str, object]:
    """Execute one reusable scope; NULL checkpoint selects bounded bootstrap."""
    data_dir, user_agent, _, _ = _validated_persistent_settings(args)
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


def xueqiu_plan(args: argparse.Namespace) -> dict[str, object]:
    if args.max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    if args.min_interval < 3.0:
        raise ValueError("Xueqiu min_interval must be at least 3.0 seconds")
    symbol = symbol_for(args.stock_code)
    data_dir = args.data_dir.expanduser().resolve()
    return {
        "mode": "PLAN_ONLY",
        "network_execution": False,
        "source": "xueqiu",
        "access": "BROWSER_MANAGED_ANONYMOUS_SESSION",
        "entry_url": f"https://xueqiu.com/S/{symbol}",
        "stock_code": args.stock_code,
        "symbol": symbol,
        "max_pages": args.max_pages,
        "bootstrap_min_pages": XUEQIU_BOOTSTRAP_MIN_PAGES,
        "min_interval_seconds": args.min_interval,
        "data_dir": str(data_dir),
        "secrets_required": "NONE",
    }


def execute_xueqiu_run(
    args: argparse.Namespace,
    *,
    transport=None,
    sleep_fn: Callable[[float], None] = time.sleep,
    run_id: str | None = None,
) -> dict[str, object]:
    """Execute only with an injected browser transport; CLI never fabricates one."""
    if transport is None:
        raise RuntimeError("browser-managed Xueqiu transport must be supplied by the host")
    data_dir = args.data_dir.expanduser().resolve()
    result = execute_and_persist_xueqiu_collection(
        db_path=data_dir / "collector.db",
        raw_data_dir=data_dir,
        stock_code=args.stock_code,
        transport=transport,
        run_id=run_id,
        collector_config=XueqiuCollectorConfig(
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
        run_id=result.run_id,
    )


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

    if args.command == "xueqiu":
        try:
            if args.plan_only:
                print(json.dumps(xueqiu_plan(args), ensure_ascii=False, indent=2))
                return 0
            # A real browser Page is intentionally owned by the caller/runtime;
            # this CLI safety gate does not silently fall back to plain HTTP.
            raise RuntimeError("browser-managed Xueqiu transport must be supplied by the host")
        except (LookupError, OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            print(f"xueqiu error: {exc}", file=sys.stderr)
            return 2

    if args.command == "raw-retention":
        try:
            if args.dry_run and args.confirm:
                raise ValueError("dry_run and confirm are mutually exclusive")
            report = purge_raw_bodies(
                db_path=args.data_dir.expanduser().resolve() / "collector.db",
                raw_data_dir=args.data_dir.expanduser().resolve(),
                retention_days=args.retention_days,
                dry_run=not args.confirm,
                confirm=args.confirm,
            )
        except (LookupError, OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            print(f"raw-retention error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return 0 if not report.errors else 1

    if args.command == "eastmoney-guba-persistent":
        try:
            if args.plan_only:
                print(json.dumps(persistent_run_plan(args), ensure_ascii=False, indent=2))
                return 0
            summary = execute_persistent_run(args)
        except (LookupError, OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            print(f"persistent run error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["status"] in ("SUCCESS", "NO_NEW_DATA") else 1

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
