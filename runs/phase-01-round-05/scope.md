# Phase 1 Round 05 Independent Tester Scope

Role: `Tester`

## Objective

Independently re-test the four Round 3 failures after Round 4, then verify the
additional frozen-contract condition for a known ID at or before the committed
watermark whose mutable source facts changed.

## Boundary

- Read-only implementation inspection and local deterministic fake-transport
  probes.
- No production code, tests, fixtures or SOURCE_SPEC modifications.
- No live source smoke after deterministic failure.

## Result rule

Round 3 findings must all pass, and the cross-condition must emit a new
observation/version for `published_at <= watermark`, before this round can
report `OFFLINE_TEST_PASS`.

