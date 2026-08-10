# Phase 1 Round 03 Tester Handoff

## Execution Identity

client: Codex
model: GPT-5
role: Tester

## Current State

phase: Phase 1
round: Round 03 (Independent Tester)
status: `TEST_FAIL`

## Read Before Continuing

- `AGENTS.md`
- `docs/data-collector/product-goal.md`
- `docs/data-collector/collaboration-contract.md`
- `docs/data-collector/data-contract.md`
- `docs/data-collector/implementation-plan.md`
- `docs/data-collector/testing-contract.md`
- `docs/data-collector/runtime-contract.md`
- `specs/eastmoney_guba.md`
- `runs/phase-01-round-01/`
- `runs/phase-01-round-02/scope.md`
- `runs/phase-01-round-02/handoff.md`
- `runs/phase-01-round-02/spec-implementation-traceability.md`

## Completed

- Independently ran the deterministic compile, collection and pytest checks.
- Inspected implementation, tests and fixtures against the frozen source spec.
- Ran local fake-transport probes for retry budgets, detail drift, watermark
  eligibility and schema-mismatch raw evidence.

## Evidence

- `test-report.md`
- `evidence.md`
- 25 tests collected and 25 passed, but four independent implementation defects
  remain.

## Changed Files

- Tester artifacts under `runs/phase-01-round-03/` only.
- No production code, tests, fixtures, SOURCE_SPEC or Developer Round 2 files
  were modified.

## Open Questions

- Existing OQ-01/OQ-02/OQ-03/OQ-04 remain unchanged.

## Known Limitations

- `LIVE_SMOKE_NOT_EXECUTED`; no live source result is claimed.
- Deterministic failures are sufficient for this Tester failure.

## Next Role

Developer

## Next Action

Fix the four `IMPLEMENTATION_DEFECT` cases in `evidence.md`, then request a new
independent Tester run. Do not enter DataClean integration or later phases.

## Do Not

- Do not modify SOURCE_SPEC to match implementation.
- Do not silently repair production code in the Tester role.
- Do not start Xueqiu, DataClean, persistence, scheduler or Phase 2 work.

