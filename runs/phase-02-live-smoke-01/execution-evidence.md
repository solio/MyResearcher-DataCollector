# Eastmoney Live Smoke 01 Execution Evidence

## Integration and offline gates

```text
main before integration: 2701e13
prep source: phase2-live-smoke-prep @ ac4f93a
cherry-pick: 4246ae4
reconciliation artifact update: a03eab5
merge / execution commit: 1acd107
```

The cherry-pick had no conflict. Round 07 Collector/runtime files were not
modified by the preparation. Pre-merge validation:

```text
git diff --check                                      exit 0
python -m compileall -q src tests                     exit 0
pytest tests/acceptance tests/integration -q          31 passed, 1 xfailed
pytest -q                                             91 passed, 1 xfailed
```

The xfail is the approved PST-017 mapping note. CLI `--help` and `--plan-only`
exited 0; plan reported `network_execution=false`, `stock_code=601012`,
`max_pages=1`, `min_interval_seconds=3.0`, explicit SQLite/raw paths and an
absent/ready data directory. The plan did not create the directory.

## Live command shape

No secrets, cookies, sessions or tokens were supplied.

```text
PYTHONPATH=src python -m myresearcher_collector.cli \
  eastmoney-guba-live-smoke 601012 \
  --max-pages 1 \
  --min-interval 3.0 \
  --timeout 20 \
  --data-dir /Users/mac/Documents/trae_projects/MyResearcher/live-smoke-data/eastmoney/phase2-live-smoke-01 \
  --confirm-live
```

Execution UTC: `2026-08-11T02:36:06.734927Z` to
`2026-08-11T02:40:07.221946Z`.

## Runtime and HTTP result

```text
run_id: 14e9b5fcdd134036999abf60cb6d074d
source: eastmoney_guba
stock_code: 601012
runtime status: PARTIAL_COLLECTION

requests total/success/failed: 81 / 81 / 0
pages requested/success/failed: 1 / 1 / 0
details requested/success/failed: 80 / 80 / 0
records received/accepted/failed: 80 / 80 / 0
failures persisted: 0

list HTTP evidence: 1 x 200
detail HTTP evidence: 80 x 200
first published_at: 2026-08-11T01:03:38.000000Z
last published_at: 2026-08-11T02:35:58.000000Z
```

`PARTIAL_COLLECTION` is the frozen SOURCE_SPEC outcome for a fresh non-empty
run that reaches the one-page hard coverage cap before the incremental
confirmation boundary. The CLI therefore exited 1. It was not relabeled. No
request, page, detail, record or persistence failure occurred.

## Persisted inspection

`inspect-run` reopened the exact run in SQLite and reproduced the runtime
summary above.

```text
SQLite: /Users/mac/Documents/trae_projects/MyResearcher/live-smoke-data/eastmoney/phase2-live-smoke-01/collector.db
raw: /Users/mac/Documents/trae_projects/MyResearcher/live-smoke-data/eastmoney/phase2-live-smoke-01/raw/eastmoney_guba

attempt rows: 81
raw evidence rows/files: 81 / 81
linked observations: 80
scope links for stock:601012: 80
observation evidence links: 160
observations missing list/detail roles: 0
raw SHA-256/size verifications: 81 / 81
valid article_list assignments: 1 / 1
valid post_article assignments: 80 / 80
valid source_metadata JSON: 80 / 80
detail final URL matches: 80 / 80
```

Checkpoint/runtime consistency:

```text
checkpoint before: NULL
checkpoint after: NULL
checkpoint row: absent
safe frontier: NULL
```

This is consistent with the bounded partial runtime declaration; persistence
did not infer or advance a checkpoint.

## Small record sanity check

Three recent observations were inspected without recording content or author
values. IDs `1757142162`, `1757139832` and `1757137786` all had:

- requested/canonical bar `601012`, `post_type=0`;
- non-empty title and content;
- present author ID/name;
- plausible UTC publication time and an Eastmoney `news,601012,<id>.html` URL;
- valid source metadata whose final detail URL matched the observation URL;
- both `list` and `detail` raw evidence roles.

No login page, HTML challenge, identity mismatch, empty-body substitution or
obvious time/URL mismatch was observed.
