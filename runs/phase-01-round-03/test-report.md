# Phase 1 Round 03 — Independent Tester Report

## Deterministic checks

Executed from the repository root:

```text
git diff --check
PYTHONPYCACHEPREFIX=/tmp/myresearcher-datacollector-pyc python -m compileall -q src tests
PYTHONDONTWRITEBYTECODE=1 python -m pytest --collect-only -q
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q
```

Results:

- `git diff --check`: exit 0.
- `compileall`: exit 0.
- pytest collection: 25 tests, exit 0.
- pytest execution: 25 passed, exit 0.
- One non-blocking `PytestCacheWarning` reported because `.pytest_cache` is not writable.

The 25 passing tests are Developer tests plus the repository baseline; they are
not treated as independent `TEST_PASS` evidence by themselves.

## Independent contract probes

All probes used local fixtures, fake transports and in-memory evidence only.
No network request was made.

| Requirement | Result |
|---|---|
| CLI status exit mapping | Independently confirmed: `SUCCESS`/`NO_NEW_DATA` exit 0; all other frozen statuses exit 1 |
| 429 retry budget | Failed: reproduced only 2 attempts, while SOURCE_SPEC requires 3 total |
| 5xx retry budget | Failed: reproduced only 2 attempts, while SOURCE_SPEC requires 3 total |
| detail content drift | Failed: changed detail body on repeated ID produced no drift/version |
| newer publication time for already-seen ID | Failed: treated as watermark-confirmed no data without detail request |
| malformed page raw evidence | Failed: `SPEC_MISMATCH` returned with zero stored raw snapshots |

## Live smoke

`LIVE_SMOKE_NOT_EXECUTED` — deterministic failures already establish a Tester
failure and no live source observation was necessary.

