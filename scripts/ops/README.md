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
by `persist_page`; the honest signals are `records_in_range`,
`records_out_of_scope` (added 2026-09-19 — see the `post_type` section below),
`pages_scanned`, the `posts` row delta, and the `backfill_coverage` rows.

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

### Only `post_type == 0` is collected — and the drop used to be invisible

The parser accepts a list row **only if `post_type == 0`**. `_parse_item` ends with

```python
if post_type != 0:
    return row          # -> page.out_of_scope_rows, never persisted
```

so 资讯 (news) posts (`post_type` 1) and 转发/长文 items (`post_type` 20) are
**fetched, counted as received, and then dropped**. They are not errors: no
`records_failed`, no schema mismatch, nothing in the `.err`. The frozen
`source_item_observations` snapshot is 8251/8251 `post_type = 0`, so this has
always been the contract — it was just never *reported*, because
`RuntimeCounters.records_out_of_scope` existed but was omitted from the report
dict. `received - in_range` therefore looked like an unexplained hole.

Measured on 601012, 2026-09-19: the raw `article_list` payload for page 1 of
`list,601012,f.html` splits **80 rows = 66 × `post_type` 0, 2 × 1, 12 × 20**, and
the report said `received=320, in_range=303` across 4 pages. `320 − 303 = 17` was
exactly the out-of-scope count, and all 17 are `post_type` 1/20. Every
"missing" post on that page — the sticky news item, `隆基绿能资讯`'s 融资净买入
bulletin, `光伏头条`'s 周事迹, and the `首个钙钛矿…` reposts — is in that set.

The counter is now surfaced in both places, so the arithmetic closes from the
artifacts alone:

* the run report JSON carries `records_out_of_scope`;
* the driver's `STAT` line prints `out_of_scope=` (rendering `None` on reports
  written before 2026-09-19, so old logs still parse).

The exact identity, with each term now pinned to a line of code:

```
received       = len(page.rows) + len(page.out_of_scope_rows)     # collector.py:918
out_of_scope   = len(page.out_of_scope_rows)                      # collector.py:919
in_range       = rows with from_time <= published_at <= to_time
                 AND source_item_id not already seen this run      # collector.py:933-938
=> received = in_range + out_of_scope + out_of_window + in_run_duplicate
```

**A non-zero residual is NORMAL — but only for `backfill_range_complete` stocks.**
The stop condition for that reason is `max(page_times) < from_time`
(`collector.py:984`), i.e. the walk deliberately reads **one whole page that lies
entirely before the window start**; those 80-odd rows are counted in `received`
and never persisted. A stock that stops on `existing_coverage_reached` stops at
the watermark, which is inside the window, so its residual is 0.

Measured 2026-09-23, and this is the proof: exactly the two
`backfill_range_complete` stocks had residuals, and re-fetching their three pages
closes every counter to the row.

| stock | 3 pages | `type0` | `非0` | `type0` before `from_time` | dup ids | `type0` in window | report `in_range` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 300487 | 240 | 169 | 71 | **94** | 0 | **75** | 75 ✅ |
| 603997 | 240 | 214 | 26 | **81** | 0 | **133** | 133 ✅ |

`240 = 75 + 71 + 94` and `240 = 133 + 26 + 81`. `非0` equals the reported
`out_of_scope` in both, and there were **zero duplicate ids** — so the residual is
wholly "out of window", not pagination overlap. The other 14 stocks had residual 0.

Baseline run that first made this visible (2026-09-19 15:51Z, all 16 stocks
stopped on `existing_coverage_reached`, hence 16/16 zero residual):

| stock | received | in_range | out_of_scope | failed |
| --- | --- | --- | --- | --- |
| 002028 | 160 | 149 | 11 | 0 |
| 002463 | 82 | 51 | 31 | 0 |
| 300487 | 80 | 58 | 22 | 0 |
| 605020 | 80 | 59 | 21 | 0 |
| 002891 / 300666 / 601012 / 688676 | 80 / 81 / 80 / 80 | 63 / 64 / 66 / 66 | 17 / 17 / 14 / 14 | 0 |
| 300054 / 603179 / 600312 / 603997 / 002648 | 80 each | 66 / 69 / 70 / 71 / 71 | 14 / 11 / 10 / 9 / 9 | 0 |
| 601888 / 603806 / 603039 | 80 each | 72 / 74 / 75 | 8 / 6 / 5 | 0 |
| all 16 | — | — | — | **0 mismatches** |

`out_of_scope` runs from **5 to 31 rows per stock per page** (6%–38%), which is the
same order as `in_range` — worth knowing before treating "received" as "collected".

**What a catch-up run looks like** (first run after a gap — 2026-09-19 15:51Z →
2026-09-23 00:54Z, ~3.4 days). Use this as the shape to expect after any pause:

| | |
| --- | --- |
| wall time | **5m51s** (vs 41s when only page 1 is needed) |
| `pages` per stock | 2–9, driven by how much of the gap that stock filled |
| `stop_reason` | 14 × `existing_coverage_reached`, 2 × `backfill_range_complete` |
| `posts_total` | 85937 → 88592 (**+2655**) |
| new rows per day | 09-20 **84** (Sun), 09-21 **1173** (Mon), 09-22 **1363** (Tue), 09-23 **35** (Wed, partial) |
| Day-count delta | `84+1173+1363+35 = 2655` ✅ |
| per-stock insert sum | **2655** ✅ (three independent reconciliations agree) |
| rows inserted outside the window | **0** |
| duplicate ids within the run | **0** |
| `covered_to` | 16/16 advanced to ≥ 2026-09-23 |
| `.err` | zero `access_block` / `manual_verification` / `Traceback` / `schema_mismatch` |

Note 09-19 stayed at 110 and 09-20 (Sunday) added only 84 — a weekend trough, the
same shape as 09-12/09-13 (95/114). Large single-day numbers like 09-22 = 1363 are
therefore not evidence of a burst; they are a normal trading day.

**Worked example that closes the arithmetic exactly** (measured live over a
browser session on 2026-09-19, 601012 page 1 — and this is the shape of every
"the page shows more than the database" report):

| quantity | count |
| --- | --- |
| entries on page 1 published on 2026-09-19 | **46** |
| of those, `post_type == 0` | **37** |
| of those, `post_type` 1 / 20 | **9** (2 + 7) |
| database rows with `published_at` on 2026-09-19 | **37** |
| **page-1 `post_type == 0` entries missing from the database** | **0** |

`46 = 37 + 9`, and `37 == 37`. The nine, with titles, are all `资讯`/转发:

| id | type | time | title | author |
| --- | --- | --- | --- | --- |
| 1775236574 | 1 | 07:49:10 | 隆基绿能：融资净买入2355.28万元，融资余额46.26亿元 | 隆基绿能资讯 |
| 1775241397 | 1 | 08:30:40 | 首个钙钛矿光伏领域国家标准出炉 (置顶) | 隆基绿能资讯 |
| 1775245497 | 20 | 09:28:03 | 首个钙钛矿光伏领域国家标准出炉 | 股友d36G661376 |
| 1775247916 | 20 | 10:01:04 | 首个钙钛矿…（转发） | 不染的林泉 |
| 1775249604 | 20 | 10:24:12 | 我想说，中国光伏企业多年深耕发展… | 潮头鱼捕快 |
| 1775252138 | 20 | 11:02:14 | 首个钙钛矿…（转发） | hjdueb008008 |
| 1775255483 | 20 | 11:48:47 | 八十万隆基今年不换股！换股就换电解铝！… | 股友855228mn50 |
| 1775264543 | 20 | 14:43:36 | 感谢，主力开始做多光伏产业… | 否极泰来一伟 |
| 1775271532 | 20 | 17:06:42 | 光伏「周事迹」国家电投、华能高层变动… | 光伏头条 |

Note the author/bar fields: the type-1 rows are `隆基绿能资讯` posting **in the
隆基绿能 bar**, while several type-20 rows have `stockbar_name` of *another* bar
(财富号评论吧 / 帝科股份吧 / 金银河吧) and are reposts pulled in. So the excluded
set is not homogeneous: it contains official 资讯 plus cross-bar reposts.

**Decision needed:** whether news/转发 items should be collected at all. The
scope filter is deliberate and **spec-frozen** — `specs/eastmoney_guba.md` states
`item scope: list entries with source post_type=0`, that alternate types "remain
in raw list evidence and explicit out-of-scope counters; they are not silently
discarded", that the boundary is "a frozen source-object scope, **not a content
decision**", and that "**adding an alternate type requires a spec change with
detail evidence**". So flipping this is a spec change, not a parser tweak. `post_type`
1/20 is not noise — it includes exchange filings and 资讯 the research may want —
but changing it changes what "a post" means for the whole corpus (see D-012, D-014).

### Counting nuance: the date you see on the site is not `published_at`

Two different Eastmoney list surfaces, two different meanings:

* `list,<code>,f.html` — **sorted by publish time**, column header 发帖时间. This
  is what `EastmoneyGubaCollector.list_url` requests.
* `list,<code>.html` — the default, **sorted by last reply**, column header
  **最后更新**. An old post that gets a reply today appears dated today.

Reading the default page and comparing its dates to `published_at` therefore
invents a gap that is not there.

**The trap is worse than "two URLs": the same page carries two date columns and
they disagree.** The payload has both `post_publish_time` (发帖时间) and
`post_last_time` (最后更新). Counting "09-19" against the wrong one changes the
answer, measured live on 601012 page 1 (2026-09-19):

| surface | 发帖时间 = 09-19 | 最后更新 = 09-19 |
| --- | --- | --- |
| `list,601012,f.html` (publish-ordered) | **46** | 51 |
| `list,601012.html` (reply-ordered) | **46** | 66 |

So "how many posts did Longi get on 09-19" has three defensible-looking answers
(46 / 51 / 66) and only one of them (`46`, the 发帖时间 count) is comparable to
`published_at`. A reader who can only see the 最后更新 column is looking at a
number that counts *posts touched on* that day, not *posts made* on it. When
reconciling, always ask which column the number came from before assuming a gap.
 Measured 2026-09-19 on 601012: the default page
showed ~66 entries dated 09-19, but the publish-ordered page showed **46** and the
database held **37** `post_type = 0` rows — with the balance being out-of-scope
items, not omissions. Posts dated 09-18 06:09, 09-18 11:04 and 09-18 21:09 all
appeared as "09-19" on the default page.

### What the login / ad / captcha interstitials mean for validity (2026-09-19)

Running with `--acquisition-mode managed-chromium` and `profile_mode=fresh` opens a
**brand-new browser profile per stock**, so the source treats every stock as a
first-time anonymous visitor and shows the interstitial sequence: login prompt →
ad layer → sometimes the identity-verification (captcha) shell. An operator
watching the windows will see this on every stock, and the browser is closed
before the challenge can be completed. **That is expected and it does not
corrupt or block the collection**, for three separate reasons:

1. **The data is not read from the rendered DOM.** `parse_list_page` calls
   `_embedded_json(html, "article_list")` — the list is parsed out of the
   *server-sent HTML payload*, explicitly "without executing JavaScript". Login
   prompts and ad interstitials are additional DOM layers; they do not remove the
   inline `article_list` payload, so they cannot replace or pollute the parsed
   rows.
2. **A challenge shell has no `article_list`, so it cannot masquerade as data.**
   `is_access_block_page` requires a known `<title>` (`身份核实` / `访问验证` /
   `安全验证` / `人机验证`) **and** a source marker (`fd_guba_validate`,
   `em_capt.js`, `validate.js`, `emcaptcha`); `browser_host` additionally gates on
   `"var article_list=" in html`. Anything else is a schema mismatch, which
   raises — it is never silently stored. **A verification page yields 0 items, not
   160.** Measured directly 2026-09-19 by tripping the block for real (a burst of
   anonymous `curl` list requests was enough): the response was 2834 bytes,
   `<title>身份核实</title>`, `<body><div id="root"></div></body>` plus
   `em_capt.js` / `validate.js`, **no `article_list`** — and feeding it to the
   project's own functions gives `is_access_block_page(...) = True` and
   `parse_list_page(...) -> GubaParseError: missing embedded article_list`. It
   cannot be parsed into "a few posts"; it hard-fails. The block is IP-level and
   persists across hosts (601012 was blocked too), and it is **not** shown on
   every request — the same anonymous session read ~16 list pages across two
   stocks before tripping it, which is why "sometimes there is no captcha" is
   expected rather than contradictory.
3. **The collector never solves a challenge.** `ChallengeAwareEastmoneyTransport`
   leaves the visible browser open, polls the live DOM every 5s up to
   `--challenge-wait` (180s), and consumes the recovered document *in place* (no
   re-navigation). On timeout it returns the original blocked response so the
   collector fails closed.

**Cheap arithmetic check that no challenge fired:** the challenge window is 180s.
Two independent runs confirm it never opened:

* 15:04Z run — whole 16-stock job **190s**, slowest single stock **51s**, every
  stock returned 2–4 full pages (`received` 160/161/240/320/322).
* 15:51Z run — whole job **41s**, slowest stock **8s**, every stock `pages` 1–2.

One fired challenge-wait would have added ≥180s to the job and would have made a
single stock take ≥180s. Neither happened — and independently, all 16 `.err`
files in both runs contained no `access_block`, `manual_verification` or
schema-mismatch text (`7296` bytes total = 16 × 456, i.e. exactly the benign
`runpy` warning plus the acquisition-mode line).

**The 15:51Z run is also the cleanest end-to-end evidence that the anonymous
client block is *not* a collection block.** At that moment the same machine's
plain-HTTP client was being served the 2834-byte `身份核实` shell on
`list,601012,f.html`, while `--acquisition-mode managed-chromium` completed
16/16 SUCCESS with **zero** blocks and picked up a genuinely new post
(`002028` / `1775290229`, published 2026-09-19 23:33:22+08:00, i.e. 18 minutes
before the run). So "the crawler is blocked" and "our HTTP client is blocked"
are different claims; the managed browser path is the one that matters.

**Confirming validity by hand** (do this rather than trusting `status=SUCCESS`):
fetch a sample of collected URLs and compare against the row. On 2026-09-19 two
were checked and matched the database on `title`, `published_at` **to the
second**, `author_name` and `stock_code`. Structural checks over the 704 new rows:
all 10-digit ids, all `published_at` as `+08:00` ISO, no empty authors, all URLs on
`guba.eastmoney.com/news`, zero duplicate ids within the run, and id↔time
monotonic for 703 of 704 adjacent pairs.

Two benign patterns that look alarming if you go looking:

* **11 of 704 URLs carry a bar code that is not the row's `stock_code`.** These are
  cross-bar posts: the URL holds the post's *own* forum (a sector peer, or a
  commodity bar). Examples: 601012 (隆基绿能) carrying `news,002459,…` (晶澳) and
  `news,600732,…` (爱旭); 002648 (卫星化学) carrying `news,600989,…` (宝丰能源) and
  `news,ufnymexcl00y,…` (the NYMEX crude-oil bar, whose cashtag is in the title).
  The titles corroborate the sector. This is normal Eastmoney behaviour, not
  mis-attribution.
* **A title that is only a cashtag** (e.g. `$沪电股份(SZ002463)$`) is a real post
  whose body begins with the cashtag, by 5 different authors — not injected ad
  spam. Keyword scans for `登录/验证/广告/扫码` produce false positives because
  `安全` appears in ordinary prose ("指数相对安全的", "安全下车").

### Cross-session anchor: is the served list complete? (`served_vs_stored_diff.py`)

Everything above proves the *pages we read* are genuine. It does **not** prove they
are *complete* — a site can serve a crawler a **degraded but self-consistent** page
(real titles, contiguous ids, 80 rows/page, `rc=1`, no time gaps) that passes every
internal consistency check. Proving non-degradation needs an anchor from **outside
the session being judged**.

```zsh
python scripts/ops/served_vs_stored_diff.py 601012 8
```

It walks the publish-ordered `f` surface and compares what the site serves *now*
against what an **earlier, different-day collection session** actually stored. Read
the two numbers in this order:

* **`served-not-stored` (real gap)** — the list offers a `post_type == 0` post the
  database lacks. Must be **0** for "we got everything". This is the honest
  completeness number.
* **`stored-not-served`** — an earlier session stored it, today's list does not
  serve it. **Not automatically bad**: a post the site has deleted is legitimately
  absent from *every* list. So each one is probed at its own URL — the site 302s
  removed posts to `/error?type=2`. Only a post that is **still live at its own URL
  yet missing from the list** would be evidence of shrinkage. The script prints a
  positive control (three ids it *did* serve) so you can see the probe can return
  `STILL-LIVE`; without that, `REMOVED` would be a property of the probe, not the site.

**Result, 601012, 2026-09-19** (walk window 09-17 09:45 → 09-19 22:36, 8 pages):

| quantity | value |
| --- | --- |
| served rows / `type0` / `非0` | 640 / 601 / 39 |
| DB rows inside the walked window | 605 |
| **`served-not-stored`, `type0`** | **0** |
| `stored-not-served` | 4 |

All 4 `stored-not-served` were confirmed **deleted** (`302 -> /error?type=2`), with
the 3-id control group all `STILL-LIVE`. So of 605 posts a different-day session had
stored, today's list still serves every one that has not been removed (601/605), and
nothing is missing from the database. Per-day inside the window: 09-17 `275` stored /
`274` served (1 deleted), 09-18 `293` / `290` (3 deleted), 09-19 `37` / `37`
(exact). The database's newest 601012 row is `2026-09-19T22:36:33+08:00` — identical
to the newest served row, to the second.

**Two traps that produced false alarms on the way here — both are now handled in the
script, and both are worth knowing before you write your own comparison:**

1. **Window mismatch manufactures gaps.** The first version compared
   `published_at >= '2026-09-18'` against a walk that reached back to 09-17, and
   reported **44** `served-not-stored` posts. All 44 were already in the database,
   dated 09-17 — purely an artefact of the DB filter being narrower than the walk.
   Fix: derive the window **from the walk itself** and compare nothing outside it.
2. **Timestamp formats are not comparable as strings.** The database writes
   `2026-09-17T09:45:08+08:00`; the list payload writes `2026-09-17 09:45:08`.
   `'T' > ' '`, so a naive `substr(published_at,1,10) >= ?` filter shifts whole days
   and a naive range comparison silently admits rows outside the window. Fix: one
   `norm()` that turns `T` into a space, strips the offset, and truncates to seconds,
   applied to **both** sides before any comparison.

**Scope note:** this was verified for 601012 only. The same run against other stocks
could not be completed because the probing itself tripped the IP-level block
(above) — that is a measurement limitation, not a negative result. Re-run
`served_vs_stored_diff.py <code> 6` for other codes and read the
`served-not-stored` line.

### `covered_to` is optimistic at page granularity

`backfill_coverage.covered_to` records the newest published time the run *walked
through*, not the newest time it successfully **persisted**. List pages hold ~80
posts, and posts shift down the ordering as new ones arrive, so a post can cross a
page boundary between two runs and be skipped by a walk that stops on
`existing_coverage_reached` — leaving a hole *below* the watermark. Measured
2026-09-19: **1 of 704** new rows (601012, `1774862173` @ 09-18 10:49:49) was
older than that stock's pre-run watermark of 11:48:23. Small, real, and currently
unfixed — the walk writes whatever page it read, so such holes are usually closed
by a later run rather than by design. Do not read `covered_to` as "no gaps exist
before this time".

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
