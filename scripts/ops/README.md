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

## Running

```bash
cd <repo>
# single pass, halts on first access block
zsh scripts/ops/enrich_all_stocks.sh

# or with bounded auto-retry
zsh scripts/ops/mop_up.sh
```

These are enrichment drivers. They are not used for collection/backfill; that is
the `backfill` CLI subcommand.
