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
# WHY --start-page 1 on the DEFAULT (DAYS) path, and not the default time-seek:
#   `plan_backfill` sets time_seek_eligible only when neither an explicit start
#   page nor a resume row exists. Time-seek picks its first page from
#   `backfill_page_anchors`, which are stale by however long collection was
#   paused. Its implementation is a BRACKETED search over the page number
#   (`page_anchor.seek_historical_page`) -- NOT a step walk, which is what this
#   comment described until 2026-10-02 and what made a live failure hard to read.
#   It probes the anchor's page first, relocates it by the count drift, then
#   halves the interval between the known bounds, bounded by `max_probes=20`.
#   It can still fail (`time seek exhausted valid page candidates`), which halts
#   the stock: observed 2026-09-16 on 601888, and again 2026-10-02 on 603039
#   after only 9 of its 20 probes (root cause -- a relocated page was marked
#   visited without being recorded as a bound -- fixed in page_anchor.py that
#   same day, with the reproduction pinned in tests/unit/test_page_anchor.py).
#   For a range whose top is "now" the newest page IS page 1, so an explicit
#   --start-page 1 skips the fragile seek AND still sets coverage_eligible=True
#   (start_page == 1), which is what makes a completed run count as proof.
#
# WHY THE FROM PATH DELIBERATELY LEAVES THE SEEK ON (the opposite choice):
#   For a top of "now" the newest page is page 1, so starting there costs one
#   page. For a backwards extension the analogous "obvious" entry point is not
#   page 1 but the page holding the stock's coverage floor -- and starting from
#   page 1 instead re-walks every already-covered page in between (measured for
#   a 2026-07-09 -> 2026-05-04 extension: 1127 of 2097 pages, 54%). Omitting
#   --start-page is what enables the seek to find that boundary page, and the
#   seek's own numbers say it should be accurate here rather than fragile: the
#   anchors it starts from ARE dated to the boundary region, and
#   `predict_page` corrects the page drift with
#   (current_source_count - anchor.source_count) / anchor.page_size. A SeekProof
#   keeps coverage_eligible true, so the coverage row this run writes is honest.
#   If the seek fails it fails loudly (`time seek exhausted valid page
#   candidates` -> COLLECTION_FAILED, pages_scanned=0), which halts the job after
#   <=20 probes rather than silently re-walking; that stock can then be re-run
#   with START_FORCE_PAGE_1=1 to fall back to the old shape.
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
# FROM=YYYY-MM-DD replaces DAYS with an explicit inclusive window start
# (Asia/Shanghai midnight), for extending coverage BACKWARDS.
#
# Why DAYS cannot express this: DAYS is relative to today, so the same command
# lands on a different date tomorrow, and there is no way to name a fixed
# historical start. `--from` is absolute.
#
# Why a `from` earlier than a stock's covered_from is enough to make the walk go
# past the watermark: coverage_stop_predicate computes
# coverage_boundary(covered, from_time), which returns None as soon as the first
# covered range starts after from_time (backfill.py:161-162) -- i.e. when
# `from_time` sits in a gap. A None boundary disables the early stop entirely
# (backfill.py:183-184), so the traversal runs down to the new window start
# instead of stopping at already-collected territory.
FROM=${FROM:-}
# `--from` and `--to` must be supplied together (backfill.py:69-71 rejects one
# without the other), while the DAYS path implicitly ends at the end of today in
# Asia/Shanghai. So a FROM run needs an explicit --to; default it to today's
# local date, which is the same end the DAYS path would have chosen.
TO=${TO:-$(date +%F)}
CHALLENGE_WAIT=${CHALLENGE_WAIT:-180}
# Turn pages after the first by clicking the page's own pager anchor instead of
# navigating to `f_<n>.html`, so each page request is a same-origin in-page
# navigation carrying a Referer. OFF by default -- it changes the shape of every
# request after the first, so a run must opt in explicitly. See
# scripts/ops/README.md, "--list-click-paging".
#
# The per-stock report always carries `list_click_paging` and the `list_navigation`
# counters, so a run can never be attributed to the wrong scheme: with the flag
# off, `click` must stay 0. That is the negative control for the instrument.
LIST_CLICK_PAGING=${LIST_CLICK_PAGING:-0}
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
all_stocks=(601012 002463 601888 300666 300054 603039 002648 002028 603179 605020 600312 002891 603806 688676 300487 603997)
stocks=("${all_stocks[@]}")
# STOCKS="300487 603806" runs an ad-hoc queue -- a second, disjoint one, or a
# retry after a halt -- in whatever order it is given. The roster above stays
# intact and coverage_drift_check still compares THAT against the database, so a
# subset run cannot look like a stock being silently dropped from collection.
if [ -n "${STOCKS:-}" ]; then
  stocks=(${=STOCKS})
fi
echo "QUEUE ${#stocks} ${stocks[*]}"

# Diagnostic only: report any stock that backfill_coverage knows about but this
# list does not. That divergence means real collection work is being skipped, so
# it must be visible at the top of every run rather than discovered later.
coverage_drift_check() {
  "$PY" - "${all_stocks[@]}" <<'PY'
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

# Per-day row counts, printed BEFORE and AFTER so every run carries its own
# baseline. Without this, reconciling "what did this run actually add?" means
# differencing against numbers someone quoted from an earlier session -- which
# is exactly how a phantom "39 rows changed day" was chased on 2026-09-19 (the
# real answer was a stale baseline: 09-17 was 1198 not 1191, 09-18 was 726 not
# 694). Deltas must come from the database, never from memory.
day_count_report() {
  "$PY" - "$1" <<'PY'
import sqlite3, sys
tag = sys.argv[1]
con = sqlite3.connect("file:data/collector.db?mode=ro", uri=True)
total = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
rows = con.execute(
    "SELECT substr(published_at,1,10) d, COUNT(*) FROM posts"
    " WHERE substr(published_at,1,10) >= date('now','-30 days')"
    " GROUP BY d ORDER BY d"
).fetchall()
print(f"DAYCOUNT {tag} posts_total={total}")
# The per-day block above is a fixed 30-day window, so it is blind to a run that
# extends coverage backwards -- exactly the run this line exists to make
# verifiable. `earliest` is the whole-table MIN(published_at) (Beijing date), so
# "did the earliest post move back to the target date?" is answerable from the
# driver's own output instead of a hand-written query.
earliest = con.execute("SELECT MIN(published_at) FROM posts").fetchone()[0]
print(f"DAYCOUNT {tag} earliest={earliest}")
for d, n in rows:
    print(f"DAYCOUNT {tag} {d} {n}")
PY
}

echo "RUN_START $(date -u +%FT%TZ) days=$DAYS from=${FROM:-none} to=${TO} tag=$RUN_TAG list_click_paging=$LIST_CLICK_PAGING"
LIST_CLICK_PAGING_FLAG=()
[ "$LIST_CLICK_PAGING" = "1" ] && LIST_CLICK_PAGING_FLAG=(--list-click-paging)

# window_to_for <stock> -> "SKIP", "NO_COVERAGE", or the --to timestamp to use.
#
# The --to for a backwards run is that stock's current coverage floor, so the
# window is exactly the gap and add_coverage merges it with the existing range.
# Two cases have to be handled here rather than left to the CLI:
#   * coverage already reaches FROM -> SKIP. Without this the window would be
#     `--from X --to X` (floor == target), which is a zero-width range; skipping
#     is both clearer and what makes the driver safe to re-run after a partial
#     success.
#   * no coverage row -> NO_COVERAGE, i.e. there is no floor to stop at.
window_to_for() {
  "$PY" - "$1" "$FROM" <<'PY'
import sqlite3, sys
from datetime import datetime, timedelta, timezone
stock, from_date = sys.argv[1], sys.argv[2]
start = (datetime.strptime(from_date, "%Y-%m-%d")
         .replace(tzinfo=timezone(timedelta(hours=8)))
         .astimezone(timezone.utc))
con = sqlite3.connect("file:data/collector.db?mode=ro", uri=True)
row = con.execute(
    "SELECT covered_from FROM backfill_coverage"
    " WHERE source='eastmoney_guba' AND stock_code=?", (stock,)
).fetchone()
if not row:
    print("NO_COVERAGE")
else:
    covered_from = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
    print("SKIP" if covered_from <= start else row[0])
PY
}
coverage_drift_check
coverage_report
day_count_report BEFORE

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
  # Window and traversal entry are PER STOCK on a backwards run, because the gap
  # to fill starts at that stock's own coverage floor.
  #
  # EXTENDING BACKWARDS (FROM set): the window top is the existing coverage
  # floor, not today, and --start-page is deliberately NOT forced to 1.
  #   * `--to <covered_from>` makes the requested range exactly the gap, so the
  #     coverage row this run writes merges with the existing one
  #     (add_coverage merges intervals, simple_store.py:190) instead of claiming
  #     time this run never walked.
  #   * omitting --start-page is what enables the time seek: time_seek_eligible
  #     requires no explicit page and no resume row (integration.py:236). The
  #     seek locates the page holding that boundary in <=20 probes and returns a
  #     SeekProof, which keeps coverage_eligible true (integration.py:342) even
  #     though pages 1..start-1 are not rescanned. Forcing --start-page 1 is
  #     exactly what makes every extension re-walk the already-covered prefix,
  #     since a walk must otherwise start at the newest page to be able to claim
  #     the whole range (integration.py:204-209).
  #   * a stock with no coverage row has no floor to stop at, so it keeps the
  #     old shape: window to today, start at page 1.
  if [ -n "$FROM" ] && [ "${START_FORCE_PAGE_1:-0}" != "1" ]; then
    WINDOW_TO=$(window_to_for "$s")
    case "$WINDOW_TO" in
      SKIP)
        echo "stock=$s skip reason=coverage_already_reaches_from"
        continue
        ;;
      NO_COVERAGE)
        RANGE_FLAG=(--from "$FROM" --to "$TO")
        START_FLAG=(--start-page 1)
        ;;
      *)
        RANGE_FLAG=(--from "$FROM" --to "$WINDOW_TO")
        START_FLAG=()
        ;;
    esac
  elif [ -n "$FROM" ]; then
    RANGE_FLAG=(--from "$FROM" --to "$TO")
    START_FLAG=(--start-page 1)
  else
    RANGE_FLAG=(--days "$DAYS")
    START_FLAG=(--start-page 1)
  fi
  echo "===== stock=$s window=${RANGE_FLAG[*]} entry=${START_FLAG[*]:-seek} start=$(date -u +%FT%TZ) ====="
  # Drop any stale report so the post-run read can only see this invocation.
  rm -f "$out"
  PYTHONPATH=src "$PY" -m myresearcher_collector.cli.main backfill \
    --source eastmoney_guba --stock "$s" --data-dir data \
    --list-only --acquisition-mode managed-chromium --confirm-live \
    --challenge-wait "$CHALLENGE_WAIT" \
    "${RANGE_FLAG[@]}" \
    "${START_FLAG[@]}" \
    "${LIST_CLICK_PAGING_FLAG[@]}" \
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
nav = d.get("list_navigation") or {}
print(
    "STAT"
    f" status={d.get('status')}"
    f" stop_reason={d.get('stop_reason')}"
    f" range_complete={d.get('range_complete')}"
    # Which navigation each list page used. Reported on every run, flagged or
    # not: with `list_click_paging=false` these read `click=0`, which is the
    # negative control proving the counter can tell the two schemes apart.
    f" click_paging={d.get('list_click_paging')}"
    f" nav_click={nav.get('click')}"
    f" nav_goto={nav.get('goto')}"
    f" nav_no_anchor={nav.get('click_no_anchor')}"
    f" nav_not_taken={nav.get('click_not_taken')}"
    f" pages={d.get('pages_scanned')}"
    f" received={d.get('records_received')}"
    f" in_range={d.get('records_in_range')}"
    # Post-type rows fetched but routed to `out_of_scope` (news/转发), i.e. never
    # persisted. Reported so `received - in_range` is explainable without code
    # archaeology; see D-012. Present only in reports written after 2026-09-19,
    # so it renders as None on older files.
    f" out_of_scope={d.get('records_out_of_scope')}"
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
      day_count_report AFTER
      echo "ALL_DONE_HALTED $(date -u +%FT%TZ)"
      exit 0
      ;;
    *"STOP collection_failed"*)
      echo "HALT_ON_COLLECTION_FAILED stock=$s exit=$rc at=$(date -u +%FT%TZ)"
      echo "RESUME_HINT: re-run this script to continue from stock=$s"
      coverage_report
      day_count_report AFTER
      echo "ALL_DONE_HALTED $(date -u +%FT%TZ)"
      exit 0
      ;;
  esac
done

coverage_report
day_count_report AFTER
echo "ALL_DONE $(date -u +%FT%TZ)"
