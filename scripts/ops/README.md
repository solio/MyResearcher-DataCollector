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
| published work split | `runtime/enrich-runs/plan.txt` | no |
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

## `enrich_plan.py`

The single source of truth for *what work exists* and *which stream owns it*.
All backlog accounting lives here so the eligibility predicate cannot drift
between the driver, the tail worker and any ad-hoc query.

| command | output |
|---|---|
| `list` | `<stock>\t<pending>` for every stock with pending work, pending desc |
| `pending <stock>` | the pending count as a bare integer (`0` if none) |
| `write-plan <outdir> [--split]` | writes `<outdir>/plan.txt` atomically, prints `PLAN_MODE` + `PLAN_DRIVER`/`PLAN_TAIL` rows |
| `pick <driver\|tail> [plan]` | `<stock> <pending>` — smallest unclaimed backlog on that side |

Eligibility = `source='eastmoney_guba'` **and** trimmed list-title length `>= 40`
**and** `content IS NULL`, minus ids already in the sidecar skip ledger. The
database is opened `mode=ro`; the ledger is attached read-only and a broken or
missing ledger degrades to "no exclusions" instead of failing the plan.

### The work list is computed, not hardcoded

Until 2026-09-16 `enrich_all_stocks.sh` carried a hardcoded 16-code array
labelled *"Ordered by original pending count (desc)"*, frozen on 2026-09-10.
That produced a false finding: the driver always started on 601888, which looked
like the work was concentrated there, when 601012 actually held the largest
backlog (251 vs 159) and simply sat later in a stale queue. The list is now
derived from the database on every run. **Do not reintroduce a literal list in
the drivers** — a stock can go from 251 pending to 0 in one run, and a frozen
queue silently misrepresents that.

### Single-stream (default) vs two-stream split (`plan.txt`)

`SPLIT` is **unset by default**, and the default plan assigns **every** stock to
`driver`:

```
driver	002648	86
driver	600312	50
driver	300666	42
driver	002028	4
```

A single-stream run therefore covers the whole backlog, `ALL_DONE` means the
backlog is genuinely exhausted, and `enrich_tail_worker.sh` finds an empty queue
and exits `TAIL_DONE` at once.

`SPLIT=1` opts into the two-way division (LPT — largest backlog first, onto the
currently lighter stream, so the sides balance by **pending rows**, not by stock
count; one stock can hold half the backlog: 86 of 182 here):

```
driver	002648	86
driver	002028	4
tail	600312	50
tail	300666	42
```

**`SPLIT=1` is only correct if `enrich_tail_worker.sh` is actually started.** In
that mode `ALL_DONE` means "done with *my* half", not "done" — the driver prints
`SPLIT_MODE:` first to make that explicit. This is why the split is opt-in: the
driver cannot know whether a worker will ever appear, and a default split would
turn `ALL_DONE` into a silent under-run.

`plan.txt` is **one** file holding both sides, replaced by write-then-rename.
`mop_up.sh` re-runs the driver in a loop and each round republishes the plan, so a
reader that saw a fresh tail list beside a stale driver list could pick an
overlapping stock. A single atomic replace cannot be torn.

To run everything on one stream regardless, set `STOCKS="..."`. That publishes a
driver-only plan and makes `enrich_tail_worker.sh` refuse to start
(`TAIL_NO_PLAN`), so an ad-hoc hand-run can never collide with a background
worker.

## `enrich_all_stocks.sh`

Resume-safe, fail-closed driver over whatever stock has pending detail work in
`data/collector.db` (legacy `posts` contract). By default it owns **the whole**
backlog; with `SPLIT=1` it owns only the `driver` side of `plan.txt` and
`enrich_tail_worker.sh` owns the rest.

- The work list is computed at run start — see `enrich_plan.py` above.
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
- `DRY_RUN=1` publishes the plan, resolves each stock's pending count and exits
  before launching a browser (`DRY_RUN_OK <ts>`). Use it to see exactly what a
  run would do, and to confirm the two queues are disjoint, before committing.

Terminal markers written to stdout (this is what the wrapper matches on):

| marker | meaning |
|---|---|
| `ALL_DONE <ts>` | every stock with pending work finished, no halt |
| `ALL_DONE_HALTED <ts>` | halted early; re-run to resume |
| `NO_BACKLOG <ts>` + `ALL_DONE <ts>` | nothing had pending work; a free no-op |
| `PLAN_DRIVER_QUEUE <codes>` | the resolved work list for this run |
| `DRY_RUN_OK <ts>` | `DRY_RUN=1` finished; nothing was fetched |
| `HALT_ON_PLAN_FAILED` | `enrich_plan.py write-plan` failed; nothing was run |
| `HALT_ON_ACCESS_BLOCK stock=<s>` / `HALT_ON_UNREADABLE_REPORT stock=<s>` | why it halted |

Historical note: the halt `case` patterns must keep the leading `*`
(`*"STOP access_block"*`) because the verdict is multi-line. An anchored pattern
silently failed to fire — fixed 2026-09-11.

## `mop_up.sh`

Bounded "retry until clean" wrapper around `enrich_all_stocks.sh`.

The driver halts the whole job on the first access block, so a persistently
blocked stock ends each round early. Because the work list is recomputed from the
database every round, a retry resumes on whatever is *currently* the largest
backlog rather than on a frozen head, but the starvation window still exists
within a round. This wrapper re-runs the driver until a round ends in `ALL_DONE `
(matched on the log's last line). Each round is still fail-closed; a fresh
browser session plus a cooldown gives the next round a new chance.

| marker | meaning |
|---|---|
| `MOPUP_DONE round=<n>` | clean completion (`ALL_DONE`), exit 0 |
| `MOPUP_HALTED round=<n> yield=<n> barren_streak=<n>` | round halted; how much it achieved |
| `MOPUP_BARREN rounds=<n> barren_streak=<n>` | yield collapsed → source-side cap, exit 1 |
| `MOPUP_MAX_ROUNDS_REACHED rounds=<n>` | round budget exhausted, exit 1 |

`MAX_ROUNDS` (12), `COOLDOWN_SECONDS` (90), `MIN_ROUND_YIELD` (1) and
`MAX_BARREN_ROUNDS` (2) are overridable by environment.

### Barren rounds: the guard the wrapper used to lack (2026-09-18)

The wrapper's original premise — "a fresh browser session plus a cooldown gives
the next round a new chance" — is **true for a transient block and false for a
source-side rate cap**, because a fresh session does not change our IP or reset a
server-side counter. `MOPUP_MAX_ROUNDS_REACHED` also looks identical whether the
12 rounds yielded 70 rows each or 1 row each, so the wrapper could not tell that
it had stopped making progress.

It now measures each round's yield (`sum of success=` across the STAT lines that
round appended) and aborts after `MAX_BARREN_ROUNDS` consecutive rounds at or
below `MIN_ROUND_YIELD`. Replayed against the real 2026-09-18 log it stops at
**round 3** instead of grinding to round 12 — saving ~29 minutes and 36 pointless
requests. `ALL_DONE` is checked *before* the yield logic, so a legitimately tiny
final round is never misread as barren.

If `MOPUP_BARREN` fires, the answer is a real cooldown (hours) or much slower
pacing (`MIN_DELAY=30 MAX_DELAY=60 zsh mop_up.sh`) — **not** another 12 rounds.
See "Sustained rate limiting" below.

Each round republishes `plan.txt`. A running `enrich_tail_worker.sh` follows the
new tail side on its next pick; the sides are always disjoint, so a republish can
never make them overlap.

## Sustained rate limiting: what it looks like (2026-09-18)

Worth recognising, because it is indistinguishable from "unlucky transient
blocks" until you measure the request log.

Trigger: a 14-day backfill collected 4963 records in 5 minutes (62 page loads),
then detail enrichment started immediately on the largest backlog (105 rows).

The signature, from `runtime/logs/eastmoney-detail-enrichment.jsonl`:

| round | requests | successes | outcome |
|---|---|---|---|
| 1 (03:55:54) | `success` ×70, `access_block` ×3, `fetch_failure` ×1 | **70** | halted after ~11 min |
| 2–12 | `success` ×1, `access_block` ×2, `fetch_failure` ×1 each | **1 each** | halted every ~3.2 min |

1. **The block responses come back in 0.2–0.3s.** That is an immediate refusal,
   not an anti-bot interstitial being served. The only successful request in each
   round takes 1.4–1.8s (a real page load), then the *next* request is refused at
   once.
2. **A fresh browser session still gets exactly one request through.** So the cap
   tracks IP/fingerprint, not session or cookie state — which is precisely why
   the cooldown-and-retry loop could not recover.
3. **Yield collapses by a factor of ~70** (70 rows in round 1, then 1 per round).
4. 25 `access_block` + 12 `fetch_failure` events; `REVISIT_CHECK OK` throughout
   (the ledger exclusion held), so this is a *capacity* problem, not a
   correctness one.

Net: 81 rows in 64 minutes ≈ 47s/row, against a healthy ~6.5s/row — **7× slower**
than the single-stream baseline measured on 2026-09-16. Rounds 2–12 alone
produced 11 rows in 53 minutes.

What actually helps: **stop**, wait, then resume. `--challenge-wait 180` and a
fresh profile cannot talk the source out of a server-side cap. If it recurs
immediately on resume, drop the rate hard (`MIN_DELAY=30 MAX_DELAY=60`) and
expect the backlog to clear over days rather than hours.

What makes it worse: more rounds. Twelve sessions hitting a refusal in one hour
is more likely to extend the block than to find a gap in it.

### Resolution (same day, 05:48Z): the cap was not a quota

Retried 58 minutes later and the remaining 192 rows went through in one clean
round (`MOPUP_DONE round=1`, 35m19s, 190 success + 1 self-recovered
`access_block`). So the earlier cut-off was **not** a hard daily quota.

**But do not read this as "the slowdown fixed it".** Two variables changed at
once and the experiment does not separate them:

| | failed run (03:55Z) | successful retry (05:48Z) |
|---|---|---|
| rate | 6.4 req/min (3–10s) | 5.39 rows/min (8–15s) |
| minutes since the backfill burst | ~0 | ~58 |

The rate only fell 16%, while the gap before it went from 0 to 58 minutes — so
the likelier dominant factor is **the burst context**, not the pacing. The
trigger was most plausibly the combination of 62 backfill page loads in 5
minutes immediately followed by an enrichment run; the source appears to cap on
a *recent burst*, not on a sustained rate.

Practical consequence, and the reason this is written down: **do not start detail
enrichment immediately after a backfill.** Leave a gap. Crossing the old
~70-request cut-off point at 5.4 rows/min produced just 1 self-recovered block,
so neither a 16% slowdown nor request 70 is the operative threshold here.

To actually isolate the cause would take a controlled re-run (same gap, default
rate), which was not worth spending against a source that had just throttled us.

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

### The stock list is hardcoded, and that is a known hazard

`backfill_all_stocks.sh` carries a literal 16-code array — the same class of
defect that made the enrichment driver look biased toward 601888 (see
`enrich_plan.py`). It is **deliberately not auto-derived yet**, because this repo
has no single authoritative stock registry:

- `backfill_coverage` lists exactly those 16, but a newly added stock has no
  coverage row, so deriving from it can never bootstrap a new stock;
- `config/targets.short-term.json` is internally inconsistent — 38 codes in
  `stocks`, 43 in `stock_names`, and 002648 / 600312 / 603997 appear only in
  `stock_names`.

Picking the wrong source would silently drop a stock from collection, which is
worse than a literal list that currently matches reality. Until that question is
settled the driver **fails loudly instead**:

```
DRIFT_CHECK driver_stocks=16 coverage_rows=16 uncovered_by_driver=0
WARN_COVERAGE_DRIFT not_in_driver_list=300487      # only when they diverge
```

`DRIFT_CHECK_ONLY=1` runs the checks and exits without collecting
(`DRIFT_CHECK_OK <ts>`). Use it to confirm the driver still agrees with the
database — e.g. after a new stock is added — without committing to a live run.

**Open question:** which artifact is the stock registry? Once that is decided,
both this driver and `enrich_plan.py` should read it instead of inferring.

### Report-counter caveat

For this legacy path `records_new`, `records_existing` and `records_versioned`
are **hardcoded to 0** (`integration.py`,
`execute_and_persist_simple_backfill_collection`). They are not a write signal
and must never be quoted as evidence that nothing was written. Rows are written
by `persist_page`; the honest signals are `records_in_range`, `pages_scanned`,
the `posts` row delta, and the `backfill_coverage` rows.

Every run now also prints its own baseline, so the row delta never has to be
differenced against a number quoted from an earlier session:

```
DAYCOUNT BEFORE posts_total=85936
DAYCOUNT BEFORE 2026-09-18 1321
DAYCOUNT BEFORE 2026-09-19 109
…
DAYCOUNT AFTER  posts_total=86640
```

`AFTER` is emitted on the normal path and on both `ALL_DONE_HALTED` paths, so a
halted run still records what it managed to write. The window is the last 30
days; `posts_total` is unfiltered.

**Why it exists.** On 2026-09-19 the per-day counts were reported as
`09-17: 1191 → 1198`, `09-18: 694 → 1321`, which reconciles to +743 rows while
the `posts` table had only grown by 704 — a 39-row gap that looked like rows
silently changing their `published_at` day. It was not. The baselines were
stale: reconstructing the true post-2026-09-18 state from `created_at` gives
`09-17 = 1198` and `09-18 = 726`, whereupon `726 + 595 = 1321` and
`1198 + 0 = 1198` both close exactly. The 39 rows never existed.
`published_at` was separately proven stable against the frozen
`source_item_observations` snapshot (8251/8251 matched within 60s, 0 drifted).

**Rule: take deltas from the database, never from memory or from a chat summary.**
`created_at` is set on insert and never rewritten, so it is also the way to
reconstruct what any past run left behind:

```sql
-- state of each day as of the end of the 2026-09-18 run
SELECT substr(published_at,1,10) d, COUNT(*) FROM posts
WHERE created_at <= '2026-09-18T03:54' GROUP BY d ORDER BY d;
-- inserts made by a specific run
SELECT substr(published_at,1,10) d, COUNT(*) FROM posts
WHERE created_at BETWEEN '2026-09-18T03:48' AND '2026-09-18T03:54' GROUP BY d;
```

`updated_at` is the other half of the ledger: rows touched in the run window but
with `created_at` before it were **counter refreshes** (read/reply/like), not new
rows, and do not move `COUNT(*)`.

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

The two-stream split above (`enrich_plan.py` + `plan.txt`) is a deliberately
smaller step in that direction: it removes the *selection* hazard (two streams
picking the same stock) without pretending to solve the four hazards listed here.
It does not add a shared rate limiter, a single ledger writer, or a
stream-aware revisit guard — those remain open.

## `enrich_tail_worker.sh`

A bounded rolling worker that uses the concurrency finding above *without* the
unsafe parts. It runs alongside `enrich_all_stocks.sh`.

- It does **not** choose its own stocks. It reads the `tail` rows of the plan the
  driver published (`runtime/enrich-runs/plan.txt`) and works only those. The two
  sides are disjoint by construction, so the streams cannot collide.
- Within its own list it rolls **smallest backlog first**, so it clears quick wins
  while the driver grinds the big ones, then moves to the next by itself.
  Pending counts are re-read from the database at pick time, so a stock cleared by
  an earlier round is skipped rather than re-requested.
- Every candidate query applies the same skip-ledger exclusion as the driver, so
  the driver's end-of-run `check_revisit.py` verdict stays valid even though this
  stream's requests land inside the driver's log window.
- It refuses to start without a plan: `TAIL_NO_PLAN`, exit 0. This is also what
  stops it from running against an ad-hoc `STOCKS=...` driver invocation.
- It exits by itself: `TAIL_DONE` once its own queue has no pending rows left,
  `TAIL_HALTED` on an access block or an unreadable report (fail-closed, same as
  the driver). It does **not** stop just because the driver printed `ALL_DONE` —
  the driver finishing says nothing about this worker's disjoint queue, so that
  case only emits a `TAIL_NOTE`.
- Its own pacing defaults to `MIN_DELAY=7.0` / `MAX_DELAY=10.0` — deliberately
  the **slow end** of the allowed 3..10s envelope, so the *combined* request
  rate stays near the single-stream rate that is known to work instead of
  doubling it. There is also a `COOLDOWN_SECONDS` (default 20) quiet gap between
  stocks. Lower `MIN_DELAY` to buy speed at higher block risk.
- `PLAN_FILE` defaults to `runtime/enrich-runs/plan.txt` and `DRIVER_LOG` to
  `runtime/enrich-runs/driver.log`; the latter is now used only for the
  `driver_holds=` audit field, not for correctness.

### Incident: why this worker was rewritten (2026-09-16)

The first version had the worker walk the **same** work list from the tail while
excluding only the stock the driver was *currently* holding, read out of the
driver's log. That is not a safe exclusion, and it failed in production:

- 05:25:44Z the worker read `driver_holds=002463` (correct — the driver was
  mid-002463) and picked 002028, the last stock with a backlog on the tail side.
- 05:29:19Z the driver finished 002463 and started 002028 itself.
- Both streams then ran `enrich-details` for 002028 concurrently, in two separate
  browser profiles (`…/20260916-052544-661732` and `…/20260916-052919-566141`).

Measured cost: in the ≥05:20Z window there were 169 request-log events across 5
run ids, of which **8 source_item_ids were requested twice** — once by each of
the two 002028 run ids (`3cb61f48…` and `de83b05a…`). No corruption (the writes
are identical-value upserts) and the revisit guard stayed clean, because a
*successful* fetch is not recorded in the skip ledger. It was pure waste, plus a
user-visible surprise: two processes apparently working the same stock.

The unreserved gap was **the stock the driver was about to take next**. The
driver's head→tail walk and the worker's tail→head walk necessarily converge, and
"exclude what the driver holds" leaves the convergence point unowned. No amount
of tightening that check fixes it: on a shared queue the meeting point is always
unreserved. Hence the split — the queues are made disjoint up front instead of
being arbitrated at runtime.

Note this still runs two processes against one SQLite file with no WAL and no
`busy_timeout`. Keep an eye on the `.err` files for `database is locked`.

## Running

```bash
cd <repo>
# enrichment: single pass, halts on first access block
#   (publishes runtime/enrich-runs/plan.txt first)
zsh scripts/ops/enrich_all_stocks.sh

# enrichment: bounded auto-retry wrapper (single stream, covers the whole backlog)
zsh scripts/ops/mop_up.sh

# enrichment: two streams. SPLIT=1 on the driver, then the worker. BOTH are
# required — the driver alone leaves the tail half untouched.
SPLIT=1 zsh scripts/ops/mop_up.sh          # terminal 1
zsh scripts/ops/enrich_tail_worker.sh      # terminal 2, AFTER the driver

# enrichment: one stock by hand. Publishes a driver-only plan, so a background
# tail worker will refuse to start rather than race you.
STOCKS="002648" zsh scripts/ops/enrich_all_stocks.sh

# enrichment: what would the driver do? (publishes plan.txt, fetches nothing)
DRY_RUN=1 zsh scripts/ops/enrich_all_stocks.sh

# what is left, without running anything
/opt/homebrew/anaconda3/bin/python scripts/ops/enrich_plan.py list

# collection: close the gap since the last run (DAYS=14 by default)
zsh scripts/ops/backfill_all_stocks.sh
```

`enrich_all_stocks.sh` / `mop_up.sh` / `enrich_tail_worker.sh` /
`check_revisit.py` / `enrich_plan.py` are enrichment tools;
`backfill_all_stocks.sh` is the collection driver. All are thin wrappers over the
CLI — parsing, schema and persistence contracts live in
`src/myresearcher_collector/`.
