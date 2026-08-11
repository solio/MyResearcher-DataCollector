# Xueqiu Tester Preparation Handoff

## Execution Identity

client: Codex
model: GPT-5
role: Independent Tester

## Current State

phase: Stage A — acceptance preparation
round: Xueqiu v0.1
status: XUEQIU_TEST_PREP: READY

## Frozen Source of Truth

- `specs/xueqiu.md` — `APPROVED`
- baseline: `743ed2f42c411cc335aa0e114044cf6cdfa97134`

## Completed

- Added deterministic sanitized page, repeated-page, invalid-item, missing-list
  and challenge fixtures.
- Added XQ-001 through XQ-020 acceptance cases.
- Added fake browser transport injection with request order, URL/query and
  thread-concurrency observability.
- Added an explicit `PENDING_IMPLEMENTATION` loader because the baseline has no
  Xueqiu production module.

## Evidence

- 20 acceptance tests collect successfully on the baseline.
- No live browser or network is used by the acceptance module.
- Fixture scan is limited to synthetic values and contains no secrets.

## Changed Files

- `tests/acceptance/test_xueqiu_acceptance.py`
- `tests/fixtures/xueqiu/*`
- `runs/phase-03-xueqiu-test-prep/*`

## Known Limitations

- Final execution is pending the Developer's exact Xueqiu commit.
- The baseline lacks the production collector seam, so acceptance execution is
  intentionally `PENDING_IMPLEMENTATION` rather than a pass/fail result.

## Next Role

Developer, then Independent Tester Stage B.

## Next Action

After `XUEQIU_DEV: READY_FOR_TEST` and an exact SHA, create a final Tester
worktree from that SHA, apply the Tester preparation commit, and run Xueqiu
acceptance, existing acceptance/integration tests and full pytest.

## Do Not

- Do not modify `src/`, persistence, CLI or `specs/xueqiu.md`.
- Do not execute live browser/network tests.
- Do not declare `XUEQIU_OFFLINE_ACCEPTANCE_PASS` in Stage A.
