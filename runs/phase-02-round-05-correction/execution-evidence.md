# Phase 2 Round 05 Correction — Execution Evidence

The integration cases use temporary directories, real SQLite, real raw-file
publication, the real Eastmoney Collector and deterministic local transports.

| Scenario | Status | Runtime Safe Frontier | Persisted Checkpoint | Failure | Result |
|---|---|---|---|---|---|
| SUCCESS happy path | `SUCCESS` | runtime result watermark | created at that frontier | none | PASS |
| NO_NEW_DATA incremental | `NO_NEW_DATA` | runtime result watermark equal to prior checkpoint | prior frontier retained; `last_safe_run_id` updated | none | PASS |
| partial safe prefix | `PARTIAL_COLLECTION` | explicit T1 from completed page prefix | T0 → T1; `watermark_after_utc=T1` | later detail retry exhaustion linked to attempt/evidence | PASS |
| partial unsafe | `PARTIAL_COLLECTION` | none | unchanged | page 2 HTTP 503 retry exhaustion | PASS |

## Deterministic regression cases

- `test_partial_runtime_safe_prefix_advances_checkpoint_exactly_to_prefix`
  executes a seeded T0 run, then a real Collector run with a completed new
  page at T1 and a later detail retry exhaustion. Reopened SQLite shows
  `PARTIAL_COLLECTION`, checkpoint T1, `watermark_after_utc=T1`, and a failure
  retaining linked raw evidence.
- `test_partial_execution_persists_failure_and_does_not_cross_checkpoint`
  preserves the existing page-2 503 scenario. The runtime declares no safe
  frontier; the reopened checkpoint tuple is unchanged.
- Existing happy and incremental tests continue to verify `SUCCESS` and
  `NO_NEW_DATA` translation and reopen behavior.

## Validation commands and actual results

```text
git diff --check                                      exit 0
PYTHONPYCACHEPREFIX=/tmp/myresearcher-datacollector-pyc \
  python -m compileall -q src tests                    exit 0
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python -m pytest --collect-only -q                   85 collected, exit 0
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python -m pytest tests/unit tests/integration -q      59 passed, 0 failed, exit 0
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python -m pytest -q                                  85 collected; 84 passed,
                                                        1 failed, 0 skipped, exit 1
```

The one full-suite failure is the pre-existing independent acceptance
PST-017 negative control (`NO_NEW_DATA` with no supplied frontier), recorded by
the Sol Reviewer as `TEST_PLAN_MAPPING_NOTE`. It was not changed or used as a
production acceptance oracle. Pytest also emitted the existing non-blocking
unwritable `.pytest_cache` warning. No network, credentials or real database
was used.
