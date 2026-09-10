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

## Eastmoney detail enrichment

The three Eastmoney execution paths have different historical persistence
boundaries:

- simple `backfill` is list-only and writes the legacy mutable `posts` table;
- persistent collection/backfill writes immutable versions to
  `source_item_observations`, with raw evidence and failure-ledger rows;
- `enrich-details` detects which of those two database contracts it was given
  and applies the same title eligibility rule to both.

By default, enrichment requests details only when the trimmed list title has
exactly 40 characters. Canonical success appends a new observation with
`content_source=detail_body` and a `detail` raw-evidence link; it never updates
the earlier `list_title` observation. Failure leaves that observation in place
and records the reason in `collection_failures`. Titles shorter than 40 are not
requested unless `--include-short-titles` is explicitly supplied.

For newly collected canonical data under `data/`, run:

```bash
PYTHONPATH=src python -m myresearcher_collector.cli.main enrich-details \
  --source eastmoney_guba \
  --stock 601012 \
  --data-dir data \
  --acquisition-mode existing-chrome \
  --confirm-live
```

Use `--plan-only` in place of `--confirm-live` to inspect the resolved data
root and title policy without opening a detail page.
