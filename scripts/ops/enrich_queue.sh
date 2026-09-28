#!/bin/zsh
# Run a queue of stocks oldest-backlog-first, tolerating a per-stock halt.
#
# WHY THIS EXISTS (2026-09-28)
# ----------------------------
# `enrich_all_stocks.sh` is deliberately fail-closed: one `access_block` ends the
# WHOLE job ("so an unattended run never grinds through every stock"). That is the
# right default when a block means "something changed, look at it". It is the
# wrong shape for a long queue at a low block rate: measured that evening, the
# rate was 1-2 blocks per ~200 requests, so a 7,500-post queue would be chopped
# into dozens of fragments, each needing a human to restart it.
#
# So this wrapper keeps the per-stock halt and adds a decision on top of it:
#
#   * one stock halting does NOT cancel the queue -- the rest still run;
#   * a COOLDOWN follows a halt, so a block is never immediately hammered;
#   * a CIRCUIT BREAKER stops everything when consecutive stocks are killed
#     early (success < EARLY_FAIL_POSTS). That is the "something changed" signal
#     the inner halt exists to surface, and it still stops the run.
#
# It does not touch the collection logic. It only decides whether to start the
# next stock.
#
# Usage:
#   scripts/ops/enrich_queue.sh 002891 603806 600312 ...      # explicit order
#   STOCKS="..." scripts/ops/enrich_queue.sh                  # or via env
#   DRY_RUN=1 scripts/ops/enrich_queue.sh                     # print the plan only
#
# Anything else in the environment (ACQ_MODE, DETAIL_REFERER, MIN_DELAY,
# MAX_DELAY, PACE_MODEL, PROFILE_DIR, ...) is passed through to the driver.
#
# Env:
#   EARLY_FAIL_POSTS default 20   below this, a halt counts toward the breaker
#   BREAKER_LIMIT    default 2    consecutive early halts before giving up
#   HALT_COOLDOWN    default 60   seconds to wait after a halt

set -u

REPO="${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}"
DRIVER="$REPO/scripts/ops/enrich_all_stocks.sh"
RUNLOG_DIR="$REPO/runtime/enrich-runs"
PY="${PY:-python3}"
EARLY_FAIL_POSTS="${EARLY_FAIL_POSTS:-20}"
BREAKER_LIMIT="${BREAKER_LIMIT:-2}"
HALT_COOLDOWN="${HALT_COOLDOWN:-60}"
DRY_RUN="${DRY_RUN:-0}"

QUEUE=()
if [ "$#" -gt 0 ]; then
  QUEUE=("$@")
elif [ -n "${STOCKS:-}" ]; then
  QUEUE=(${=STOCKS})
fi
if [ ${#QUEUE[@]} -eq 0 ]; then
  echo "QUEUE_FAILED reason=no_stocks_given"
  exit 2
fi
[ -f "$DRIVER" ] || { echo "QUEUE_FAILED reason=driver_missing path=$DRIVER"; exit 2; }

echo "QUEUE_START $(date -u +%FT%TZ) stocks=${QUEUE[*]}"
echo "QUEUE_POLICY early_fail_below=$EARLY_FAIL_POSTS breaker=$BREAKER_LIMIT cooldown=${HALT_COOLDOWN}s"
if [ "$DRY_RUN" = "1" ]; then
  for s in "${QUEUE[@]}"; do
    echo "QUEUE_DRY_RUN stock=$s"
  done
  echo "QUEUE_DRY_RUN_OK"
  exit 0
fi

consecutive_early=0
for s in "${QUEUE[@]}"; do
  echo "QUEUE_STOCK_BEGIN stock=$s at=$(date -u +%FT%TZ)"
  STOCKS="$s" zsh "$DRIVER" 2>&1 | sed "s/^/[${s}] /"
  rc=$?

  # Read the verdict off the report, not the driver's stdout: the report is the
  # artefact that survives an interrupted run.
  read -r success stopped <<<"$("$PY" - "$RUNLOG_DIR/$s.json" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    print("0 None"); raise SystemExit(0)
print(f"{d.get('success') or 0} {d.get('stopped')}")
PY
)"
  success="${success:-0}"

  if [ "$stopped" = "True" ]; then
    echo "QUEUE_STOCK_HALTED stock=$s success=$success cooldown=${HALT_COOLDOWN}s"
    if [ "$success" -lt "$EARLY_FAIL_POSTS" ]; then
      consecutive_early=$((consecutive_early + 1))
    else
      consecutive_early=0
    fi
    if [ "$consecutive_early" -ge "$BREAKER_LIMIT" ]; then
      echo "QUEUE_CIRCUIT_BREAKER consecutive_early_halts=$consecutive_early at=$s"
      echo "QUEUE_STOPPED $(date -u +%FT%TZ)"
      exit 1
    fi
    sleep "$HALT_COOLDOWN"
  else
    echo "QUEUE_STOCK_DONE stock=$s success=$success rc=$rc"
    consecutive_early=0
  fi
done

echo "QUEUE_DONE $(date -u +%FT%TZ)"
