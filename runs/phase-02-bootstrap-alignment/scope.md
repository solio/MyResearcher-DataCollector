# Phase 2 — Eastmoney Bootstrap Runtime Alignment Scope

Role: `Developer`

Objective: implement the Reviewer-frozen Eastmoney fresh-checkpoint bootstrap
semantics in the Collector runtime and persistent single-stock execution path,
with deterministic regression evidence.

Frozen behavior implemented in this run:

- committed checkpoint `NULL` means `BOOTSTRAP_PENDING`, regardless of prior
  raw evidence, observations or known IDs;
- bootstrap requests pages 1, 2 and 3 sequentially, with no skipping or
  concurrency;
- a complete window returns `SUCCESS` with
  `stop_reason=bootstrap_complete` and a runtime-declared safe frontier equal
  to the newest successfully resolved in-scope `published_at`;
- any required page/detail, parse, schema/spec or cancellation failure leaves
  the initial checkpoint `NULL`;
- a retry with checkpoint still `NULL` starts again at page 1;
- established-checkpoint incremental behavior remains the approved Phase 1
  strategy, including unknown-old-ID eligibility.

Out of scope: live network execution, batch orchestration, Xueqiu, DataClean,
scheduler/cron, parallel collection, full-history backfill, persistence or
checkpoint schema redesign, and bootstrap resume cursors.

The historical `runs/phase-02-live-smoke-01/` evidence is read-only and remains
`EASTMONEY_LIVE_SMOKE: PASS` with runtime `PARTIAL_COLLECTION` and checkpoint
`NULL`.
