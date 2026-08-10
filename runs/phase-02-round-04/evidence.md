# Phase 2 Round 04 — Checkpoint Monotonicity Evidence

All cases use temporary synthetic SQLite stores and the public persistence API.

| Case | Test | Expected | Result |
|---|---|---|---|
| no current checkpoint → T1 | existing safe-frontier test | first frontier commits | Passed |
| current T1 → newer T2 | `test_checkpoint_allows_forward_advance` | checkpoint advances to T2 | Passed |
| current T1 → equal T1 | `test_checkpoint_equal_frontier_is_idempotent` | accepted without time regression | Passed |
| current T2 → older T1 | `test_checkpoint_regression_fails_closed_and_keeps_run_running` | `PersistenceError`; checkpoint/run unchanged | Passed |
| partial current T2 → older T1 | `test_partial_checkpoint_regression_fails_closed` | `PersistenceError`; checkpoint/run unchanged | Passed |

The regression assertions verify the prior `watermark_utc` and
`last_safe_run_id` remain unchanged. They also verify the failed candidate run
remains `RUNNING` (and, for the complete-run case, has no
`watermark_after_utc`). This demonstrates rollback of the checkpoint and
terminal-run update as one transaction.

## Commands

```text
git diff --check                                      exit 0
PYTHONPYCACHEPREFIX=/tmp/myresearcher-datacollector-pyc \
  python -m compileall -q src tests                    exit 0
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python -m pytest --collect-only -q                   55 collected, exit 0
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python -m pytest -q                                  55 passed, exit 0
```

Developer-side results are evidence for review, not an independent Tester
acceptance result.
