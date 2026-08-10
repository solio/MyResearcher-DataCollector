# Phase 2 Round 01 — Storage Open Questions

Only questions that can change the Storage Contract are listed.

## OPEN_QUESTION OQ-S01 — DataClean export envelope

Which exact versioned serialization and batch manifest will DataClean consume
(JSONL/NDJSON, a local archive, or another agreed envelope)? Until resolved,
the stable boundary is conceptual: committed observation plus provenance and
raw references; no direct table coupling is allowed.

## OPEN_QUESTION OQ-S02 — Consistent backup snapshot

What operator-approved procedure snapshots the SQLite file and raw directory as
one portable backup, especially while a run is active? No backup files or
automation are introduced in this round.

## OPEN_QUESTION OQ-S03 — Retention/reconciliation authority

How long are raw bytes retained, and who authorizes deletion of orphan or
expired evidence? Until decided, referenced raw evidence is never deleted and
only verified temporary/orphan state may be quarantined by an explicit tool.

## OPEN_QUESTION OQ-S04 — Process concurrency

Is a second local Collector process prohibited by the runtime contract, or
should SQLite serialization be the expected behavior? The schema assumes
foreign keys and transactional writes but does not claim multi-process
coordination.

## Not open questions

The following are already determined by frozen contracts and are not deferred:

- raw evidence and observations are immutable;
- request lineage is retained even when bytes are content-deduplicated;
- failure is distinct from empty/no-data;
- a checkpoint cannot cross an unresolved gap; a partial run may advance only
  through a runtime-declared, proven safe contiguous frontier;
- an unknown ID cannot be skipped solely because it is at/before the watermark;
- Collector does not perform DataClean cleaning or sentiment/finance work.
