# Phase 2 Round 07 — Safe Frontier Regression Evidence

## Primary independent regression

`tests/acceptance/test_persistence_integration_acceptance.py::test_pst020_partial_detail_failure_cannot_cross_unresolved_mid_page_item`

Actual result after the fix: PASS. The runtime declaration is `None`, the
reopened checkpoint remains `('2026-08-10T00:00:00.000000Z', 'seed-run')`, and
the run remains `PARTIAL_COLLECTION` with `watermark_after_utc=NULL`.

## Developer integration coverage

`test_partial_runtime_safe_prefix_advances_checkpoint_exactly_to_prefix`
still passes for a genuine safe prefix: an unresolved item strictly after T1
does not invalidate T1, and the partial run commits exactly T1.

`test_partial_execution_persists_failure_and_does_not_cross_checkpoint`
still passes for the unsafe page-503 case. The runtime declares no frontier and
the checkpoint is unchanged.

The existing SUCCESS, NO_NEW_DATA, unknown-old-ID, PST-018/PST-019 and
checkpoint-monotonicity paths remain green.

## Validation

```text
git diff --check                                      exit 0
PYTHONPYCACHEPREFIX=/tmp/myresearcher-datacollector-pyc \
  python -m compileall -q src tests                    exit 0
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python -m pytest tests/acceptance tests/integration -q
  32 collected; 31 passed; 1 xfailed; exit 0
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python -m pytest -q
  87 executed; 86 passed; 1 xfailed; exit 0
```

The xfail is the pre-existing PST-017 `TEST_PLAN_MAPPING_NOTE`. Pytest emitted
only the existing non-blocking unwritable `.pytest_cache` warning. No network,
credentials or real database was used.
