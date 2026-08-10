# MyResearcher-DataCollector

MyResearcher-DataCollector is the acquisition boundary between external sources and MyResearcher-DataClean. It fetches, parses, minimally normalizes raw structure, preserves traceability, and reports acquisition outcomes. It does not clean content, infer sentiment, make financial judgments, or generate trading decisions.

Current status: **Phase 0 project bootstrap**. No production data source is implemented.

## Pipeline

```text
External Sources
       ↓
Fetch → Parse → Raw structural normalization → Trace → Observe
       ↓
normalized raw records
       ↓
MyResearcher-DataClean
```

## Project layout

- `agents/`: the three approved role definitions.
- `docs/data-collector/`: product, architecture, data, source, test, runtime and phase contracts.
- `specs/`: one evidence-backed SOURCE_SPEC per future production adapter.
- `src/myresearcher_collector/`: package skeleton; no source implementation in Phase 0.
- `tests/`: unit, integration and sanitized fixture boundaries.
- `runs/`: phase/round scope and evidence.
- `scripts/`: future deterministic project utilities.

## Development gate

A production adapter may start only after a Source Researcher has produced an approved `specs/<source-name>.md`. Phase 1 is limited to one source, one spec, one adapter, one raw contract, real sanitized fixtures, tests, and one runner loop.

Read [AGENTS.md](AGENTS.md) before doing any work.

## Phase 0 checks

From this directory:

```bash
python -m compileall -q src tests
python -m pytest --collect-only -q
```

Zero collected tests is expected until Phase 1; syntax and project configuration must still be valid.
