# Phase 2 Round 05 — Collector-Persistence Integration Report

## Baseline

`34c823d113c7d97dfdf4cad64f369183bf420179` — Phase 2 Round 04 persistence
correction. The working tree was clean at round start.

## Integration Boundary

The new public application boundary is
`myresearcher_collector.integration.execute_and_persist_collection(...)`.
It owns one execution from `RUNNING` creation through Collector completion,
attempt/evidence/failure mapping, observation persistence, conditional
checkpoint finalization, close and return of a deterministic execution
summary. Callers provide a Collector transport; they do not manually split a
`CollectionResult` into SQL calls.

The boundary uses two minimal adapters:

- `_CapturingTransport` records every actual transport invocation, including
  retries and returned HTTP bytes, and publishes those exact bytes through the
  real `RawEvidenceStore`.
- `_CapturingEvidenceStore` adapts the Collector's existing `put` protocol to
  the already-published response event, verifying that the Collector-consumed
  payload is byte-identical to the persisted payload.

`SQLitePersistence.known_item_ids` is the only persistence API addition. It
reads accepted source identities for a scope so an incremental Collector run
receives the committed identity set and checkpoint.

## Data Flow

```text
Collector transport request
  → captured request attempt
  → actual response bytes → RawEvidenceStore.publish
  → raw_evidence metadata
  → Collector parser → accepted GubaSourceItem
  → SQLitePersistence.record_observation
  → collection_failures when runtime reports a failure
  → runtime status and declared watermark → finish_run/checkpoint
  → close → reopen and verify_evidence
```

The Collector remains the owner of Eastmoney parsing, pagination, retry,
watermark eligibility, drift and runtime status. The integration layer does
not reparse HTML or infer a frontier from observation counts. It translates
the runtime result's watermark/status/failure declaration into the existing
`SafeFrontier` API; unresolved partial work is passed as an unsafe frontier.

## Files Changed

- `src/myresearcher_collector/__init__.py`
- `src/myresearcher_collector/integration.py`
- `src/myresearcher_collector/storage/sqlite_store.py`
- `tests/integration/test_persistent_collector.py`
- `runs/phase-02-round-05/integration-report.md`
- `runs/phase-02-round-05/execution-evidence.md`
- `runs/phase-02-round-05/handoff.md`
- `runs/phase-02-round-05/status.txt`

## Scope Check

No SOURCE_SPEC, Storage Contract, Phase 1 semantics, independent Tester plan,
DataClean export, live-source execution, scheduler, database migration,
legacy code, backup/retention workflow, deployment, performance test,
sentiment or downstream research behavior was added or changed. The tests use
temporary directories, local synthetic fixtures, real SQLite and real raw-file
publication; the only transport is a local mapping fake.

## Validation

```text
git diff --check                                      exit 0
PYTHONPYCACHEPREFIX=/tmp/myresearcher-datacollector-pyc \
  python -m compileall -q src tests                    exit 0
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python -m pytest --collect-only -q                   58 collected, exit 0
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python -m pytest -q                                  58 passed, 0 failed, exit 0
```

Pytest emitted only the existing non-blocking unwritable `.pytest_cache`
warning. No network, credentials or real database was used.
