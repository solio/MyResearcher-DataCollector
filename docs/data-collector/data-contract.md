# Data Contract

## 1. Status and evidence

Contract status: **PARTIALLY_FROZEN**.

The acquisition invariants in section 2 are frozen by the Phase 0 task. The candidate field envelope in section 3 is `PROVISIONAL`, because the accessible MyResearcher-DataClean project is still in bootstrap and explicitly has no confirmed input schema or implementation.

Evidence checked:

- separately accessible legacy MyResearcher record dictionaries and `news_items` persistence;
- accessible DataClean `docs/state/`, capability ledger and `docs/knowledge/data.md`;
- Phase 0 task contract.

No runtime database or real output data was read.

## 2. Frozen acquisition invariants

- A record represents acquired source material, not cleaned or semantically classified material.
- Collection failure, parsing failure, partial success and no new data are distinct outcomes.
- Source provenance and collection time must be retained.
- Original publish time must never be silently substituted with update or collection time.
- Missing data remains missing; it is not silently converted to zero, empty text, a generated author, or an invented timestamp.
- Record and parser/schema versions must make replay behavior identifiable.
- Source identity rules belong to the approved SOURCE_SPEC and must be evidence-backed.
- Raw evidence must remain replayable through retained raw content or a durable `raw_ref`; the exact retention mechanism is not yet frozen.

Evidence level: `CONFIRMED — task contract`, consistent with `PROVISIONAL` DataClean principles.

## 3. Provisional minimum raw envelope

The table is a proposal for Phase 1 contract negotiation, not a final approved schema.

| Candidate field | Candidate type | Required / nullable | Proposed semantic meaning | Status |
|---|---|---|---|---|
| `schema_version` | string | required, non-null | version of this Collector output envelope | PROVISIONAL |
| `source` | string | required, non-null | canonical source name defined by SOURCE_SPEC | PROVISIONAL |
| `source_item_id` | string | conditionally required, nullable pending identity policy | source-provided stable item identity; never a guessed hash unless specified | OPEN QUESTION |
| `stock_code` | string | optional, nullable | source-explicit stock association only; not investment classification | OPEN QUESTION |
| `author_id` | string | optional, nullable | source-provided author identity | PROVISIONAL |
| `author_name` | string | optional, nullable | source display name; not a substitute for `author_id` | PROVISIONAL |
| `title` | string | optional, nullable | source title as acquired | PROVISIONAL |
| `content` | string | optional, nullable | source body as acquired or structurally extracted | PROVISIONAL |
| `published_at` | timestamp/string | optional, nullable | original source publication time only | PROVISIONAL |
| `collected_at` | UTC timestamp | required, non-null | Collector observation/acquisition time | PROVISIONAL |
| `url` | string | optional, nullable | canonical or observed item URL; identity role source-specific | PROVISIONAL |
| `source_metadata` | object | optional, nullable | source-specific fields not promoted into the common envelope | PROVISIONAL |
| `raw_ref` | string/object | conditionally required | replayable reference to retained raw evidence | OPEN QUESTION |

## 4. Time contract

- `published_at`: original publish time, if the source exposes it and its semantics are verified.
- update time, observation time and collection time are different facts and must use separate fields if retained.
- `collected_at`: UTC instant produced by Collector.
- timezone conversion requires SOURCE_SPEC evidence; an unknown timezone must remain unresolved rather than assumed.
- serialization format and precision remain open until DataClean and the first source contract agree.

## 5. Identity contract

- `(source, source_item_id)` is a candidate stable identity only where the SOURCE_SPEC documents the source guarantee.
- URL uniqueness observed in the legacy repository is not promoted into the new contract without source evidence.
- missing author ID cannot be replaced by a hash of author name.
- fallback item identity, collision behavior and versioning must be explicitly approved per source.

## 6. Versioning contract

Every output must identify its envelope schema version. Phase 1 must decide whether parser/collector/identity versions are separate top-level fields or structured provenance. A semantic field change requires a version change and fixture migration evidence; silent reinterpretation is forbidden.

## 7. DataClean compatibility status

The accessible DataClean project confirms only provisional needs for immutable/replayable RAW data and provenance including source, author, timestamp and processing/rule versions. It currently exposes no confirmed input entry point, executable schema, required-field list, identity assumption, timezone format or raw-reference transport.

Therefore Collector → DataClean compatibility is `OPEN QUESTION`, not confirmed.

## 8. Non-goals

This contract does not define CleanRecord, deduplication, quality eligibility, sentiment, stance, finance labels, aggregation or trading signals.
