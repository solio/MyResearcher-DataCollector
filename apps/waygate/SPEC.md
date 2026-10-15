# Waygate v1 — 2026-10-14

Status: implementation authorized by the user; Windows/DevEco/device acceptance
is delegated to the user and their other agent. Entry point: `runs.md`.

## Outcome

A native HarmonyOS ArkTS app with a durable verification inbox, the existing H5
console, and an operator-controlled ArkWeb verification screen. One hub address
and login on the phone. Existing fleet registration remains authoritative.
An additive Python hub service owns SSH credentials and temporary relay sessions;
the phone never receives a server SSH key or changes its system proxy.

## Boundaries

- Reuse `scripts/ops/verify_http_backfill.py` for target, route, original-request
  probe and scoped resume guards. No source parser, data schema, collector rate,
  production training DB, or existing collector deployment changes.
- Fleet polling reads collector status only. Offline/stale status cannot resolve
  an incident. Deduplicate a node barrier by instance/job/stock/task, including
  when a failed manual probe changes its request id. Transport failures do not
  generate CAPTCHA instructions. Historical evidence alone is not an incident.
- Only an active `challenge` permits the manual verification flow. Other active
  auth/rate blocks link to the console with their actual classification.
- One verification session per node, expiring after 10 minutes. Recheck owner,
  revision, request and route before setup, source navigation and confirmation.
  Pause through the existing helper; do not resume siblings.
- Relay authenticated WSS to the node's current HTTP proxy through temporary
  SSH. Allow HTTPS CONNECT only to approved public source resource hosts and
  the neutral IP check. No open proxy, arbitrary SSH commands, direct fallback,
  cookie export, TLS interception or automated CAPTCHA solving.
- ArkWeb proxy applies to all Web components: destroy console Web before opening
  verification, await proxy callback, validate browser/node exit IP, then navigate
  original URL. Destroy verification Web before removing proxy. Backgrounding,
  cancellation, lost heartbeat and expiry close resources without probing.
- Explicit “验证完成并继续” authorizes exactly one guarded probe and only the
  verified stock's resume. Persist dispatch before sending it: a timeout/restart
  is an uncertain result, never permission to repeat the probe automatically.
- Pending inbox and push outbox survive restart. OS Push Kit is optional until
  AGC credentials/signing/permission are configured. Provider acceptance is not
  device delivery. Same incident/device uses a stable message deduplication id.
- The user performs Windows SDK/signing/real-device deployment and acceptance;
  offline tests do not prove ArkWeb, Huawei push or a real CAPTCHA succeeds.

## Deliverables

`harmony/` DevEco project (API 20 minimum for TCP server close); `hub/` service,
focused offline tests, one native Linux systemd example and nginx location;
private configuration example; Windows build/check script; API/architecture and
test handoff in `runs.md`. All public referenced project files are committed.
Runtime DBs, signing material, tokens and actual node configuration stay ignored.

## 2026-10-15: existing main-console startup

The user authorizes server implementation and requests one startup command on
the existing Linux Docker main console. Add a script that discovers the running
collector's published port and token mount and starts only a Waygate container.
The user's subsequent correction removes nginx discovery, validation, mutation
and reload from this command. It must also not require the public URL to work
before starting the backend. Generate an optional /v1/, /console/, /health route
snippet separately for the operator's existing nginx configuration. Confirm the
local hub inbox only; never label that as public connectivity. Preserve collector
container/tasks/registry and existing SSH settings. Do not claim remote startup
from local checks. Report missing SSH configuration separately from app access.
