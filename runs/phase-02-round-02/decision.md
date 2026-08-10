# Phase 2 Round 02 — Correction Decision

`STORAGE_CONTRACT_CORRECTED_READY_FOR_REVIEW`

Two Round 01 contract blockers were corrected and no new scope was introduced.

1. Checkpoint movement is conditional safe-frontier advancement. Collector
   runtime declares the frontier; persistence validates complete records and
   absence of unresolved gaps, then commits atomically. `PARTIAL_COLLECTION`
   may advance only under those conditions.
2. Observation provenance is source-agnostic. Supporting evidence must satisfy
   the applicable SOURCE_SPEC; Eastmoney's required list/detail lineage remains
   explicit, but future sources need not fabricate either topology.

No Phase 1 semantics, schema strategy, infrastructure choice or DataClean
implementation was changed.
