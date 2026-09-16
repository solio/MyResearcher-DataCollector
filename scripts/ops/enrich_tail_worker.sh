#!/bin/zsh
# Rolling "tail worker": runs a second, low-rate enrichment stream alongside
# scripts/ops/enrich_all_stocks.sh without ever touching the same stock.
#
# Rationale and the risks it does NOT remove are documented in
# scripts/ops/README.md ("Concurrency: experiment result"). Summary:
#   * the work list comes from the plan the DRIVER publishes at run start
#     (runtime/enrich-runs/plan.txt, written atomically by enrich_plan.py). The
#     driver owns its rows, this worker owns the `tail` rows; the two sets are
#     disjoint by construction, so a collision is impossible rather than
#     improbable;
#   * this replaced a design where the worker walked the *same* list from the
#     tail and excluded only the stock the driver currently held. On 2026-09-16
#     that collided: both streams landed on 002028 and 8 of its candidates were
#     requested twice. The unreserved gap was the stock the driver was about to
#     take next — "exclude what the driver holds" cannot work on a shared queue;
#   * within its own list it rolls smallest-backlog-first, so it clears quick
#     wins while the driver grinds the big ones, then moves on by itself;
#   * every candidate query applies the same skip-ledger exclusion as the
#     driver, so the driver's end-of-run check_revisit.py verdict stays valid
#     even though this stream's requests land inside the driver's log window;
#   * it REFUSES to start without a plan (`TAIL_NO_PLAN`), which is also what
#     stops it from running against an ad-hoc `STOCKS=...` driver invocation.
#
# Because its own pacing is deliberately the slow end of the allowed 3..10s
# envelope, the COMBINED request rate stays near the single-stream envelope that
# is known to work, instead of doubling it. Override MIN_DELAY/MAX_DELAY to
# trade safety for speed.
#
# Terminal markers: TAIL_DONE, TAIL_NO_PLAN, TAIL_HALTED.

set -u

SCRIPT_DIR=${0:A:h}
REPO=${SCRIPT_DIR:h:h}
PY=${PY:-/opt/homebrew/anaconda3/bin/python}
RUNLOG_DIR="$REPO/runtime/enrich-runs"
PLAN_FILE=${PLAN_FILE:-"$RUNLOG_DIR/plan.txt"}
DRIVER_LOG=${DRIVER_LOG:-"$RUNLOG_DIR/driver.log"}
MIN_DELAY=${MIN_DELAY:-7.0}
MAX_DELAY=${MAX_DELAY:-10.0}
CHALLENGE_WAIT=${CHALLENGE_WAIT:-180}
COOLDOWN_SECONDS=${COOLDOWN_SECONDS:-20}
mkdir -p "$RUNLOG_DIR"
cd "$REPO" || exit 9

# The stocks this worker owns, smallest backlog first, skipping any that has no
# pending rows left (the driver may have reached it, or an earlier round here
# cleared it). The plan file carries the stock codes; the pending counts are
# re-read from the database at pick time so the ordering reflects reality now
# rather than the snapshot taken when the driver started.
# Prints "<stock> <pending>", or nothing when the queue is drained.
pick_next() {
  [ -f "$PLAN_FILE" ] || return 0
  "$PY" "$SCRIPT_DIR/enrich_plan.py" pick tail "$PLAN_FILE"
}

# The stock the driver is currently holding, read from its own log. Kept purely
# as an audit line — correctness no longer depends on it.
driver_current_stock() {
  [ -f "$DRIVER_LOG" ] || { print ""; return }
  sed -n 's/^===== stock=\([0-9][0-9]*\) .*/\1/p' "$DRIVER_LOG" | tail -1
}

driver_finished() {
  [ -f "$DRIVER_LOG" ] || return 1
  case "$(tail -n 1 "$DRIVER_LOG")" in
    "ALL_DONE "*) return 0 ;;
    *) return 1 ;;
  esac
}

echo "TAIL_WORKER_START $(date -u +%FT%TZ) plan=$PLAN_FILE min_delay=$MIN_DELAY max_delay=$MAX_DELAY"
if [ ! -f "$PLAN_FILE" ]; then
  echo "TAIL_NO_PLAN plan=$PLAN_FILE at=$(date -u +%FT%TZ)"
  echo "TAIL_NO_PLAN: start enrich_all_stocks.sh first — it publishes the split."
  exit 0
fi
echo "TAIL_QUEUE $(awk -F'\t' '$1=="tail" && $2!="" {printf "%s ", $2}' "$PLAN_FILE")"

for round in $(seq 1 64); do
  if driver_finished; then
    echo "TAIL_NOTE round=$round driver=ALL_DONE at=$(date -u +%FT%TZ) (still draining own queue)"
  fi

  pick=$(pick_next)
  if [ -z "$pick" ]; then
    echo "TAIL_DONE round=$round driver_holds=$(driver_current_stock) at=$(date -u +%FT%TZ) reason=tail_queue_drained"
    exit 0
  fi

  stock=${pick%% *}
  pend=${pick##* }
  echo "TAIL_PICK round=$round stock=$stock pending=$pend driver_holds=$(driver_current_stock) at=$(date -u +%FT%TZ)"

  out="$RUNLOG_DIR/tail-$stock.json"
  err="$RUNLOG_DIR/tail-$stock.err"
  rm -f "$out"
  PYTHONPATH=src "$PY" -m myresearcher_collector.cli.main enrich-details \
    --source eastmoney_guba --stock "$stock" --data-dir data \
    --acquisition-mode managed-chromium --confirm-live \
    --min-delay "$MIN_DELAY" --max-delay "$MAX_DELAY" \
    --challenge-wait "$CHALLENGE_WAIT" --challenge-retries 1 \
    > "$out" 2> "$err"
  rc=$?

  verdict=$("$PY" - "$stock" "$out" <<'PY'
import json, sys
path = sys.argv[2]
try:
    d = json.load(open(path, encoding="utf-8"))
except Exception as exc:
    print(f"STOP unreadable_report:{exc}")
    raise SystemExit(0)
print(
    "TAIL_STAT"
    f" stock={sys.argv[1]}"
    f" requested={d.get('requested')}"
    f" success={d.get('success')}"
    f" failed={d.get('failed')}"
    f" filled={d.get('content_filled')}"
    f" skipped_not_found={d.get('skipped_not_found_added')}"
    f" remaining={d.get('candidates_remaining')}"
    f" access_blocks={d.get('access_block_count')}"
    f" stopped={d.get('stopped')}"
)
if d.get("stopped"):
    print("STOP access_block")
PY
)
  echo "$verdict"

  # Leading `*` is required: $verdict is multi-line.
  case "$verdict" in
    *"STOP access_block"*)
      echo "TAIL_HALTED stock=$stock reason=access_block at=$(date -u +%FT%TZ)"
      exit 0
      ;;
    *"STOP unreadable_report"*)
      echo "TAIL_HALTED stock=$stock reason=unreadable_report exit=$rc at=$(date -u +%FT%TZ)"
      exit 0
      ;;
  esac

  sleep "$COOLDOWN_SECONDS"
done

echo "TAIL_MAX_ROUNDS_REACHED rounds=64 at=$(date -u +%FT%TZ)"
exit 1
