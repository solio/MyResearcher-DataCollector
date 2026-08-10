# Implementation Plan

## Phase 0 — Project bootstrap

Goal: establish project boundary, directories, roles, long-lived contracts, SOURCE_SPEC mechanism, package/test skeleton and auditable phase evidence.

Allowed outputs: documentation and empty deterministic skeletons only.

Exit: structural checks pass and `runs/phase-00-onboarding/status.txt` is exactly `PHASE_0_PASS`; otherwise `PHASE_0_BLOCKED`.

No production collection behavior is authorized.

## Phase 1 — First-source minimal closed loop

Frozen outcome:

```text
ONE REAL SOURCE
    +
ONE SOURCE_SPEC
    +
ONE ISOLATED ADAPTER
    +
FROZEN RAW CONTRACT
    +
SANITIZED REAL FIXTURES
    +
DETERMINISTIC TESTS
    +
ONE CLI RUN LOOP
```

Principle: one source working correctly before many sources working partially.

Before implementation, Phase 1 must:

1. select one source through an explicit phase scope;
2. research and approve its SOURCE_SPEC;
3. resolve the Collector → DataClean minimum envelope and transport blockers;
4. define exact runtime outcomes and fixture acceptance;
5. keep network-dependent research separate from offline regression tests.

Phase 1 is proposed only. It has not started and is not authorized by this plan.

## Phase 2 — Collector reliability

Provisional direction only: use evidence from Phase 1 to improve retry, timeout, incremental behavior, observability and replay safety. Do not preselect infrastructure or detailed design.

## Phase 3 — Second-source validation

Provisional direction only: test whether the shared contract and isolation boundaries generalize to one additional source without creating a universal source-switching implementation.

## Phase 4 — Incremental collection and operational observation

Provisional direction only: define operational coverage and incremental behavior after real source evidence exists.

## Global gates

- Evidence before assumption.
- SOURCE_SPEC before production implementation.
- Contract changes are versioned and recorded in the decision log.
- Real-source mismatch blocks implementation as `SPEC_MISMATCH`.
- A later phase requires a new explicit scope and authorization.
