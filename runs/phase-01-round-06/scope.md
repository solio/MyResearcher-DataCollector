# Phase 1 Round 06 — Incremental / Historical Refresh Spec Correction

Role: `Source Researcher`

## Objective

Reconcile the frozen `eastmoney_guba` incremental semantics with the Phase 1
business goal and light-design principle after the Round 5 cross-condition.

## Allowed

- Narrow edits to `specs/eastmoney_guba.md` identity/observation, historical
  refresh, incremental strategy and acceptance wording.
- One concise decision artifact and handoff/status artifacts.

## Forbidden

- Re-researching endpoints or source behavior.
- Any change to `src/`, `tests/`, fixtures or production code.
- Xueqiu, DataClean, persistence, scheduler, sentiment, finance or trading work.

## Exit

Freeze the corrected rule and hand off to Developer for implementation/test
alignment only. Do not start Developer or Tester work in this round.

