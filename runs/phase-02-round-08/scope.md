# Phase 2 Round 08 — Final Independent Re-Test Scope

## Role

Independent Tester

## Objective

Re-test the sole Round 06 blocking defect after the Round 07 narrow fix and
confirm that the frozen Phase 2 persistence/integration acceptance has no
regression.

## Tested object

- Repository: `MyResearcher-DataCollector`
- Tested commit:
  `16f70c8c39750a88202fa5d9ea8a3f10a9f22763`
- Commit subject: `Fix partial safe frontier cross-gap proof`
- Branch: `main`

## Boundaries

- Read repository artifacts and frozen contracts; do not use Developer chat
  history as evidence.
- Do not modify production code, persistence design, Storage Contract or
  historical Round 06/Round 07 artifacts.
- Execute only deterministic offline tests with temporary SQLite/raw stores
  and synthetic transports.
- Do not run live smoke or merge any branch.

## Required checks

1. Re-run the Round 06 cross-gap case: A at `T1` succeeds, eligible B at
   `Tmid` (`T0 < Tmid < T1`) exhausts detail retries, and neither runtime nor
   persistence crosses B.
2. Confirm PST-018/PST-019 behavior remains valid: a proven safe prefix may
   advance, while an unsafe/no-frontier terminal state does not.
3. Re-run frozen acceptance/integration and the full deterministic suite.
4. Retain PST-017 as the approved `TEST_PLAN_MAPPING_NOTE` xfail; do not
   reopen it without new independent runtime evidence.
