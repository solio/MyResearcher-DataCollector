# Phase 2 Persistence Risk Priorities

Priority reflects irreversible correctness risk, not scenario count or likely
implementation effort. The matrix has 14 P0, 7 P1 and 1 P2 rows.

## P0 — irreversible loss, corruption or unsafe progress (14)

| IDs | Dominant risk | Blocking evidence |
| --- | ------------- | ----------------- |
| PST-004 | Broken foreign keys permit lineage that can never be replayed. | Any orphan lineage commit or disabled FK connection. |
| PST-005, PST-007, PST-008 | Raw bytes are mispublished, overwritten, or referenced before durability. | Wrong bytes/hash/path, overwrite, `.partial` reference or false terminal success. |
| PST-009 | SQLite finalization partially commits across evidence, observation, failure, checkpoint and run state. | Any half graph, advanced checkpoint with missing required state, or false success/no-data. |
| PST-010 | A referenced raw file is missing/corrupt but reads as empty or valid. | Any silent read, success report or failure to detect hash/size mismatch. |
| PST-013 | Immutable observation history is overwritten or an old version is reused after reappearance. | Missing `A -> B -> A` version, mutated v1, UPDATE/DELETE effect or v3 aliasing v1. |
| PST-015, PST-016 | Retry/parse/schema failure becomes no-data, loses acquired evidence or advances state. | Missing failure/attempt/body lineage, fabricated observation, `NO_NEW_DATA`, or checkpoint movement. |
| PST-017, PST-018, PST-019, PST-020 | Checkpoint advances without a proven safe contiguous frontier or refuses a valid partial prefix. | Movement beyond a gap, status-only movement, zero-row no-data inference, non-atomic advance, or failure to advance exactly to a valid partial frontier. |
| PST-021 | Watermark is treated as completeness proof and suppresses an unknown old item. | Unknown X is skipped solely because `published_at <= watermark`. |

P0 execution order after migration smoke should be: raw integrity/atomicity,
observation immutability, checkpoint matrix, then the protected Phase 1
integration regression. Stop on the first result that risks further mutation of
the same temporary store; preserve its complete file/row evidence.

## P1 — replay, lineage and schema trust (7)

| IDs | Dominant risk | Blocking evidence |
| --- | ------------- | ----------------- |
| PST-001, PST-002, PST-003 | Official migration cannot establish/reopen one identifiable schema or accepts drift. | Missing contract relation/constraint/index/history, repeat migration/data change, or fail-open drift repair. |
| PST-006 | Physical content dedup erases request/evidence history. | Fewer than two attempt/evidence lineages for two requests, or content hash used as evidence identity. |
| PST-011 | An accepted Eastmoney observation lacks source-required support or substitutes missing/time facts. | Missing list/detail lineage, broken parent chain, `NULL` converted to zero, or time facts copied into each other. |
| PST-014 | Retry history disappears or persisted IDs vary by process/runtime state. | Missing attempts, support linked to the wrong attempt, unstable/colliding IDs, or runtime-hash/object-derived identity. |
| PST-022 | Storage hard-codes Eastmoney's list/detail topology as a global invariant. | A demonstrable unconditional global requirement. Absence of a generic test surface alone is non-failing. |

`PST-022` is counted as P1 because an explicit contradiction would corrupt
future-source provenance semantics, but its classification remains
`STRUCTURAL_INSPECTION`.

## P2 — idempotency and association semantics (1)

| IDs | Dominant risk | Blocking evidence |
| --- | ------------- | ----------------- |
| PST-012 | Identical reacquisition creates false versions, loses new lineage, or splits identity by requested scope. | Version 2 for identical facts, mutation of v1, missing new run/evidence, duplicate/missing scope association, or scope-dependent logical identity. |

P2 is still frozen-contract acceptance and therefore blocking. Its lower
priority means the failure is less immediately destructive than checkpoint or
raw-data corruption, not that it is optional.

## Failure classification rules

- A `MUST` or `REGRESSION` mismatch is `TEST_FAIL` and blocks acceptance.
- A `STRUCTURAL_INSPECTION` blocks only with concrete committed evidence of a
  contract violation.
- Infrastructure outside the frozen local SQLite/filesystem target is not a
  workaround and is not requested by this plan.
- Backup, retention, automatic orphan cleanup, concurrency, export format,
  performance and live-source behavior remain explicitly non-blocking/out of
  scope; they must not be promoted into an acceptance failure.
