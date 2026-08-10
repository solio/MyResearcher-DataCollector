# Phase 1 Round 07 Developer Handoff

## Execution identity

client: Codex
model: GPT-5
role: Developer

## Current state

phase: Phase 1
round: Round 07 — Developer Alignment
status: `DEV_ALIGNMENT: READY_FOR_RETEST`

## Source contract

Use the corrected and re-frozen `specs/eastmoney_guba.md` from Round 06.
Historical known IDs at or before the committed watermark need not be
detail-fetched solely to find mutable metadata/content edits. Unknown IDs are
eligible; seen IDs newer than the watermark remain eligible; authorized
acquisition drift remains versioned.

## Changed files

- `src/myresearcher_collector/sources/eastmoney_guba/collector.py`
- `tests/unit/test_eastmoney_guba_collector.py`
- `runs/phase-01-round-02/spec-implementation-traceability.md`
- `runs/phase-01-round-07/*`

## Regression alignment

Preserved the Round 3/4 retry, watermark, raw-evidence and authorized-drift
regressions. Added the unknown-old-ID eligibility regression. Round 5 remains
immutable historical evidence and is not rewritten.

## Validation commands

```text
git diff --check
PYTHONPYCACHEPREFIX=/tmp/myresearcher-datacollector-pyc python -m compileall -q src tests
PYTHONDONTWRITEBYTECODE=1 python -m pytest --collect-only -q
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q
```

No live source/network run was performed.

## Next role

Tester

Please independently re-test the corrected incremental boundary and all
preserved acceptance regressions. Developer tests are not `TEST_PASS`.
