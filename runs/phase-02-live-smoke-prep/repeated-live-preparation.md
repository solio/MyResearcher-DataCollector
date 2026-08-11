# Repeated Eastmoney Live Execution Preparation

Status: `REPEATED_LIVE_PREP: READY`

Prepared main SHA: `d20ab45b9e383783216f356fb7d2ca7decf7e1d2`

The worktree is clean and the offline `--help`/`--plan-only` checks passed.
No real Eastmoney request was made. The repository does not currently contain
the required `BOOTSTRAP_BATCH_OFFLINE_ACCEPTANCE_PASS` marker, so the commands
below are prepared only and must remain unexecuted until that marker appears on
this SHA or an accepted non-semantic descendant.

## State directory

Use one new local directory and reuse it for both single-stock runs:

```bash
LIVE_DIR=/Users/mac/Documents/trae_projects/MyResearcher/.live/eastmoney-601012-bootstrap
```

Run 1 and Run 2 must use exactly this same `--data-dir`. Do not put the
directory in git, and do not add credentials or cookies to it.

## Run 1 — three-page bootstrap (prepared, not executed)

Offline preflight (already run; it did not create the directory):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m myresearcher_collector.cli \
  eastmoney-guba-persistent 601012 \
  --data-dir /Users/mac/Documents/trae_projects/MyResearcher/.live/eastmoney-601012-bootstrap \
  --max-pages 3 \
  --min-interval 3.0 \
  --timeout 20 \
  --plan-only
```

Expected plan: `network_execution=false`, `collection_mode=BOOTSTRAP_PENDING`,
`checkpoint=null`, and `max_pages=3`.

After the offline gate is present, the exact live command is:

```bash
PYTHONPATH=src python -m myresearcher_collector.cli \
  eastmoney-guba-persistent 601012 \
  --data-dir /Users/mac/Documents/trae_projects/MyResearcher/.live/eastmoney-601012-bootstrap \
  --max-pages 3 \
  --min-interval 3.0 \
  --timeout 20 \
  --confirm-live
```

Expected contract outcome for a clean source response:

```text
checkpoint_before = NULL
bootstrap mode
pages requested/success/failed = 3 / 3 / 0
status = SUCCESS
stop_reason = bootstrap_complete
checkpoint_after != NULL
```

The command prints the persisted run summary. Save its `run_id`, then inspect
the exact run read-only:

```bash
PYTHONPATH=src python -m myresearcher_collector.cli inspect-run \
  --data-dir /Users/mac/Documents/trae_projects/MyResearcher/.live/eastmoney-601012-bootstrap \
  --run-id <RUN1_RUN_ID>
```

## Run 2 — same state, ordinary incremental (prepared, not executed)

Only after Run 1 has completed, run this plan against the same directory:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m myresearcher_collector.cli \
  eastmoney-guba-persistent 601012 \
  --data-dir /Users/mac/Documents/trae_projects/MyResearcher/.live/eastmoney-601012-bootstrap \
  --max-pages 3 \
  --min-interval 3.0 \
  --timeout 20 \
  --plan-only
```

Expected plan: `collection_mode=INCREMENTAL` and
`checkpoint=<Run1 checkpoint_after>`. This plan is read-only.

The exact live Run 2 command is identical except for `--confirm-live`:

```bash
PYTHONPATH=src python -m myresearcher_collector.cli \
  eastmoney-guba-persistent 601012 \
  --data-dir /Users/mac/Documents/trae_projects/MyResearcher/.live/eastmoney-601012-bootstrap \
  --max-pages 3 \
  --min-interval 3.0 \
  --timeout 20 \
  --confirm-live
```

Valid status is `SUCCESS`, `NO_NEW_DATA`, or `PARTIAL_COLLECTION`, depending
on the live source. The result is correct only if
`checkpoint_before == Run1 checkpoint_after` and bootstrap is not selected.
Inspect it with:

```bash
PYTHONPATH=src python -m myresearcher_collector.cli inspect-run \
  --data-dir /Users/mac/Documents/trae_projects/MyResearcher/.live/eastmoney-601012-bootstrap \
  --run-id <RUN2_RUN_ID>
```

## Evidence capture

For each run, retain the sanitized `inspect-run` JSON and record:

```text
main SHA
command, stock_code, data_dir, run_id
checkpoint_before, checkpoint_after, status
pages requested/success/failed
requests total/success/failed
records received/accepted/failed
raw evidence location/count/file count
SQLite location
first/last published_at
```

Use this read-only SQLite query to add request-kind counts, observation counts,
and raw-evidence lineage for a sampled observation (replace `<RUN_ID>`):

```bash
sqlite3 /Users/mac/Documents/trae_projects/MyResearcher/.live/eastmoney-601012-bootstrap/collector.db <<'SQL'
.headers on
.mode column
SELECT request_kind, outcome, count(*) AS attempts
FROM collection_attempts
WHERE run_id = '<RUN_ID>'
GROUP BY request_kind, outcome
ORDER BY request_kind, outcome;
SELECT run_id, status, watermark_before_utc, watermark_after_utc,
       safe_frontier_utc, counters_json
FROM collection_runs WHERE run_id = '<RUN_ID>';
SELECT o.source_item_id, o.title, o.content, o.author_name,
       o.published_at_utc, o.url, e.evidence_role,
       e.filesystem_path, e.content_sha256
FROM source_item_observations AS o
JOIN observation_evidence AS oe ON oe.observation_id = o.observation_id
JOIN raw_evidence AS e ON e.evidence_id = oe.evidence_id
WHERE e.run_id = '<RUN_ID>'
ORDER BY o.published_at_utc DESC
LIMIT 6;
SQL
```

The sampled raw files must be present at `filesystem_path`; compare each file's
SHA-256 with `content_sha256`. The approved CLI currently does not persist a
`stop_reason` column or include it in `inspect-run`; do not infer it from a
status. The expected bootstrap reason above is the Collector contract, but a
report-only observability change or an accepted tester artifact is still needed
if exact post-run `stop_reason` evidence is required.

## Three-stock batch (prepared, not executed)

The existing target file contains exactly three stocks (`600519`, `300750`,
`002594`). After `LIVE_BOOTSTRAP_PASS` and `LIVE_INCREMENTAL_PASS`, and only
after the same gate check, prepare/run this sequential command:

Offline plan (already run; no network and no data directory creation):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m myresearcher_collector.cli \
  collect-batch \
  --targets config/targets.example.json \
  --data-dir /Users/mac/Documents/trae_projects/MyResearcher/.live/eastmoney-3-stock-batch \
  --max-pages 3 \
  --timeout 20 \
  --plan-only
```

Future live command, still **not executed**:

```bash
PYTHONPATH=src python -m myresearcher_collector.cli \
  collect-batch \
  --targets config/targets.example.json \
  --data-dir /Users/mac/Documents/trae_projects/MyResearcher/.live/eastmoney-3-stock-batch \
  --max-pages 3 \
  --timeout 20 \
  --confirm-live
```

The batch is sequential and delegates to the existing persistent single-stock
boundary. No concurrency, scheduler, crawler, or alternate persistence path is
prepared.

Secrets required: `NONE`.

Remaining blockers: the required `BOOTSTRAP_BATCH_OFFLINE_ACCEPTANCE_PASS`
marker is absent, and exact persisted `stop_reason` reporting is not exposed
by the current approved CLI/SQLite report. Therefore no `--confirm-live`
command has been run and no LIVE PASS is claimed.
