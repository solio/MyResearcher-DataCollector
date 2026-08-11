# Phase 2 Round 07 — Safe Frontier Cross-Gap Correction Report

## Baseline and defect input

Baseline: `7cb584cbd356d067994474b59e0e41e195f4a976`.

The sole input was the deterministic Round 06 failure
`test_pst020_partial_detail_failure_cannot_cross_unresolved_mid_page_item`:
page 1 completed item A at T1, then eligible item B at Tmid (T0 < Tmid < T1)
failed detail acquisition. The runtime incorrectly retained T1 and the
integration committed it.

## Root cause

The runtime tracked the latest completed-page frontier but, on a later detail
failure, did not compare the failed eligible row's publication time with that
candidate. It therefore treated every later detail failure as occurring after
the candidate, even when the unresolved row was inside the candidate's range.

## Fix

`EastmoneyGubaCollector` now invalidates the candidate when an unresolved
eligible detail row has `published_at <= completed_page_frontier`, or when no
completed candidate exists. A failure strictly after the candidate may leave
the earlier prefix valid; later completed pages cannot silently move the
candidate after an unresolved gap. The runtime declaration is therefore
`None` for the Round 06 cross-gap case.

`integration._safe_frontier` remains a translation-only adapter: it accepts
only the explicit `CollectionResult.safe_frontier` declaration and never
computes a timestamp. `SQLitePersistence`, its schema, Storage Contract and
monotonicity logic were not changed.

## Responsibility boundary

```text
Collector runtime: prove a contiguous safe frontier and reject cross-gap candidates
Integration: translate the runtime declaration
Persistence: validate and atomically commit it
```

## PST-017

No production change was made for the independent Tester's PST-017 mapping
note. Its existing xfail remains documented and is not a blocker for this
Developer fix.

## Changed files

- `src/myresearcher_collector/sources/eastmoney_guba/collector.py`
- `tests/integration/test_persistent_collector.py`
- `runs/phase-02-round-07/*`

No Persistence implementation, Storage Contract, Tester acceptance file,
legacy code, DataClean, scheduler or live-source code was modified.
