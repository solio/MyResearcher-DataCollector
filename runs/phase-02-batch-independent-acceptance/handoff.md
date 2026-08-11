# Phase 2 Minimal Batch — Independent Tester Handoff

## Status

`BATCH_OFFLINE_PREP_PASS`

## Tested commit

`80aa943`

## Evidence

- Target validation accepts only `eastmoney_guba`, six-digit string stocks,
  stable first-seen deduplication and non-empty targets; invalid input is
  rejected before runner/network execution.
- BatchRunner is sequential; ordinary stock failure continues to the next
  target and `SPEC_MISMATCH`, `CANCELLED` and persistence/schema/raw-store
  corruption stop with explicit reasons.
- Real `execute_batch_collection` using one SQLite/raw root kept
  `stock:600519` and `stock:300750` runs, observations, checkpoints, raw
  evidence and run IDs isolated; summary matched persisted state.
- `collect-batch --plan-only` reported ordered sequential targets and
  `network_execution=false` without constructing transport or creating
  SQLite/raw state.
- Unit batch tests: 6 passed. Full deterministic suite: 97 passed, 1
  unrelated xfail.

## Alignment

`MERGE_ALIGNMENT_REQUIRED`: commit `80aa943` predates bootstrap main and has
historical CLI default `--max-pages=2`. Do not classify this as Batch defect.

## Next Action

`WAIT_FOR_BOOTSTRAP_MAIN`

After bootstrap main passes, reconcile `phase2-minimal-batch`, run final
regression and then merge. Tester must not merge or run real network.
