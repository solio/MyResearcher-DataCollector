# Backfill v0.1 — Independent Test Results

## Commands

```text
git diff --check                                      exit 0
PYTHONPYCACHEPREFIX=/tmp/myresearcher-datacollector-pyc \
  python -m compileall -q src tests                   exit 0
```

Developer Backfill tests:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python -m pytest -q tests/unit/test_backfill.py \
  tests/integration/test_backfill_persistence.py
14 passed
```

Tester-owned Backfill acceptance:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python -m pytest -q tests/integration/test_backfill_acceptance.py
14 passed
```

Existing source/persistence/retention/batch/Xueqiu regression set:

```text
102 passed, 1 approved xfail
exit 0
```

Full exact tree:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q
222 passed, 1 approved xfail
exit 0
```

The one xfail is the previously approved non-Backfill PST-017 mapping note.
No live source, Xueqiu, credentials or real network was used.

## Independent scenario evidence

- BF-A01: range traversal stopped at the first page wholly older than
  `from_time`; newer-than-`to_time` list rows were not detail-fetched; only
  inclusive in-range rows became observations; `SUCCESS`,
  `backfill_range_complete`, `range_complete=true`.
- BF-A02: real `execute_and_persist_backfill_collection` preserved a seeded
  forward checkpoint in SQLite, not merely in the report.
- BF-A03: successful fresh Backfill left the checkpoint `NULL`; ordinary
  forward plan remained `BOOTSTRAP_PENDING`.
- BF-A04: persisted known IDs did not stop traversal before the time boundary.
- BF-A05: overlapping page ID detail was requested once and emitted once;
  page traversal continued.
- BF-A06: invalid detail schema produced `SPEC_MISMATCH` /
  `detail_schema_mismatch`; raw evidence and failure lineage remained; the
  checkpoint was unchanged.
- BF-A07/A08: date-only bounds and `--days` use inclusive Asia/Shanghai
  calendar semantics and produced identical UTC bounds under UTC,
  America/New_York and Asia/Shanghai host TZ settings.
- BF-A09/A11: three requested details reconciled as 2 success + 1 failure and
  classified `PARTIAL_COLLECTION`.
- BF-A10: three exhausted details reconciled as 0 success + 3 failures and
  classified `COLLECTION_FAILED` / `all_candidate_details_failed`; raw
  evidence and failures persisted.
- BF-A12: an empty requested range completed successfully with zero details;
  zero details did not become failure.
- BF-A13: max-pages before the time boundary produced
  `PARTIAL_COLLECTION`, `max_pages_reached`, `range_complete=false`.
- BF-A14: repeated identical Backfill created new runs/evidence lineage but no
  duplicate observation version.
- BF-A15: changed source facts appended observation version 2; repeating the
  changed facts did not create version 3.
- BF-A16: invalid range failed before transport access.
- BF-A17: `backfill --plan-only` resolved source/stock/range/checkpoint without
  network, raw, observation or checkpoint mutation.
- BF-A18: partial, collection-failed and spec-mismatch terminal states each
  preserved the forward checkpoint in direct SQLite queries.
- BF-A19: Backfill used ordinary `raw_evidence`, `observation_evidence` and
  `raw_body_state` rows; no Backfill-specific raw store/table was introduced.
- BF-A20: existing Eastmoney, Persistence, Xueqiu offline, Retention and Batch
  acceptance suites remained green in the regression set and full suite.
