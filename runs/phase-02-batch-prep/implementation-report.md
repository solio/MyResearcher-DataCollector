# Phase 2 Minimal Batch Preparation Implementation Report

## Boundary

`myresearcher_collector.batch.BatchRunner` receives a validated static target
set and invokes one injected single-stock runner at a time, preserving target
order. `execute_batch_collection` is the production adapter: each iteration
calls the existing `execute_and_persist_collection` boundary with the shared
approved data layout and the stock-specific scope supplied by that boundary.

The batch layer does not parse source responses, compute watermarks or touch
SQLite tables. It converts each single-stock persisted report into a batch
summary and records failures without inventing a second runtime status model.

## Target format

The first version uses stdlib JSON, equivalent to the approved simple YAML/TOML
option:

```json
{"source":"eastmoney_guba","stocks":["600519","300750","002594"]}
```

`config/targets.example.json` is a non-secret example. Targets are validated
before any runner or transport is constructed; exact duplicates are removed
while first-seen order is retained.

## Failure and isolation policy

`SUCCESS` and `NO_NEW_DATA` count as successful targets;
`PARTIAL_COLLECTION` is counted separately. Ordinary per-stock runner errors
become a recorded `COLLECTION_FAILED` result and the next target still runs.
`SPEC_MISMATCH`, `CANCELLED` and persistence/schema errors stop the batch with
an explicit `stop_reason`. Each outcome retains its stock code, run ID, records
accepted and checkpoint before/after values from the single-stock authority.

## CLI

```text
python -m myresearcher_collector.cli collect-batch \
  --targets config/targets.example.json \
  --data-dir /tmp/collector-data --plan-only
```

Plan output states source, ordered deduplicated stocks, stock count,
`execution=sequential`, data root and `network_execution=false`. Actual CLI
execution requires explicit `--confirm-live`; no live batch was run in this
round.

## Files changed

- `src/myresearcher_collector/batch.py`
- `src/myresearcher_collector/__init__.py`
- `src/myresearcher_collector/cli/main.py`
- `config/targets.example.json`
- `tests/unit/test_batch.py`
- `runs/phase-02-batch-prep/*`

No Collector, Persistence schema, Storage Contract, SOURCE_SPEC, bootstrap
semantics or live-smoke artifact was changed.
