# Phase 2 Final Offline Persistence / Integration Acceptance Report

Status: `PHASE_2_OFFLINE_ACCEPTANCE_FAIL`

## Baseline

- current main HEAD: `a4a150d` (`Phase 2 Round 05 Correction — Partial Safe Frontier Integration`)
- frozen Storage Contract: `runs/phase-02-round-01/storage-contract.md`
- frozen Test Plan: `runs/phase-02-test-plan/`
- independent implementation commit: `014c166` (`Add independent persistence acceptance tests`)
- final execution used current main only; Developer chat history was not used

The working tree was clean before the final test additions. No live source,
credentials or real database was used.

## Mapping note

`TEST_PLAN_MAPPING_NOTE`: the historical PST-017 negative control directly
calling `finish_run(status="NO_NEW_DATA", safe_frontier=None)` is not treated
as a production blocker. The Sol Reviewer mapping is accepted: NO_NEW_DATA
truth is a Collector/runtime outcome, not a persistence inference from zero
observations. The test is retained as a non-blocking `xfail` documentation
point. The real integration path is still tested for runtime-declared outcome
and frontier behavior.

## Executed commands

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
python -m pytest tests/acceptance tests/integration -q
```

Result: exit `1`; 32 collected, 30 passed, 1 xfailed, 1 failed.

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
python -m pytest -q
```

Result: exit `1`; 87 executed, 85 passed, 1 xfailed, 1 failed.

The xfail is the documented PST-017 mapping note. The single failure is the
cross-gap integration defect below.

## Applicable acceptance coverage

- PST-001 through PST-016: independent persistence acceptance passed.
- PST-017: runtime/integration mapping note accepted; safe SUCCESS and
  NO_NEW_DATA frontier paths passed.
- PST-018/PST-019: partial safe-prefix and unsafe-frontier persistence paths
  passed.
- PST-020: simple explicit unresolved-gap component case passed; required
  Collector → Integration cross-gap case failed.
- PST-021: real unknown-old-ID integration path passed; the item was acquired
  and persisted even though its publication time was at/before the checkpoint.
- PST-022: direct source-agnostic evidence probe passed.

## Final classification

`IMPLEMENTATION_DEFECT`

Next Role: `Developer`

Do not proceed to `EASTMONEY_LIVE_SMOKE` until the defect is corrected and the
Independent Tester re-runs the final offline gate.
