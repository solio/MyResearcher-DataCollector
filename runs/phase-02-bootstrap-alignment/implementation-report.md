# Eastmoney Bootstrap Alignment — Implementation Report

## Runtime

- Added `BOOTSTRAP_MIN_PAGES = 3`.
- `EastmoneyGubaCollector.collect()` selects bootstrap by a `NULL` watermark
  unless an explicitly bounded non-bootstrap caller opts out.
- Bootstrap validates `max_pages >= 3` before requesting the source and then
  traverses exactly pages 1, 2 and 3 in the existing synchronous request loop.
- Valid empty bootstrap pages do not shorten the required window.
- The runtime frontier is the maximum valid publication time from every
  successfully list/detail-resolved in-scope bootstrap row, including a known
  ID re-resolved during a retry even when persistence need not append a
  duplicate observation.
- Bootstrap failure states never expose a safe frontier.
- No new `CollectionStatus` was added.

## Persistent integration and runner

- `execute_and_persist_collection()` treats an absent/`NULL` checkpoint as
  bootstrap by default, rejects a short bootstrap before network execution or
  run creation, and passes the runtime-declared frontier unchanged through the
  existing `SafeFrontier` translation.
- Added `eastmoney-guba-persistent`, a reusable single-stock CLI boundary whose
  first successful run bootstraps and whose later runs use the existing
  incremental path. `--plan-only` inspects `BOOTSTRAP_PENDING` versus
  `INCREMENTAL` without creating a transport or mutating storage.
- The separately frozen `eastmoney-guba-live-smoke` still requires a fresh
  directory and explicitly uses the original bounded non-bootstrap behavior;
  its one-page partial semantics were not retroactively changed.

## Incremental preservation

- Unknown IDs at or before the committed watermark remain detail-eligible.
- When such an older unknown ID is successfully resolved, the runtime frontier
  stays at least equal to the existing watermark instead of attempting a
  checkpoint regression.
- Known historical IDs at/before the watermark still do not require detail
  refresh, and the existing watermark-confirmation, cross-gap and valid partial
  safe-prefix paths are unchanged.

## Changed implementation/test files

- `src/myresearcher_collector/sources/eastmoney_guba/collector.py`
- `src/myresearcher_collector/sources/eastmoney_guba/__init__.py`
- `src/myresearcher_collector/integration.py`
- `src/myresearcher_collector/cli/main.py`
- `tests/unit/test_eastmoney_guba_collector.py`
- `tests/unit/test_live_smoke_cli.py`
- `tests/integration/test_persistent_collector.py`

Pre-existing batch/config work in the dirty worktree is unrelated and remains
outside this run's implementation claim.
