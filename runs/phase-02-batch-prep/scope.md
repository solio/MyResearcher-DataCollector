# Phase 2 Minimal Batch Preparation Scope

## Baseline

`7a2db17a6bb9209c63a25608949a8f2c95af239a`

## Goal

Add only static target validation, sequential orchestration over the existing
single-stock persistent boundary, summary reporting and an offline `--plan-only`
CLI path.

## Non-goals

No target selection, bootstrap semantics, persistence redesign, scheduler,
parallelism, live batch run, Xueqiu, DataClean or upstream integration.
