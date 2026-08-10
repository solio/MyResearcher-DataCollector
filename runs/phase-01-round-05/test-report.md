# Phase 1 Round 05 — Independent Re-Test Report

## Deterministic suite

Executed:

```text
git diff --check
PYTHONPYCACHEPREFIX=/tmp/myresearcher-datacollector-pyc python -m compileall -q src tests
PYTHONDONTWRITEBYTECODE=1 python -m pytest --collect-only -q
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q
```

Results:

- `git diff --check`: exit 0.
- `compileall`: exit 0.
- collection: 30 tests, exit 0.
- execution: 30 passed, exit 0.
- One non-blocking pytest cache-permission warning.

## Independent Round 3 re-test

All four prior findings passed independently:

| Finding | Result |
|---|---|
| timeout/429/5xx retry budget | 3 total attempts |
| 403 access-block budget | 2 total attempts |
| changed detail facts | new version and drift count |
| known ID newer than watermark | item acquired; not `NO_NEW_DATA` |
| schema mismatch raw evidence | one raw snapshot retained before `SPEC_MISMATCH` |

## Additional cross-condition

Condition:

```text
known source_item_id
+ published_at <= committed watermark
+ mutable list/detail facts changed
```

Observed result:

```text
old_watermark_mutable_change NO_NEW_DATA empty_page 0 0 0
```

The changed row was not detail-fetched, no drift was recorded and no new
observation/version was emitted. This violates the frozen identity/incremental
requirements that changed raw facts become a new immutable observation/version.

## Live smoke

`LIVE_SMOKE_NOT_EXECUTED` — the deterministic cross-condition failure is
sufficient for this result.

