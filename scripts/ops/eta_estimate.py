#!/usr/bin/env python3
"""Predict how long a queue will take, from the configured pacing.

WHY A SEPARATE SCRIPT: the driver's DRY_RUN branch already knows the per-stock
pending counts, and an estimate is the one thing that turns those counts into a
decision ("is this a 40-minute job or an overnight one?"). Keeping the arithmetic
here means it can be tested without running the driver, and the driver stays a
sequencer.

THE MODEL, and every assumption in it, is printed rather than hidden. Per post a
single stream pays:

    sleep_before                paced delay between posts
  + read_pause (amortised)      longtail only: (read_min+read_max)/2 / read_every
  + navigations x dwell         referer mode opens a list page first
  + navigations x request       measured page cost, calibrated from the ledger

`request` is NOT guessed: it is the median of `request_duration_sec` over the
most recent successful ledger rows (15,686 samples on 2026-09-28 gave a median of
0.35s -- so the sleep dominates and the estimate is not sensitive to it). If the
ledger is missing the script says so and falls back rather than pretending.

WHAT IT IS NOT: a promise. It ignores access blocks and their 180s waits,
parse failures, and the first-run cost of a cold profile. It also assumes
`--workers` streams actually divide the queue evenly, which is an idealisation --
the same total offer split across more streams is not free (concurrency raises
the block rate; measured that day: 1 stream 1.5% vs 5 streams 6.2%).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

DEFAULT_LEDGER = "runtime/logs/eastmoney-detail-enrichment.jsonl"
FALLBACK_REQUEST_SEC = 0.4


def sleep_mean(pace_model: str, min_delay: float, max_delay: float) -> float:
    """Expected `sleep_before` for one post, matching the collector's sampler."""
    if pace_model == "uniform":
        # random.uniform(min, max)
        return (min_delay + max_delay) / 2.0
    # min + Exp(mean = max - min), i.e. mean == max, capped at 3*max. The cap trims
    # a negligible amount of the tail (for min=3,max=5: P(X>15) = e^-6 ~ 0.25%), so
    # ignoring it here is stated rather than silently assumed.
    return min_delay + max(0.0, max_delay - min_delay)


def calibrate_request_sec(ledger: Path, samples: int) -> tuple[float, str]:
    """Median measured request time over recent successful rows."""
    if not ledger.exists():
        return FALLBACK_REQUEST_SEC, f"ledger-missing(fallback={FALLBACK_REQUEST_SEC})"
    values: list[float] = []
    try:
        with ledger.open(encoding="utf-8") as handle:
            lines = handle.readlines()[-samples:]
    except OSError:
        return FALLBACK_REQUEST_SEC, f"ledger-unreadable(fallback={FALLBACK_REQUEST_SEC})"
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if str(row.get("source_item_id")) == "1":       # test fixtures live here
            continue
        if row.get("result") != "success":
            continue
        value = row.get("request_duration_sec")
        if isinstance(value, (int, float)) and value > 0:
            values.append(float(value))
    if not values:
        return FALLBACK_REQUEST_SEC, f"no-samples(fallback={FALLBACK_REQUEST_SEC})"
    return statistics.median(values), f"ledger-median({len(values)}/last {samples} rows)"


def empirical_per_post(ledger: Path, samples: int) -> tuple[float | None, int]:
    """Median gap between consecutive successful posts inside the same run.

    THE CROSS-CHECK. The analytic model is built from the configured pacing; this
    is what the same ledger says the run actually did. They answer different
    questions (the model predicts, this describes), so a large disagreement is
    information, not noise -- it means today's config differs from the config that
    produced the rows, or the source got slower. Gaps over 60s are dropped: those
    are challenge waits and operator breaks, not per-post cadence.
    """
    if not ledger.exists():
        return None, 0
    try:
        with ledger.open(encoding="utf-8") as handle:
            lines = handle.readlines()[-samples:]
    except OSError:
        return None, 0
    per_run: dict[str, list[str]] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("result") != "success" or str(row.get("source_item_id")) == "1":
            continue
        per_run.setdefault(str(row.get("run_id")), []).append(str(row.get("timestamp")))
    from datetime import datetime, timezone

    gaps: list[float] = []
    for stamps in per_run.values():
        if len(stamps) < 5:
            continue
        stamps.sort()
        parsed = []
        for stamp in stamps:
            try:
                parsed.append(datetime.fromisoformat(stamp.replace("Z", "+00:00")))
            except ValueError:
                continue
        for a, b in zip(parsed, parsed[1:]):
            delta = (b - a).total_seconds()
            if 0 < delta <= 60:
                gaps.append(delta)
    if not gaps:
        return None, 0
    return statistics.median(gaps), len(gaps)


def human(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds/60:.0f}m"
    return f"{int(seconds//3600)}h{int((seconds%3600)//60):02d}m"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stock", action="append", default=[],
                        help="CODE=PENDING, repeatable, in queue order")
    parser.add_argument("--pace-model", choices=("uniform", "longtail"), default="uniform")
    parser.add_argument("--min-delay", type=float, default=3.0)
    parser.add_argument("--max-delay", type=float, default=5.0)
    parser.add_argument("--read-every", type=int, default=20)
    parser.add_argument("--read-min", type=float, default=30.0)
    parser.add_argument("--read-max", type=float, default=90.0)
    parser.add_argument("--detail-referer", type=int, choices=(0, 1), default=0)
    parser.add_argument("--dwell-min", type=float, default=0.4)
    parser.add_argument("--dwell-max", type=float, default=1.6)
    parser.add_argument("--workers", type=int, default=1,
                        help="streams you intend to run; divides the wall clock")
    parser.add_argument("--ledger", default=DEFAULT_LEDGER)
    parser.add_argument("--ledger-samples", type=int, default=2000)
    args = parser.parse_args(argv)

    queue: list[tuple[str, int]] = []
    for item in args.stock:
        code, _, pending = item.partition("=")
        try:
            queue.append((code.strip(), int(pending)))
        except ValueError:
            print(f"DRY_RUN_ETA_FAILED reason=bad_stock_arg value={item!r}", file=sys.stderr)
            return 2
    if not queue:
        print("ETA_FAILED reason=no_stocks_given", file=sys.stderr)
        return 2
    if args.workers < 1:
        print("ETA_FAILED reason=workers_must_be_ge_1", file=sys.stderr)
        return 2

    request_sec, calibration = calibrate_request_sec(Path(args.ledger), args.ledger_samples)
    base_sleep = sleep_mean(args.pace_model, args.min_delay, args.max_delay)
    navs = 2 if args.detail_referer else 1
    dwell_sec = (args.dwell_min + args.dwell_max) / 2.0 if args.detail_referer else 0.0
    read_pause = (
        (args.read_min + args.read_max) / 2.0 / args.read_every
        if args.pace_model == "longtail" and args.read_every > 0 else 0.0
    )
    per_post = base_sleep + read_pause + navs * (dwell_sec + request_sec)

    print(
        f"DRY_RUN_ETA_MODEL pace={args.pace_model} min={args.min_delay} max={args.max_delay} "
        f"sleep_mean={base_sleep:.2f}s read_pause={read_pause:.2f}s navs={navs} "
        f"dwell={dwell_sec:.2f}s request={request_sec:.2f}s "
        f"per_post={per_post:.2f}s workers={args.workers} calibrated_by={calibration}"
    )
    if args.workers > 1:
        print(
            "ETA_WARNING workers>1 assumes the streams divide the queue evenly and "
            "that concurrency is free; measured 2026-09-28 it is not "
            "(1 stream 1.5% blocks vs 5 streams 6.2%)."
        )

    # DELIBERATELY SHORT PER LINE. The driver has already printed one
    # `DRY_RUN stock=… pending=…` per stock, and `posts_per_min` is a property of
    # the config alone -- identical on every line. Repeating both here made the
    # estimate its own echo. So: `pending` and the rate appear once, the per-stock
    # lines carry only what varies with the stock.
    # ONE LINE PER STOCK. The caller already knows the stock list and its pending
    # counts; printing those again next to an `eta=` line listed every stock twice
    # (and `posts_per_min` is a property of the config alone, so it was repeated on
    # every line for no information). So each stock appears once, carrying both
    # numbers, and the rate appears once in the total.
    total = sum(p for _, p in queue)
    rate = 60.0 / per_post * args.workers
    for code, pending in queue:
        print(f"DRY_RUN stock={code} pending={pending} "
              f"eta={human(pending * per_post / args.workers)}")
    print(f"DRY_RUN_ETA_TOTAL pending={total} posts_per_min={rate:.1f} "
          f"eta={human(total * per_post / args.workers)} workers={args.workers}")
    measured, measured_n = empirical_per_post(Path(args.ledger), args.ledger_samples)
    if measured is not None:
        print(
            f"DRY_RUN_ETA_CROSSCHECK observed_per_post={measured:.2f}s over {measured_n} gaps "
            f"vs model={per_post:.2f}s "
            f"({(per_post/measured - 1) * 100:+.0f}%) "
            "-- the observed side reflects whatever pacing produced those rows"
        )
    else:
        print("DRY_RUN_ETA_CROSSCHECK unavailable (no consecutive successful rows in the window)")

    print(
        "DRY_RUN_ETA_CAVEAT ignores access blocks and their 180s waits, parse failures, and "
        "the first run on a cold profile."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
