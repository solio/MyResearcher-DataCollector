# Raw Evidence Retention v0.1 — Developer Implementation

## Scope

This change is storage-only. Source collectors, source specifications, observation
schema, and batch behavior are unchanged.

## Delivered

- SQLite schema version 2 adds `raw_body_state(source, content_sha256, body_state,
  purged_at_utc, updated_at_utc)`.
- Existing v1 databases migrate in place. Existing evidence and observation rows
  remain immutable; every existing referenced body is initialized as `PRESENT`.
- Raw evidence metadata, evidence lineage, and observations remain permanent.
  Content-addressed bodies are eligible after the default seven-day retention
  window only when every reference is old and no running/failure/spec evidence
  protects the body.
- `raw-retention` is dry-run by default. Physical deletion requires `--confirm`;
  the state row is changed to `PURGED` only after the file deletion succeeds.
- Republishing the same content-addressed body restores `PRESENT` and clears the
  purge timestamp.
- Integrity checks verify path confinement, byte size, and SHA-256 before purge.
- Physical purge uses `<sha>.body.purging`: the body is atomically renamed,
  state is committed as `PURGED`, and only then is the tombstone removed. A
  failed state update rolls the tombstone back to `.body`; startup recovers
  PRESENT and PURGED tombstones conservatively.
- Publication rejects a `PublishedRaw` whose source differs from its collection
  run source before inserting either evidence or body-state rows.

## Verification coverage

RET-001 through RET-013 cover age, shared references, failure/spec retention,
purged/missing semantics, republish, dry-run behavior, migration, historical
observation drift/versioning, interrupted purge recovery, and source identity
validation. The full suite also preserves existing Eastmoney, Xueqiu,
persistence, and batch behavior.
