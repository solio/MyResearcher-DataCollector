# Open Questions

## Q-001

Question: What exact entry point and serialized input schema will MyResearcher-DataClean accept from Collector?

Why it matters: Collector cannot freeze required fields, transport or compatibility tests without a downstream contract.

Evidence checked: DataClean `AGENTS.md`, project/current-round state, capability ledger, data knowledge and decision log.

Current hypothesis: DataClean will require replayable immutable RAW records with provenance, but concrete fields and entry point are not yet defined.

Blocking: `NO` for Phase 0; `YES` before Phase 1 adapter output is approved.

Evidence level: `OPEN QUESTION`; hypothesis is `PROVISIONAL`.

## Q-002

Question: Which single real source is authorized for Phase 1?

Why it matters: Source research, identity, time semantics, fixtures and adapter layout depend on the selected source.

Evidence checked: Phase 0 task and legacy repository implementations.

Current hypothesis: None. Existing Guba/Xueqiu code proves prior use, not current source selection or current behavior.

Blocking: `NO` for Phase 0; `YES` for Phase 1 start.

Evidence level: `OPEN QUESTION`.

## Q-003

Question: How are raw evidence and `raw_ref` retained and transported?

Why it matters: Replayability and traceability require either preserved raw content or a durable immutable reference.

Evidence checked: DataClean provisional knowledge and legacy SQLite/record behavior.

Current hypothesis: A backend-neutral reference envelope is preferable until volume and DataClean transport are known.

Blocking: `NO` for Phase 0; `YES` before production persistence.

Evidence level: `OPEN QUESTION`; hypothesis is `HYPOTHESIS`.

## Q-004

Question: What is the fallback item identity policy when a source has no stable `source_item_id`?

Why it matters: A guessed URL/hash policy can merge distinct items or split updates and makes replay/version behavior ambiguous.

Evidence checked: Legacy URL uniqueness and inconsistent source outputs; no DataClean identity contract exists.

Current hypothesis: Fallback rules must be source-specific, versioned and collision-tested.

Blocking: `NO` for Phase 0; `YES` for any affected Phase 1 source.

Evidence level: `OPEN QUESTION`; hypothesis is `PROVISIONAL`.

## Q-005

Question: What canonical timestamp serialization, precision and unresolved-time representation will Collector and DataClean share?

Why it matters: Legacy code uses local formatted datetimes, while publish, update and collection times require separate, timezone-safe semantics.

Evidence checked: Legacy Guba/Xueqiu outputs and DataClean data open questions.

Current hypothesis: UTC timestamps plus explicit unresolved/source-timezone metadata, with exact format decided jointly.

Blocking: `NO` for Phase 0; `YES` before the raw schema freezes.

Evidence level: `OPEN QUESTION`; hypothesis is `PROVISIONAL`.

## Q-006

Question: Is `stock_code` part of the common raw envelope or source-specific association metadata?

Why it matters: Collector may preserve a source-explicit association but must not infer an investment classification.

Evidence checked: Legacy stock-target workflows and absence of a DataClean field contract.

Current hypothesis: Preserve only source-explicit associations; do not make the field universally required.

Blocking: `NO` for Phase 0; depends on the Phase 1 source.

Evidence level: `OPEN QUESTION`; hypothesis is `PROVISIONAL`.

## Q-007

Question: Which parser, collector, identity and provenance versions must be separate top-level fields?

Why it matters: Replay and schema evolution require identifying the deterministic behavior that produced a record.

Evidence checked: DataClean provisional processing/rule-version requirement and Phase 0 versioning rule.

Current hypothesis: Keep schema, collector/parser and identity versions separable unless the approved envelope provides an equivalent structured provenance object.

Blocking: `NO` for Phase 0; `YES` before Phase 1 contract approval.

Evidence level: `OPEN QUESTION`; hypothesis is `PROVISIONAL`.

## Q-008

Question: What legal, terms-of-service, authentication, cookie and rate-limit constraints apply to the selected source?

Why it matters: A technically reachable entry point may still be unsuitable or require operational controls.

Evidence checked: No source research was authorized in Phase 0; legacy code cannot establish current policy.

Current hypothesis: None; Source Researcher must document evidence in the selected SOURCE_SPEC.

Blocking: `NO` for Phase 0; `YES` before live Phase 1 acquisition.

Evidence level: `OPEN QUESTION`.

## Q-009

Question: Should a Phase 1 adapter migrate selected legacy behavior or be implemented independently from a newly researched spec?

Why it matters: Reuse may preserve unverified identity/time/error assumptions, while greenfield work may duplicate valid behavior.

Evidence checked: Legacy implementations exist, but no current SOURCE_SPEC or deterministic fixture suite exists in the new project.

Current hypothesis: Treat legacy code as evidence and candidate reference, never as the contract.

Blocking: `NO` for Phase 0; decision follows source selection and research.

Evidence level: `OPEN QUESTION`; hypothesis is `PROVISIONAL`.

## Q-010

Question: What exact terminal status enum and retry-aware counter reconciliation will Phase 1 freeze beyond `NO_NEW_DATA` and `COLLECTION_FAILED`?

Why it matters: Partial request/page/item failures must remain observable and machine-testable.

Evidence checked: Phase 0 runtime requirements; no runner implementation exists.

Current hypothesis: Define success, partial success, source failure, parse failure and cancellation only after one source's behavior is researched.

Blocking: `NO` for Phase 0; `YES` before the Phase 1 runner contract freezes.

Evidence level: `OPEN QUESTION`; hypothesis is `PROVISIONAL`.
