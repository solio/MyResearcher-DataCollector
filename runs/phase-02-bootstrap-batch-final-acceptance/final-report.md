# Final Bootstrap + Batch Offline Acceptance

## Result

`BOOTSTRAP_BATCH_OFFLINE_ACCEPTANCE_PASS`

`Next Role: Live Executor`

## Tested repository

- Branch: `main`
- Tested commit: `d20ab45b9e383783216f356fb7d2ca7decf7e1d2`
- `origin/main` was fetched and fast-forward pulled before testing.
- Working tree was clean at test start. No production files, specs, or CLI implementation were modified.
- No live Eastmoney/network request was executed; transports were deterministic fakes or plan-only.

## Commands and results

| Command | Result |
|---|---|
| `git fetch origin` / `git switch main` / `git pull --ff-only origin main` / `git status` / `git rev-parse HEAD` | PASS; pinned to the commit above |
| `git diff --check` | PASS |
| `PYTHONPYCACHEPREFIX=/tmp/myresearcher-datacollector-pyc python -m compileall -q src tests` | PASS |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/acceptance -q` | `35 passed, 1 xfailed` |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/integration -q` | `7 passed` |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q` | `113 passed, 1 xfailed` |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/unit/test_batch.py tests/integration/test_batch_bootstrap_integration.py -q` | `7 passed` |

## Case results

- BST-001 through BST-008: PASS. Fresh three-page bootstrap, failure-safe NULL checkpoint behavior, restart with observations but no checkpoint, minimum-page guard, resolved-publication safe frontier, unknown-old eligibility, and incremental transition are covered by `tests/acceptance/test_bootstrap_acceptance.py`.
- Batch target validation: PASS. Invalid targets are rejected; duplicates are removed exactly once while preserving first-seen order.
- Sequential execution: PASS. Maximum active runner count is one and target order is preserved.
- Failure policy: PASS. Ordinary per-stock failures continue; `SPEC_MISMATCH`, cancellation, and global persistence errors stop the batch.
- Plan-only/no-network: PASS. Plan declares `network_execution=false` and does not create the data directory.
- Real persistence isolation: PASS for `600519`, `300750`, and `002594`; run IDs, scope keys, observations, raw evidence, checkpoints, and statuses remain isolated.
- Combined scenario: PASS. Fresh A (`600519`) and B (`300750`) use bootstrap (three list + three detail calls each); existing C (`002594`) uses ordinary incremental (two list calls), with isolated checkpoint/run/observation/evidence state.
- Batch remains orchestration-only: the consolidated `batch.py` delegates to the approved single-stock persistence boundary and contains no batch-specific checkpoint/bootstrap manipulation.
- PST-018, PST-019, PST-020, PST-021 and the fixed cross-gap safe-frontier coverage: PASS.
- Approved PST-017 mapping-note xfail remains the sole expected xfail.

## Failures / unexpected dependencies

None. No unexpected batch dependency was observed.

