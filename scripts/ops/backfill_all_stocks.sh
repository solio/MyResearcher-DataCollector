#!/bin/zsh
# Resume-safe, fail-closed historical backfill driver for the 16 Eastmoney
# Guba stocks in data/collector.db (legacy `posts` contract).
#
# See scripts/ops/README.md for the full operating contract. Summary:
#   * each stock runs as its own `backfill` invocation, managed-chromium,
#     list-only, writing to the legacy mutable `posts` table;
#   * the range is `--days DAYS` ending today (Asia/Shanghai), so the windows
#     overlap on re-run -- that is intentional and free: a fully covered range
#     short-circuits to SUCCESS with 0 pages (`already_covered`);
#   * FAIL-CLOSED: any non-SUCCESS status halts the whole job so an unattended
#     run never grinds through every stock after an access block.
#
# IMPORTANT report-counter caveat: for the simple/legacy backfill path the
# `records_new` / `records_existing` / `records_versioned` fields are hardcoded
# to 0 (see integration.py execute_and_persist_simple_backfill_collection).
# They are NOT a write signal. Rows are written by persist_page, so the honest
# progress signals are `records_in_range`, `pages_scanned` and -- above all --
# the `posts` row delta and the `backfill_coverage` rows.
#
# WHY --start-page 1 (and not the default time-seek):
#   `plan_backfill` sets time_seek_eligible only when neither an explicit start
#   page nor a resume row exists. Time-seek picks its first page from
#   `backfill_page_anchors`, which are stale by however long collection was
#   paused. With a ~2-week-old anchor it predicts a far page, then walks with an
#   exponentially growing step whose direction is "toward lower page numbers",
#   undershoots page 0 and raises `time seek exhausted valid page candidates`
#   (observed 2026-09-16: 601888, status=COLLECTION_FAILED, pages_scanned=0).
#   For a range whose top is "now" the newest page IS page 1, so an explicit
#   --start-page 1 skips the fragile seek AND still sets coverage_eligible=True
#   (start_page == 1), which is what makes a completed run count as proof.
#
# Sources live here (tracked); all outputs stay under the gitignored runtime/:
#   runtime/logs/backfill-<stock>-<yyyymmdd>.json|.err  per-stock run reports

set -u

# Repo root is derived from this script's own location so the driver is not
# pinned to one machine's absolute path. Layout: <repo>/scripts/ops/<this file>.
SCRIPT_DIR=${0:A:h}
REPO=${SCRIPT_DIR:h:h}
PY=${PY:-/opt/homebrew/anaconda3/bin/python}
DAYS=${DAYS:-14}
CHALLENGE_WAIT=${CHALLENGE_WAIT:-180}
LOG_DIR="$REPO/runtime/logs"
mkdir -p "$LOG_DIR"
cd "$REPO" || exit 9

RUN_TAG=$(date -u +%Y%m%d)
# Ordered by expected volume (desc), matching the enrichment driver.
#
# NOTE: this list is hardcoded, and that is a known hazard -- the same class of
# bug that made the enrichment driver look biased toward 601888 (see
# enrich_plan.py). It is deliberately NOT auto-derived yet, because there is no
# single authoritative stock registry in this repo:
#   * backfill_coverage lists exactly these 16, but a newly added stock has no
#     coverage row yet, so deriving from it can never bootstrap a new stock;
#   * config/targets.short-term.json is internally inconsistent (38 codes in
#     `stocks`, 43 in `stock_names`; 002648 / 600312 / 603997 appear only in
#     `stock_names`).
# Picking the wrong source would silently drop a stock from collection, which is
# worse than a literal list that currently matches reality. Until the registry
# question is settled, coverage_drift_check() below fails loudly instead.
stocks=(601012 002463 601888 300666 300054 603039 002648 002028 603179 605020 600312 002891 603806 688676 300487 603997)

# Diagnostic only: report any stock that backfill_coverage knows about but this
# list does not. That divergence means real collection work is being skipped, so
# it must be visible at the top of every run rather than discovered later.
coverage_drift_check() {
  "$PY" - "${stocks[@]}" <<'PY'
import sqlite3, sys
known = set(sys.argv[1:])
con = sqlite3.connect("file:data/collector.db?mode=ro", uri=True)
covered = {c for (c,) in con.execute(
    "SELECT stock_code FROM backfill_coverage WHERE source='eastmoney_guba'")}
missing = sorted(covered - known)
print(f"DRIFT_CHECK driver_stocks={len(known)} coverage_rows={len(covered)}"
      f" uncovered_by_driver={len(covered - known)}")
if missing:
    print(f"WARN_COVERAGE_DRIFT not_in_driver_list={' '.join(missing)}")
    print("WARN_COVERAGE_DRIFT this run will NOT collect them; reconcile stocks[] first")
PY
}

coverage_report() {
  "$PY" - <<'PY'
import sqlite3
con = sqlite3.connect("file:data/collector.db?mode=ro", uri=True)
rows = con.execute(
    "SELECT stock_code, covered_from, covered_to FROM backfill_coverage"
    " WHERE source='eastmoney_guba' ORDER BY stock_code"
).fetchall()
print(f"COVERAGE rows={len(rows)}")
for code, frm, to in rows:
    print(f"COVERAGE {code} {frm} -> {to}")
PY
}

echo "RUN_START $(date -u +%FT%TZ) days=$DAYS tag=$RUN_TAG"
coverage_drift_check
coverage_report

# DRIFT_CHECK_ONLY=1 runs the health checks above and exits without collecting.
# Use it to confirm the driver still agrees with the database, e.g. after a new
# stock is added, without committing to a live run.
if [ "${DRIFT_CHECK_ONLY:-0}" = "1" ]; then
  echo "DRIFT_CHECK_OK $(date -u +%FT%TZ)"
  exit 0
fi
for s in "${stocks[@]}"; do
  out="$LOG_DIR/backfill-$s-$RUN_TAG.json"
  err="$LOG_DIR/backfill-$s-$RUN_TAG.err"
  echo "===== stock=$s start=$(date -u +%FT%TZ) ====="
  # Drop any stale report so the post-run read can only see this invocation.
  rm -f "$out"
  PYTHONPATH=src "$PY" -m myresearcher_collector.cli.main backfill \
    --source eastmoney_guba --stock "$s" --days "$DAYS" --data-dir data \
    --list-only --start-page 1 --acquisition-mode managed-chromium --confirm-live \
    --challenge-wait "$CHALLENGE_WAIT" \
    > "$out" 2> "$err"
  rc=$?

  verdict=$("$PY" - "$s" "$out" <<'PY'
import json, sys
path = sys.argv[2]
try:
    d = json.load(open(path, encoding="utf-8"))
except Exception as exc:  # unreadable report is itself a stop condition
    print(f"STOP unreadable_report:{exc}")
    raise SystemExit(0)
print(
    "STAT"
    f" status={d.get('status')}"
    f" stop_reason={d.get('stop_reason')}"
    f" range_complete={d.get('range_complete')}"
    f" pages={d.get('pages_scanned')}"
    f" received={d.get('records_received')}"
    f" in_range={d.get('records_in_range')}"
    f" failed={d.get('records_failed')}"
    f" from={d.get('effective_from_time')}"
    f" to={d.get('effective_to_time')}"
)
if d.get("status") != "SUCCESS":
    print("STOP collection_failed")
PY
)
  echo "$verdict"

  # NOTE: leading `*` is required -- $verdict is multi-line, so an anchored
  # pattern like `STOP collection_failed*` can never match and the halt would
  # silently fail to fire (the exact bug the enrichment driver hit 2026-09-11).
  case "$verdict" in
    *"STOP unreadable_report"*)
      echo "HALT_ON_UNREADABLE_REPORT stock=$s exit=$rc at=$(date -u +%FT%TZ)"
      echo "RESUME_HINT: re-run this script to continue from stock=$s"
      coverage_report
      echo "ALL_DONE_HALTED $(date -u +%FT%TZ)"
      exit 0
      ;;
    *"STOP collection_failed"*)
      echo "HALT_ON_COLLECTION_FAILED stock=$s exit=$rc at=$(date -u +%FT%TZ)"
      echo "RESUME_HINT: re-run this script to continue from stock=$s"
      coverage_report
      echo "ALL_DONE_HALTED $(date -u +%FT%TZ)"
      exit 0
      ;;
  esac
done

coverage_report
echo "ALL_DONE $(date -u +%FT%TZ)"
