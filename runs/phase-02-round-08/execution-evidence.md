# Phase 2 Round 08 — Independent Execution Evidence

## Tested commit

```text
16f70c8c39750a88202fa5d9ea8a3f10a9f22763
Fix partial safe frontier cross-gap proof
```

The worktree was clean before the Round 08 artifacts were created. No
production source, persistence implementation, frozen contract or prior-round
artifact was changed.

## Primary cross-gap re-test

Independent case:

- checkpoint `T0 = 2026-08-10T00:00:00.000000Z`;
- page 1 item A at source-local `2026-08-10 10:00:00`, UTC `T1 =
  2026-08-10T02:00:00.000000Z`; detail succeeds;
- page 2 distinct eligible item B at source-local `2026-08-10 09:00:00`, UTC
  `Tmid = 2026-08-10T01:00:00.000000Z`;
- B detail returns HTTP 503 for all three retry attempts.

The independent test
`tests/acceptance/test_persistence_integration_acceptance.py::test_pst020_partial_detail_failure_cannot_cross_unresolved_mid_page_item`
passed. A separate offline diagnostic of the same public integration boundary
reported:

```text
runtime_safe_frontier = None
runtime_status = PARTIAL_COLLECTION
detail_b_attempts = 3
persisted_checkpoint = ('2026-08-10T00:00:00.000000Z', 'seed-run')
run_state = ('PARTIAL_COLLECTION', None)
```

Therefore the runtime-declared frontier does not cross unresolved eligible B,
and Persistence does not advance the checkpoint to `T1`.

## Partial safe-prefix regression

The targeted PST-018/PST-019 and integration partial-prefix/no-frontier checks
ran independently:

```text
8 passed
```

PST-018 still advances a genuinely proven contiguous prefix exactly to its
frontier. PST-019 keeps the checkpoint unchanged for partial-without-frontier,
collection failure, spec mismatch and cancellation. The integration safe
prefix case also continues to advance, while the unsafe page-failure case
does not.

## Frozen acceptance and full suite

Required acceptance/integration command:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/acceptance tests/integration -q
```

Result:

```text
31 passed, 1 xfailed in 0.55s
exit 0
```

Full deterministic command:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q
```

Result:

```text
86 passed, 1 xfailed in 0.76s
exit 0
```

The single xfail is the already approved PST-017
`TEST_PLAN_MAPPING_NOTE`. No new blocking failure appeared. No network,
credentials, real source or non-temporary database was used.
