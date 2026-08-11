# Eastmoney Backfill Live Round 01 — Inspection

Inspection used the same data directory:

```text
/Users/mac/Documents/trae_projects/MyResearcher/MyResearcher-DataCollector/data/live-backfill-eastmoney-601012
```

The repository `inspect-run` command was used with run ID
`3a714f1bbb364565839384eae6c76596`, followed by SQLite URI `mode=ro` with
`PRAGMA query_only=ON`. No database mutation was performed.

## Run summary

```text
run_id: 3a714f1bbb364565839384eae6c76596
source: eastmoney_guba
stock: 601012
status: SPEC_MISMATCH
stop_reason: schema_mismatch
range_complete: false
pages_requested: 1
pages_success: 0
pages_failed: 1
requests_total: 1
requests_success: 1
requests_failed: 0
records_received: 0
records_accepted: 0
records_failed: 1
details_requested: 0
details_success: 0
details_failed: 0
first_published_at: NULL
last_published_at: NULL
```

## Persistence and lineage

```text
CollectionRun: 1
CollectionAttempt: 1
RawEvidence metadata: 1
RawEvidence body files: 1
SourceItemObservation: 0
CollectionFailure: 1
failure/evidence lineage: YES
checkpoint rows: 0
```

The persisted list attempt was the approved URL for page 1 and HTTP 200. The
RawEvidence metadata records content type `text/html; charset=utf-8`, byte size
2834, and SHA-256
`d9bc3154679106ea3fe92b69042d743009a13a75813d660405fc2868a6174f5a`; the body
is retained under the isolated data directory. The failure row links the run,
attempt and evidence and records `schema_mismatch`.

## Checkpoint hard gate

```text
checkpoint_before: NULL
checkpoint_after: NULL
checkpoint_equal: YES
checkpoint_updated: false
safe_frontier: NULL
```

The required Backfill invariant held. No SourceItemObservation was expected
after the first page failed schema validation, and no checkpoint was created
or advanced.
