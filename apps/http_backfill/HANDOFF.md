# HTTP idle-time backfill — handoff

Date: 2026-10-01, Asia/Shanghai. Version: http-backfill.v5.

The user explicitly requests one migration command and deletion of the live
experiment.sqlite3 afterward. v5 now uses collector.db as the sole local runtime
database, preserving the original posts table's fields and adding app ledger
state in the same database. The previous v4 fleet management and verified merge
remain enabled. Root production data and source semantics remain outside scope.
Remote server migration has not been executed from this local workspace.

## v5 migration and validation

- `git pull && bash migrate-storage.sh` from apps/http_backfill builds before
  downtime, stops the worker, runs migrate_storage.py --data-dir /data in a
  disposable container, and starts/checks the new service only after success.
- The migrator uses worker.lock and SQLite backups including committed WAL;
  verifies integrity, complete old ledger rows/IDs, post/body/metadata facts,
  raw lengths/hashes, UUID, source halt/cooldown and existing immutable exports.
  It atomically publishes collector.db and records a durable receipt before
  deleting experiment.sqlite3 and its WAL/SHM. Backups are private under
  data/migration-backups/. Interrupted publication/cleanup is resumable;
  failures keep evidence and prevent automatic service start.
- Current body text lives only in original posts.content. http_post_state stores
  source metadata without another content column or duplicate post_content;
  http_posts is a read view. Full detail payload API responses reconstruct from
  freshly verified raw. Immutable export versions remain historical evidence.
- Node projection/provenance/export use one borrowed SQLite connection and one
  transaction; fleet retains its standalone original-schema store adapter.
  New directories create only collector.db. Old or incomplete migrations must
  finish the CLI before v5 startup. Runtime rejects unowned/schema-mismatched
  or symlinked databases. Migration streams rows rather than loading all bodies.
- v5 hubs accept v4/v5 node export contracts, allowing hub-first upgrades.
  Stable instance UUIDs and existing export sequences keep pinned registrations
  and synchronization cursors valid. Fleet data/registry/raw and tokens remain.
- Independent real-data clone verification passed for both the original v1
  pilot and a v4-upgraded clone: 179 requests, 229 posts, 240 observations,
  3 page observations, 176 bodies and all 179 raw hashes preserved. Original
  enrich query still finds 9 long-title candidates; 44 old short pending items
  become list_only at normal startup. All 229 preexisting v4 export rows remain
  byte-for-byte unchanged, ending at sequence 229; v1 acquires its first baseline
  and subsequent policy versions. Both clones merge successfully and reopen /
  rerun idempotently without recreating experiment.sqlite3. Zero source requests.
- Original content_rules/SimplePostStore/detail_enrichment tests: 20 PASS.
  H5 actual-app.js fleet tests: 8 PASS. Shell wrapper tests: 3 PASS, covering
  build failure, migration failure and success ordering. Full migration/runtime
  suite: 181 PASS with ResourceWarning treated as errors (21.524 seconds),
  including 9 migration cases and 3 atomic detail/raw cases. JavaScript syntax,
  Compose configuration and git diff whitespace checks pass.
- Linux amd64 image rebuild reused the cached curl/CA installation. A real v1
  clone migrated inside the network-disabled container, then the new server
  returned authenticated v5/paused/ready with 179 requests, 229 posts, 176
  bodies, 44 list_only and collector.db as its runtime path. No experiment file
  was recreated, source requests stayed zero, and a concurrent migration was
  refused by the worker lock. Disposable server container was removed.
  Linux regression uses a read-only sanitized-fixture mount (fixtures are not
  part of the production Docker context): migration 9, authenticated three-server
  HTTP 4 and atomic detail/raw 3 tests all PASS (16 total, 7.268 seconds).

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
- App-local collector.db contains original SimplePostStore posts plus task,
  request, recovery and provenance tables. Missing body is NULL and observed
  empty body is an empty string. Source-unavailable skip facts are in the same
  database. Source commits and raw remain durable before local replay; a local
  storage failure stops acquisition, and repair retains source halt/cooldown.
- Authenticated requests/events pagination uses independent descending ID
  cursors and fixed snapshot membership. H5 shows 30 requests or 20 events per
  page with previous/next/latest controls. History remains visible while status
  polls. The old default array API is retained; paged=1 opts into a page object.
  Indexed keyset reads and a bounded total cache avoid growing page DOM and
  repeated whole-ledger counts on routine polling.
- FleetManager registers up to 16 remote v4/v5 services with stable pinned UUIDs,
  validated explicit URLs and write-only private tokens. Same-instance and clone
  registrations are rejected. Token omission on edit preserves the secret;
  public responses/errors never echo it. registry.json is 0600 in a 0700 folder.
- The hub proxies existing task/status/control/history APIs for the selected
  node. Remote writes get one attempt after identity verification; uncertain
  responses are marked ambiguous and never automatically replayed. Remote 401
  is a node error, not a reason to expire the hub session. NodeClient disables
  environment proxies and redirects and bounds response time/size.
- Independent background work polls node status every 10 seconds and checks
  export growth every 60 seconds. Explicit sync and remaining export pages are
  durably queued. Each node has one in-flight task, with four total worker slots.
  BACKFILL_FLEET_SYNC_ENABLED=0 disables automatic merge but retains export and
  explicit synchronization, suitable for remote collector-only machines.
- compatible_store publishes immutable export snapshots after compatible writes
  and establishes baselines for already projected v3 posts. Authenticated export
  returns contiguous sequence pages with a fixed snapshot; response budgeting
  permits large bodies without building unbounded pages. Raw transfers validate
  node identity, request ID, actual byte size, containment and SHA-256.
- MergeStore reparses retained source bytes and verifies structural identity,
  publication/title/body/collection facts before accepting each whole batch.
  data/fleet/collector.db uses original SimplePostStore; merge.sqlite3 preserves
  all node versions, original request facts, range snapshots, conflicts and
  per-instance cursors. Raw is content addressed; identical bytes are stored
  once while every original node/request association remains distinct.
- A verified body enriches a list-only observation and cannot be erased by
  later missing data. Identity/body conflicts preserve all versions and project
  one complete deterministic observation, never blended fields. Durable redo
  precedes compatible projection and cursors. Bad transfer does not advance
  cursor. Restart repairs committed batches locally; damaged retained raw has
  an observable recovery error and can be repaired from node evidence APIs,
  preserving the damaged file in quarantine. Other nodes remain usable.
- H5 registers/edits/removes nodes, switches the existing task/control/history
  views, shows connection/sync/errors and central counts/path. Switching resets
  old cursors and isolates late responses. Mutations bind one target and lock
  switching; dirty drafts require explicit discard. Tokens clear after save,
  failure or cancellation. Removal retains central data and remote task state.

## Previous v4 validation

165 offline app tests pass with ResourceWarning treated as errors. Coverage
includes existing Engine/recovery/lifecycle/HTTP behavior plus >=40 boundaries,
short-title migration, retained genuine bodies, original store/enrich semantics,
incremental projections, replay after crash, local-only storage repair, preserved
independent source blocks, authenticated paging and actual app.js history/DOM
behavior. New coverage includes 14 export/merge tests, 15 registry/client/scheduler
tests, 4 real authenticated three-server HTTP tests and 8 actual app.js fleet
tests. Original content_rules, SimplePostStore and detail_enrichment unit tests
passed in v3 (20 tests); src is unchanged in v4. JavaScript syntax and Compose
configuration pass.

A backup clone of the paused v1 real trial database migrated successfully:
179 requests, 229 posts, 240 row observations, 3 page observations and 179 raw
files remained unchanged; every recorded raw SHA-256 matched. The compatible
store contains all 229 posts, retains all 176 genuine bodies, and original enrich
queries identify the same 9 remaining long-title candidates. All 44 old pending
short titles become list_only. Repeated projection/reopen is idempotent and
preserves timestamps. No source request was sent by this check.
The same clone additionally established a 229-record immutable export baseline,
merged all 229 posts and 176 bodies, independently verified 179 raw responses,
and persisted/reopened cursor 229 without conflicts or recovery errors.
The original trial directory was not migrated or edited by this verification.

Actual 390x844 v4 browser verification used the hub and two real HTTP collector
servers with injected source fixtures and no source workers. Registering node B,
switching its task/history view and explicit merge worked; overlapping posts
yielded 3 unique posts and 2 bodies, and the submitted token input cleared.
Document width equals viewport width. The screenshot uses offline demonstration
records, not the user's server or sustained source availability evidence. v3
phone pagination verification and its regression tests remain valid.

Linux amd64 rebuild retained the base/apt instructions and reused the cached
curl/CA installation layer. The Docker context excludes data, credentials, Git
and unrelated research runs. Disposable container checks cover health,
authenticated v4 status, automatic compatible/fleet DB creation,
edit/remove/archive, paged event traversal, authenticated fleet/export and empty
explicit synchronization, plus Secure/HttpOnly /collector/ cookie path, with
zero source attempts. All 4 real three-server HTTP tests additionally pass inside
the Linux amd64 Python 3.12 image. Temporary UI servers and smoke containers were
removed after validation; the browser viewport override was reset.

## Deployment and evidence limits

The user confirmed their earlier server build/deployment. v5 remote migration is not
executed here: no remote access credentials or verified checkout path were
provided. README.zh-CN.md supplies the one-command migration wrapper and standalone CLI. Existing /collector/ nginx, loopback
port 8790, bind-mounted data and token remain compatible. Startup is paused;
the user continues after inspecting the upgraded task.
Other servers deploy the same app, expose their verified HTTPS nginx endpoint or
an explicitly configured private BACKFILL_BIND_ADDRESS, and register through the
hub H5. Each keeps its own data directory/UUID. Their stock/date jobs are configured
individually in the one hub view; no automatic global job partitioning or source
request failover is implemented or claimed.

Local v1 trial started 2026-09-30 20:00:59 Shanghai using ordinary curl for
601012, window 2025-09-30 through 2026-09-30. It was paused during this work:
179 attempts, 3 forward pages, 176 nonempty complete bodies, 229 discovered
eligible posts, 53 pending and no failures in that observed period. This is
local evidence, not the user's server status or a sustained-access guarantee.
No new live source traffic was required for v4 verification. The original local
trial remains paused with its old loaded code; clone checks did not upgrade it.

All results remain observed_pages_only, coverage_complete=false,
dataset_complete=false and model_database_eligible=false. Local reconciliation
does not prove deleted/unavailable or never-observed history complete. A queue
ending does not establish a one-year dataset. Keep partial data in this isolated
research store and evaluate future long trials using the actual request ledger.
