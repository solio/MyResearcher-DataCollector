#!/bin/zsh
# Unattended backfill -> enrich chain with an out-of-band captcha watchdog.
#
# WHY A CHAIN AND NOT TWO COMMANDS
#   The whole point is "nobody is at the keyboard". Two hand-typed commands would
#   leave a window where the first has stopped and nothing is watching, and the
#   second would start against a source that just challenged us. One process
#   tree with one observer covering both phases closes that window.
#
# WHY THE OBSERVER IS A SEPARATE PROCESS, NOT A THREAD
#   It has to survive the thing it kills. A thread inside the driver dies with
#   the driver, so it can never be the component that stops it. This is a
#   side-channel process that reads artefacts the run writes anyway and never
#   talks to the browser itself.
#
# WHAT IT GUARDS
#   The drivers already fail closed, but D-016 showed a challenge can exist only
#   in the rendered DOM -- an overlay drawn over a page whose payload still
#   parses -- so "the transport said success" is not proof that nobody is being
#   challenged. The observer does not trust the run's opinion of itself.
#
#   The observer is calibrated by scripts/ops/captcha_watchdog_selftest.py, which
#   proves both directions (fires on each signal class and kills the whole tree;
#   stays quiet otherwise). Run that before trusting a live run.
#
# CONTROLS
#   LIST_CLICK_PAGING=1  turn backfill's later pages by clicking the pager
#   DETAIL_REFERER=1     reach each enriched detail via its bar's list page
#   DAYS / MIN_DELAY / MAX_DELAY / POLL_SECONDS / STOCKS are passed through.
#   DRY_RUN=1 resolves both plans and exits without a browser (no observer).

set -u

SCRIPT_DIR=${0:A:h}
REPO=${SCRIPT_DIR:h:h}
PY=${PY:-/opt/homebrew/anaconda3/bin/python}

# Both default ON here: this runner exists to exercise the current mechanisms.
# Set either to 0 to run that leg on its older path.
LIST_CLICK_PAGING=${LIST_CLICK_PAGING:-1}
DETAIL_REFERER=${DETAIL_REFERER:-1}
POLL_SECONDS=${POLL_SECONDS:-5}
DRY_RUN=${DRY_RUN:-0}

LOG_DIR="$REPO/runtime/logs"
mkdir -p "$LOG_DIR"
cd "$REPO" || exit 9

RUN_TS=$(date -u +%Y%m%dT%H%M%SZ)
TAG=$(date -u +%Y%m%d)
LOG="$LOG_DIR/unattended-chain-$RUN_TS.log"
WD_LOG="$LOG_DIR/captcha-watchdog-$RUN_TS.log"
RESULT="$REPO/runtime/watchdog-result.txt"
rm -f "$RESULT"

if [ "$DRY_RUN" = "1" ]; then
  echo "DRY_RUN: resolving both plans, no browser, no observer"
  # The backfill driver has no DRY_RUN; DRIFT_CHECK_ONLY is its no-collection
  # mode. Using the wrong name here would have launched a real backfill.
  DRIFT_CHECK_ONLY=1 LIST_CLICK_PAGING="$LIST_CLICK_PAGING" \
    zsh scripts/ops/backfill_all_stocks.sh | tail -5
  DETAIL_REFERER="$DETAIL_REFERER" DRY_RUN=1 zsh scripts/ops/enrich_all_stocks.sh | tail -4
  exit 0
fi

echo "CHAIN_START $(date -u +%FT%TZ) tag=$TAG list_click_paging=$LIST_CLICK_PAGING detail_referer=$DETAIL_REFERER poll=${POLL_SECONDS}s" >> "$LOG"

# One subshell owns both phases, so the observer has exactly one root PID and
# cannot leave the second phase running after it stops the first.
(
  echo "CHAIN_PHASE backfill start=$(date -u +%FT%TZ)"
  LIST_CLICK_PAGING="$LIST_CLICK_PAGING" zsh scripts/ops/backfill_all_stocks.sh
  backfill_rc=$?
  echo "CHAIN_PHASE backfill end rc=$backfill_rc"

  # The backfill driver exits 0 even when it halts fail-closed, so its exit code
  # cannot be used to decide whether to continue. The halt markers are the signal;
  # without this check the enrich leg would start into a source that just
  # challenged us.
  #
  # The marker set must be COMPLETE, and it is easy to get wrong: the first
  # revision grepped `HALT_ON_ACCESS_BLOCK`, which is the **enrich** driver's
  # marker and does not exist here at all, so the guard never fired and the enrich
  # leg started against a blocked source. Grep the whole log (it is per-run) and
  # cover every halt path rather than a remembered subset.
  if grep -qE "HALT_ON_|ALL_DONE_HALTED" "$LOG"; then
    echo "CHAIN_SKIP_ENRICH reason=backfill_halted rc=$backfill_rc"
  else
    echo "CHAIN_PHASE enrich start=$(date -u +%FT%TZ)"
    DETAIL_REFERER="$DETAIL_REFERER" zsh scripts/ops/enrich_all_stocks.sh
    echo "CHAIN_PHASE enrich end rc=$?"
  fi
  echo "CHAIN_PHASES_DONE $(date -u +%FT%TZ)"
) >> "$LOG" 2>&1 &
CHAIN_PID=$!
echo "CHAIN_PID $CHAIN_PID" >> "$LOG"

"$PY" scripts/ops/captcha_watchdog.py \
  --root-pid "$CHAIN_PID" \
  --backfill-tag "$TAG" \
  --poll-seconds "$POLL_SECONDS" \
  --result-file "$RESULT" \
  --log-file "$WD_LOG" >> "$LOG" 2>&1 &
WD_PID=$!

wait "$CHAIN_PID"
CHAIN_RC=$?

# The chain is done, but if it was the OBSERVER that ended it, the observer is
# still working: it kills the tree first and writes its result file afterwards.
# Terminating it immediately (the first revision did) races that write, so the
# run reported CHAIN_FINISHED rc=143 while the watchdog had in fact fired. Give
# it a bounded window to finish and exit with its own status.
grace=0
while [ "$grace" -lt 20 ] && kill -0 "$WD_PID" 2>/dev/null; do
  sleep 1
  grace=$((grace + 1))
done
if kill -0 "$WD_PID" 2>/dev/null; then
  # It is still running, which means the chain ended on its own and the observer
  # has nothing left to guard. Stop it so a finished run leaves no stray watcher.
  kill -TERM "$WD_PID" 2>/dev/null
fi
wait "$WD_PID" 2>/dev/null
WD_RC=$?

if [ -f "$RESULT" ]; then
  echo "CHAIN_STOPPED_BY_WATCHDOG rc=$CHAIN_RC wd_rc=$WD_RC"
  cat "$RESULT" >> "$LOG"
  echo "CHAIN_STOPPED_BY_WATCHDOG $(cat "$RESULT")"
  exit 2
fi

echo "CHAIN_FINISHED rc=$CHAIN_RC wd_rc=$WD_RC $(date -u +%FT%TZ)"
echo "CHAIN_FINISHED rc=$CHAIN_RC wd_rc=$WD_RC $(date -u +%FT%TZ)" >> "$LOG"
exit "$CHAIN_RC"
