# HTTP idle-time backfill — handoff

Date: 2026-10-01, Asia/Shanghai. Version: http-backfill.v3.

The user has deployed v1 on one server and explicitly authorized fixing stock
configuration editing/deletion and improving pause/recovery, challenge handling
and continuous list/detail work. The user subsequently requested activity paging,
reuse of the original collector.db structure and original detail trigger. This
release implements those corrections and targets the deployed single server.
Multi-node scheduling, one central console and data merge remain future work.

Implementation is confined to this app; the existing root Docker context
allowlist is retained.
Production src/parser is imported read-only. No production DB/config/source
contract is changed, and no automatic model promotion is implemented.

## Delivered behavior

- PATCH api/jobs/current edits paused/quiescent jobs. Stock addition/removal
  preserves unaffected progress; date edits reconstruct scope from retained raw
  evidence and reuse complete bodies. Interval/client edits preserve queues.
- DELETE current job or last stock archives the job and cancels pending work.
  Raw bytes, posts, request/observation history and configuration revisions stay.
  GET api/jobs returns lightweight history without full evidence snapshots.
- Running H5 edit/remove/archive first pauses and waits for the current request
  to finish. Saving never automatically starts acquisition.
- Active source halts and cooldown survive edits/archive. A detached failed
  target remains available for one explicit probe; it records evidence but
  cannot write new posts, associations or tasks into a changed scope. Success
  stays paused. Deletion cannot bypass a halt.
- Pause/restart and detail processing revalidate list position with ID/source
  publication anchors, rescan the observed interval and prioritize new details.
  Two matching canonical ID/time sets permit advancement from the relocated
  physical page; all requests obey the same >=60-second gap. Missing anchors
  retain explicit gaps; ambiguous position stops safely.
- Captured mixed type20 placements are not publication ordered. Normal type0
  rows drive time navigation when present. All source rows remain evidence;
  all-nonstandard anchors may compare IDs but carry time_order_unverified gaps.
- Known exact challenge titles and explicit static CAPTCHA containers are
  detected even alongside valid data. Template assets alone are not blocks.
  JavaScript-only challenges remain unobservable. No 100% claim applies.
- v1 schema migration is additive. A stable instance UUID accompanies status,
  request and post outputs for future provenance. Data/token paths are unchanged.
- Shared detail_enrichment_trigger now controls the detail queue: stripped title
  length >=40 by default, including exactly 40. Short titles remain list_only
  records with list_title provenance. Existing genuine bodies are retained,
  including short and empty bodies. Old pending short details become skipped;
  a halted short-detail target retains its cooldown and one probe-only action.
- App-local data/collector.db reuses SimplePostStore and its existing schema,
  including original posts queries and detail-enrichment candidate semantics.
  No private columns or ledger tables are added to that compatible database.
  Missing body is NULL and observed empty body is an empty string. Removed or
  unavailable detail evidence reuses the original local enrich skip ledger.
- experiment.sqlite3 retains request/task/recovery/provenance and projection
  bookkeeping. Startup replays committed evidence idempotently; subsequent
  source responses project only affected posts. Raw hashes, source identities,
  publication and original acquisition timestamps are independently verified.
  Raw/request commits precede compatible writes. A local storage failure stops
  acquisition; start/retry repair locally and retain source halt/cooldown.
- Authenticated requests/events pagination uses independent descending ID
  cursors and fixed snapshot membership. H5 shows 30 requests or 20 events per
  page with previous/next/latest controls. History remains visible while status
  polls. The old default array API is retained; paged=1 opts into a page object.
  Indexed keyset reads and a bounded total cache avoid growing page DOM and
  repeated whole-ledger counts on routine polling.

## Validation

124 offline app tests pass with ResourceWarning treated as errors. Coverage
includes existing Engine/recovery/lifecycle/HTTP behavior plus >=40 boundaries,
short-title migration, retained genuine bodies, original store/enrich semantics,
incremental projections, replay after crash, local-only storage repair, preserved
independent source blocks, authenticated paging and actual app.js history/DOM
behavior. Original content_rules, SimplePostStore and detail_enrichment unit tests
also pass: 20 tests. JavaScript syntax and Compose configuration pass.

A backup clone of the paused v1 real trial database migrated successfully:
179 requests, 229 posts, 240 row observations, 3 page observations and 179 raw
files remained unchanged; every recorded raw SHA-256 matched. The compatible
store contains all 229 posts, retains all 176 genuine bodies, and original enrich
queries identify the same 9 remaining long-title candidates. All 44 old pending
short titles become list_only. Repeated projection/reopen is idempotent and
preserves timestamps. No source request was sent by this check.
The original trial directory was not migrated or edited by this verification.

Actual 390x844 browser verification used an isolated fake-response Engine without
a source worker. Request and event histories navigated independently and retained
their pages during status refresh, with 30/20 rendered rows and document width
equal to viewport width. The screenshot uses offline demonstration records;
it is not the user's live server or evidence of sustained source availability.

Linux amd64 rebuild retained the base/apt instructions and reused the cached
curl/CA installation layer. The Docker context excludes data, credentials, Git
and unrelated research runs. Disposable container checks cover health,
authenticated v3 status, automatic compatible DB creation, edit/remove/archive,
paged event traversal and Secure/HttpOnly /collector/ cookie path, with zero
source attempts. The temporary smoke container was removed after validation.

## Deployment and evidence limits

The user confirmed their earlier server build/deployment. v3 remote upgrade is not
executed here: no remote access credentials or verified checkout path were
provided. README.zh-CN.md supplies pause, stop, whole-data backup, git pull and
docker compose up -d --build commands. Existing /collector/ nginx, loopback
port 8790, bind-mounted data and token remain compatible. Startup is paused;
the user continues after inspecting the upgraded task.

Local v1 trial started 2026-09-30 20:00:59 Shanghai using ordinary curl for
601012, window 2025-09-30 through 2026-09-30. It was paused during this work:
179 attempts, 3 forward pages, 176 nonempty complete bodies, 229 discovered
eligible posts, 53 pending and no failures in that observed period. This is
local evidence, not the user's server status or a sustained-access guarantee.
No new live source traffic was required for v3 verification. The original local
trial remains paused with its old loaded code; clone checks did not upgrade it.

All results remain observed_pages_only, coverage_complete=false,
dataset_complete=false and model_database_eligible=false. Local reconciliation
does not prove deleted/unavailable or never-observed history complete. A queue
ending does not establish a one-year dataset. Keep partial data in this isolated
research store and evaluate future long trials using the actual request ledger.
