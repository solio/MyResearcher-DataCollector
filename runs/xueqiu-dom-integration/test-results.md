# Xueqiu DOM integration deterministic test results

Commands run from the repository root:

```text
git diff --check
PASS

PYTHONPYCACHEPREFIX=/tmp/myresearcher-datacollector-pyc python -m compileall -q src tests
PASS

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/unit/test_xueqiu_dom.py tests/integration/test_xueqiu_dom_backfill.py -q
18 passed

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q
328 passed, 1 xfailed
```

The new tests cover symbol mapping, normal DOM parsing, modified-time
semantics, detail timestamp parsing, async stale-DOM protection, active-page
and ID progression, short pages continuing, created-time boundary filtering,
page-level durability after a later failure, duplicate-page failure, and
idempotent reruns. Correction tests additionally cover temporary detail pages,
serial close behavior, missing/failed detail navigation, main-page preservation,
manual unproven start-page resume suppression, and legitimate exact-range
resume continuation. The full suite includes the existing Eastmoney and old
Xueqiu JSON-path tests.

Live network smoke: **NOT EXECUTED**.
