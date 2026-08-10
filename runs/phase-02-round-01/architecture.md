# Phase 2 Round 01 — Storage Architecture

## Approved shape for review

```text
Eastmoney Collector
        │  accepted observations + request outcomes
        ▼
Persistence boundary
  ├─ SQLite: runs, attempts, evidence index, observations, failures,
  │          checkpoint state
  └─ local filesystem: immutable raw HTTP response bytes
        │
        ▼
Versioned read-only Collector export envelope
        │
        ▼
DataClean
```

This is a local-first design for one user and one source at a time. SQLite is
not exposed as DataClean's stable API; the export boundary prevents DataClean
from coupling to private table names. The export carries source identity,
observation facts, versions and replayable raw references.

## Publish/recovery path

Raw response bytes are first written and fsynced to a same-directory temporary
file, then atomically published to a content-addressed path with no-clobber
semantics. Only after that succeeds does a SQLite transaction commit the
evidence index, observation lineage, failures and safe checkpoint/run status.
An unreferenced published file is recoverable orphan state; a referenced
missing or hash-mismatched file is a hard integrity failure.

## Why no larger infrastructure

The Phase 1 product is single-user, offline-friendly and local. No evidence
requires PostgreSQL/MySQL, object storage, Kafka, Redis, a data lake or a
distributed coordination service. Adding one would increase failure and
deployment surface without satisfying a frozen requirement.

## Responsibility boundary

Collector owns source requests, parsing, source semantics and runtime outcome.
Persistence owns append-only storage, lineage, atomic publication and safe
checkpoint commit. Collector runtime declares any safe contiguous frontier;
Persistence validates and atomically commits it but does not recompute page
order, item eligibility or source completeness. DataClean owns cleaning and
downstream interpretation. No layer performs sentiment, finance or trading
decisions here.
