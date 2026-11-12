# Operator verification node coverage — 2026-10-19

## Goal

Add imported nodes `8.148.251.114` and `8.148.252.117` to the local
`verify_backfill.command` selection flow.

## In scope

- Add CLI aliases `251` and `252` while retaining full-IP selection.
- Populate the interactive node choices from the private, ignored SSH node
  registry so all configured nodes can be selected.
- Keep read-only `--check` usable when a stopped task has no valid HTTP/mixed
  route; report the route issue without changing node/task state.
- Keep interactive verification strict about the task's effective route.
- Update the operator-verification spec, handoff, and usage documentation.

## Out of scope

- Changing collector proxy settings, Mihomo selection, task state, or server
  configuration.
- Adding SSH key material, tokens, or private node configuration to Git.
- Source requests, CAPTCHA navigation, probe, or resume during validation.

## Acceptance

- CLI aliases and full IPs resolve only through the private node registry.
- The interactive list contains the new aliases and any configured unaliased IPs.
- `--check` is read-only and reports missing route configuration instead of
  failing before showing status.
- Actual verification still refuses to invent or switch proxy routes.
- Focused operator-verification tests and Python syntax checks pass.
