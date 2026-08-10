# Phase 1 Round 02 Implementation Notes

## Boundary decisions

- The source package returns source/domain records and runtime results. It does not claim the provisional DataClean envelope is frozen.
- `InMemoryRawEvidenceStore` is a test/developer abstraction only; it is not a production persistence choice.
- HTTP transport is injected so parser and collector tests never need network access.
- Only `post_type=0` rows are emitted. Nonzero source rows remain in the captured page evidence and are counted as out-of-scope.
- Page overlap is acquisition-level idempotency by `(source, source_item_id)`; semantic/content deduplication is not implemented.

## Contract blockers retained

- OQ-01: DataClean executable entry point and envelope.
- OQ-02: durable raw snapshot/manifest representation.
- OQ-03: cross-project version and timestamp serialization.
- OQ-04: production operational schedule/rate approval.

No `SPEC_MISMATCH` has been observed during implementation.

## Validation note

The repository's pytest configuration adds `src` to the test import path. The CLI is exercised from a source checkout with `PYTHONPATH=src`; no package installation or network smoke is required for deterministic checks.
