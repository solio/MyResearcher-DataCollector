# Eastmoney Truthfulness Acceptance Test Results

- `git diff --check`: PASS
- `PYTHONPYCACHEPREFIX=/tmp/myresearcher-datacollector-pyc python -m compileall -q src tests`: PASS
- Independent reviewer tests: **4 passed**
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/integration -q`: **55 passed**
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q`: **253 passed, 1 approved xfailed**
- Real external network in this acceptance: **NO**

Existing bounded existing-Chrome navigation evidence was reviewed but was not treated
as Collector/RawEvidence acceptance evidence.
