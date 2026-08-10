# Phase 2 Round 01 — Storage Decision Log

## DECIDED

### D-01 — SQLite plus local immutable files

Use SQLite for structured metadata/index/state and a local filesystem for raw
response bytes. This is the smallest architecture satisfying append-only raw
evidence, lineage, incremental state and offline replay for a single user.

### D-02 — Request lineage is separate from content identity

Every request attempt and response receives its own persisted lineage even when
the content SHA-256 is already present. Content-addressed physical deduplication
may share bytes, but it cannot merge request observations.

### D-03 — Observations are append-only versions

The logical key is `(source, source_item_id, scope_key)`. Identical current
facts are idempotently linked; changed facts acquired in an authorized path
append `observation_version + 1`. Historical known IDs at/before the Phase 1
watermark are not made subject to a new refresh obligation.

### D-04 — Checkpoint is optimization state

`collector_checkpoints` stores the last safe watermark/run only. It is never a
source completeness claim and cannot suppress an unknown source ID merely from
its publication time.

### D-05 — Filesystem publication precedes SQLite reference

A raw file is durable and hash-verified before its evidence row is committed.
Checkpoint and terminal success are committed only with the corresponding
metadata transaction. This is the minimal recoverable ordering across two
storage systems.

### D-06 — DataClean uses a stable export boundary

DataClean consumes committed observations through a versioned read-only export
envelope, not private SQLite tables. This minimizes coupling while keeping the
database internal to Collector storage.

## DEFERRED

### D-07 — Exact export serialization

JSONL/NDJSON is the default candidate, but field naming, envelope version and
batch manifest need the DataClean contract decision before implementation.

### D-08 — Backup/restore procedure

Portable backup of SQLite plus raw files is required before production use, but
the exact snapshot/restore and consistency tooling is not frozen here.

### D-09 — Retention and orphan garbage collection

No automatic deletion policy is authorized. Reconciliation rules are defined,
but retention duration and operator workflow remain open.

### D-10 — Multi-process access policy

The target is single-user. Whether a second process is rejected or merely
serialized by SQLite locking is deferred until runtime integration is scoped.

## REJECTED

### R-01 — Direct DataClean writes into Collector tables

Rejected because it couples DataClean to private schema and permits downstream
code to bypass append-only and lineage invariants.

### R-02 — Distributed/cloud infrastructure in the first store

Rejected: no Phase 1 evidence requires it; it would add operational failure
surface without a current contract benefit.

### R-03 — UPDATE-in-place latest-item table as the source of truth

Rejected because it destroys immutable observations, source drift evidence and
replayability. A future read model may derive a latest view, but not replace
the append-only observation table.

### R-04 — Checkpoint-only incremental filtering

Rejected because Phase 1 explicitly preserves unknown IDs at or before the
watermark as eligible.
