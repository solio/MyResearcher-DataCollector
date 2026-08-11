# Eastmoney Guba Live Smoke Preparation

Role: `Developer`

Branch/worktree: `phase2-live-smoke-prep` / `MyResearcher-DataCollector-live-smoke-prep`

Baseline: `7cb584c` (`PHASE_2_OFFLINE_ACCEPTANCE_FAIL`; no live request was made)

## Preparation artifact

Current live-run capability:

- Baseline: **NO**. The existing `eastmoney-guba` CLI runs the Collector with
  in-memory evidence and bypasses persistence. The complete
  `execute_and_persist_collection` path exists, but baseline CLI cannot invoke
  it with a production transport or report its persisted run.
- Prepared branch: **YES, gated on fresh Offline Acceptance PASS**. The new
  command invokes the existing Collector -> RawEvidenceStore -> Persistence ->
  SQLite -> checkpoint chain. It does not implement another crawler or state
  system.

Code changes required: **YES**

Changes made:

- added `eastmoney-guba-live-smoke` CLI wiring to the existing persistent
  integration;
- restricted the command to `source=eastmoney_guba`, one six-digit stock code,
  `max_pages` 1 or 2, HTTPS GET-only source access, and a smoke-specific minimum
  interval of 3 seconds;
- required an explicit new/empty local `--data-dir` and `--confirm-live`;
- added `--plan-only`, which does not construct a network transport;
- added read-only `inspect-run`, backed only by existing SQLite runtime,
  counter, evidence and checkpoint data;
- added deterministic fixture-transport tests. No Collector, safe-frontier,
  checkpoint, schema, Storage Contract or SOURCE_SPEC semantics were changed.

Preparation verification (offline only):

```text
python -m compileall -q src tests
  exit 0

python -m pytest -q \
  tests/unit/test_live_smoke_cli.py \
  tests/integration/test_persistent_collector.py
  9 passed

python -m pytest -q
  90 passed, 1 xfailed, 1 failed
```

The one full-suite failure is the pre-existing PST-020 cross-gap acceptance
failure recorded at the baseline, not a preparation regression. No live
network test was collected or executed.

Future live command:

```bash
PYTHONPATH=src python -m myresearcher_collector.cli \
  eastmoney-guba-live-smoke 601012 \
  --data-dir /absolute/local/path/eastmoney-guba-601012-live-smoke \
  --max-pages 1 \
  --min-interval 3.0 \
  --timeout 20 \
  --confirm-live
```

Offline plan inspection for the same command:

```bash
PYTHONPATH=src python -m myresearcher_collector.cli \
  eastmoney-guba-live-smoke 601012 \
  --data-dir /absolute/local/path/eastmoney-guba-601012-live-smoke \
  --max-pages 1 \
  --min-interval 3.0 \
  --timeout 20 \
  --plan-only
```

Expected output:

- `run_id`, `source`, `stock_code`, `scope_key`, `status`;
- request totals/successes/failures;
- page requested/success/failed counters;
- record received/accepted/failed counters (`records_accepted` is the current
  runtime's `records_parsed`, not a new counter);
- raw evidence directory, evidence-row count and distinct raw-file count;
- SQLite location;
- checkpoint before/after, checkpoint-updated flag and runtime-declared safe
  frontier;
- first/last `published_at` linked to this run;
- persisted attempt/failure counts.

The bounded first run will normally report `PARTIAL_COLLECTION` when it reaches
the 1- or 2-page hard cap before the SOURCE_SPEC confirmation boundary. That is
the existing honest runtime status and must not be relabeled as success. Inspect
the same persisted result with:

```bash
PYTHONPATH=src python -m myresearcher_collector.cli inspect-run \
  --data-dir /absolute/local/path/eastmoney-guba-601012-live-smoke \
  --run-id <run_id>
```

Data location:

```text
/absolute/local/path/eastmoney-guba-601012-live-smoke/
  collector.db
  raw/
    eastmoney_guba/
      <sha256>.body
```

The live command refuses a non-empty data directory. `inspect-run` opens the
existing SQLite database with `mode=ro`.

Secrets required: **NONE**

The approved SOURCE_SPEC says the observed public surfaces require neither
authentication nor cookies. The command accepts no cookie, token or session.
A descriptive non-secret User-Agent may be supplied with `--user-agent` or
`MYRESEARCHER_EASTMONEY_USER_AGENT`; its value is not included in the run
summary. Do not add authentication unless future source evidence and an
approved contract change require it.

Remaining blocker:

- **Execution gate only:** baseline `7cb584c` records
  `PHASE_2_OFFLINE_ACCEPTANCE_FAIL` for the unresolved partial-safe-frontier
  cross-gap case. Merge/rebase the eventual Developer correction, obtain a
  fresh Independent Tester Offline Acceptance PASS, and rerun the deterministic
  preparation tests before using `--confirm-live`.
- Do not execute LIVE_SMOKE and do not infer Phase 2 PASS from this preparation.

## Future execution checklist

1. Confirm the repository records a fresh Independent Tester Offline
   Acceptance PASS on the exact code to execute.
2. Confirm the preparation changes are integrated without modifying the frozen
   Storage Contract, SOURCE_SPEC or persistence/checkpoint semantics.
3. Choose a new local absolute data directory outside the repository; do not
   place credentials or unrelated files in it.
4. Run the `--plan-only` command and verify `network_execution=false`,
   `stock_code=601012`, `max_pages=1`, `min_interval_seconds>=3.0`, and
   `data_dir_ready=true`.
5. Run the `--confirm-live` command exactly once. Do not add cookies, CAPTCHA
   bypass, parallel requests or a lower rate interval.
6. Save only the sanitized JSON summary. Inspect raw bodies locally; do not
   commit them.
7. Use `inspect-run` to reconcile run counters, evidence, SQLite and checkpoint
   values. A bounded `PARTIAL_COLLECTION` remains partial and is not Phase 2
   PASS.

LIVE_SMOKE_PREP: READY
