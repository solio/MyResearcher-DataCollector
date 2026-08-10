# Phase 2 Round 02 — Storage Contract Correction Report

## Baseline

`e68d38338dcd7d33915e3d1f98b2589ed300f5e3`

The baseline branch was clean and all Phase 2 Round 01 documents were present.

## SC-B01 — Partial checkpoint semantics

### Old semantics

Round 01 categorically stated that `PARTIAL_COLLECTION`,
`COLLECTION_FAILED`, `SPEC_MISMATCH` and `CANCELLED` leave the prior checkpoint
unchanged. This over-constrained `PARTIAL_COLLECTION`.

### Corrected semantics

`PARTIAL_COLLECTION` is not categorically forbidden from checkpoint
advancement. The Collector runtime must first produce a proven safe contiguous
frontier. Persistence may advance only to that runtime-declared frontier when
all required raw evidence, observations, attempts/failures and metadata before
it are committed atomically and no unresolved gap crosses it.

Unresolved acquisition failure, eligible item, parse failure preventing accepted
persistence, persistence failure, identity ambiguity or source/spec mismatch
blocks advancement. Persistence validates this condition; it does not
recompute page order, completeness, item eligibility or watermark semantics.
Terminal status alone is neither permission nor prohibition.

### Affected files

- `runs/phase-02-round-01/storage-contract.md`
- `runs/phase-02-round-01/architecture.md`
- `runs/phase-02-round-01/decision-log.md`
- `runs/phase-02-round-01/open-questions.md`
- `runs/phase-02-round-01/handoff.md`

### Phase 1 preservation

The correction preserves Phase 1 watermark safety: an unknown item at or before
the watermark remains eligible, and a partial run cannot cross unresolved work.
It only permits a runtime-proven contiguous prefix to be safely committed.

## SC-B02 — Evidence invariant topology

### Old semantics

Round 01 described every observation as requiring list and detail evidence,
which incorrectly made the current Eastmoney request topology global.

### Corrected source-agnostic invariant

Every persisted observation must link to one or more supporting RawEvidence
rows sufficient to justify its persisted facts under the applicable
`SOURCE_SPEC`. Evidence roles are explicit and versioned.

### Eastmoney-specific behavior retained

For `eastmoney_guba` Phase 1, accepted observations still retain the list and
accepted detail evidence required by its frozen SOURCE_SPEC. A future source
with one direct item response is not required to fabricate list evidence.

### Affected files

- `runs/phase-02-round-01/storage-contract.md`
- `runs/phase-02-round-01/decision-log.md`
- `runs/phase-02-round-01/handoff.md`

## Scope Check

- no persistence production code;
- no migration or database creation;
- no SOURCE_SPEC change;
- no Phase 1 change;
- no DataClean implementation;
- no new architecture.

## New Open Questions

`None.` Existing deferred questions remain unchanged.

## Final State

`STORAGE_CONTRACT_CORRECTED_READY_FOR_REVIEW`
