# Eastmoney Backfill Live Round 01 — Execution

## Exact command

```bash
PYTHONPATH=src python -m myresearcher_collector.cli.main backfill \
  --source eastmoney_guba \
  --stock 601012 \
  --days 7 \
  --data-dir ./data/live-backfill-eastmoney-601012 \
  --max-pages 20 \
  --confirm-live
```

## Execution

```text
HEAD: 1d4d29ba52efa2c601886f28e84c9b5f7110580c
start: 2026-08-11T08:54:32.540834Z
end: 2026-08-11T08:54:32.725361Z
run_id: 3a714f1bbb364565839384eae6c76596
source: eastmoney_guba
stock: 601012
from_time: 2026-08-04T16:00:00Z
to_time: 2026-08-11T15:59:59.999999Z
max_pages: 20
real network: YES
```

## Runtime result

```text
status: SPEC_MISMATCH
stop_reason: schema_mismatch
range_complete: false
pages_scanned: 0
records_received: 0
records_in_range: 0
records_new: 0
records_existing: 0
records_versioned: 0
records_failed: 1
details_requested: 0
details_success: 0
details_failed: 0
earliest_observed_at: NULL
latest_observed_at: NULL
checkpoint_before: NULL
checkpoint_after: NULL
```

The first list request returned HTTP 200 HTML titled as an Eastmoney identity
verification page rather than the approved embedded `article_list` payload.
The collector preserved the response and classified the page as
`SPEC_MISMATCH`/`schema_mismatch`. No login, CAPTCHA solving, bypass, retry or
manual request manipulation was performed.

Per the Executor rules this is a terminal live result. No second Backfill was
run and no production code, spec, test or database row was edited manually.
