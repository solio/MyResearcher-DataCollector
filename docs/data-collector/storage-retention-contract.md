# Raw Evidence Retention v0.1

`RawEvidence` metadata is permanent and immutable. This includes request and
final URLs, collection time, HTTP metadata, SHA-256, byte size, filesystem
lineage, run/attempt/failure lineage, and observation links.

Physical raw response bodies are immutable while present but retention-based.
The default `RAW_BODY_RETENTION_DAYS` is **7 days**. Body state is tracked per
`(source, content_sha256)` in `raw_body_state` as `PRESENT` or `PURGED`.

Normal `SUCCESS`, `NO_NEW_DATA`, and failure-free `PARTIAL_COLLECTION` bodies
may become eligible after the retention window. Bodies referenced by a running
run, a `COLLECTION_FAILED`/`SPEC_MISMATCH` run, or an explicit
`collection_failures` row are not auto-purged in v0.1.

`raw-retention` defaults to a dry run. Physical deletion requires `--confirm`.
Deletion succeeds before state changes to `PURGED`; a deletion error leaves the
state `PRESENT`. A `PRESENT` body that is unexpectedly missing remains an
integrity failure. A `PURGED` body is reported as intentionally expired rather
than corruption, while its permanent metadata and observation lineage remain.

Schema v1 databases migrate in place to v2. Existing runs, evidence,
observations, checkpoints, and physical bodies are preserved and initialized as
`PRESENT`.
