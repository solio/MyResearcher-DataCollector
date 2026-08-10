# Phase 1 Round 06 Source Researcher Handoff

## Execution Identity

client: Codex
model: GPT-5
role: Source Researcher

## Current State

phase: Phase 1
round: Round 06 — Spec Correction
status: `SPEC_CORRECTION: APPROVED`

## Changed source contract

`specs/eastmoney_guba.md` now freezes:

- no required historical detail refresh for known IDs at or before committed
  watermark solely to discover mutable metadata or content edits;
- continued acquisition of IDs newer than the committed watermark;
- version/drift preservation whenever source-item/detail facts are actually
  acquired in an authorized path;
- historical refresh and content-edit detection as Phase 1 out of scope.

## Historical evidence

- Round 5 `OFFLINE_TEST_FAIL` remains immutable.
- The Tester was correct under the old wording.
- `runs/phase-01-round-06/decision.md` records the business/value/cost decision
  and reclassification.

## Changed files

- `specs/eastmoney_guba.md`
- `runs/phase-01-round-06/scope.md`
- `runs/phase-01-round-06/decision.md`
- `runs/phase-01-round-06/status.txt`
- `runs/phase-01-round-06/handoff.md`

No `src/`, `tests/` or fixture files changed. No source research or live smoke
was performed.

## Next Role

Developer

## Next Action

Check the existing implementation, tests and traceability against the corrected
spec. Adjust only code/test evidence that conflicts with the narrowed rule;
do not add historical refresh machinery. Then request an independent Tester
re-test.

## Remaining blockers

OQ-01/OQ-02/OQ-03/OQ-04 remain unchanged. Xueqiu, DataClean integration,
production persistence, scheduler, sentiment, finance and trading remain out
of scope.

