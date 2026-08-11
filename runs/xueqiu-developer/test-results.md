# Xueqiu v0.1 Test Results

## Deterministic fixtures/tests

Sanitized fixtures are under `tests/fixtures/xueqiu/`. The acceptance matrix covers XQ-001 through XQ-020, including page/last_id continuity, mapping/time/nullability, invalid schema, duplicate/repeated pages, bootstrap/incremental rules, access failures, coverage cap, raw lineage and Eastmoney regression scope. Additional parser and real SQLite/RawEvidence integration coverage is in `tests/unit/test_xueqiu_parser.py` and `tests/integration/test_xueqiu_persistent.py`.

## Commands

```text
git diff --check                         PASS
PYTHONPYCACHEPREFIX=/tmp/myresearcher-datacollector-pyc python -m compileall -q src tests  PASS
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/acceptance/test_xueqiu_acceptance.py tests/unit/test_xueqiu_parser.py tests/integration/test_xueqiu_persistent.py -q  PASS
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q  PASS
```

Correction results: XQ-021 incremental frontier/SQLite checkpoint advancement, XQ-022A-D browser-page pagination/redaction, XQ-023 repeated incremental page classification, and XQ-024 historical drift versioning tests pass. Full suite after correction: `160 passed, 1 xfailed`. The approved PST-017 mapping-note xfail remains unchanged.

No real Xueqiu or other live network request was executed.
