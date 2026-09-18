#!/bin/zsh
# Bounded "retry until clean" wrapper around the fail-closed driver.
#
# Why: enrich_all_stocks.sh halts the whole job on the first access block. That
# is correct for unattended safety, but it also starves every stock after the
# blocking one. The driver's work list is recomputed from the database on every
# round (see enrich_plan.py), so a retry starts on whatever is currently the
# biggest backlog rather than on a stale frozen head — but a persistently
# blocked stock still ends each round early.
#
# This wrapper re-runs the driver until a round completes with no halt. Each
# round is still fail-closed (it stops at the first block); a fresh browser
# session per stock gives the next round a new chance, and a cooldown between
# rounds lets the source throttle relax. Rounds are capped so this can never
# grind forever.
#
# ---------------------------------------------------------------------------
# BARREN ROUNDS (learned the hard way, 2026-09-18).
#
# The premise above — "a fresh browser session plus a cooldown gives the next
# round a new chance" — is TRUE for a transient block and FALSE for a
# source-side rate cap. A fresh session does not change our IP or reset a
# server-side counter, and 90s is nowhere near long enough.
#
# On 2026-09-18 the source cut us off after ~70 detail fetches. Every subsequent
# round produced exactly ONE successful request and then two instantaneous
# `access_block` rejections (0.2-0.3s each — not an interstitial being served,
# an immediate refusal). Twelve rounds of that burned 64 minutes for 11 rows,
# and the retrying itself almost certainly deepened the block.
#
# The wrapper could not tell, because MOPUP_MAX_ROUNDS_REACHED looks identical
# whether the rounds yielded 70 rows or 1 each. So it now measures each round's
# yield and gives up when that collapses:
#
#   * a round's yield = sum of `success=` over the STAT lines that round added;
#   * yields at or below MIN_ROUND_YIELD count as "barren";
#   * MAX_BARREN_ROUNDS consecutive barren rounds -> MOPUP_BARREN, exit 1.
#
# Two consecutive barren rounds is unambiguous evidence of a source-side cap
# rather than transient noise, and stopping there is the point. On 2026-09-18
# it would have stopped at round 3 instead of round 12, saving ~50 minutes of
# counterproductive requests.
#
# If MOPUP_BARREN fires the correct response is a real cooldown (hours) or a
# much slower pacing (`MIN_DELAY=30 MAX_DELAY=60 zsh mop_up.sh`) — NOT another
# 12 rounds.
# ---------------------------------------------------------------------------
#
# Each round republishes plan.txt. If enrich_tail_worker.sh is running, it will
# follow the new tail side on its next pick; the two sides are always disjoint,
# so a republish can never make them overlap.
#
# See scripts/ops/README.md for the full operating contract.

set -u

# Repo root derived from this script's location: <repo>/scripts/ops/<this file>.
SCRIPT_DIR=${0:A:h}
REPO=${SCRIPT_DIR:h:h}
LOG="$REPO/runtime/enrich-runs/driver.log"
MAX_ROUNDS=${MAX_ROUNDS:-12}
COOLDOWN_SECONDS=${COOLDOWN_SECONDS:-90}
MIN_ROUND_YIELD=${MIN_ROUND_YIELD:-1}
MAX_BARREN_ROUNDS=${MAX_BARREN_ROUNDS:-2}

cd "$REPO" || exit 9

barren=0
for ((round = 1; round <= MAX_ROUNDS; round++)); do
  echo "MOPUP_ROUND $round start=$(date -u +%FT%TZ)"

  # Snapshot the shared log so this round's yield can be measured from only the
  # lines this round appended (the log is appended to across runs).
  lines_before=0
  [ -f "$LOG" ] && lines_before=$(wc -l < "$LOG" | tr -d ' ')

  zsh "$SCRIPT_DIR/enrich_all_stocks.sh" >> "$LOG" 2>&1

  last_line=$(tail -n 1 "$LOG")
  case "$last_line" in
    "ALL_DONE "*)
      # Clean completion: every stock with pending work finished without a halt.
      # Checked before the yield logic, so a legitimately tiny final round is
      # never misread as barren.
      echo "MOPUP_DONE round=$round at=$(date -u +%FT%TZ)"
      exit 0
      ;;
  esac

  # `STAT requested=105 success=70 failed=1 ...` -> sum the success= values.
  round_yield=$(tail -n "+$((lines_before + 1))" "$LOG" \
    | awk -F'success=' '/^STAT /{split($2, a, " "); s += a[1]} END{print s + 0}')

  if [ "${round_yield:-0}" -le "$MIN_ROUND_YIELD" ]; then
    barren=$((barren + 1))
  else
    barren=0
  fi

  echo "MOPUP_HALTED round=$round yield=$round_yield barren_streak=$barren last=[$last_line]; cooling ${COOLDOWN_SECONDS}s at=$(date -u +%FT%TZ)"

  if [ "$barren" -ge "$MAX_BARREN_ROUNDS" ]; then
    echo "MOPUP_BARREN rounds=$round barren_streak=$barren last_yield=$round_yield at=$(date -u +%FT%TZ)"
    echo "MOPUP_BARREN: the source is rate-limiting us, not failing transiently."
    echo "MOPUP_BARREN: stopping. Wait hours, or retry with MIN_DELAY=30 MAX_DELAY=60."
    exit 1
  fi

  sleep "$COOLDOWN_SECONDS"
done

echo "MOPUP_MAX_ROUNDS_REACHED rounds=$MAX_ROUNDS at=$(date -u +%FT%TZ)"
exit 1
