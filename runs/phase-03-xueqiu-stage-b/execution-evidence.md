# Xueqiu Independent Tester — Stage B Evidence

- Production baseline: `bdbdc58c1f8d0a929ad4402c0de90631255be968`
- Tester worktree branch: `test/xueqiu-final-acceptance`
- Network/browser: offline only; deterministic transports and sanitized fixtures
- Production files modified by Tester: NONE

## Required gates

| Gate | Result |
| --- | --- |
| `git diff --check` | PASS |
| `PYTHONPYCACHEPREFIX=/tmp/myresearcher-datacollector-pyc python -m compileall -q src tests` | PASS |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/acceptance -q` | 55 passed, 1 approved xfail |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/integration -q` | 19 passed |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q` | 160 passed, 1 approved xfail |

## Xueqiu coverage

- XQ-001 through XQ-020: PASS
- XQ-021 and XQ-022 browser/persistence supplement: PASS
- XQ-023 repeated new page: `PARTIAL_COLLECTION`, safe frontier NULL, checkpoint unchanged: PASS
- XQ-024 historical drift version 2, identical repeat does not create version 3, checkpoint unchanged: PASS
- Fake/injected browser transport, page/last_id continuity, sanitized raw evidence and secret redaction: PASS

## Regression coverage

Full suite includes Eastmoney, persistence/checkpoint isolation, raw evidence, batch and existing acceptance/integration tests. No live network was used.
