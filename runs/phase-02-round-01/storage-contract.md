# Phase 2 Round 01 — Storage Contract

Status: `READY_FOR_REVIEW` — design only; no database, migration or production
persistence code is created in this round.

This document is the sole normative storage contract for this round. It
persists the Phase 1 invariants; it does not redefine the Eastmoney
`SOURCE_SPEC`.

## 1. Scope and terms

The first implementation target is a single-user, local-first store:

```text
SQLite metadata/index/state + local immutable raw-evidence files
```

SQLite is the authority for metadata, lineage, observations, failures and
checkpoint state. The filesystem is the authority for raw response bytes.

`source` is the approved source name (`eastmoney_guba`). `scope_key` identifies
the requested collection scope (for example `stock:600001`) and is not a
semantic stock classification.

All persisted timestamps are UTC RFC3339 strings with `Z`, or SQL `NULL` when
the source fact is unavailable. Original source time and source raw time text
remain separate fields/metadata.

## 2. CollectionRun

`CollectionRun` is one attempted Collector execution, including runs that
finish with no data or failure.

Required identity and lifecycle:

- `run_id`: immutable UUID string allocated once at run start; it is never a
  Python object identity or runtime hash. A retry/resume of the same execution
  reuses the caller's run ID; a new execution gets a new ID.
- `source`, `scope_key`: source and requested scope.
- `status`: `RUNNING`, `SUCCESS`, `NO_NEW_DATA`, `PARTIAL_COLLECTION`,
  `COLLECTION_FAILED`, `SPEC_MISMATCH` or `CANCELLED`.
- `started_at_utc`, `finished_at_utc`: finish is nullable while running.
- `collector_version`, `parser_version`, `schema_version`.
- `watermark_before_utc`, `watermark_after_utc`: checkpoint values observed
  before and after the run; both are nullable.
- counters from the Phase 1 runtime result, stored as explicit non-negative
  integers rather than inferred from child rows.
- `failure_count`/failure relationships through `collection_failures` and
  `collection_attempts`.

ID generation is explicit: `run_id` is a persisted UUID allocated by the run
boundary; `attempt_id`, `evidence_id` and `observation_id` are SHA-256 hex
identifiers over a versioned label plus `run_id` and their stable ordinal (for
example `evidence-v1\n<run_id>\n<attempt_ordinal>`). The same resumed execution
therefore regenerates the same child IDs, while no ID depends on Python's
process hash or object address.

The terminal status is written only after all required evidence, observations,
failures and any eligible checkpoint change are committed.
`NO_NEW_DATA` is never inferred from zero child rows alone.

## 3. RawEvidence

Each acquired HTTP response has one immutable `RawEvidence` metadata row, even
when parsing later fails. A failed response body, when present, is still
evidence.

Fields:

- `evidence_id`: deterministic-format UUID/hex string allocated from the run
  and request-attempt ordinal; never content hash alone.
- `run_id`, `attempt_id`: required lineage to the run and actual request.
- `evidence_kind`: `list`, `detail` or `other_approved`.
- `request_url`, `final_url`: final URL nullable only when no response URL was
  established; redirect policy failures retain the attempted URL and failure.
- `fetched_at_utc`, `http_status`, `content_type` (nullable),
  `content_sha256`, `byte_size`, `filesystem_path`.
- `created_at_utc` and storage-format version.

`content_sha256` identifies bytes and permits physical content-addressed
deduplication. It does **not** identify a request: two requests with identical
bytes retain two evidence rows and two attempt/run lineages.

Raw bytes are append-only. A referenced file is never edited in place or
replaced with a later response.

## 4. SourceItemObservation

An observation is an accepted, actually acquired source-item snapshot. It is
append-only and must be linked to one or more supporting RawEvidence rows that
are sufficient under the applicable source specification.

Logical identity is `(source, source_item_id)`; the Phase 1 source identity is
not split by requested bar. `scope_key` is a requested-scope association kept
in `observation_scopes`, and one observation can be associated with multiple
bars. `observation_version` is a monotonic integer per source-item identity.

Persisted facts include:

- `observation_id`: immutable UUID/hex allocated from the run and observation
  ordinal; no Python hash/object identity.
- `source`, `source_item_id`, `observation_version`.
- `observed_at_utc`/`collected_at_utc`, `published_at_utc`,
  `source_updated_at_utc` and nullable `display_time_utc`.
- nullable `author_id`, `author_name`, `title`; `content` for accepted detail
  observations; nullable engagement counts where source data is missing.
- `url`, canonical bar values, `post_type`, `post_state` and `post_top_status`
  as source facts, not quality labels. Requested-bar association is stored in
  `observation_scopes`.
- `content_sha256` (of normalized source content when present),
  `source_times_raw_json`, `source_metadata_json`, `schema_version`,
  `collector_version`, `parser_version`.

### Observation creation and idempotency

Within a run, an identical overlap is linked to existing lineage and does not
create a second observation. Across runs, compare the canonical source-fact
fingerprint with the latest observation for the same logical identity:

- same fingerprint: no new observation row; add run/evidence lineage;
- changed source/detail facts during an authorized acquisition: append a new
  row with `observation_version + 1` and drift metadata;
- a later reappearance after a change is a new observation event, not a reuse
  of an old historical row.

`UPDATE` and `DELETE` of observations are forbidden. Corrections are new
observations with explicit drift/correction metadata. A known item at or
before the Phase 1 watermark is not required to be detail-fetched solely to
discover historical changes; this storage contract does not add refresh work.

`observation_evidence(observation_id, evidence_id, evidence_role)` is a join
table. Its roles are explicit and versioned, and the linked evidence set must
satisfy the applicable SOURCE_SPEC. For `eastmoney_guba` Phase 1, accepted
observations retain the required list and detail evidence roles; a future
source with one direct item response need not fabricate a list role.
`observation_scopes(observation_id, scope_key, requested_bar_code)` records
every requested-bar association without mutating the observation when another
bar sees the same source item.

## 5. CollectionAttempt and CollectionFailure

`collection_attempts` records every list/detail request attempt, including
retries. It is the minimal event abstraction for transport lineage:

- `attempt_id`, `run_id`, sequential `attempt_ordinal`;
- `request_kind` (`list`/`detail`), `request_url`, `started_at_utc`,
  `finished_at_utc`;
- `outcome` (`success`, `http_error`, `timeout`, `transport_error`,
  `redirect_rejected`, `cancelled`), nullable `http_status`, `retry_after`;
- `retry_number`, `retry_budget`, and sanitized error class/message.

`collection_failures` records terminal or parse-side failures that are not
themselves HTTP attempts:

- `failure_id`, `run_id`, nullable `attempt_id`, nullable `evidence_id`;
- `phase` (`list`, `detail`, `parse`, `schema`, `run`),
  `failure_class` (`timeout`, `rate_limit`, `http_error`,
  `redirect_boundary`, `parser_failure`, `schema_mismatch`,
  `retry_exhaustion`, `cancelled` or `other_explicit`),
  `occurred_at_utc`, sanitized `message`.

This preserves the distinction between a 429 attempt, retry exhaustion and a
schema failure after a body was stored. Credentials, cookies and raw secrets
are forbidden in messages.

## 6. CollectorCheckpoint

One row per `(source, scope_key)` stores optimization state:

- `watermark_utc` (nullable),
- `last_safe_run_id` (nullable),
- `updated_at_utc`.

The pair is unique. A checkpoint is not source truth and cannot establish that
all earlier source IDs exist. Eligibility must still consult stored source
identity/observations: an unknown ID remains eligible even when its valid
publication time is at or before the checkpoint watermark.

Checkpoint advancement is allowed only in the same SQLite transaction that
commits all required observations, raw evidence, attempt/failure lineage and
metadata through a runtime-declared `safe_frontier`. The terminal status name
alone does not decide advancement. In particular, `PARTIAL_COLLECTION` MAY
advance conditionally when the Collector has produced a proven safe contiguous
frontier and persistence has committed everything required before it.

Persistence MUST NOT compute or reinterpret that frontier. It validates only
that the runtime supplied one, that no unresolved gap crosses it, and that all
required records before it are committed. An acquisition failure, unresolved
eligible item, parse failure preventing accepted persistence, persistence
failure, identity ambiguity or source/spec mismatch crossing the frontier
blocks advancement. If those checks fail, the prior checkpoint remains
unchanged.

## 7. Storage invariants

1. **Immutable raw evidence:** published bytes and evidence metadata are
   append-only. A hash/path mismatch is a hard storage error.
2. **Immutable observations:** no in-place UPDATE/DELETE; versions and drift
   are appended.
3. **Traceability:** every observation has sufficient supporting RawEvidence
   lineage under its applicable SOURCE_SPEC; every evidence row has a run and
   request-attempt lineage. Evidence roles are not globally fixed to
   list/detail.
4. **Deterministic identity:** UUID/hex IDs are explicit persisted values;
   content hashes use SHA-256 over bytes/canonical fields. No unstable Python
   `hash()` or object identity is used.
5. **Failure != empty:** terminal status and failure rows distinguish no data,
   partial acquisition, failed acquisition, parse/schema failure and cancel.
6. **Checkpoint safety:** checkpoint movement is conditional on a runtime-
   declared proven safe contiguous frontier and complete persistence before it;
   `PARTIAL_COLLECTION` is neither an automatic advance nor an automatic ban.
7. **Source semantics:** missing remains `NULL`, numeric zero remains zero,
   and source publication/update/observation times are not substituted.

## 8. Atomic publish and recovery

The filesystem and SQLite cannot share one atomic transaction, so publishing is
ordered and recoverable:

1. Start/record `RUNNING` run in SQLite.
2. Write each response to a temporary file in the same raw directory; flush,
   `fsync`, validate byte size/SHA-256, and atomically rename with no-clobber
   semantics to its content-addressed final path. `fsync` the directory.
3. In one SQLite transaction insert attempts, raw-evidence rows, observations,
   failures and (only when the runtime-declared safe frontier passes storage
   validation) the checkpoint and terminal run status.
4. Commit. Only a committed terminal run may be reported as `SUCCESS` or
   `NO_NEW_DATA`.

If the process stops before step 3, an unreferenced raw file is an orphan and
the run is not successful; reconciliation may quarantine/delete only verified
unreferenced files. If a referenced file is missing or has a different hash,
the store fails closed and never reports success. Temporary files are never
referenced and may be cleaned after an explicit age/reconciliation check.

## 9. Local file layout

The minimum portable layout is:

```text
data/
  collector.db
  raw/
    eastmoney_guba/
      <sha256>.body
    .tmp/
      <run_id>-<ordinal>.partial
```

The final raw path is content-addressed by the complete lowercase SHA-256.
Temporary files remain under `raw/.tmp` on the same filesystem so the publish
rename is atomic. A final path that already exists is never overwritten: its
bytes are re-hashed and reused only if the hash and size match; otherwise the
run fails closed. A crash may leave `.partial` files or unreferenced final
content; reconciliation may quarantine only files proven unreferenced and
must never delete a referenced evidence path.

## 10. Recommended SQLite schema (design only)

The following names are contract-level proposals, not an applied migration.
All tables use text IDs and UTC text timestamps; foreign keys are enabled for
every connection.

```sql
collection_runs(
  run_id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  scope_key TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at_utc TEXT NOT NULL,
  finished_at_utc TEXT,
  collector_version TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  watermark_before_utc TEXT,
  watermark_after_utc TEXT,
  counters_json TEXT NOT NULL,
)

collection_attempts(
  attempt_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
  attempt_ordinal INTEGER NOT NULL,
  request_kind TEXT NOT NULL,
  request_url TEXT NOT NULL,
  started_at_utc TEXT NOT NULL,
  finished_at_utc TEXT,
  outcome TEXT NOT NULL,
  http_status INTEGER,
  retry_after_seconds REAL,
  retry_number INTEGER NOT NULL,
  retry_budget INTEGER NOT NULL,
  error_class TEXT,
  error_message TEXT,
  UNIQUE(run_id, attempt_ordinal)
)

raw_evidence(
  evidence_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
  attempt_id TEXT NOT NULL REFERENCES collection_attempts(attempt_id),
  evidence_kind TEXT NOT NULL,
  request_url TEXT NOT NULL,
  final_url TEXT,
  fetched_at_utc TEXT NOT NULL,
  http_status INTEGER,
  content_type TEXT,
  content_sha256 TEXT NOT NULL,
  byte_size INTEGER NOT NULL,
  filesystem_path TEXT NOT NULL,
  storage_version TEXT NOT NULL
)

source_item_observations(
  observation_id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  source_item_id TEXT NOT NULL,
  observation_version INTEGER NOT NULL,
  observed_at_utc TEXT NOT NULL,
  published_at_utc TEXT NOT NULL,
  source_updated_at_utc TEXT,
  display_time_utc TEXT,
  author_id TEXT,
  author_name TEXT,
  title TEXT,
  content TEXT NOT NULL,
  content_sha256 TEXT,
  url TEXT NOT NULL,
  canonical_bar_code TEXT,
  canonical_bar_name TEXT,
  post_type INTEGER NOT NULL,
  post_state INTEGER,
  post_top_status INTEGER,
  read_count INTEGER,
  reply_count INTEGER,
  like_count INTEGER,
  forward_count INTEGER,
  source_times_raw_json TEXT NOT NULL,
  source_metadata_json TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  collector_version TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  drift_from_observation_id TEXT,
  UNIQUE(source, source_item_id, observation_version),
  FOREIGN KEY(drift_from_observation_id) REFERENCES source_item_observations(observation_id)
)

observation_evidence(
  observation_id TEXT NOT NULL REFERENCES source_item_observations(observation_id),
  evidence_id TEXT NOT NULL REFERENCES raw_evidence(evidence_id),
  evidence_role TEXT NOT NULL,
  PRIMARY KEY(observation_id, evidence_id, evidence_role)
)

observation_scopes(
  observation_id TEXT NOT NULL REFERENCES source_item_observations(observation_id),
  scope_key TEXT NOT NULL,
  requested_bar_code TEXT NOT NULL,
  PRIMARY KEY(observation_id, scope_key)
)

collection_failures(
  failure_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
  attempt_id TEXT REFERENCES collection_attempts(attempt_id),
  evidence_id TEXT REFERENCES raw_evidence(evidence_id),
  phase TEXT NOT NULL,
  failure_class TEXT NOT NULL,
  occurred_at_utc TEXT NOT NULL,
  message TEXT NOT NULL
)

collector_checkpoints(
  source TEXT NOT NULL,
  scope_key TEXT NOT NULL,
  watermark_utc TEXT,
  last_safe_run_id TEXT REFERENCES collection_runs(run_id),
  updated_at_utc TEXT NOT NULL,
  PRIMARY KEY(source, scope_key)
)
```

Important indexes: `(source, source_item_id, observation_version)`,
`(run_id, request_kind, attempt_ordinal)`, `(content_sha256)`,
`(source, scope_key, published_at_utc)` and failure `(run_id, failure_class)`.

The exact CHECK expressions, timestamp validator and migration sequencing are
implementation work for a later approved round, not this contract design.

## 11. DataClean boundary

The public boundary is a versioned, read-only Collector export envelope built
from committed observations. DataClean must not depend on private SQLite table
names and must receive `raw_ref`/evidence identifiers and source provenance
alongside raw fields. The initial export is local JSONL/NDJSON or an equivalent
stable file envelope; its exact serialization is an open question below.

The SQLite database remains the Collector's internal source of truth. Direct
DataClean writes to the database and direct mutation of raw files are forbidden.
An export may include only committed observations and explicit lineage; it must
never turn a failed/partial run into accepted records.

## 12. Explicit non-goals

No cleaning, sentiment, LLM scoring, author aggregation, scheduler,
distributed/cloud storage, Kafka/Redis, dashboard, multi-tenant, HA database,
retention automation or production deployment topology is defined here.
