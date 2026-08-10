# Phase 2 Round 04 — Persistence Developer Correction Report

## Baseline

`912684797945d840e08770b559e14aff4204bc58` (Phase 2 Round 03
Persistence Developer). The previously observed review artifact was removed
before work resumed; the working tree was then clean at this baseline.

## Confirmed blocker

`SQLitePersistence.finish_run()` could overwrite a newer
`collector_checkpoints.watermark_utc` with an older candidate frontier. This
could regress both complete and partial safe frontiers.

## Correction

`src/myresearcher_collector/storage/sqlite_store.py::finish_run` now reads the
existing checkpoint inside the same write transaction that updates the
checkpoint and terminal run. Both values are canonical UTC text produced by
`utc_text`; they are compared as timezone-aware `datetime` values. A candidate
older than the existing frontier raises `PersistenceError` before the UPSERT.
The transaction rolls back, so the checkpoint is unchanged and the candidate
run remains `RUNNING`. Equal frontiers remain idempotently accepted, and newer
frontiers advance normally.

## Regression evidence

`tests/unit/test_storage.py` adds deterministic coverage for forward advance,
equal-frontier idempotency, complete-run regression, and partial-run
regression. The regression cases assert the prior checkpoint remains intact
and the attempted run is not terminalized.

## Scope check

Only the confirmed checkpoint monotonicity defect and its minimal tests were
changed. No Storage Contract, SOURCE_SPEC, tester plan, raw evidence model,
observation logic, schema/migration model, identity model, SafeFrontier
semantics, legacy code, or next phase was modified.

## Validation

- `git diff --check`: exit 0
- `PYTHONPYCACHEPREFIX=/tmp/myresearcher-datacollector-pyc python -m compileall -q src tests`: exit 0
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest --collect-only -q`: 55 collected, exit 0
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q`: 55 passed, exit 0

The pytest run emitted only the existing non-blocking unwritable
`.pytest_cache` warning. No network, credentials, real database, or production
data was used.
