#!/bin/zsh
# Extend coverage backwards, then enrich what that produced; cool down and retry
# on any block.
#
# WHY THIS EXISTS (2026-10-02)
# ----------------------------
# The two drivers each stop on their own signals and neither knows about the
# other: `backfill_all_stocks.sh` halts on any non-SUCCESS stock, and
# `enrich_queue.sh` tolerates a halted stock but gives up after two consecutive
# early halts. For a multi-hour unattended run whose whole point is to survive
# the source being unfriendly, somebody has to own the outer loop: notice a
# halt, wait, and try again. That is this script, and it does nothing else --
# it does not collect, parse, or write rows.
#
# THE COOLDOWN IS THE POINT: a block is the source telling us to slow down, so
# the response to a block is to stop touching it for `COOLDOWN` seconds, not to
# retry immediately (retrying immediately is what made things worse on
# 2026-09-23).
#
# WHAT "DONE" MEANS, and why the pending check is not optional
# -----------------------------------------------------------
# `enrich_queue.sh` prints QUEUE_DONE when it has walked its whole stock list --
# but a stock that halts is MOVED PAST with work left over, so QUEUE_DONE does
# NOT mean the backlog is empty. The only honest completion signal is asking the
# backlog: `enrich_plan.py list` must come back empty. Without that check this
# script would exit with thousands of candidates still pending and call it done.
#
# Env:
#   BACKFILL_FROM  required, e.g. 2026-03-04   (--from: extend the START back)
#   COOLDOWN       default 1800 s              (30 min after any block)
#   MAX_ROUNDS     default 12                  (outer-loop safety)
#   ENRICH_MODE    default managed-chromium    (`chrome-cdp` needs a browser the
#                                               operator launched; nothing here
#                                               may depend on that surviving)
#   ENRICH_WORKER  default 1                   (one persistent identity, not a
#                                               fresh one per stock)
#   ENRICH_ORDER   default asc                 (the newly backfilled rows are the
#                                               OLD end of the backlog)

set -u

REPO="${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}"
PY="${PY:-/opt/homebrew/anaconda3/bin/python}"
BACKFILL_FROM="${BACKFILL_FROM:-}"
COOLDOWN="${COOLDOWN:-1800}"
MAX_ROUNDS="${MAX_ROUNDS:-12}"
ENRICH_MODE="${ENRICH_MODE:-managed-chromium}"
ENRICH_WORKER="${ENRICH_WORKER:-1}"
ENRICH_ORDER="${ENRICH_ORDER:-asc}"

# classify <phase> ------------------------------------------------ from stdin
# Prints one verdict. Kept as a function so it can be tested without running
# either driver; the markers are copied from the drivers' own halt paths:
#   backfill: HALT_ON_UNREADABLE_REPORT / HALT_ON_COLLECTION_FAILED -> ALL_DONE_HALTED
#   enrich:   QUEUE_STOCK_HALTED / HALT_ON_ACCESS_BLOCK            -> QUEUE_DONE
classify() {
  # `grep -E`, not `grep 'A\|B'`: BSD grep's BRE does not do alternation, so the
  # first version matched nothing and a BLOCKED enrich phase fell through to
  # DONE -- i.e. the script would have exited reporting success with the whole
  # backlog still pending. The self-test below is what caught it.
  local phase="$1" text
  text=$(cat)
  if [ "$phase" = "backfill" ]; then
    # `time_seek_failure` is NOT a block and cooling down does not help it: the
    # driver's own header prescribes the fallback (`START_FORCE_PAGE_1=1`, walk
    # from page 1 and skip the seek). Reported with the stock code, which comes
    # from the HALT_ON_COLLECTION_FAILED line -- the STAT line has no code.
    if printf '%s' "$text" | grep -q 'stop_reason=time_seek_failure'; then
      local seek_stock
      seek_stock=$(printf '%s' "$text" | sed -n 's/.*HALT_ON_COLLECTION_FAILED stock=\([0-9][0-9]*\).*/\1/p' | head -1)
      echo "SEEK_FAILED ${seek_stock:-unknown}"; return
    fi
    if printf '%s' "$text" | grep -q '^ALL_DONE_HALTED'; then echo BLOCKED; return; fi
    if printf '%s' "$text" | grep -q '^ALL_DONE '; then echo DONE; return; fi
    echo UNEXPECTED; return
  fi
  # A stock that halts is skipped, so QUEUE_DONE alone is NOT completion --
  # only "queue finished AND the backlog is empty" is.
  if printf '%s' "$text" | grep -qE 'QUEUE_CIRCUIT_BREAKER|QUEUE_STOPPED'; then
    echo BREAKER; return
  fi
  if printf '%s' "$text" | grep -qE 'HALT_ON_ACCESS_BLOCK|HALT_ON_UNREADABLE_REPORT'; then
    echo BLOCKED; return
  fi
  if printf '%s' "$text" | grep -q '^QUEUE_DONE'; then echo DONE; return; fi
  echo UNEXPECTED
}


if [ "${ORCH_SELFTEST:-0}" != "1" ] && [ -z "$BACKFILL_FROM" ]; then
  # The ORCH_SELFTEST exclusion is deliberate, not tidiness: a guard placed
  # ahead of the self-test means the self-test cannot run without satisfying
  # run-time preconditions -- which is how this guard first swallowed it. Same
  # shape as the profile-occupancy guard that blocked DRY_RUN on 2026-09-28.
  echo "ORCH_FAILED reason=backfill_from_required"
  echo "ORCH_HINT BACKFILL_FROM=2026-03-04 $0   # 2 months before the 2026-05-04 floor"
  exit 2
fi

# rotate_to_end <stock> -- move the stock that just hit a block to the back of the
# queue. WAITING IS NOT ENOUGH (operator, 2026-10-02): the cooldown pauses the run,
# but the next round used to restart the same list in the same order, so the stock
# that had just been challenged was the FIRST thing retried and got challenged
# again. Rotating means the retry begins somewhere else; the blocked stock comes
# back last, after every other stock has had its turn.
rotate_to_end() {
  # Explicit 1-based indexing, no array slices: zsh arrays start at 1 (so
  # `queue[0]` is unset and a `[0]` reference aborts the script under `set -u`),
  # and `${array[@]:n}` does not skip n elements the way it reads. Both mistakes
  # were caught by the self-test below rather than in a live run.
  local target="$1" found=0
  local n=${#queue[@]}
  local i=1
  local -a rest=()
  [ "$n" -gt 0 ] || return 0

  while [ "$i" -le "$n" ]; do
    if [ "${queue[$i]}" = "$target" ]; then
      found=1
    else
      rest+=("${queue[$i]}")
    fi
    i=$((i + 1))
  done

  if [ "$found" -eq 0 ]; then
    # Target not in the queue: rotate by one anyway, so the next round starts on
    # a different stock even when the blocked one could not be identified.
    [ "$n" -gt 1 ] || return 0
    rest=()
    local head="${queue[1]}"
    i=2
    while [ "$i" -le "$n" ]; do
      rest+=("${queue[$i]}")
      i=$((i + 1))
    done
    rest+=("$head")
    echo "ORCH_ROTATE moved=$head to=tail reason=unknown_target queue=${rest[*]}"
  else
    rest+=("$target")
    echo "ORCH_ROTATE moved=$target to=tail queue=${rest[*]}"
  fi
  queue=("${rest[@]}")
}

# Rotating the STOCK is not enough when the identity is what got flagged
# (2026-10-02: four consecutive stocks hit access blocks on the SAME profile, and
# the last two were blocked on their very first request -- a fresh stock on a
# hot identity fails immediately, so moving stocks just moves the block).
# Four persistent profiles exist; cycle through them on every block.
worker_ids=("" 1 2 3)          # "" = the base profile, then -1/-2/-3
worker_idx=1
# Start the cursor on whatever ENRICH_WORKER says, so passing WORKER_ID=2 starts
# there and the first rotation moves to 3 rather than silently staying put.
_i=1
for _w in "${worker_ids[@]}"; do
  [ "$_w" = "$ENRICH_WORKER" ] && worker_idx="$_i"
  _i=$(( _i + 1 ))
done
unset _i _w 2>/dev/null || true
next_identity() {
  worker_idx=$(( (worker_idx % ${#worker_ids[@]}) + 1 ))
  ENRICH_WORKER="${worker_ids[$worker_idx]}"
  echo "ORCH_ROTATE_IDENTITY worker_id=${ENRICH_WORKER:-base(no suffix)}"
}

# In CDP mode the browser is the operator's and there is nothing to rotate: the
# only levers are moving to another stock and waiting longer. Rotating a
# WORKER_ID there is a no-op that just burns the rotation budget -- which is
# exactly what happened on 2026-10-03: four pointless rotations, then the run
# stopped claiming all identities had been tried.
rotate_identity_or_cool() {
  if [ "$ENRICH_MODE" = "chrome-cdp" ]; then
    echo "ORCH_ROTATE_IDENTITY skipped reason=cdp_has_no_separate_identity"
    cooldown "$((COOLDOWN * 4))"
  else
    next_identity
    cooldown "$COOLDOWN"
  fi
}

# Which stock does this phase output say was halted?
halted_stock() {
  sed -n 's/.*HALT_ON_COLLECTION_FAILED stock=\([0-9][0-9]*\).*/\1/p' | tail -1
}

if [ "${ORCH_SELFTEST:-0}" = "1" ]; then
  # The decision logic is the only thing here that can silently do the wrong
  # thing for hours, so it gets its own oracle: real marker strings, and the
  # cases where "done" would be a lie.
  fail=0
  check() { # check <phase> <expected> <text>
    local got; got=$(printf '%s' "$3" | classify "$1")
    if [ "$got" = "$2" ]; then
      echo "SELFTEST ok   $1 -> $got"
    else
      echo "SELFTEST FAIL $1 -> $got (expected $2)"
      fail=1
    fi
  }
  check backfill DONE   $'RUN_START ...\nSTAT status=SUCCESS stop_reason=backfill_range_complete\nALL_DONE 2026-10-02T01:00:00Z'
  check backfill BLOCKED $'STAT status=COLLECTION_FAILED stop_reason=access_block\nHALT_ON_COLLECTION_FAILED stock=601012 exit=0\nALL_DONE_HALTED 2026-10-02T01:00:00Z'
  check backfill BLOCKED $'HALT_ON_UNREADABLE_REPORT stock=002463 exit=143\nALL_DONE_HALTED 2026-10-02T01:00:00Z'
  check backfill UNEXPECTED $'RUN_START ...\n(process died mid-run, no terminal line)'
  check backfill 'SEEK_FAILED 603039' $'STAT status=COLLECTION_FAILED stop_reason=time_seek_failure range_complete=False pages=0\nHALT_ON_COLLECTION_FAILED stock=603039 exit=1\nALL_DONE_HALTED'
  check backfill 'SEEK_FAILED unknown' $'STAT status=COLLECTION_FAILED stop_reason=time_seek_failure\nALL_DONE_HALTED'
  check backfill BLOCKED $'STAT status=COLLECTION_FAILED stop_reason=access_block\nHALT_ON_COLLECTION_FAILED stock=601012 exit=0\nALL_DONE_HALTED'
  check enrich DONE    $'QUEUE_STOCK_DONE stock=601012 success=282 rc=0\nQUEUE_DONE 2026-10-02T01:00:00Z'
  check enrich BLOCKED $'QUEUE_STOCK_HALTED stock=600312 success=186 cooldown=60s\n[600312] HALT_ON_ACCESS_BLOCK stock=600312\nQUEUE_DONE'
  check enrich BLOCKED $'[601012] HALT_ON_UNREADABLE_REPORT stock=601012 exit=143\nQUEUE_DONE'
  check enrich BREAKER $'QUEUE_CIRCUIT_BREAKER consecutive_early_halts=2 at=600312\nQUEUE_STOPPED 2026-10-02T01:00:00Z'
  check enrich UNEXPECTED $'(no terminal line)'
  queue=(601012 002463 601888)
  rotate_to_end 002463 >/dev/null
  [ "${queue[*]}" = "601012 601888 002463" ] && echo "SELFTEST ok   rotate known" || { echo "SELFTEST FAIL rotate known -> ${queue[*]}"; fail=1; }
  ENRICH_WORKER=1; worker_idx=2; worker_ids=("" 1 2 3)
  next_identity >/dev/null
  [ "$ENRICH_WORKER" = "2" ] && echo "SELFTEST ok   identity 1->2" || { echo "SELFTEST FAIL identity 1->2 -> $ENRICH_WORKER"; fail=1; }
  next_identity >/dev/null; next_identity >/dev/null
  [ -z "$ENRICH_WORKER" ] && echo "SELFTEST ok   identity 3->base" || { echo "SELFTEST FAIL identity 3->base -> $ENRICH_WORKER"; fail=1; }
  next_identity >/dev/null
  [ "$ENRICH_WORKER" = "1" ] && echo "SELFTEST ok   identity wraps to 1" || { echo "SELFTEST FAIL identity wrap -> $ENRICH_WORKER"; fail=1; }
  queue=(601012 002463 601888)
  rotate_to_end 999999 >/dev/null
  [ "${queue[*]}" = "002463 601888 601012" ] && echo "SELFTEST ok   rotate unknown" || { echo "SELFTEST FAIL rotate unknown -> ${queue[*]}"; fail=1; }
  ORCH_STOCKS="601888 002463" q=(${=ORCH_STOCKS})
  [ "${#q[@]}" -eq 2 ] && [ "${q[1]}" = "601888" ] \
    && echo "SELFTEST ok   orch_stocks split" \
    || { echo "SELFTEST FAIL orch_stocks split -> ${q[*]}"; fail=1; }
  if [ "$fail" != "0" ]; then echo "SELFTEST_RESULT FAIL"; exit 1; fi
  echo "SELFTEST_RESULT PASS"
  exit 0
fi


# Cooldown with a heartbeat. A bare `sleep 1800` is indistinguishable from a
# dead process: on 2026-10-02 the operator killed a run because 30 minutes of
# silence looked like a crash. An unattended job must say it is still alive.
cooldown() {
  local total="$1" elapsed=0 step=120
  while [ "$elapsed" -lt "$total" ]; do
    local remain=$((total - elapsed))
    echo "ORCH_COOLDOWN remaining=${remain}s at=$(date -u +%FT%TZ)"
    local nap=$step
    [ "$nap" -gt "$remain" ] && nap="$remain"
    sleep "$nap"
    elapsed=$((elapsed + nap))
  done
  echo "ORCH_COOLDOWN_END at=$(date -u +%FT%TZ)"
}

# Backlog, restricted to the stocks this run owns. The global count is wrong here
# whenever ORCH_STOCKS excludes a stock somebody else is enriching (2026-10-03:
# 300666 was being run by the operator), because "done" would then never be
# reached and the orchestrator would loop through its rounds doing nothing.
pending_for_queue() {
  PYTHONPATH=src "$PY" scripts/ops/enrich_plan.py list 2>/dev/null \
    | awk -v want="${queue[*]}" '
        BEGIN { n = split(want, w, " "); for (i = 1; i <= n; i++) keep[w[i]] = 1 }
        { if ($1 in keep) total += $2 }
        END { print total + 0 }'
}

pending() {
  PYTHONPATH=src "$PY" scripts/ops/enrich_plan.py list 2>/dev/null | grep -c . || true
}

echo "ORCH_START $(date -u +%FT%TZ) backfill_from=$BACKFILL_FROM cooldown=${COOLDOWN}s"
echo "ORCH_POLICY enrich_mode=$ENRICH_MODE worker=$ENRICH_WORKER order=$ENRICH_ORDER max_rounds=$MAX_ROUNDS stocks=${ORCH_STOCKS:-roster} skip_backfill=${ORCH_SKIP_BACKFILL:-0}"

typeset -A seek_retries=()
# The working order. Passed to every phase, so a rotation survives into the next
# run instead of being reset by the driver's own hardcoded roster.
# ORCH_STOCKS restricts the whole run to a given list, so a second orchestrator
# can work the stocks nobody else is touching instead of colliding with another
# operator's inline runs (2026-10-03: three of the operator's own queues were
# live, and a default run would have re-fetched their stocks).
ORCH_STOCKS="${ORCH_STOCKS:-}"
queue=()
backfill_stocks="$ORCH_STOCKS"
round=0
while [ "$round" -lt "$MAX_ROUNDS" ]; do
  round=$((round + 1))
  echo "ORCH_ROUND round=$round at=$(date -u +%FT%TZ)"

  # ---- phase 1: backfill (extend the start backwards) -------------------
  if [ "${ORCH_SKIP_BACKFILL:-0}" = "1" ]; then
    # ENRICH-ONLY MODE. Needed because ORCH_STOCKS also restricts the backfill:
    # passing a stock that has no coverage row sends the driver down its
    # NO_COVERAGE branch (`--from FROM --to today --start-page 1`), i.e. a full
    # list walk -- which is what 603129 started doing on 2026-10-03 while the
    # intent was to enrich 7 posts. With this flag the run never touches the
    # backfill path at all.
    echo "ORCH_PHASE backfill=skipped reason=orch_skip_backfill"
    bf=""
  else
  bf=$(STOCKS="$backfill_stocks" FROM="$BACKFILL_FROM" \
       zsh "$REPO/scripts/ops/backfill_all_stocks.sh" 2>&1)
  fi
  printf '%s\n' "$bf" | grep -E '^(RUN_START|QUEUE |STAT |HALT_ON_|ALL_DONE)' || true
  if [ "${#queue[@]}" -eq 0 ] && [ -n "$ORCH_STOCKS" ]; then
    queue=(${=ORCH_STOCKS})
  fi
  if [ "${#queue[@]}" -eq 0 ]; then
    q_line=$(printf '%s\n' "$bf" | sed -n 's/^QUEUE [0-9]* //p' | head -1)
    [ -n "$q_line" ] && queue=(${=q_line})
  fi
  if [ "${ORCH_SKIP_BACKFILL:-0}" = "1" ]; then
    bf_verdict=DONE
  else
    bf_verdict=$(printf '%s' "$bf" | classify backfill)
  fi
  case "$bf_verdict" in
    DONE) echo "ORCH_PHASE backfill=done" ;;
    SEEK_FAILED*)
      seek_stock="${bf_verdict#SEEK_FAILED }"
      tries=${seek_retries[$seek_stock]:-0}
      if [ "$tries" -ge 2 ] || [ "$seek_stock" = "unknown" ]; then
        echo "ORCH_BLOCKED phase=backfill reason=seek_failed_giving_up stock=$seek_stock tries=$tries"
        cooldown "$COOLDOWN"; continue
      fi
      seek_retries[$seek_stock]=$((tries + 1))
      # Targeted fallback: only that stock, only page-1 entry -- a plain re-run
      # would fail the same way, and re-running every stock would re-walk the
      # ones already done (622 pages on the first attempt).
      echo "ORCH_SEEK_FALLBACK stock=$seek_stock attempt=$((tries + 1)) entry=--start-page 1"
      fb=$(STOCKS="$seek_stock" START_FORCE_PAGE_1=1 FROM="$BACKFILL_FROM" \
           zsh "$REPO/scripts/ops/backfill_all_stocks.sh" 2>&1)
      printf '%s\n' "$fb" | grep -E '^(STAT |HALT_ON_|ALL_DONE)' || true
      printf '%s' "$fb" | classify backfill | grep -q '^DONE' \
        && echo "ORCH_SEEK_FALLBACK ok stock=$seek_stock" \
        || echo "ORCH_SEEK_FALLBACK failed stock=$seek_stock (will retry after cooldown)"
      continue ;;
    BLOCKED)
      blocked=$(printf '%s' "$bf" | halted_stock)
      echo "ORCH_BLOCKED phase=backfill round=$round stock=${blocked:-?} cooldown=${COOLDOWN}s"
      rotate_to_end "${blocked:-}"
      backfill_stocks="${queue[*]}"
      cooldown "$COOLDOWN"; continue ;;
    *)
      echo "ORCH_UNEXPECTED phase=backfill round=$round cooldown=${COOLDOWN}s"
      cooldown "$COOLDOWN"; continue ;;
  esac

  # ---- phase 2: enrich, then ask the backlog whether it is actually done --
  stocks=("${queue[@]}")
  if [ "${#stocks[@]}" -eq 0 ]; then
    echo "ORCH_FAILED reason=no_stocks_from_backfill_queue"
    exit 2
  fi
  eq=$(ACQ_MODE="$ENRICH_MODE" WORKER_ID="$ENRICH_WORKER" ENRICH_ORDER="$ENRICH_ORDER" \
       DETAIL_REFERER=0 MIN_DELAY=3.0 MAX_DELAY=5.0 PACE_MODEL=uniform \
       zsh "$REPO/scripts/ops/enrich_queue.sh" "${stocks[@]}" 2>&1)
  printf '%s\n' "$eq" | grep -E '^(QUEUE_STOCK_HALTED|QUEUE_STOCK_DONE|QUEUE_CIRCUIT|QUEUE_STOPPED|QUEUE_DONE|ORCH|.*HALT_ON_)' || true
  case "$(printf '%s' "$eq" | classify enrich)" in
    BREAKER)
      # The breaker means "consecutive stocks died early" -- on one identity that
      # is a statement about the IDENTITY, so the first response is to change it
      # rather than to abandon the run. Only give up once every identity has been
      # tried in this cycle.
      breaker_rotations=${breaker_rotations:-0}
      if [ "$ENRICH_MODE" = "chrome-cdp" ]; then
        breaker_budget=${MAX_BREAKER_ROTATIONS:-3}
      else
        breaker_budget=${#worker_ids[@]}
      fi
      if [ "$breaker_rotations" -lt "$breaker_budget" ]; then
        breaker_rotations=$((breaker_rotations + 1))
        echo "ORCH_BREAKER_ROTATE attempt=$breaker_rotations/$breaker_budget mode=$ENRICH_MODE round=$round"
        rotate_to_end "$(printf '%s' "$eq" | sed -n 's/^QUEUE_STOCK_HALTED stock=\([0-9][0-9]*\).*/\1/p' | head -1)"
        rotate_identity_or_cool
        continue
      fi
      echo "ORCH_STOP phase=enrich reason=circuit_breaker_after_all_rotations round=$round"
      exit 1 ;;
    BLOCKED)
      blocked=$(printf '%s' "$eq" | sed -n 's/^QUEUE_STOCK_HALTED stock=\([0-9][0-9]*\).*/\1/p' | tail -1)
      echo "ORCH_BLOCKED phase=enrich round=$round stock=${blocked:-?} cooldown=${COOLDOWN}s"
      rotate_to_end "${blocked:-}"
      rotate_identity_or_cool
      continue ;;
    UNEXPECTED)
      echo "ORCH_UNEXPECTED phase=enrich round=$round cooldown=${COOLDOWN}s"
      cooldown "$COOLDOWN"; continue ;;
  esac

  left=$(pending_for_queue)
  echo "ORCH_PENDING left=$left round=$round (own stocks only)"
  if [ "${left:-0}" -eq 0 ]; then
    echo "ORCH_DONE rounds=$round at=$(date -u +%FT%TZ)"
    exit 0
  fi
  # Queue finished its list but the backlog is not empty: those stocks halted.
  echo "ORCH_ROUND_INCOMPLETE left=$left -> cooling down then another round"
  cooldown "$COOLDOWN"
done

echo "ORCH_MAX_ROUNDS_REACHED rounds=$MAX_ROUNDS at=$(date -u +%FT%TZ)"
exit 1
