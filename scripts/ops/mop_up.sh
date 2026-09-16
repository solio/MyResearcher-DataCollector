#!/bin/zsh
# Bounded "retry until clean" wrapper around the fail-closed driver.
#
# Why: enrich_all_stocks.sh halts the whole job on the first access block. That
# is correct for unattended safety, but it also starves every stock after the
# blocking one -- the driver always restarts from the head of its list, so a
# persistently-blocked early stock means later stocks never run.
#
# This wrapper re-runs the driver until a round completes with no halt. Each
# round is still fail-closed (it stops at the first block); a fresh browser
# session per stock gives the next round a new chance, and a cooldown between
# rounds lets the source throttle relax. Rounds are capped so this can never
# grind forever.
#
# See scripts/ops/README.md for the full operating contract.

set -u

# Repo root derived from this script's location: <repo>/scripts/ops/<this file>.
SCRIPT_DIR=${0:A:h}
REPO=${SCRIPT_DIR:h:h}
LOG="$REPO/runtime/enrich-runs/driver.log"
MAX_ROUNDS=${MAX_ROUNDS:-12}
COOLDOWN_SECONDS=${COOLDOWN_SECONDS:-90}

cd "$REPO" || exit 9

for ((round = 1; round <= MAX_ROUNDS; round++)); do
  echo "MOPUP_ROUND $round start=$(date -u +%FT%TZ)"
  zsh "$SCRIPT_DIR/enrich_all_stocks.sh" >> "$LOG" 2>&1

  last_line=$(tail -n 1 "$LOG")
  case "$last_line" in
    "ALL_DONE "*)
      # Clean completion: every stock with pending work finished without a halt.
      echo "MOPUP_DONE round=$round at=$(date -u +%FT%TZ)"
      exit 0
      ;;
  esac

  echo "MOPUP_HALTED round=$round last=[$last_line]; cooling ${COOLDOWN_SECONDS}s at=$(date -u +%FT%TZ)"
  sleep "$COOLDOWN_SECONDS"
done

echo "MOPUP_MAX_ROUNDS_REACHED rounds=$MAX_ROUNDS at=$(date -u +%FT%TZ)"
exit 1
