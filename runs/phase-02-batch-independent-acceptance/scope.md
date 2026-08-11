# Phase 2 Minimal Batch — Independent Acceptance Scope

## Role

Independent Tester

## Tested object

- Branch: `phase2-minimal-batch`
- Developer commit: `80aa943`
  (`Minimal Batch Collection Preparation`)
- The exact commit was tested in a clean detached temporary worktree. The
  named branch worktree had separate uncommitted bootstrap-alignment edits;
  those edits were not included in this evidence and were not modified.

## Goal

Verify that the Batch layer validates a static target set, orchestrates the
existing single-stock persistent boundary sequentially, reports per-stock
outcomes and summarizes the batch without reimplementing Collector,
watermark, checkpoint, Persistence or source parsing behavior.

## Required checks

- source and six-digit target validation, stable first-seen deduplication and
  empty-set rejection before runner/network construction;
- strict sequential order;
- ordinary failure continuation and explicit global stop behavior;
- real `execute_batch_collection` persistence isolation for `600519` and
  `300750` on one SQLite/raw-data root;
- `collect-batch --plan-only` with no transport, SQLite or raw-directory
  creation;
- no live network and no `--confirm-live`.

## Explicit alignment note

Commit `80aa943` predates the Reviewer Bootstrap Contract. Its batch CLI keeps
`--max-pages` default `2`; the frozen bootstrap minimum is `3`. This historical
branch/main timing difference is recorded as `MERGE_ALIGNMENT_REQUIRED`, not a
Batch orchestration defect. After bootstrap main is ready, reconcile the batch
branch and run final regression before merge.
