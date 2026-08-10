# Phase 1 Round 01 Open Questions

Only unresolved questions are active here. Resolved Phase 0 questions are listed afterward so later roles do not reopen them without new evidence.

## Active — blocking production output/integration, not Source Spec approval

### OQ-01 — Collector → DataClean executable boundary

- Question: What exact serialized envelope and entry point will DataClean accept?
- Evidence: DataClean is still `PROJECT_BOOTSTRAP`, has `NO_ACTIVE_ROUND`, and its capability ledger contains governance only at commit `d2696799a930a3c8f0eff5f70723db2b388fb9af`.
- Status: `UNKNOWN — downstream repository fact`.
- Impact: the approved source semantics and offline adapter fixtures may proceed to Developer design, but no production DataClean integration or compatibility claim may be implemented.
- Owner/next action: project owner must authorize a DataClean contract round; Developer must not invent the downstream endpoint.

### OQ-02 — Raw evidence retention and `raw_ref`

- Question: Which immutable local representation and manifest format will retain list/detail snapshots and produce durable `raw_ref` values?
- Evidence: replayability is frozen; backend and transport are not.
- Status: `OPEN QUESTION`.
- Impact: blocks production persistence and end-to-end runner completion. It does not change the source requirement that both list and required detail evidence be replayable.

### OQ-03 — Common envelope/version serialization

- Question: What exact timezone-aware timestamp serialization and which schema/parser/collector/identity versions are top-level versus structured provenance?
- Evidence: source timezone and source field semantics are now frozen in `specs/eastmoney_guba.md`; DataClean serialization remains provisional.
- Status: `OPEN QUESTION`.
- Impact: Developer may build deterministic source parsing behind an internal boundary, but must not freeze or advertise a cross-project envelope without a contract decision.

### OQ-04 — Production operational approval

- Question: What owner-approved production schedule and applicable terms/rate constraints govern sustained live acquisition?
- Evidence: bounded anonymous public research succeeded; no official numeric rate limit or production SLA was established.
- Status: `OPEN QUESTION`.
- Impact: offline development is allowed under the frozen spec. Production live scheduling is not approved by this research round.

## Active — deferred source/scope

### OQ-05 — Xueqiu authorized access model

- Question: Is there a supported/reasonable long-term access model for public Xueqiu discussions, and if so does it require ordinary login, a durable session, a documented endpoint or another authorized mechanism?
- Evidence: anonymous search returned HTTP 400/error `400016`; the stock page returned a WAF challenge.
- Status: `BLOCKED / DEFERRED`.
- Impact: no Xueqiu Source Spec approval or production adapter. Do not request or commit user cookies/tokens; do not bypass WAF/CAPTCHA.

### OQ-06 — Guba replies as independent records

- Question: Can first-level and nested replies be acquired reliably with stable pagination, failure semantics and independent identity?
- Evidence: source-owned JavaScript names independent reply fields, but a successful current reply collection was not reproduced.
- Status: `BLOCKED OUTSIDE PHASE 1 TOP-LEVEL SCOPE`.
- Impact: replies must not be emitted as top-level posts or represented with invented IDs.

### OQ-07 — Historical tail and deletion semantics

- Question: What historical depth is actually traversable and how does the source represent removed/deleted posts over time?
- Evidence: page traversal and a large reported count were observed; the tail and deletion states were not.
- Status: `UNKNOWN — non-blocking for bounded Phase 1 runs`.
- Impact: do not claim full-history completeness; a later backfill spec change requires direct evidence.

## Resolved Phase 0 questions

- Phase 0 Q-002: `RESOLVED` — `FIRST_SOURCE = eastmoney_guba`.
- Phase 0 Q-004 for this source: `RESOLVED` — missing/non-numeric `post_id` fails closed; no fallback identity.
- Phase 0 Q-006 for this source: `RESOLVED` — requested bar and canonical post bar are separate source-explicit facts.
- Phase 0 Q-008 for development access: `PARTIALLY RESOLVED` — anonymous top-level Guba pages reproduced; numeric rate/production policy remains OQ-04.
- Phase 0 Q-009: `RESOLVED` — implement from the new spec; legacy code is evidence only.
- Phase 0 Q-010 at source level: `RESOLVED` — source spec freezes `SUCCESS`, `NO_NEW_DATA`, `PARTIAL_COLLECTION`, `COLLECTION_FAILED`, `SPEC_MISMATCH` and `CANCELLED` distinctions. Exact serialized runtime envelope remains part of OQ-03.
