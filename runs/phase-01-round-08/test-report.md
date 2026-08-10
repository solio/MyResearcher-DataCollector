# Phase 1 Independent Re-Test Report

## Baseline

Tested commit: `725cf4e1b841599cb9ab28b63c282cb81c8d52b5`.

## Spec Basis

- `AGENTS.md`
- `specs/eastmoney_guba.md` (approved Round 06 correction)
- `runs/phase-01-round-06/decision.md`
- `runs/phase-01-round-06/handoff.md`
- `runs/phase-01-round-07/alignment-report.md`
- `runs/phase-01-round-07/handoff.md`
- `runs/phase-01-round-02/spec-implementation-traceability.md`
- `docs/data-collector/testing-contract.md`
- `docs/data-collector/runtime-contract.md`

## Tests Added / Changed

- Added deterministic `synthetic_row`, `synthetic_list_page` and
  `synthetic_detail` helpers to the Tester test module.
- Added `test_mixed_incremental_page_handles_known_and_unknown_old_and_new_ids`
  for the required four-way mixed page.
- Added `test_historical_known_first_page_does_not_stop_before_unknown_second_page`
  for pagination boundary behavior.
- Existing single-scenario unknown-old, known-old and known-new tests were
  independently re-run.

## Required Scenario Matrix

| Scenario | Expected | Actual | Evidence | Result |
|---|---|---|---|---|
| unknown old ID | eligible | detail requested; observation emitted | `test_unknown_id_at_or_before_watermark_is_still_eligible` | PASS |
| known old ID | historical skip allowed | no detail request; boundary confirmed | `test_watermark_confirmation_returns_no_new_data_without_detail_requests` | PASS |
| known newer ID | legal processing path | detail requested; observation emitted | `test_seen_id_newer_than_watermark_is_eligible` | PASS |
| unknown newer ID | collect normally | detail requested; observation emitted | `test_mixed_incremental_page_handles_known_and_unknown_old_and_new_ids` | PASS |
| mixed page | no premature filtering | only known-old skipped; other three acquired | `test_mixed_incremental_page_handles_known_and_unknown_old_and_new_ids` | PASS |
| pagination boundary | no incorrect early stop | second-page unknown acquired; empty third page terminates | `test_historical_known_first_page_does_not_stop_before_unknown_second_page` | PASS |

## Regression Results

Commands and results:

```text
git diff --check                                      exit 0
PYTHONPYCACHEPREFIX=/tmp/myresearcher-datacollector-pyc \
  python -m compileall -q src tests                    exit 0
PYTHONDONTWRITEBYTECODE=1 python -m pytest --collect-only -q
  33 collected, exit 0
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q
  33 passed, 0 failed, 0 skipped, exit 0
```

One non-blocking `PytestCacheWarning` reported that the checkout's
`.pytest_cache` is not writable. It did not affect collection or execution.
No live smoke was executed.

## Confirmed Blockers

None.

## Non-Blocking Observations

No new observation beyond the frozen limitations. Durable persistence,
DataClean transport/envelope, scheduler, historical refresh/deletion/tail
semantics and official numeric rate limits remain outside this Phase 1 test.

## Final Verdict

`TEST_PASS`
