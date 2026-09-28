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
# Pacing shape (2026-09-28). `longtail` replaces the tightly-bounded
# `random.uniform(MIN_DELAY, MAX_DELAY)` loop with an Exponential tail plus a
# scheduled "stop and read" pause every READ_EVERY posts; `uniform` reproduces
# the historical loop exactly and is the A/B control arm.
#
# These MUST be defaulted here, with `:-`, and referenced as plain variables
# below. The first version of this block defaulted them only inside the flag
# array and used bare `$PACE_MODEL` in the RUN_START echo -- under `set -u` that
# aborts the whole script at the echo, before a single request is made. That is
# the failure the operator hit; see the DRY_RUN note lower down for why the
# dry run did not catch it.
# Which browser runtime. `chrome-cdp` attaches to a Chrome the OPERATOR launched
# (normal sandbox, no --no-sandbox, navigator.webdriver=false) instead of letting
# Playwright launch one:
#   nohup "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
#     --user-data-dir="$HOME/.myresearcher-chrome" --no-first-run \
#     --no-default-browser-check --remote-debugging-port=9222 \
#     --remote-allow-origins=* about:blank >/tmp/chrome-cdp.log 2>&1 &
# Endpoint override: MYRESEARCHER_CDP_ENDPOINT (default http://127.0.0.1:9222).
# How long to hold for a human to clear a challenge, and how many windows before
# a captured post is given up on. Deliberately still 180/1 by default: measured
# 2026-09-28, a captured detail page stayed captured for the whole 180s window
# (WAIT_TIMEOUT, no dialog), so a second window mostly buys dead time. What makes
# a challenge cheap is the queue executor tolerating the halt -- see
# enrich_queue.sh -- not a longer wait. Exposed so it can be tuned per run.
CHALLENGE_WAIT=${CHALLENGE_WAIT:-180}
CHALLENGE_RETRIES=${CHALLENGE_RETRIES:-1}
ACQ_MODE=${ACQ_MODE:-managed-chromium}
PACE_MODEL=${PACE_MODEL:-longtail}
READ_EVERY=${READ_EVERY:-20}
READ_MIN=${READ_MIN:-30}
READ_MAX=${READ_MAX:-90}
# BROWSER IDENTITY, NOT PACING (2026-09-28).
#
# The source hands the browser an identity cookie on first contact -- observed in
# the profiles as `nid18` plus `gviem`/`gviem_create_time` on .eastmoney.com,
# alongside `ADVC/ADVS/ASL` and a JSESSIONID. Those are exactly the things a risk
# engine keys on, and **every run used to throw the whole profile away**
# (`_fresh_managed_profile_path()`), so each run arrived as a brand-new visitor
# with a brand-new identity. The operator's own Chrome, on the SAME IP, browses
# guba without a challenge -- which is what makes the browser identity, not the
# IP and not the interval, the thing to fix first.
#
# So the driver now points managed-chromium at one STABLE profile directory that
# is reused across runs, and therefore accumulates cookies/history like a real
# browser does.
#
#   PROFILE_DIR unset  -> <repo>/.runtime/browser-profiles/eastmoney-managed-persistent
#   PROFILE_DIR=""     -> do not pass --profile-dir; back to a fresh profile per
#                         run (the historical behaviour, and the control arm if
#                         this is ever measured properly)
#
# NOTE FOR THE PRUNER: the persistent directory is deliberately a SIBLING of
# `eastmoney-managed`, not a child, because `prune_browser_profiles.sh` deletes
# from `eastmoney-managed` -- keeping the identity we spent requests earning
# inside the set that gets swept would defeat the whole point.
# NOTE ON THE DEFAULTING OPERATOR: this uses `${VAR-default}` (single dash), not
# `${VAR:-default}`. With `:-` an explicitly empty value is treated as unset, so
# `PROFILE_DIR= ...` would silently fall back to the persistent path and the
# "go back to a fresh profile" escape hatch would be a lie -- which is exactly
# what the first version of this block did.
#
# *** ONE PERSISTENT PROFILE == ONE CONCURRENT STREAM *** (regression fixed
# 2026-09-28, hours after I introduced it). Making the profile persistent buys
# identity continuity and costs parallelism: a Chrome user-data-dir is an
# exclusive resource, so two runs pointed at the same directory do not get two
# browsers -- the second launch hands its URL to the first instance and exits,
# leaving both runs navigating the SAME tab. The symptom is silent: a new tab
# appears and sits on about:blank.
#
# So each concurrent stream needs its own profile. Set WORKER_ID per stream:
#   WORKER_ID=1 ...   -> .../eastmoney-managed-persistent-1
#   WORKER_ID=2 ...   -> .../eastmoney-managed-persistent-2
# Unset (the default) keeps the single-stream path unchanged.
WORKER_ID=${WORKER_ID:-}
PROFILE_DIR=${PROFILE_DIR-$REPO/.runtime/browser-profiles/eastmoney-managed-persistent${WORKER_ID:+-$WORKER_ID}}
PROFILE_FLAG=()
if [ -n "$PROFILE_DIR" ]; then
  PROFILE_FLAG=(--profile-dir "$PROFILE_DIR")
fi

# Fail loudly when the profile is already held by a live process. Without this
# the collision above is invisible: the run does not error, it just never
# progresses, and the operator is left staring at about:blank.
# Only when the profile is actually used: `chrome-cdp` ignores --profile-dir
# entirely (it attaches to a browser somebody else launched), so checking there
# would abort a perfectly good run.
# DETECT ONLY -- DO NOT EXIT HERE. The exit has to come after the DRY_RUN block:
# a dry run launches no browser and touches no profile, so gating it on profile
# occupancy made `DRY_RUN=1` fail on a machine where a run happened to be live.
# That is the whole point of a dry run -- it must be runnable at any time. The
# busy state is still computed here so it can be REPORTED by the dry run too.
PROFILE_BUSY_PID=""
if [ "$ACQ_MODE" = "managed-chromium" ] && [ -n "$PROFILE_DIR" ] && [ -L "$PROFILE_DIR/SingletonLock" ]; then
  holder=$(readlink "$PROFILE_DIR/SingletonLock" | sed 's/.*-//')
  if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
    PROFILE_BUSY_PID="$holder"
  fi
fi
# DETAIL_REFERER=1 reaches each detail by first opening that bar's list page and
# then navigating in-page with JS, so the detail request carries a Referer and
# Sec-Fetch-Site: same-origin (a plain `goto(referer=...)` would carry the header
# but no initiator, i.e. Sec-Fetch-Site: none -- measured, see README
# "referer_probe.py"). It DOUBLES the navigations per detail, so it is a switch
# rather than a default, and it is echoed on the RUN_START line so a report can
# always be traced back to the mode that produced it.
DETAIL_REFERER=${DETAIL_REFERER:-0}
# How long to sit on the bar's list page before the JS jump to the detail.
# Splitting the pause this way moves part of the inter-request sleep to where a
# reader would actually pause, and stops the scheme's two hops from being back to
# back. Defaults are ON only when the scheme that opens a list page is on (there
# is no list page otherwise), they are RANDOMISED between min and max because a
# fixed gap is its own signature, and both are echoed on RUN_START so the mode is
# never ambiguous.
DETAIL_DWELL_MIN=${DETAIL_DWELL_MIN:-0.4}
DETAIL_DWELL_MAX=${DETAIL_DWELL_MAX:-1.6}
if [ "$DETAIL_REFERER" != "1" ]; then
  DETAIL_DWELL_MIN=0
  DETAIL_DWELL_MAX=0
fi
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
  # never see a partial driver list and mistake the rest for unclaimed work. It is
  # still written when an ad-hoc queue is requested, so a stale split cannot be
  # mistaken for a live one.
  mv -f "$tmp" "$PLAN_FILE"
  echo "PLAN_OVERRIDE stocks=${STOCKS} (no split published; tail worker will refuse)"
  # ...but the QUEUE is built from the environment, NOT by reading the plan back.
  # N simultaneous ad-hoc instances share one plan path, so a read-back lets
  # whoever wrote last decide what all of them run. Observed 2026-09-27 on a
  # five-instance run: two of the five silently duplicated one queue's work while
  # the stock that instance was supposed to cover was left unclaimed.
  stocks=(${=STOCKS})
else
  if [ "$SPLIT" = "1" ]; then
    plan write-plan "$RUNLOG_DIR" --split || { echo "HALT_ON_PLAN_FAILED"; exit 0; }
    echo "SPLIT_MODE: this process owns only the driver rows; the tail rows need enrich_tail_worker.sh"
  else
    plan write-plan "$RUNLOG_DIR" || { echo "HALT_ON_PLAN_FAILED"; exit 0; }
  fi
  stocks=()
  while IFS=$'\t' read -r role code _pending; do
    [ "$role" = "driver" ] && [ -n "$code" ] && stocks+=("$code")
  done < "$PLAN_FILE"
fi
if [ ${#stocks[@]} -eq 0 ]; then
  echo "NO_BACKLOG $(date -u +%FT%TZ)"
  echo "ALL_DONE $(date -u +%FT%TZ)"
  exit 0
fi
echo "PLAN_DRIVER_QUEUE ${stocks[*]}"

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
echo "RUN_START $RUN_START_ISO jsonl_baseline=$JSONL_BASELINE min_delay=$MIN_DELAY max_delay=$MAX_DELAY detail_referer=$DETAIL_REFERER dwell=${DETAIL_DWELL_MIN}-${DETAIL_DWELL_MAX} pace_model=$PACE_MODEL read_every=$READ_EVERY profile_dir=${PROFILE_DIR:-fresh} worker=${WORKER_ID:-none} acq_mode=$ACQ_MODE profile_busy=${PROFILE_BUSY_PID:-no} challenge_wait=$CHALLENGE_WAIT challenge_retries=$CHALLENGE_RETRIES"
DETAIL_REFERER_FLAG=()
[ "$DETAIL_REFERER" = "1" ] && DETAIL_REFERER_FLAG=(--detail-referer-from-list)
DETAIL_DWELL_FLAG=()
if [ "$DETAIL_DWELL_MAX" != "0" ]; then
  DETAIL_DWELL_FLAG=(--detail-dwell-min "$DETAIL_DWELL_MIN" --detail-dwell-max "$DETAIL_DWELL_MAX")
fi
# Pacing knobs (added 2026-09-28): the per-post delay shape and the scheduled
# "stop and read" pause. Defaults are the collector's own (longtail, every 20
# posts); override to compare or to revert to the historical uniform loop.
PACE_FLAG=(--pace-model "$PACE_MODEL" --read-every "$READ_EVERY" \
           --read-min "$READ_MIN" --read-max "$READ_MAX")

# DRY_RUN=1 resolves the plan, the per-stock pending counts, the RUN_START
# preamble and every flag array, then exits without launching a browser.
#
# IT DELIBERATELY SITS HERE, AFTER THE START-UP PATH, AND NOT NEXT TO THE PLAN
# READING. It used to exit before the RUN_START echo and the flag arrays, which
# made it useless as a smoke test: a bare `$PACE_MODEL` in that echo aborting
# the whole script under `set -u` slipped straight through a green dry run and
# only surfaced as `PACE_MODEL: parameter not set` when the operator ran it for
# real. A dry run has to walk the same shell path as a live run, up to the point
# where it would touch the network -- otherwise "it dry-runs clean" means
# nothing.
if [ "${DRY_RUN:-0}" = "1" ]; then
  for s in "${stocks[@]}"; do
    echo "DRY_RUN stock=$s pending=$(pending_count "$s")"
  done
  echo "DRY_RUN_OK $(date -u +%FT%TZ)"
  exit 0
fi

# A live run must NOT share a persistent profile -- see the note where it is
# detected. This is the refusal; the dry run above has already returned.
if [ -n "$PROFILE_BUSY_PID" ]; then
  echo "PROFILE_BUSY profile_dir=$PROFILE_DIR held_by_pid=$PROFILE_BUSY_PID"
  echo "PROFILE_BUSY hint: a persistent Chrome profile is exclusive. Give this"
  echo "PROFILE_BUSY hint: stream its own profile with WORKER_ID=<n>, or stop"
  echo "PROFILE_BUSY hint: the other run. (DRY_RUN=1 is never blocked by this.)"
  exit 3
fi

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
    --acquisition-mode "$ACQ_MODE" --confirm-live \
    --min-delay "$MIN_DELAY" --max-delay "$MAX_DELAY" \
    --challenge-wait "$CHALLENGE_WAIT" --challenge-retries "$CHALLENGE_RETRIES" \
    "${DETAIL_REFERER_FLAG[@]}" \
    "${DETAIL_DWELL_FLAG[@]}" \
    "${PACE_FLAG[@]}" \
    "${PROFILE_FLAG[@]}" \
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
