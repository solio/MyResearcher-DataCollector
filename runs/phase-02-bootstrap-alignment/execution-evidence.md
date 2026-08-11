# Eastmoney Bootstrap Alignment — Deterministic Execution Evidence

No live network, credentials or real-source execution was used.

## Bootstrap behavior

Deterministic coverage proves:

- fresh success requests list pages 1, 2 and 3 in order, resolves required
  details synchronously, returns `SUCCESS/bootstrap_complete`, declares the
  newest resolved publication time and commits that exact checkpoint;
- page/detail failure inside the window returns a non-success state with no
  safe frontier and no checkpoint;
- prior observations with checkpoint `NULL` do not suppress bootstrap: retry
  begins at page 1, re-executes the complete window and establishes the
  checkpoint only after full success;
- `max_pages` 1 or 2 fails before transport use and before a collection run is
  created;
- a larger cap still executes only the frozen three-page bootstrap window;
- the persistent CLI performs bootstrap on first use and ordinary incremental
  `NO_NEW_DATA` confirmation on later use of the same scope.

## Frozen regression coverage

The acceptance/integration suite retains evidence for PST-018 through PST-021:
valid partial safe prefix advancement, unsafe-state no-advance, unresolved-gap
non-crossing, and unknown-old-ID eligibility. Established-checkpoint
confirmation still returns `NO_NEW_DATA` without historical detail refresh.

## Required validation

Final results recorded after implementation and artifact creation:

```text
git diff --check
exit 0

PYTHONPYCACHEPREFIX=/tmp/myresearcher-datacollector-pyc \
python -m compileall -q src tests
exit 0

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
python -m pytest tests/acceptance tests/integration -q
41 passed, 1 xfailed
exit 0

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
python -m pytest -q
106 passed, 1 xfailed
exit 0
```

The single xfail remains the approved PST-017 mapping-note case; no new xfail
or failure was introduced.
