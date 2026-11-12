# Handoff: operator verification node coverage

Status: IMPLEMENTED; TEST_PASS for alias resolution and read-only node checks.

## Changes

- `verify_backfill.command 251` resolves to `8.148.251.114`.
- `verify_backfill.command 252` resolves to `8.148.252.117`.
- Full IPs remain valid. The interactive chooser lists aliases and all full
  addresses present in the local private `nodes.json`.
- Read-only `--check` now returns halt and task state with `route_error` when
  the current task has no usable HTTP/mixed route. Browser verification still
  rejects that route and does not switch to Mihomo implicitly.
- The two SSH entries already existed in the ignored local registry; no key,
  token, collector task, or server setting was changed.

## Validation

- `python3 apps/http_backfill/tests/test_operator_verification.py`: **21 tests
  passed**.
- `python3 -m py_compile scripts/ops/verify_http_backfill.py
  apps/http_backfill/tests/test_operator_verification.py`: passed.
- `git diff --check`: passed.
- `./scripts/ops/verify_backfill.command 251 --check`: passed; remote state
  returned, including two blocked stock records and their route errors.
- `./scripts/ops/verify_backfill.command 252 --check`: passed; remote state
  returned, including two blocked stock records and their route errors.
- These checks used read-only status APIs only; no Guba navigation, source
  probe, pause, resume, or proxy write occurred.

## Operational limitation

Both nodes currently report that the blocked tasks do not use an unauthenticated
HTTP/mixed route. The command can inspect them, but the headed verification and
`--resume` workflow will continue to refuse until the matching task route is
configured in the console. The helper deliberately does not infer or switch to
the installed Mihomo listener.

## Files

- `scripts/ops/verify_http_backfill.py`
- `apps/http_backfill/SPEC.md`
- `apps/http_backfill/README.zh-CN.md`
- `apps/http_backfill/HANDOFF.md`
- `apps/http_backfill/tests/test_operator_verification.py`
- `runs/operator-verification-node-coverage-2026-10-19/scope.md`
- `runs/operator-verification-node-coverage-2026-10-19/handoff.md`
