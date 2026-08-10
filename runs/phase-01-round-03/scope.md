# Phase 1 Round 03 Tester Scope

Role: `Tester`

## Mission

Independently validate the `eastmoney_guba` Phase 1 Round 2 implementation
against the frozen `specs/eastmoney_guba.md`, project contracts and the current
Round 2 scope.

## Allowed

- Read-only inspection of source, tests and fixtures.
- Deterministic local pytest/compile checks.
- Test-only in-memory probes and sanitized fixture transformations.
- Tester run artifacts under this directory.

## Forbidden

- Production implementation changes.
- Changes to `specs/eastmoney_guba.md` or Round 2 Developer conclusions.
- Xueqiu, DataClean integration, durable production persistence, scheduler,
  sentiment, finance or trading work.
- High-frequency or authenticated live collection.

## Exit

Report `TEST_PASS` only if the frozen required behavior is independently
verified. Any implementation defect remains a failure routed to Developer.

