#!/bin/zsh
# Resume-safe, fail-closed detail-enrichment driver for the 16 Eastmoney Guba
# stocks in data/collector.db (legacy `posts` contract).
#
# See scripts/ops/README.md for the full operating contract. Summary:
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

# Ordered by original pending count (desc).
stocks=(601888 601012 002463 002028 002648 300054 605020 688676 300666 600312 603039 603179 002891 603997 603806 300487)

pending_count() {
  "$PY" - "$1" <<'PY'
import os, sqlite3, sys
con = sqlite3.connect("file:data/collector.db?mode=ro", uri=True)
LEDGER = "data/collector.detail_enrichment_skips.db"
# Exclude posts already recorded as missing in the sidecar skip ledger, so a
# stock whose only remaining candidates are known-404 stops being retried.
exclude = ""
if os.path.exists(LEDGER):
    try:
        con.execute(f"ATTACH DATABASE 'file:{LEDGER}?mode=ro' AS skips")
        present = con.execute(
            "SELECT COUNT(*) FROM skips.sqlite_master"
            " WHERE type='table' AND name='detail_enrichment_skips'"
        ).fetchone()[0]
        if present:
            exclude = (
                " AND p.source_item_id NOT IN ("
                "SELECT source_item_id FROM skips.detail_enrichment_skips"
                " WHERE source='eastmoney_guba')"
            )
    except sqlite3.Error:
        exclude = ""
L = "LENGTH(TRIM(COALESCE(p.title,'')))"
row = con.execute(
    f"SELECT COUNT(*) FROM posts p WHERE p.stock_code=? AND {L}>=40"
    f" AND p.content IS NULL{exclude}",
    (sys.argv[1],),
).fetchone()
print(row[0])
PY
}

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
echo "RUN_START $RUN_START_ISO jsonl_baseline=$JSONL_BASELINE"
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
    --challenge-wait 180 --challenge-retries 1 \
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
