"""Minimal CLI boundary for the approved Eastmoney Guba source."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime

from myresearcher_collector.models import CollectionStatus
from myresearcher_collector.sources.eastmoney_guba import (
    CollectorConfig,
    EastmoneyGubaCollector,
)


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
