# Phase 1 Round 04 Developer Scope

Role: `Developer`

## Objective

Close only the four `IMPLEMENTATION_DEFECT` findings recorded by the
independent Tester in `runs/phase-01-round-03/evidence.md`.

## Allowed

- `sources/eastmoney_guba` retry, identity/version, watermark and evidence
  behavior fixes;
- deterministic regression tests using existing synthetic fixtures and fake
  transports;
- Developer traceability notes and this Round 4 handoff.

## Forbidden

- Xueqiu, DataClean integration, durable production persistence, scheduler,
  sentiment, finance, trading or unrelated refactoring;
- modifying `specs/eastmoney_guba.md`;
- modifying Round 3 Tester artifacts or claiming `TEST_PASS`.

## Exit

After validation, report `DEVELOPER_FIX: READY_FOR_RETEST` only. A new
independent Tester round must decide whether Round 3 `TEST_FAIL` is closed.

