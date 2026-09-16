#!/bin/zsh
# Rolling "tail worker": keeps pulling the smallest-backlog stock while the
# main fail-closed driver works from the head of the list.
#
# Rationale and the risks it does NOT remove are documented in
# scripts/ops/README.md ("Concurrency: experiment result"). Summary:
#   * the main driver walks its list head -> tail and halts the whole job on any
#     access block it sees;
#   * this worker walks tail -> head, so the two meet in the middle;
#   * it never touches the stock the driver is currently holding, and every
#     candidate query excludes ids already in the skip ledger, so the driver's
#     end-of-run revisit guard stays valid;
#   * it exits as soon as the only remaining backlog belongs to the driver.
#
# Because its own pacing is deliberately the slow end of the allowed 3..10s
# envelope, the COMBINED request rate stays near the single-stream envelope that
# is known to work, instead of doubling it. Override MIN_DELAY/MAX_DELAY to
# trade safety for speed.
#
# Terminal markers: TAIL_DONE, TAIL_HALTED, TAIL_DRIVER_DONE.

set -u

SCRIPT_DIR=${0:A:h}
REPO=${SCRIPT_DIR:h:h}
PY=${PY:-/opt/homebrew/anaconda3/bin/python}
DRIVER_LOG=${DRIVER_LOG:-"$REPO/runtime/enrich-runs/driver.log"}
MIN_DELAY=${MIN_DELAY:-7.0}
MAX_DELAY=${MAX_DELAY:-10.0}
CHALLENGE_WAIT=${CHALLENGE_WAIT:-180}
COOLDOWN_SECONDS=${COOLDOWN_SECONDS:-20}
RUNLOG_DIR="$REPO/runtime/enrich-runs"
mkdir -p "$RUNLOG_DIR"
cd "$REPO" || exit 9

# Pick the smallest non-zero backlog, excluding the stock the driver holds.
# Prints "<stock> <pending>" or nothing when there is no work left for us.
pick_next() {
  "$PY" - "$1" <<'PY'
import os, sqlite3, sys
exclude_stock = sys.argv[1] if len(sys.argv) > 1 else ""
con = sqlite3.connect("file:data/collector.db?mode=ro", uri=True)
LEDGER = "data/collector.detail_enrichment_skips.db"
ex = ""
if os.path.exists(LEDGER):
    try:
        con.execute(f"ATTACH DATABASE 'file:{LEDGER}?mode=ro' AS skips")
        present = con.execute(
            "SELECT COUNT(*) FROM skips.sqlite_master"
            " WHERE type='table' AND name='detail_enrichment_skips'"
        ).fetchone()[0]
        if present:
            ex = (" AND p.source_item_id NOT IN ("
                  "SELECT source_item_id FROM skips.detail_enrichment_skips"
                  " WHERE source='eastmoney_guba')")
    except sqlite3.Error:
        ex = ""
L = "LENGTH(TRIM(COALESCE(p.title,'')))"
stocks = ("601888 601012 002463 002028 002648 300054 605020 688676 "
          "300666 600312 603039 603179 002891 603997 603806 300487").split()
work = []
for s in stocks:
    if s == exclude_stock:
        continue
    n = con.execute(
        f"SELECT COUNT(*) FROM posts p WHERE p.stock_code=? AND {L}>=40"
        f" AND p.content IS NULL{ex}",
        (s,),
    ).fetchone()[0]
    if n:
        work.append((n, s))
if work:
    work.sort()
    print(f"{work[0][1]} {work[0][0]}")
PY
}

# The stock the driver is currently holding, from its own log.
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

echo "TAIL_WORKER_START $(date -u +%FT%TZ) driver_log=$DRIVER_LOG min_delay=$MIN_DELAY max_delay=$MAX_DELAY"
for round in $(seq 1 64); do
  if driver_finished; then
    echo "TAIL_DRIVER_DONE round=$round at=$(date -u +%FT%TZ)"
    exit 0
  fi

  held=$(driver_current_stock)
  pick=$(pick_next "$held")
  if [ -z "$pick" ]; then
    echo "TAIL_DONE round=$round held=$held at=$(date -u +%FT%TZ) reason=no_unheld_backlog"
    exit 0
  fi

  stock=${pick%% *}
  pend=${pick##* }
  echo "TAIL_PICK round=$round stock=$stock pending=$pend driver_holds=$held at=$(date -u +%FT%TZ)"

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
