# MyResearcher-DataCollector

MyResearcher-DataCollector is the acquisition boundary between external sources and MyResearcher-DataClean. It fetches, parses, minimally normalizes raw structure, preserves traceability, and reports acquisition outcomes. It does not clean content, infer sentiment, make financial judgments, or generate trading decisions.

Current status: **Phase 1 Round 2 Developer implementation**. The isolated Eastmoney Guba source adapter is implemented behind its approved Source Spec; Tester review is pending.

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
- `src/myresearcher_collector/`: source-isolated adapter, internal raw models and minimal CLI boundary.
- `tests/`: unit, integration and sanitized fixture boundaries.
- `runs/`: phase/round scope and evidence.
- `scripts/`: future deterministic project utilities.

## Development gate

A production adapter may start only after a Source Researcher has produced an approved `specs/<source-name>.md`. Phase 1 is limited to one source, one spec, one adapter, one raw contract, real sanitized fixtures, tests, and one runner loop.

Read [AGENTS.md](AGENTS.md) before doing any work.

## Deterministic checks

From this directory:

```bash
python -m compileall -q src tests
python -m pytest --collect-only -q
python -m pytest -q

# From a source checkout, exercise the source boundary without a package install:
PYTHONPATH=src python -m myresearcher_collector.cli --help
```

## Canonical Storage

Normal collection and backfill commands use one shared collector data root
(`data/` by default) and one `collector.db`. Sources, stocks, and runs are
separated by metadata inside the database. Use `--data-dir` only for tests,
smoke runs, or explicitly isolated experiments.
