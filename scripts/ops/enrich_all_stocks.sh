#!/bin/zsh
# Resume-safe, fail-closed detail-enrichment driver for the Eastmoney Guba rows
# in data/collector.db (legacy `posts` contract).
#
# See scripts/ops/README.md for the full operating contract. Summary:
#   * the work list is computed from the database at run start by
#     scripts/ops/enrich_plan.py, ordered by pending count (desc); it is NOT
#     hardcoded. Override with STOCKS="601888 002463" for an ad-hoc single pass;
#   * the same helper publishes a disjoint head/tail split
#     (runtime/enrich-runs/plan.txt) so this driver and enrich_tail_worker.sh can
#     run at the same time without ever selecting the same stock. See
#     "Two-stream split" in the README;
#   * eligibility = trimmed list-title length >= 40 (see content_rules.py);
#   * each stock runs as its own `enrich-details` invocation, managed-chromium;
#   * stocks with no pending rows are skipped, so the script is idempotent and
#     can be re-run anytime to resume without redoing finished work;
#   * posts recorded as missing (404) in the sidecar ledger
#     data/collector.detail_enrichment_skips.db are excluded from the pending
#     count, so dead posts are never re-requested;
#   * FAIL-CLOSED: if any stock reports `stopped` (an access/verification block
#     that did not clear within --challenge-wait x --challenge-retries), the
#     whole job halts so an unattended run never grinds through every stock.
#
# Sources live here (tracked); all outputs stay under the gitignored runtime/:
#   runtime/enrich-runs/<stock>.json|.err   per-stock run reports
#   runtime/logs/eastmoney-detail-enrichment.jsonl  request log
# The tool exit code is 1 whenever any candidate failed (e.g. deleted 404
# posts), so it is deliberately NOT used as the stop signal.
#
# FIX 2026-09-11: the halt `case` patterns used to require the verdict string to
# *begin* with "STOP ...", but the verdict is multi-line ("STAT ...\nSTOP ..."),
# so the patterns never matched and the job never halted on an access block.
# Patterns now use a leading `*` so they match anywhere in the verdict.

set -u

# Repo root is derived from this script's own location so the driver is not
# pinned to one machine's absolute path. Layout: <repo>/scripts/ops/<this file>.
SCRIPT_DIR=${0:A:h}
REPO=${SCRIPT_DIR:h:h}
PY=${PY:-/opt/homebrew/anaconda3/bin/python}
RUNLOG_DIR="$REPO/runtime/enrich-runs"
mkdir -p "$RUNLOG_DIR"
cd "$REPO" || exit 9

# --- Work list: computed at run time, never hardcoded -----------------------
# Until 2026-09-16 this was a hardcoded 16-code array "ordered by original
# pending count (desc)" frozen on 2026-09-10. It made the driver *look* biased
# toward whichever stock happened to sit first, and it silently rotted every
# time the backlog moved. The list is now derived from the database by
# scripts/ops/enrich_plan.py (one source of truth for the eligibility predicate
# and for the two-stream split).
#
# The stripped form is:
#   PLAN_FILE     runtime/enrich-runs/plan.txt — one atomic file holding BOTH
#                 sides, so the two streams can never read a torn split;
#   this process  the stocks on the `driver` rows, in published order.
#
# Set STOCKS to force a specific set/order instead (e.g. STOCKS="002648" to
# resume one stock by hand); then no plan is published and the tail worker
# refuses to start, so an ad-hoc run can never collide with it.
#
# SPLIT=1 opts into the two-stream plan: half the backlog is assigned to
# enrich_tail_worker.sh and this process will NOT touch it. That mode is only
# correct if the worker is actually started — ALL_DONE then means "done with my
# half". The default (SPLIT unset) assigns everything here, so ALL_DONE means
# the whole backlog is exhausted.
SPLIT=${SPLIT:-0}
# Pacing. These were previously left to the CLI defaults (3.0/10.0) with no way
# to override and no record of what was used, so a run could not be slowed down
# or analysed after the fact. The defaults here match the CLI, but now the rate
# is explicit, overridable, and echoed into the log (see RUN_START).
#
# Why you would slow down: on 2026-09-18 the source capped us after ~70 detail
# fetches in 11 minutes at the default rate. See "Sustained rate limiting" in
# scripts/ops/README.md.
MIN_DELAY=${MIN_DELAY:-3.0}
MAX_DELAY=${MAX_DELAY:-10.0}
# DETAIL_REFERER=1 reaches each detail by first opening that bar's list page and
# then navigating in-page with JS, so the detail request carries a Referer and
# Sec-Fetch-Site: same-origin (a plain `goto(referer=...)` would carry the header
# but no initiator, i.e. Sec-Fetch-Site: none -- measured, see README
# "referer_probe.py"). It DOUBLES the navigations per detail, so it is a switch
# rather than a default, and it is echoed on the RUN_START line so a report can
# always be traced back to the mode that produced it.
DETAIL_REFERER=${DETAIL_REFERER:-0}
PLAN_FILE="$RUNLOG_DIR/plan.txt"

# NOTE: must be a function, not `PLAN="$PY $SCRIPT_DIR/enrich_plan.py"`.
# zsh does not word-split unquoted parameter expansions (unlike bash/sh), so
# `$PLAN write-split ...` would be looked up as one command named
# "<python> <path>" and fail with "no such file or directory".
plan() {
  "$PY" "$SCRIPT_DIR/enrich_plan.py" "$@"
}

pending_count() {
  plan pending "$1"
}

if [ -n "${STOCKS:-}" ]; then
  tmp="$PLAN_FILE.tmp.$$"
  printf '%s\n' "# PLAN_OVERRIDE stocks=${STOCKS}" > "$tmp"
  for s in ${=STOCKS}; do
    printf 'driver\t%s\t0\n' "$s" >> "$tmp"
  done
  # Atomic replace, same as enrich_plan.py: a tail worker reading mid-write must
  # never see a partial driver list and mistake the rest for unclaimed work.
  mv -f "$tmp" "$PLAN_FILE"
  echo "PLAN_OVERRIDE stocks=${STOCKS} (no split published; tail worker will refuse)"
elif [ "$SPLIT" = "1" ]; then
  plan write-plan "$RUNLOG_DIR" --split || { echo "HALT_ON_PLAN_FAILED"; exit 0; }
  echo "SPLIT_MODE: this process owns only the driver rows; the tail rows need enrich_tail_worker.sh"
else
  plan write-plan "$RUNLOG_DIR" || { echo "HALT_ON_PLAN_FAILED"; exit 0; }
fi

stocks=()
while IFS=$'\t' read -r role code _pending; do
  [ "$role" = "driver" ] && [ -n "$code" ] && stocks+=("$code")
done < "$PLAN_FILE"
if [ ${#stocks[@]} -eq 0 ]; then
  echo "NO_BACKLOG $(date -u +%FT%TZ)"
  echo "ALL_DONE $(date -u +%FT%TZ)"
  exit 0
fi
echo "PLAN_DRIVER_QUEUE ${stocks[*]}"

# DRY_RUN=1 resolves the plan and the per-stock pending counts, then exits
# without launching a browser. Use it to see exactly what a run would do — and,
# with the tail worker, to confirm the two queues are disjoint — before
# committing to a live run.
if [ "${DRY_RUN:-0}" = "1" ]; then
  for s in "${stocks[@]}"; do
    echo "DRY_RUN stock=$s pending=$(pending_count "$s")"
  done
  echo "DRY_RUN_OK $(date -u +%FT%TZ)"
  exit 0
fi

# Guard: no post already marked missing may be re-requested by this run.
revisit_guard() {
  "$PY" scripts/ops/check_revisit.py \
    --from-line "$JSONL_BASELINE" --run-start "$RUN_START_ISO"
}

# Snapshot the enrichment jsonl before the run so the post-run guard only
# inspects *this* run's requests (historical "discovery" rows are not re-visits).
JSONL="$REPO/runtime/logs/eastmoney-detail-enrichment.jsonl"
JSONL_BASELINE=0
if [ -f "$JSONL" ]; then
  JSONL_BASELINE=$(wc -l < "$JSONL" | tr -d ' ')
fi

RUN_START_ISO=$(date -u +%FT%TZ)
echo "RUN_START $RUN_START_ISO jsonl_baseline=$JSONL_BASELINE min_delay=$MIN_DELAY max_delay=$MAX_DELAY detail_referer=$DETAIL_REFERER"
DETAIL_REFERER_FLAG=()
[ "$DETAIL_REFERER" = "1" ] && DETAIL_REFERER_FLAG=(--detail-referer-from-list)
for s in "${stocks[@]}"; do
  pend=$(pending_count "$s")
  if [ "${pend:-0}" -eq 0 ]; then
    echo "stock=$s skip reason=no_pending"
    continue
  fi
  echo "===== stock=$s pending=$pend start=$(date -u +%FT%TZ) ====="
  # Drop any stale/empty report from a previously interrupted invocation so the
  # post-run read can only ever see this invocation's result.
  rm -f "$RUNLOG_DIR/$s.json"
  PYTHONPATH=src "$PY" -m myresearcher_collector.cli.main enrich-details \
    --source eastmoney_guba --stock "$s" --data-dir data \
    --acquisition-mode managed-chromium --confirm-live \
    --min-delay "$MIN_DELAY" --max-delay "$MAX_DELAY" \
    --challenge-wait 180 --challenge-retries 1 \
    "${DETAIL_REFERER_FLAG[@]}" \
    > "$RUNLOG_DIR/$s.json" 2> "$RUNLOG_DIR/$s.err"
  rc=$?

  verdict=$("$PY" - "$s" "$RUNLOG_DIR/$s.json" <<'PY'
import json, sys
path = sys.argv[2]
try:
    d = json.load(open(path, encoding="utf-8"))
except Exception as exc:  # unreadable report is itself a stop condition
    print(f"STOP unreadable_report:{exc}")
    raise SystemExit(0)
print(
    "STAT"
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

  # NOTE: leading `*` is required — $verdict is multi-line, so an anchored
  # pattern like `STOP access_block*` can never match and the halt silently
  # fails to fire (this was the 2026-09-11 bug).
  case "$verdict" in
    *"STOP access_block"*)
      echo "HALT_ON_ACCESS_BLOCK stock=$s at=$(date -u +%FT%TZ)"
      echo "RESUME_HINT: re-run this script to continue from stock=$s"
      revisit_guard
      echo "ALL_DONE_HALTED $(date -u +%FT%TZ)"
      exit 0
      ;;
    *"STOP unreadable_report"*)
      echo "HALT_ON_UNREADABLE_REPORT stock=$s exit=$rc at=$(date -u +%FT%TZ)"
      revisit_guard
      echo "ALL_DONE_HALTED $(date -u +%FT%TZ)"
      exit 0
      ;;
  esac
done

revisit_guard
echo "ALL_DONE $(date -u +%FT%TZ)"
