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

## Cross-Client / Cross-Model Collaboration

This project may be maintained by multiple AI coding clients and models, including Codex with GPT/Codex models, Claude Code CLI with DeepSeek models, and future clients/models. No Agent may assume that another client can see its chat, that another model remembers prior decisions, or that hidden context is shared.

The Git repository is the authoritative shared project memory. Requirements, contracts, specifications, decisions, evidence, open questions, implementation state, test state and handoff state must be persisted as repository artifacts. `Chat history is NOT part of the project contract.`

`Role`, `Client` and `Model` are separate concepts:

- Role: the current responsibility (`Source Researcher`, `Developer` or `Tester`).
- Client: the execution environment (for example Codex or Claude Code CLI).
- Model: the model running in that client (for example a GPT/Codex model or DeepSeek).

Roles may be executed by different clients and models. Role authority comes from repository contracts, not model capability. A stronger model gains no extra authority; a weaker model receives no relaxed acceptance criteria. See `docs/data-collector/collaboration-contract.md` for the handoff protocol and source-of-truth priority.

## Mandatory reading order

Before starting any task, read:

1. `AGENTS.md`
2. `docs/data-collector/product-goal.md`
3. `docs/data-collector/collaboration-contract.md`
4. `docs/data-collector/data-contract.md`
5. `docs/data-collector/implementation-plan.md`
6. the current run's `scope.md`
7. the applicable `specs/<source-name>.md`
8. `current handoff.md` when present

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
