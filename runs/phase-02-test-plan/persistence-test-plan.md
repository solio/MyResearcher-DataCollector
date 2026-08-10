# Phase 2 Persistence Acceptance Test Plan

Plan status: `TEST_PLAN_READY`

Final planning state: `PERSISTENCE_TEST_PLAN_READY`

This is an independent Tester plan. It defines future acceptance work; it does
not execute tests or assess the in-progress persistence implementation.

## 1. Authority and independence

The sole normative persistence source is the corrected
`runs/phase-02-round-01/storage-contract.md`. Round 02 is used only to confirm
the already-closed safe-frontier and source-agnostic evidence corrections.

Planning baseline: committed `HEAD` `9d1b0e0` (`Phase 2 Round 02 — Storage
Contract Correction`). All repository inputs were read with `git show HEAD:...`.
The Developer's uncommitted storage files were identified only by path through
`git status --short`; their contents were not opened, searched, imported or
executed. No Developer scratch/report was read and no implementation choice was
used as an acceptance oracle.

Supporting frozen inputs:

- `AGENTS.md` and the mandatory project contracts;
- `specs/eastmoney_guba.md`;
- `docs/data-collector/runtime-contract.md`;
- `docs/data-collector/testing-contract.md`;
- `runs/phase-01-round-02/spec-implementation-traceability.md`;
- all committed artifacts in Phase 1 Rounds 06, 07 and 08;
- the Phase 2 Round 01 architecture, decision log and open questions;
- the Phase 2 Round 02 correction report and decision.

Contract priority remains:

```text
corrected frozen Storage Contract
    > current test-planning scope
    > frozen Eastmoney SOURCE_SPEC
    > implementation assumptions
```

## 2. Acceptance boundary

The matrix contains:

- 20 `MUST` scenarios (`PST-001` through `PST-020`);
- 1 blocking Phase 1 `REGRESSION` (`PST-021`);
- 1 `STRUCTURAL_INSPECTION` (`PST-022`);
- no `NON_BLOCKING` acceptance scenario.

A failing `MUST` or `REGRESSION` blocks persistence acceptance. The structural
inspection blocks only if committed code contains a demonstrable global
list/detail requirement that contradicts the corrected contract. A missing
generic-source injection surface does not itself block and does not require the
Developer to add future-source machinery.

The acceptance oracle is externally observable persisted state and behavior:

- the supported storage/migration entry points delivered by the Developer;
- read-only SQLite schema/history and row inspection in an isolated test store;
- raw-file path, bytes, SHA-256 and size inspection;
- reopen/read/integrity outcomes;
- terminal run, failure and checkpoint state;
- committed-code structural inspection only for `PST-022`.

Tests must not call implementation-private helpers merely to match the
Developer's structure. Faults are injected at documented storage boundaries
(filesystem operation, SQLite transaction/commit, or supplied test doubles),
without prescribing helper names.

## 3. Future deterministic fixture model

No fixture is created in this planning round. After the Developer commit, the
Tester will use only temporary synthetic stores and bytes:

- an empty temporary root for the official migration path;
- a correctly migrated store with a frozen schema fingerprint and seed rows;
- synthetic list/detail response bytes and a synthetic `other_approved` direct
  response if a generic public injection surface already exists;
- fixed UTC timestamps, caller-supplied UUID run IDs and stable ordinals;
- two requests returning identical bytes for physical-dedup testing;
- pre-created, missing and byte-mutated content-addressed raw files;
- injected failure points before raw publish and during SQLite finalization;
- canonical observation fact sequences `A`, `A`, `B`, `A`;
- checkpoint timelines with safe prefixes and explicit unresolved gaps.

All tests are offline. They must not use real Eastmoney, credentials, live
responses, production data, or a non-temporary persistence database.

## 4. Contract oracles

### Schema and migration

Fresh initialization must be performed only by the official migration path.
Acceptance verifies the eight contract relations, migration version/history,
primary/unique/not-null constraints, the specified foreign-key graph and index
coverage for the contract lookup paths. The contract does not freeze exact
CHECK expressions, migration table names or index names, so tests assert their
semantics rather than inventing names.

The important `(source, scope_key, published_at_utc)` lookup may be implemented
through the observation/scope join; acceptance requires effective declared
index coverage, not an impossible single-table index shape.

### Raw evidence

Raw response bytes are durable before SQLite references them. Final files are
complete lowercase SHA-256-addressed, immutable and no-clobber. Physical byte
deduplication never deduplicates request, attempt or evidence lineage. A
`.partial` path can never be committed as RawEvidence.

### Observations and evidence

Logical identity is `(source, source_item_id)`. Observations are immutable
versions; identical reacquisition adds lineage and associations without a new
version, while `A -> B -> A` persists versions `1, 2, 3`. Eastmoney acceptance
requires sufficient list and detail evidence. The generic storage invariant is
one or more source-appropriate, explicitly role-labelled evidence rows.

### Attempts, failures and IDs

Every request attempt survives retry and exhaustion. A body acquired before a
parse/schema failure remains RawEvidence. Run IDs are caller-owned UUIDs;
attempt, evidence and observation IDs are stable across process/reopen for the
same run plus ordinal and are never based on Python `hash()` or object identity.

### Checkpoints

Persistence validates but never computes the runtime safe frontier. Checkpoint
movement is transactional, cannot cross an unresolved gap, and is decided by
the supplied proven frontier rather than terminal status alone. A partial run
may advance to a proven safe prefix. Zero observations do not prove
`NO_NEW_DATA`, and a watermark never proves all older source IDs are known.

## 5. Planned realization after Developer commit

1. Pin the exact Developer commit and inspect its committed diff and handoff.
2. Map the delivered public migration/store boundaries to the implementation-
   independent actions in `acceptance-matrix.md`.
3. Implement deterministic pytest coverage for `PST-001` through `PST-021`.
4. Perform `PST-022` as structural inspection, adding the optional generic
   direct-response probe only if the delivered interface already permits it.
5. Run the persistence subset first, then the complete offline suite so Phase 1
   regressions remain protected.
6. Report `TEST_PASS` or `TEST_FAIL` with the pinned commit, commands, exits,
   row/file evidence and failing IDs. Do not repair production code while in
   the Tester role.

## 6. Explicit exclusions

This plan does not cover DataClean JSONL/NDJSON format or cleaning, sentiment,
LLMs, scheduler behavior, backups/restores, retention automation, automatic
orphan deletion, multi-process locking, PostgreSQL, object/cloud storage,
Kafka, Redis, performance/load, production deployment or live Eastmoney smoke.
The deferred Phase 2 questions do not block this persistence acceptance plan.

This planning round also creates no pytest file, migration, database or raw
fixture, and modifies no production code, frozen contract or SOURCE_SPEC.

## 7. Spec-block assessment and stop

No frozen-contract ambiguity was found that would change a planned PASS/FAIL
decision. Implementation-selected migration/index names and the possible lack
of a generic-source injection surface are handled with semantic assertions and
`STRUCTURAL_INSPECTION`, respectively; neither is a specification blocker.

`PERSISTENCE_TEST_PLAN_READY`
