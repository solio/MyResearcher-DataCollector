# Phase 2 Persistence Independent Test-Planning Handoff

## Execution identity

role: `Tester`

track: `Persistence Independent Test Planning`

status: `TEST_PLAN_READY`

final state: `PERSISTENCE_TEST_PLAN_READY`

## Frozen basis

The sole normative persistence contract is the corrected
`runs/phase-02-round-01/storage-contract.md` at committed planning baseline
`9d1b0e0`. Phase 2 Round 02 was used only to understand the two already-closed
corrections:

- conditional runtime-declared safe-frontier checkpoint advancement;
- source-agnostic supporting-evidence topology.

Neither issue was reopened. Phase 1's unknown-old-ID regression and frozen
Eastmoney list/detail provenance were retained.

## Deliverables

- `persistence-test-plan.md`
- `acceptance-matrix.md`
- `risk-priorities.md`
- `handoff.md`
- `status.txt`

## Scenario accounting

- MUST scenarios: 20 (`PST-001` through `PST-020`)
- blocking regressions: 1 (`PST-021`)
- structural inspections: 1 (`PST-022`)
- non-blocking scenarios: 0
- all-row risk count: P0 = 14, P1 = 7, P2 = 1
- executable blocking count excluding structural inspection: 21

After the Developer commit, `PST-001` through `PST-021` can be implemented
directly as deterministic offline pytest scenarios against temporary SQLite and
filesystem stores. `PST-022` is structural inspection; it gains an additional
generic direct-response pytest probe only if the delivered public interface
already supports synthetic generic-source injection.

## Independence record

All specification and baseline reads were pinned to committed `HEAD` via
`git show HEAD:...`. The working tree reported three uncommitted persistence
production paths, but their contents were not read, searched, imported,
executed or used to shape the plan. No Developer private scratch/report or
implementation discussion was consulted.

No production code, tests, migrations, database, raw evidence, SOURCE_SPEC or
Storage Contract was created or modified. No live Eastmoney or real data was
accessed. This handoff does not pre-judge Developer acceptance and does not
start independent test implementation/execution.

## Next action

Stop at `TEST_PLAN_READY`. After the Persistence Developer submits a pinned
commit and handoff, begin a separate Phase 2 Persistence Independent Test
Implementation & Execution round using this matrix.

`PERSISTENCE_TEST_PLAN_READY`
