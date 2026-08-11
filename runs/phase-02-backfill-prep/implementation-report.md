# Backfill v0.1 — Implementation Report

Implemented:

- `backfill` CLI mode with explicit source, stock, inclusive range, `--days`,
  `--max-pages`, `--plan-only` and `--confirm-live` safety gate.
- Eastmoney sequential historical traversal that does not use known IDs or
  the forward checkpoint as a stop condition.
- Newer-than-`to_time` rows remain list evidence; only in-range rows request
  required detail and become observations.
- Shared CollectionRun/Attempt/RawEvidence/Observation persistence is reused.
  Backfill always finalizes with `safe_frontier=None`; an explicit assertion
  compares SQLite checkpoint before and after execution.
- Exact-fact replay uses existing observation fingerprint/version semantics.
- Deterministic tests cover range traversal, boundary, known-ID traversal,
  max-pages partial, range validation, plan-only, fresh NULL checkpoint and
  existing checkpoint/idempotency.

Xueqiu offline backfill: NOT_READY in this implementation; the approved
browser-managed live host remains unmodified. Xueqiu live backfill: NOT
EXECUTED.

No schema or migration was added. No live network execution was performed.

Validation evidence: `git diff --check`; compileall with
`PYTHONPYCACHEPREFIX=/tmp/myresearcher-datacollector-pyc`; pytest collection
reported 190 tests; full offline pytest reported `189 passed, 1 xfailed`.

## Correction pass

The review blockers were corrected without schema or source-contract changes:

- overlap pages now deduplicate only IDs acquired in the current run; persisted
  IDs never stop historical traversal;
- required detail schema mismatch returns `SPEC_MISMATCH` with
  `detail_schema_mismatch`, retaining captured list/detail evidence;
- date-only and `--days` ranges use fixed `Asia/Shanghai` calendar semantics;
- detail counters increment success/parsed only after fetch, parse and merge,
  preserving `requested = success + failed`.

Correction validation: 207 tests collected; full suite `206 passed, 1
xfail`. Real network remained NOT EXECUTED.
