# AGENTS.md

## Project

`MyResearcher-DataCollector`

## Mission

Reliable acquisition of externally sourced raw investment-related data.

Pipeline position:

```text
External Sources
    ↓
MyResearcher-DataCollector
    ↓
MyResearcher-DataClean
```

DataCollector answers only: did we acquire external data reliably, completely, repeatably, and traceably?

## Roles

- Source Researcher
- Developer
- Tester

Do not add roles without a future approved requirement.

## Core rules

- Evidence before assumption.
- Spec before implementation.
- Source behavior before abstraction.
- Agent handles uncertainty; code handles deterministic behavior.
- Collection failure is not no-data.
- Raw data remains traceable.
- One source working correctly before many sources working partially.

## Mandatory reading order

Before starting any task, read:

1. `AGENTS.md`
2. `docs/data-collector/product-goal.md`
3. `docs/data-collector/data-contract.md`
4. `docs/data-collector/implementation-plan.md`
5. the current run's `scope.md`
6. the applicable `specs/<source-name>.md`

If instructions conflict, priority is:

```text
frozen contract
> current phase scope
> SOURCE_SPEC
> agent inference
```

An Agent must block and report uncertainty instead of overriding an explicit contract.

## Forbidden responsibilities

- data cleaning or content-quality decisions
- sentiment or stance classification
- finance or investment classification
- signal generation
- backtesting
- trading decisions
- credentials in code, fixtures, logs, reports, or examples
- source implementation without a frozen same-name SOURCE_SPEC

## Phase 0 boundary

Phase 0 creates contracts and skeletons only. It must not implement a crawler, source adapter, parser, network client, persistence backend, scheduler, LLM behavior, or Phase 1 feature.
