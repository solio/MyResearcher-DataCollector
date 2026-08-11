# Phase 2 Round 05 Correction — Partial Safe Frontier Integration

## Baseline

The Round 05 implementation commit named by the review is
`0ee7766677049aa6c552980b7f76f67438c98cf6`. At execution time the checked-out
HEAD was `014c16667b25e2509fbbd7d997484919989a6e65`, which adds the independent
acceptance-test commit on top; those Tester files were not modified. An
untracked `review.patch` was present and was left untouched.

## Previous defect

The integration `_safe_frontier(result)` used `result.watermark` for complete
outcomes but converted every `PARTIAL_COLLECTION` into
`SafeFrontier(all_required_persisted=False, unresolved_gaps=...)`. Therefore a
partial run could never pass a runtime-proven safe contiguous prefix to
persistence, even when all work through that prefix was complete.

## Corrected semantics

`CollectionResult` now carries an explicit `safe_frontier` declaration. The
Eastmoney runtime declares a frontier for `SUCCESS`/verified `NO_NEW_DATA`, and
for the narrow partial case where a previously completed page prefix is safe
and a later detail request fails. It does not declare one for a failed page,
coverage cap, schema failure, or a partial run with no completed safe prefix.

The runtime tracks the latest fully completed page prefix. Integration now
translates only `result.safe_frontier` into `SafeFrontier(result.safe_frontier)`;
it never derives a timestamp from `result.items`, failure strings, page counts,
or observation counts. Persistence remains responsible for validating and
atomically committing the declaration, including Round 04 monotonicity.

Thus:

```text
runtime proven frontier → integration translates → persistence validates/commits
no proven frontier       → integration passes none → checkpoint unchanged
```

## Responsibility boundary

- Collector runtime owns pagination, item eligibility, source completeness and
  safe contiguous-frontier proof.
- Integration owns attempt/raw/failure/observation mapping and translation of
  the runtime declaration.
- Persistence owns schema/lineage validation, atomic terminalization and
  checkpoint monotonicity.

## PST-017 note

No production change was made for the independent Tester's PST-017 negative
control. Sol Reviewer classified that failure as `TEST_PLAN_MAPPING_NOTE`; in
particular, `SQLitePersistence.finish_run` and the NO_NEW_DATA contract were
not changed.

## Files changed

- `src/myresearcher_collector/models/runtime.py`
- `src/myresearcher_collector/sources/eastmoney_guba/collector.py`
- `src/myresearcher_collector/integration.py`
- `tests/integration/test_persistent_collector.py`
- `runs/phase-02-round-05-correction/*`

No Storage Contract, persistence schema, raw evidence design, Tester plan,
Tester acceptance files, legacy code, DataClean or live-source code changed.
