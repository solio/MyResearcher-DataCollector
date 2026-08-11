# Test Results

- `git diff --check`: PASS
- `PYTHONPYCACHEPREFIX=/tmp/myresearcher-datacollector-pyc python -m compileall -q src tests`: PASS
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/acceptance/test_raw_retention_acceptance.py -q`: **18 passed**
- Independent reviewer tests `tests/integration/test_raw_retention_reviewer.py`: **12 passed**
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/integration -q`: **33 passed**
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q`: **192 passed, 1 approved xfailed**

The xfail is the repository's pre-approved acceptance exception; no new failure was introduced.
