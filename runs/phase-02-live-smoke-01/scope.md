# Phase 2 Eastmoney Live Smoke 01 Scope

Role: `Executor`

Objective: execute one fresh, bounded real-network smoke through the approved
Eastmoney Collector and persistence chain after the Phase 2 offline gate closed.

Execution baseline:

- Offline PASS artifact/main before integration: `2701e13`
- Independently tested Round 07 code: `16f70c8`
- Live-smoke source branch/commit: `phase2-live-smoke-prep` / `ac4f93a`
- Merge and execution commit: `1acd107`

Limits: `stock_code=601012`, `max_pages=1`, one request in flight, minimum
request interval 3 seconds, HTTPS GET-only, fresh isolated data directory, no
cookies/tokens, no bypass behavior, and no second/incremental run.

Out of scope: production redesign, Phase 3, Xueqiu, DataClean, scheduler,
target-set work and any source-protection bypass.
