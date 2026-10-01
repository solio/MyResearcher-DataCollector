# HTTP idle-time backfill — handoff

Date: 2026-10-01, Asia/Shanghai. Version: http-backfill.v5.

## Latest extension: actual pacing audit and usable data exports

The user requests a concrete merge/export path and challenges a collector's
5,274-post count against a 60-second interval. That server's ledger has not been
provided or read here. A list request may return many posts; no server-compliance
claim is inferred from its count or from offline tests.

- `rate_audit.py` is standalone stdlib and runs read-only against a fixed SQLite
  snapshot via `python - --db ... --interval 60 < rate_audit.py`, including inside
  an already running old container. It does not construct Engine, read tokens,
  take worker.lock or request the source. Full history streams by request ID.
  Confirmed attempts, kind/purpose/probe/redirect counts, previous-finish to next-
  start gaps, bounded violation details and unknown/in-flight/clock facts remain
  distinct. Fixed lower-limit verdicts do not assert historical configuration
  compliance; recorded revision policies and uncertainties are separate.
  Old compatible-only collector.db may fall back explicitly to a real sibling
  experiment.sqlite3 ledger; actual db_path is returned. Exit 0/1/2/3 means scoped
  pass / known short gap / insufficient evidence / read or input error.
- Engine.status includes a last-1,000-row audit, cached for at most 15 seconds
  and invalidated by ledger completion or connection writes/config changes.
  H5 shows selected-node scope and unknowns, never treats missing/unknown audit
  facts as a pass, and only calculates a clearly scoped confirmed-request rate.
  No source scheduler, transport, cooldown, restart or CAPTCHA behavior changed.
- `data_export.py` streams one compatible posts snapshot to CSV or JSONL, keeping
  the original 15 fields and NULL versus acquired empty-body distinctions.
  Download API authenticates before export, prepares a private temporary file
  before attachment headers and excludes private operational data. H5 local/fleet
  exports always target the hub's local or merged store independent of the
  currently selected remote node. Invalid/auth/error responses are not saved as
  data files. CLI refuses output overwrite and can also be fed to an old container
  without stopping collection.
- `transfer.py` exports/imports ZIP post-evidence sequences for disconnected
  nodes, not the full failure/challenge/probe ledger. Read-only exports use one
  committed journal snapshot and retain linked raw/hash evidence. Source-post,
  export-post and unexported counts are explicit. Complete archive/hash/source
  preflight precedes destination creation. Replay uses existing MergeStore rules,
  retains body/conflicts and is idempotent; unsafe/corrupt packages are rejected.
  The import holds destination worker.lock and refuses an active hub to prevent
  cross-process fleet writes. Prefer existing live hub synchronization, or import
  into a separate offline root; stopping the hub is required only for CLI import
  into its already active root. No root production writes or promotion occurred.

Validation: 235 offline app tests PASS with ResourceWarning errors; 34 new
export/download/rate/transfer tests also PASS inside cached Linux amd64 Python
3.12 with external networking disabled. Source calls are injected fixtures.
Checks include same-post enrichment/dedup, genuine empty bodies, WAL snapshot
consistency, repeated/conflicting imports, corrupted late raw/payload, archive
paths/limits, old ledgers, unknown timing, short gaps, configuration revisions,
stdin execution, authenticated real HTTP downloads and actual app.js rendering.
JavaScript syntax and diff whitespace checks PASS. Dockerfile/apt layer unchanged.

Actual browser verification shows old-node audit absence as unverifiable and
new scoped 60-second fixture facts; selected remote views preserve hub download
scope. At 390x844 the document width is 390. Mobile screenshots remain ignored
under runtime/screenshots/http-backfill-export-mobile-20261001.jpg and
http-backfill-rate-export-mobile-20261001.jpg. HTTP file contents were independently
verified by tests; the in-app browser displayed the download handoff message but
its download-event API timed out, so an OS-saved browser file is not claimed.
Temporary demonstration servers are closed and viewport overrides reset.
No real source requests, remote deployment, running pilot restart or migration
was required. README supplies exact online merge, direct download, isolated ZIP
merge and current-container audit commands for the operator.

## Latest correction: node deployment has ready-made defaults

The user rejects needing to configure four environment switches per collector.
Nodes now use `docker compose -f compose.node.yml up -d --build` with no .env:
default host listener 0.0.0.0:8790, API-only=1, automatic node merge=0, private
automatic token and persistent data. The standalone preset keeps the existing
Compose project/service/data paths, preserving data/token/UUID on a role switch.
Hub defaults remain loopback/H5 with the existing nginx. Optional existing bind
and host-port overrides are respected; they are not required node setup.
The now-unnecessary deploy/node.env.example was removed and README corrected.
All acquisition configuration remains in the hub. Nodes only need a running
service reachable through the network/security group and their first token
registered in the hub. Old-layout node migration uses migrate-storage.sh --node
for the complete build/stop/migration/start/health sequence.

Validation: actual Docker Compose config resolution with no .env confirms the
node/hub share project name http_backfill, build configuration and the exact
data bind mount. Node defaults resolve 0.0.0.0:8790 / API-only=1 / auto merge=0;
hub defaults still resolve 127.0.0.1:8790 / H5 / auto merge=1. Optional existing
bind/port overrides resolve correctly while node role flags remain fixed.
All 7 migration wrapper regressions and bash syntax/diff checks PASS. No source
requests, image rebuild, live service switch or remote deployment was performed.

## Latest correction: one hub, collectors registered by IP/port

The user clarifies that remote collectors do not have domains/nginx and may
only expose a private server port. H5 registration defaults to host IP, integer
port and HTTP scheme, and sends those fields to the hub. All controls and
evidence transfer remain authenticated server-to-server; the phone talks only
to the hub. Existing base_url/path-prefix registrations remain compatible.
Public connection fields are derived from existing registry URLs, preserving
UUID pinning, private tokens and transfer cursors without a data migration.

Collector startup uses the ready-made compose.node.yml described above.
It applies BACKFILL_API_ONLY=1 and BACKFILL_FLEET_SYNC_ENABLED=0 itself.
API-only collectors do not serve H5 or browser
sessions, but retain Bearer-authenticated control/export and /healthz. Only the
hub needs the existing public HTTPS /collector/ nginx location. Keep each node's
own data/UUID; no per-node .env is required.
The network/security group must permit the hub to reach the node port; no public
node domain or phone-to-node connection is required. README has exact steps.

Offline validation PASS with ResourceWarning treated as errors: fleet 20,
real authenticated three-server HTTP 6, actual app.js UI 12, server 11 and
activity/pagination 6 (55 total). The actual API-only node is registered by
IP/port, controlled/exported/merged through the hub, and survives registry/cursor
reopen; malformed/mixed addresses return 400 without a node/source request.
Real 390x844 browser validation registered an isolated API-only node through
the H5 and reopened its direct fields with an empty token input; document width
is 390 and the dialog fits. Local demonstration screenshot is retained at
runtime/screenshots/http-backfill-ipport-mobile-20261001.png (ignored by Git).
Compose's resolved node config verifies private host IP, alternate host port,
fixed container port 8790, API-only=1 and node automatic merge=0. JavaScript
syntax and diff whitespace checks pass. No live source traffic or remote
deployment was performed; temporary UI servers/databases were removed and
the temporary browser viewport override reset.

The previously untracked research run was organized separately: reports and
small evidence summaries are repository memory; full raw/JSONL/database exports
and historical diagnostic scripts were SHA-verified and archived outside Git.
See runs/guba-http-research-20260930/EVIDENCE-ARCHIVE.md. Its current handoff now
records the genuine request-78 challenge and incomplete backfill rather than
the stale preliminary PASS. Production/runtime data and the paused local pilot
were not moved or migrated by this cleanup.

## Latest correction: post counts and enrichment subsets

The user flags the misleading stock counters “window posts 293 / list text 241 /
body 52 / pending 0”. These are states of the same source posts, not separate
title/body records. The posts primary key remains (source, source_item_id), and
normal detail acquisition updates content on the existing row while retaining
its list title. Fleet also deduplicates by source identity; immutable versions
are evidence and do not inflate the selected posts count.

H5 uses acquired-post totals and explicit “among these” detail subsets. The
policy state list_only means enrichment was not triggered, not the number of
titles acquired or proof that short titles are complete. Stock totals prefer
the observed-post count from the API. No backend/schema/policy change or data
migration is required for this UI correction. Original title/body snapshot
semantics, source cooldown and task queues are retained.

Offline verification adds before/after enrichment assertions for the same IDs,
rowid, title, unchanged total and content on the existing row. UI verification
uses the user's 293/241/52/0 example against the actual app.js rendering.
Targeted strict-ResourceWarning regressions PASS: unified Engine 4, actual UI 9,
activity/pagination 6 (19 total). JavaScript syntax and diff whitespace checks
PASS. No source requests, backend changes or remote deployment were performed.

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
Other servers deploy the same app with their explicitly configured private
BACKFILL_BIND_ADDRESS/host port and register IP/port/token through the hub H5;
nginx/domain setup is only required for the existing public hub. Old node HTTPS
path endpoints remain compatible. Each keeps its own data directory/UUID. Their stock/date jobs are configured
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
