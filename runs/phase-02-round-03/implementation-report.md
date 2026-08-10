# Phase 2 Round 03 — Persistence Implementation Report

## Baseline

`9d1b0e0` — Phase 2 Round 02 Storage Contract Correction, as observed at
round start. During this round, a separate concurrent commit `7040640` added
the independent persistence test plan; it was not modified or used as an
implementation input.

The implementation follows the sole normative contract in
`runs/phase-02-round-01/storage-contract.md` and the two Round 02 corrections.

## Files Changed

- `src/myresearcher_collector/storage/__init__.py`
- `src/myresearcher_collector/storage/models.py`
- `src/myresearcher_collector/storage/raw_store.py`
- `src/myresearcher_collector/storage/schema.py`
- `src/myresearcher_collector/storage/sqlite_store.py`
- `tests/unit/test_storage.py`
- `runs/phase-02-round-03/implementation-report.md`
- `runs/phase-02-round-03/evidence.md`
- `runs/phase-02-round-03/handoff.md`
- `runs/phase-02-round-03/status.txt`

The concurrent `runs/phase-02-test-plan/` artifacts were not modified or used
as an implementation input.

## Contract Mapping

| Contract object/invariant | Implementation |
|---|---|
| CollectionRun | `schema.py` `collection_runs`; `SQLitePersistence.start_run` and `finish_run` |
| RawEvidence | `raw_store.py::RawEvidenceStore`; `sqlite_store.py::record_raw_evidence` |
| SourceItemObservation | `sqlite_store.py::record_observation`, canonical fact fingerprint, immutable triggers |
| Attempt/Failure | `collection_attempts`, `collection_failures`; `record_attempt`, `record_failure` |
| Checkpoint | `collector_checkpoints`; `SafeFrontier` validation in `finish_run` |
| Atomic publish | temp write/flush/fsync, SHA/size validation and `os.link` no-clobber publish before DB reference |
| Identity/idempotency | `(source, source_item_id)`, latest fingerprint comparison, monotonic versions and scope joins |
| Schema/migration safety | explicit schema version/history checksum, exact object/column/SQL drift validation, foreign keys |

## Key Decisions

- The public storage API is a small explicit class boundary; no ORM,
  repository framework or persistence plugin hierarchy was added.
- Physical raw bytes are content-addressed, while evidence and attempt IDs
  retain independent request lineage.
- Persistence accepts runtime-declared `SafeFrontier`; it never derives page
  order, item eligibility or source completeness.
- Evidence roles are caller-provided and source-agnostic. Eastmoney callers can
  retain `list` and `detail`; no global list/detail check is imposed.
- A grouped `SQLitePersistence.transaction()` context commits metadata,
  observations, failures, terminal status and eligible checkpoint together.

## Commands

```text
git diff --check                                      exit 0
PYTHONPYCACHEPREFIX=/tmp/myresearcher-datacollector-pyc \
  python -m compileall -q src tests                    exit 0
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python -m pytest --collect-only -q                   51 collected, exit 0
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python -m pytest -q                                  51 passed, exit 0
```

One non-blocking `PytestCacheWarning` reported an unwritable `.pytest_cache`;
it did not affect test collection or execution.

## Known Limits

Only frozen deferred items remain: DataClean export serialization, backup/
restore tooling, retention/orphan operator workflow, multi-process policy and
live source behavior. No DataClean export, backup, retention, scheduler or
live source functionality was implemented.
