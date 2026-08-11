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
