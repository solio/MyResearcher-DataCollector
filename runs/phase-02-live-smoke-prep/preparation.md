# Eastmoney Guba Live Smoke Preparation

Role: `Developer`

Source branch/commit: `phase2-live-smoke-prep` / `ac4f93a`

Original preparation baseline: `7cb584c`

Reconciled main baseline: `2701e13` (`PHASE_2_OFFLINE_ACCEPTANCE_PASS`)

## Preparation artifact

Current live-run capability:

- Baseline: **NO**. The existing `eastmoney-guba` CLI runs the Collector with
  in-memory evidence and bypasses persistence. The complete
  `execute_and_persist_collection` path exists, but baseline CLI cannot invoke
  it with a production transport or report its persisted run.
- Prepared branch: **YES**. Round 08 independently closed the Offline Gate at
  main `2701e13`, after testing the Round 07 fix commit `16f70c8`. The new
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

Original preparation verification at old baseline (historical, offline only):

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

The historical failure above was the then-open PST-020 cross-gap defect. Round
07 corrected it and Round 08 independently recorded
`PHASE_2_OFFLINE_ACCEPTANCE_PASS`: acceptance/integration `31 passed, 1
xfailed`, full suite `86 passed, 1 xfailed`, both exit 0. Reconciled validation
is recorded before merge and live execution.

Reconciled validation on latest PASS main (offline only):

```text
main before integration: 2701e13
prep source commit: ac4f93a
cherry-pick commit: 4246ae4

git diff --check
  exit 0

PYTHONPYCACHEPREFIX=/tmp/myresearcher-datacollector-pyc \
  python -m compileall -q src tests
  exit 0

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python -m pytest tests/acceptance tests/integration -q
  31 passed, 1 approved xfail; exit 0

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q
  91 passed, 1 approved xfail; exit 0

eastmoney-guba-live-smoke 601012 --max-pages 1 \
  --min-interval 3.0 --timeout 20 --data-dir <fresh-path> --plan-only
  network_execution=false; data_dir_ready=true; exit 0
```

The approved xfail remains the PST-017 mapping note. No network request or
data-directory creation occurred during reconciliation/plan validation.

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

- **NONE for the offline gate.** Round 08 closed it at `2701e13`; PST-020 and
  PST-018/PST-019 pass independently. Reconciled deterministic validation and
  `--plan-only` must still pass before merge and `--confirm-live`.
- Live execution remains a separate outcome and does not alter the historical
  Phase 2 acceptance artifacts.

## Future execution checklist

1. Confirm the repository records a fresh Independent Tester Offline
   Acceptance PASS on the code being integrated (confirmed by Round 08).
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
