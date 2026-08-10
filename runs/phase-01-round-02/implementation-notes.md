# Phase 1 Round 02 Implementation Notes

## Boundary decisions

- The source package returns source/domain records and runtime results. It does not claim the provisional DataClean envelope is frozen.
- `InMemoryRawEvidenceStore` is a test/developer abstraction only; it is not a production persistence choice.
- HTTP transport is injected so parser and collector tests never need network access.
- Only `post_type=0` rows are emitted. Nonzero source rows remain in the captured page evidence and are counted as out-of-scope.
- Page overlap is acquisition-level idempotency by `(source, source_item_id)`;
  identical observations are suppressed, while changed source facts are
  retained as immutable `observation_version` records and counted as
  identity-content drift. Semantic/content deduplication is not implemented.
- The source item has a frozen `schema_version`; unpromoted source fields are
  retained under `source_metadata.extra`, and optional malformed update/display
  timestamps remain nullable with field errors.
- Transport policy validates approved HTTPS redirects/final URLs, honors safe
  numeric `Retry-After`, applies bounded jittered backoff and enforces the
  minimum source request interval. Cancellation and partial-run watermark
  protection are source-isolated runtime behaviors.
- Retry budgets are three total attempts for timeout/429/5xx and two total
  attempts for 403 access blocks. Structural page/detail bytes are retained
  before parsing so `SPEC_MISMATCH` remains replayable.

## Contract blockers retained

- OQ-01: DataClean executable entry point and envelope.
- OQ-02: durable raw snapshot/manifest representation.
- OQ-03: cross-project version and timestamp serialization.
- OQ-04: production operational schedule/rate approval.

No `SPEC_MISMATCH` has been observed during implementation.

## Validation note

The repository's pytest configuration adds `src` to the test import path. The CLI is exercised from a source checkout with `PYTHONPATH=src`; no package installation or network smoke is required for deterministic checks.
