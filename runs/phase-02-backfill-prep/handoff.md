# Backfill v0.1 Developer Handoff

Status: `BACKFILL_DEV_CORRECTION: READY_FOR_TEST`

Next role: Tester.

The Tester should independently verify the BF-001..BF-015 cases using the
deterministic tests under `tests/unit/test_backfill*.py` and
`tests/integration/test_backfill_persistence.py`, with special attention to
the SQLite checkpoint remaining byte-for-byte unchanged for success, partial,
failure and fresh-NULL runs.

The implementation intentionally does not claim historical completeness. It
does not implement Xueqiu live backfill, scheduler behavior, resume cursors or
any DataClean integration.

The correction regression cases are BF-016 through BF-019. The exact-tree
suite must remain the authority for independent review; Developer tests do not
close the prior review by themselves.

BF-020 adds the required all-candidate-detail failure classification and
Persistence evidence check.
