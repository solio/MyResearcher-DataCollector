# Operational scripts (`scripts/ops`)

Documented operational runbooks for Eastmoney Guba detail enrichment. These
scripts drive the CLI, they do not implement source behaviour — all parsing,
schema and persistence contracts live in `src/myresearcher_collector/`.

This subdirectory exists so the drivers are **versioned source artifacts**
(`AGENTS.md`: the Git repository is the authoritative shared project memory)
instead of untracked files in `runtime/`. `scripts/README.md` keeps its
"deterministic utilities" intent for the directory root; `ops/` is the
documented home for network/browser-driving runbooks.

## Source vs. output

| | location | tracked |
|---|---|---|
| scripts (this dir) | `scripts/ops/` | yes |
| per-stock run reports | `runtime/enrich-runs/<stock>.json` / `.err` | no |
| driver wrapper log | `runtime/enrich-runs/driver.log` | no |
| request log (jsonl) | `runtime/logs/eastmoney-detail-enrichment.jsonl` | no |
| backfill run reports | `runtime/logs/backfill-<stock>-<yyyymmdd>.json` / `.err` | no |
| skip ledger | `data/collector.detail_enrichment_skips.db` | no |

Everything these scripts *write* stays under the gitignored `runtime/` and
`data/`. `PY` defaults to `/opt/homebrew/anaconda3/bin/python` (the interpreter
that has Playwright installed) and can be overridden by exporting `PY`.

Repo root is derived from the script's own location, so the scripts are not
pinned to one machine's absolute path.

## `enrich_all_stocks.sh`

Resume-safe, fail-closed driver over the 16 Eastmoney stocks in
`data/collector.db` (legacy `posts` contract).

- Eligibility: trimmed list-title length `>= 40` (see `content_rules.py`).
- Each stock is one `enrich-details` invocation, `--acquisition-mode
  managed-chromium`, `--challenge-wait 180 --challenge-retries 1`.
- A stock with zero pending rows is skipped → re-running is idempotent and
  resumes without redoing finished work.
- Posts already marked missing in the sidecar skip ledger are excluded from the
  pending count, so a known-404 post is never re-requested.
- **Fail-closed:** if a stock's report has `stopped=true` (an access /
  verification block that did not clear), the whole job halts. The CLI exit code
  is `1` whenever *any* candidate failed (e.g. a deleted 404 post), so exit code
  is deliberately not the stop signal — the halt is driven by the JSON report.
- If a report is unreadable/missing, that is also a halt condition.

Terminal markers written to stdout (this is what the wrapper matches on):

| marker | meaning |
|---|---|
| `ALL_DONE <ts>` | every stock with pending work finished, no halt |
| `ALL_DONE_HALTED <ts>` | halted early; re-run to resume |
| `HALT_ON_ACCESS_BLOCK stock=<s>` / `HALT_ON_UNREADABLE_REPORT stock=<s>` | why it halted |

Historical note: the halt `case` patterns must keep the leading `*`
(`*"STOP access_block"*`) because the verdict is multi-line. An anchored pattern
silently failed to fire — fixed 2026-09-11.

## `mop_up.sh`

Bounded "retry until clean" wrapper around `enrich_all_stocks.sh`.

The driver always restarts from the head of its stock list, so a persistently
blocked early stock starves every stock after it. This wrapper re-runs the
driver until a round ends in `ALL_DONE ` (matched on the log's last line). Each
round is still fail-closed; a fresh browser session plus a cooldown gives the
next round a new chance.

- `MAX_ROUNDS` (default `12`), `COOLDOWN_SECONDS` (default `90`) are overridable
  by environment.
- Exit `0` = clean completion (`MOPUP_DONE`), exit `1` = rounds exhausted
  (`MOPUP_MAX_ROUNDS_REACHED`).

## `check_revisit.py`

Read-only guard asserting the regression this work exists to prevent: a post
already marked missing must never be re-requested.

Cross-checks the request jsonl against the skip ledger. Two boundaries keep the
verdict honest:

- `--from-line N` — only inspect requests after the Nth jsonl line, so
  historical rows are not counted as re-visits.
- `--run-start ISO` — ledger entries first seen at/after that instant are
  *discoveries of this run*, not pre-existing marks. Without it the guard
  reports its own discovery event as a violation.

`enrich_all_stocks.sh` snapshots the jsonl line count and the run start time and
invokes the guard on every exit path (clean completion and both halt paths).

| exit code | meaning |
|---|---|
| `0` | `REVISIT_CHECK OK` — no already-marked post was re-requested |
| `2` | `REVISIT_CHECK VIOLATION` — one or more re-requests found |
| `3` | inputs unavailable (jsonl missing) |

Manual use:

```bash
cd <repo>
/opt/homebrew/anaconda3/bin/python scripts/ops/check_revisit.py \
  --from-line 0 --run-start 2026-09-16T03:00:00Z
```

## `backfill_all_stocks.sh`

Resume-safe, fail-closed historical **collection** driver over the same 16
Eastmoney stocks. This is the counterpart to `enrich_all_stocks.sh`: enrich
fills `content` for rows that already exist, backfill acquires the list rows
themselves.

- One `backfill` invocation per stock, `managed-chromium`, `--list-only`, so it
  writes the legacy mutable `posts` table (the same contract
  `backfill_coverage` is keyed to).
- The window is `--days DAYS` (env `DAYS`, default `14`) ending today in
  Asia/Shanghai. Windows deliberately overlap on re-run, and that is free: a
  fully covered range short-circuits to `SUCCESS` with `pages_scanned=0`
  (`already_covered`), so re-running is idempotent and doubles as the resume
  cursor.
- Traversal starts at `--start-page 1` — see the header comment in the script
  for why the default time-seek must be bypassed, and note that it still yields
  `coverage_eligible=True`.
- `coverage_stop` ends the traversal on the first page wholly inside already
  covered territory, so the run collects the gap and stops at the boundary
  instead of rescanning the whole history.
- **Fail-closed:** any `status != SUCCESS` halts the job (`ALL_DONE_HALTED`);
  re-run to resume from that stock.

Terminal markers: `ALL_DONE <ts>` / `ALL_DONE_HALTED <ts>`, plus
`HALT_ON_COLLECTION_FAILED stock=<s>` or `HALT_ON_UNREADABLE_REPORT stock=<s>`.
It also prints `COVERAGE <stock> <from> -> <to>` rows before the run and on
every exit path.

### Report-counter caveat

For this legacy path `records_new`, `records_existing` and `records_versioned`
are **hardcoded to 0** (`integration.py`,
`execute_and_persist_simple_backfill_collection`). They are not a write signal
and must never be quoted as evidence that nothing was written. Rows are written
by `persist_page`; the honest signals are `records_in_range`, `pages_scanned`,
the `posts` row delta, and the `backfill_coverage` rows.

### Known defect this driver works around

`seek_historical_page` fails on stale anchors. `choose_anchor` picks the anchor
nearest `target_to`, which for a range ending "now" is the newest page from the
*previous* run — stale by the whole collection pause. `predict_page` then
projects a plausible-looking far page, and the walk loop steps with an
exponentially growing `step` in the "toward lower page numbers" direction,
undershooting page 0 and raising
`SeekFailure: time seek exhausted valid page candidates`. Because
`execute_and_persist_simple_backfill_collection` catches broad `Exception`, the
run reports only `status=COLLECTION_FAILED, stop_reason=time_seek_failure` with
`pages_scanned=0` — no traceback, no failures list, nothing in the `.err` file.

Observed 2026-09-16 on stock 601888. `--start-page 1` avoids it entirely. The
algorithm itself is still unfixed; a proper fix should clamp the step so it
cannot cross below page 1 rather than raising.

## Concurrency: experiment result (2026-09-16)

**Do not naively run two drivers in parallel.** A bounded experiment was run to
find out what actually happens, and the honest answer is "it worked, but it was
unsafe and the plumbing does not model it".

What was tested: the running driver was on stock 601012 (the largest backlog)
and a second, independent `enrich-details` was launched for stock 300487 —
i.e. head and tail of the pending list — for 29s.

Observed, all on the shared live database:

| window | throughput | per request |
|---|---|---|
| driver alone, before overlap (640s) | 9.1 req/min | 6.6s |
| during overlap, both streams (29s) | 14.5 req/min | 4.1s |
| driver alone, after overlap (41s) | 11.7 req/min | 5.1s |

- 4/4 requests succeeded, `access_block_count=0`, `stopped=False`, no
  `database is locked` anywhere, and the driver was not disturbed.
- Concurrency is real, not an illusion: within one 55s window the request log
  interleaves 601012 and 300487 ids, two of them in the *same second*, and two
  independent browser-profile directories exist side by side.
- Measured gain ≈ 1.6x on a 29s sample. Treat that as an upper bound: the two
  streams each self-throttle 3-10s, so the gain comes from overlapping page-load
  latency and browser startup, not from a free 2x.

Why it is nevertheless unsafe, and why a real parallel design needs an
orchestrator rather than N independent driver processes:

1. **Fail-closed coupling.** The driver halts the *whole job* on any access
   block it observes. Doubling the instantaneous request rate raises the chance
   of a block, and a block seen by the driver stops the run — so a parallel
   experiment can kill the main job. That it survived here was luck, not design.
2. **The revisit guard assumes a single stream.** `check_revisit.py` is invoked
   with `--from-line <baseline> --run-start <run start>`, i.e. it treats the
   whole log window as one run. A parallel stream's lines fall inside that
   window. The verdict stays valid only because parallel candidates apply the
   same ledger exclusion — a parallel run that touched an already-marked id
   would produce a false `REVISIT_CHECK VIOLATION`.
3. **The database has no `busy_timeout` and is not in WAL mode**
   (`journal_mode=delete`, relying on Python's 5s default). Four light writes
   did not exercise this. Two streams committing for an hour on large stocks can
   produce `database is locked`, which surfaces as `failed` posts needing a
   re-run — recoverable, but it looks like source breakage.
4. **Skip-ledger writes are best-effort** and degrade to a no-op on any sqlite
   error. A lock during a 404 mark would *silently drop the mark*, and the next
   run would re-request a deleted post — precisely the regression the guard
   exists to catch.

If parallelism is wanted later, build it as one orchestrator with a bounded
worker pool, a **shared** rate limiter so the combined request rate stays inside
the proven-safe single-stream envelope, a single ledger writer (or WAL +
`busy_timeout`), and a guard that knows how many streams are in flight.

## Running

```bash
cd <repo>
# enrichment: single pass, halts on first access block
zsh scripts/ops/enrich_all_stocks.sh

# enrichment: bounded auto-retry wrapper
zsh scripts/ops/mop_up.sh

# collection: close the gap since the last run (DAYS=14 by default)
zsh scripts/ops/backfill_all_stocks.sh
```

`enrich_all_stocks.sh` / `mop_up.sh` / `check_revisit.py` are enrichment
drivers; `backfill_all_stocks.sh` is the collection driver. Both are thin
wrappers over the CLI — all parsing, schema and persistence contracts live in
`src/myresearcher_collector/`.
