# Phase 2 Minimal Batch — Independent Execution Evidence

## Baseline and method

Tested exact commit:

```text
80aa943 Minimal Batch Collection Preparation
```

The commit was checked out in a clean detached temporary worktree. No
production code or Developer files were changed by the Tester. All collection
checks used synthetic in-memory HTTP responses and temporary persistence
directories.

## Target validation

Independent checks passed for:

- `source=eastmoney_guba` as the only accepted source;
- six-digit string enforcement;
- invalid source, invalid length, non-string target, non-list targets and
  empty target set rejected as `BatchConfigError`;
- invalid inputs rejected before any runner invocation;
- exact duplicate removal with first-seen order preserved:
  `600519, 300750, 600519, 002594` →
  `('600519', '300750', '002594')`.

Observed result: `TARGET_VALIDATION=PASS` and `TARGET_ORDER_DEDUP=PASS`.

## Sequential execution and stop policy

The committed BatchRunner has a direct target-order loop and no thread,
async, multiprocessing or parallel executor path. Independent runner checks
observed:

- ordinary `B` runner failure: `A → B → C` all invoked, two successes and one
  recorded `COLLECTION_FAILED`, `stop_reason=None`;
- `SPEC_MISMATCH`: stops after the affected target with
  `spec_mismatch:300750`;
- `CANCELLED`: stops after the affected target with
  `cancelled:300750`;
- `PersistenceError`, `SchemaError`, `RawStoreError` and SQLite database
  errors: each records `global_persistence_error:300750` and stops before the
  following target.

All these checks passed. The committed unit module also passed its six tests.

## Real persistence isolation

This check called the real `execute_batch_collection` with a deterministic
transport factory, not stubbed `SingleStockOutcome` values. Both targets used
the same temporary `collector.db` and raw-data root:

```text
summary.targets_total=2
summary.targets_completed=2
summary.targets_success=2
summary.targets_failed=0
summary.stop_reason=None
transport_order=['600519', '300750']
transport_calls={'600519': 3, '300750': 3}
```

Persisted runs:

```text
batch-real-isolation-600519 → scope stock:600519 → SUCCESS
batch-real-isolation-300750 → scope stock:300750 → SUCCESS
```

Persisted observations and checkpoints remained isolated:

```text
observations: 60051901 → stock:600519; 30075001 → stock:300750
checkpoint: stock:600519 → ...02:00:00Z, last_safe_run_id=batch-real-isolation-600519
checkpoint: stock:300750 → ...03:00:00Z, last_safe_run_id=batch-real-isolation-300750
raw evidence: 3 rows per run, grouped by its own run_id
```

The batch summary's two per-stock results matched the persisted run IDs,
statuses, accepted-record counts and checkpoint-after values. The transport
active-call guard observed no overlap.

## Plan-only

Command:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m myresearcher_collector.cli \
  collect-batch --targets config/targets.example.json \
  --data-dir /private/tmp/myresearcher-batch-plan.1aQdKY --plan-only
```

Output reported:

```text
source=eastmoney_guba
stocks=['600519', '300750', '002594']
stock_count=3
execution=sequential
network_execution=false
```

The target data directory remained empty: no transport was constructed and no
`collector.db` or `raw` directory was created.

## Regression

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/unit/test_batch.py -q
6 passed

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q
97 passed, 1 xfailed
exit 0
```

The existing xfail is unrelated to Batch orchestration. No network, live
transport, credentials or `--confirm-live` was used.
