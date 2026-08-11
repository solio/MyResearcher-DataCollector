# Eastmoney Backfill v0.1 — Independent Acceptance Scope

## Role

Independent Tester

## Tested baseline

`b01c608c3497e2ea5bc8f3c4b43f1bbad3e114a2`

`HEAD` and `origin/main` both resolved to this exact Developer correction
baseline. `git fetch --all` was attempted but the sandbox could not connect to
GitHub over SSH; no source/live-network request was made and the local exact
baseline was already confirmed.

## Authority and boundaries

- Frozen `specs/eastmoney_guba.md`, data/runtime contracts and the Backfill
  acceptance semantics are authoritative.
- Tester changes are limited to `tests/` and `runs/backfill-acceptance/`.
- No `src/`, specs or production persistence/collector/CLI code was changed.
- All collection paths use deterministic fake transports and temporary stores.
- Xueqiu Backfill remains `NOT_READY` and is not an acceptance dependency;
  existing Xueqiu offline regression still runs.
