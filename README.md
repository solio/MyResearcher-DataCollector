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
- `scripts/`: deterministic project utilities; `scripts/ops/`: documented operational runbooks for detail enrichment.

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

By default, enrichment requests details when the trimmed list title is at least
40 characters long (`>= 40`). A length of exactly 40 uses the historical
`list_title_length_eq_40` trigger; a length greater than 40 uses the additive
`list_title_length_gt_40` trigger. Canonical success appends a new observation with
`content_source=detail_body` and a `detail` raw-evidence link; it never updates
the earlier `list_title` observation. Failure leaves that observation in place
and records the reason in `collection_failures`. Titles shorter than 40 are not
requested unless `--include-short-titles` is explicitly supplied.

A detail URL whose post has been deleted returns the site's error shell
(`error404_page`) instead of an article payload. Such rows are **never removed**:
the legacy `posts` row or the canonical `list_title` observation is kept intact
(its `content` simply stays `NULL`), and the post id is recorded in a sidecar
skip ledger (`<collector-stem>.detail_enrichment_skips.db`, next to
`collector.db`) with reason `detail_not_found`. Later runs exclude ledgered ids
from the candidate set, so a known-missing post is requested once and then never
re-visited; the run report exposes the count as `skipped_not_found_added`.
Detection deliberately requires both the 404 markers *and* the absence of a
`post_article` payload, so a genuine future schema change is never mistaken for
a deleted post (which would permanently skip a live article). The ledger lives
outside `collector.db` because the canonical collector schema is a frozen
contract re-validated on every open, so an extra table there would be rejected
as drift.

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
