# Low-frequency HTTP backfill console — experimental contract

## 2026-10-01 authorized v5 single-database migration

The user explicitly requests one migration command and deletion of the live
`experiment.sqlite3` after successful migration. This supersedes the earlier
separate local ledger/projection design; the root production database remains
outside this app's scope.

- `collector.db` becomes the app's sole local runtime database. Keep the original
  SimplePostStore `posts` columns and read interface; add app-owned ledger tables.
  Current verified body text lives in `posts` only. App post state retains list
  metadata and detail metadata without another current body copy; immutable
  federation versions and retained raw responses remain evidence.
- Preserve all ledger IDs, task queues, jobs, observations, coverage, recoveries,
  cooldowns, source halt reasons, instance UUID and immutable export sequences.
  Existing fleet files, tokens and raw response paths are retained unchanged.
- A stopped-worker-only offline migration uses SQLite backups (including WAL),
  validates integrity, complete ledger equality and retained raw hashes, publishes
  the unified database atomically, then removes `experiment.sqlite3` and its
  sidecars. Durable backups and a migration receipt make interruption and reruns
  recoverable. Failed validation never deletes the old database.
- Runtime rejects an unmigrated legacy directory with an actionable command;
  fresh directories create only `collector.db`. Migration never requests Guba.
  Startup stays paused or preserves a source halt, using existing restart rules.
- A single Docker wrapper builds the new image, stops the worker, migrates the
  mounted `/data`, and starts the service only after migration succeeds. Offline
  regression covers existing v1/v4 data, failures, interruption, idempotence,
  continued acquisition, export/merge and original posts-reader compatibility.


Status: v5 SINGLE-DATABASE MIGRATION IMPLEMENTED; LOCAL VERIFICATION PASS;
USER HAS DEPLOYED AN EARLIER RELEASE; REMOTE UPGRADE NOT EXECUTED HERE.
Date: 2026-10-01, Asia/Shanghai. Role: Developer.

## Confirmed user goal

Unattended idle-time, server-deployable public Guba acquisition without GUI.
First evaluate one source request per minute over a continuous run; the purpose
is to learn observed availability, time to challenge and useful data throughput.
Long-term date-window backfill can later extend to one, two, five or more years;
this version makes no promise that such source history exists or is accessible.
Mobile H5 console shows progress, pauses and evidence, and permits explicit retry.
This app is an isolated experiment, not a production transport amendment.

## Access/data boundary

- Existing `specs/eastmoney_guba.md` list/detail semantics and strict parsers apply.
- Public HTTPS f.html/f_N.html and exact source-observed standard type0 detail links.
- Ordinary curl or urllib transport; no browser, browser state export, CAPTCHA
  solving, proxy rotation, browser/TLS impersonation or invented challenge tokens.
- One global source request initiation per >=60 seconds, including redirects,
  failures and manually requested probe; timing persisted across process restarts.
- Preserve raw response bytes/hash and every attempt. Network-level ambiguous
  failures are explicit; HTTP200 is insufficient. Strict decode/parse and
  list/detail identity/publication agreement precede accepting complete data.
- Real challenge, HTTP403/429, unknown/partial response or transport failure
  pauses automatically. Template captcha script alone is not a challenge.
  Payload with challenge is blocked. Lack of JS execution remains an explicit
  observability limitation; do not label every challenge a proven IP blacklist.
- Block reason/evidence persist across restart. No automatic retry loop after a
  block. Manual retry schedules ONE probe at/after persisted rate/cooldown;
  success stays paused until user chooses continue. Retry never erases history.
- Isolated app-local SQLite and raw directory. Reuse SimplePostStore in the
  app's `data/collector.db`; never write the root production `data/collector.db`,
  model data or production coverage. Partial data stays in the experiment and
  export labels its completion/coverage. No automatic production promotion.
- For each list page, retain all source rows; queue in-window type0 details
  selected by the shared stripped-title-length >=40 rule before the next list
  page. Deduplicate source IDs and preserve bar associations. No semantic
  filtering or title substitution for missing detail bodies.
- A full job has explicit stocks/date range and per-stock list coverage plus
  per-item detail outcomes. Source exhaustion before requested history means a
  visible coverage gap. Valid empty source body is separate from nonempty body.
- Default app state is paused. User creates/configures and starts the trial.
  One worker process owns the SQLite/queue; process crash preserves evidence and
  pauses on unknown in-flight outcome. A browser merely observes/controls.

## Core API (Python)

`Engine(data_dir, transport=None, clock=None)` with `status()`,
`create_job(config)`, `update_job(config)`, `remove_stock(stock)`, `delete_job()`,
`jobs()`, `start()`, `pause()`, `retry()`, `tick()`,
`requests(limit=50)`, `events(limit=50)`, `raw_posts(limit=100, offset=0)`, `close()`.
Config: required `stocks` (array of six-digit strings), `from_date`/`to_date`
(inclusive Shanghai YYYY-MM-DD); optional `interval_seconds>=60`, `client`
(`curl` or `urllib`). Invalid input raises ValueError; state conflicts RuntimeError.
Transport callable `(url, client) -> Response(status, body, headers, url, error)`;
clock returns epoch seconds. `tick` never sleeps and initiates at most one request.

Status includes state (`idle/running/paused/blocked/error/completed`), reason,
job config, next_request_at, current target, cumulative attempts/list_pages/
unique_posts/body_complete/pending/removed/failures, last success, observed run
duration, blocking evidence and per-stock coverage/gaps. All timestamps explicit.

## H5 HTTP interface

- Relative static URLs and APIs work under `/collector/` nginx prefix.
- GET `api/status`, `api/requests?limit=50`, `api/events?limit=50`,
  `api/posts?limit=100&offset=0` return JSON. JSON responses not cacheable.
  Requests/events support `paged=1` ID cursor snapshots as specified below.
- POST `api/jobs` accepts config; POST `api/control` accepts
  `{action: start|pause|retry}`. Errors JSON with readable `error`, HTTP400 for
  invalid configuration, HTTP409 for state conflict. No external messaging.
- POST `api/session` accepts `{token}` and establishes an HttpOnly session;
  GET `api/session` reports authenticated state; DELETE ends the session.
  API requests require auth. Deploy token comes from env/local configuration,
  never tracked or shown in logs. Browser does not persist the secret token.
- H5 responsive Chinese UI: authentication, status/time since run/next due,
  list/body/remaining counters, current stock/page, reason and evidence,
  start/pause/single-probe retry, stock/date/interval/client configuration,
  recent requests/events and completion limitations. Polling status is local
  API traffic and does not cause source requests. Confirmation explains retry
  sends one real source request after cooldown, with no CAPTCHA handling.

## Delivery/verification

Offline tests with injected responses/clock cover timing, persistence, challenge,
schema failure, page/detail sequencing, coverage and API controls/auth. UI verified
at phone width. Provide runnable local entry, Docker/systemd/nginx examples for
Linux and existing labelapp style. No server deployment without target details.
Live one-minute stability remains unverified until a real continuous trial runs;
never infer reliability from offline tests or a short run.

## 2026-09-30 authorized single-server improvements

The user has started one server and explicitly requests implementation of the
identified gaps. The later v4 amendment below now includes multi-node control
and merge; one server remains usable without registering other nodes.

- A paused, quiescent current job can edit stocks, date range, interval and
  client. Removing a stock or deleting a job cancels its pending work and
  archives scope/configuration; acquired posts, raw responses and request
  history remain. Editing never clears source halt/cooldown. An archived halted
  target remains available for one explicit probe; deletion cannot bypass a halt.
- Add PATCH api/jobs/current, DELETE api/jobs/current,
  DELETE api/jobs/current/stocks/{stock}, GET api/jobs. The H5 form chooses
  create vs update and has explicit stock removal/job archive controls.
- Pause/restart and long detail processing mark source list position for
  revalidation. Persist ID/time anchors; rescan the anchor interval, enqueue any
  newly discovered details, compare successive observations and relocate the
  next physical page. Missing anchors/ambiguous order pause with evidence rather
  than guess. Every recovery request uses the existing global interval.
- Reconciliation reports observed interval checks, drift and outstanding
  details. It does not prove all deleted/unavailable source history; the
  production/model eligibility flag remains false.
- Extend known static challenge detection conservatively; normal template
  captcha resources alone are not a block. JavaScript-only challenges remain
  unobservable, so no 100% detection promise is permitted.
- Migrate v1 SQLite additively and keep existing data/token paths. Keep the
  Docker base and apt install instruction so the server can reuse its cached
  dependency layer. Offline tests must cover lifecycle, blocking, recovery
  drift, migration and authentication; no fresh source traffic is needed for
  implementation verification.
- Persist a stable instance UUID and include it in status, request and post
  query output for later provenance. Multi-node orchestration and merge are
  not implemented in this release.
- Prefer non-pinned type0 rows for time navigation: captured type20 placements
  can be out of publication order. All source rows remain evidence; all
  nonstandard intervals may compare IDs but carry time_order_unverified gaps.
  Two matching canonical (ID, publication time) sets are local interval
  observations, not proof of global completeness or identical display order.

## 2026-10-01 authorized reuse and record navigation correction

The user identifies missing activity pagination, divergent collector data shape
and unnecessary detail requests. This amendment supersedes the experimental
all-type0-detail policy above; acquisition of posts is not filtered by length.

- Reuse eastmoney_guba.content_rules.detail_enrichment_trigger. By default only
  stripped list titles of length >=40 queue detail acquisition, matching the
  existing browser enrichment rule. Short titles remain acquired list records,
  labelled content_source=list_title, never claimed as verified detail bodies.
  Existing detail bodies remain detail_body even for short titles. No title is
  discarded and no semantic/content-quality filter is added.
- Migrate already queued short-title details to list-only/skipped work without
  deleting evidence. An existing halted short-title target retains one explicit
  validation probe and cooldown; that probe cannot enqueue new short details.
  Counters distinguish list-only records, required details and verified bodies.
- Reuse the existing SimplePostStore and model mapping for an app-local
  data/collector.db with the same posts/backfill schema as the deployed browser
  collector. The independent experiment.sqlite3 retains task/request/recovery
  state and source provenance. Do not open or write the root production
  data/collector.db, invent completed coverage, or promote data to models.
  Missing detail content remains NULL in the compatible posts table; title and
  body are distinct. Real empty detail content remains an observed empty string.
- Project existing and new observations idempotently into the compatible store,
  preserving source IDs, original publication and acquisition times, nullable
  fields and acquired detail bodies. Validate retained raw hashes. Use per-write
  incremental projection rather than scanning all posts after every request.
  Raw/request data must commit first so a projection failure is recoverable
  locally without requesting the source again. No duplicate raw storage is
  required. Compatibility database adds no private ledger tables; projection
  bookkeeping belongs to experiment.sqlite3.
- Preserve GET api/requests and api/events array responses by default. Add
  paged=1 with limit (1..200), optional positive before_id and snapshot_id;
  return {items,has_more,next_cursor,snapshot_id,total}. Descending ID keyset
  traversal uses a fixed upper snapshot, so concurrent inserts cannot skip or
  duplicate records during older-page browsing. Reject malformed cursors.
- H5 gives requests and events independent previous/next/latest controls.
  Latest pages may refresh; historical pages retain their cursor and entries
  while status continues to poll. Rendering stays bounded to one page.
- Verify zero-source-request migration on a clone of the paused old database,
  idempotent reopen/projection, >=40 boundaries, retained completed short bodies,
  blocked-target migration, compatibility with original store/enrichment query,
  stable pagination during new inserts, authenticated API and mobile controls.

## 2026-10-01 authorized multi-instance completion (v4)

The user explicitly corrects the prior narrowed scope and requests the already
discussed one-console multi-instance management and merged data implementation.
This amendment supersedes prior statements deferring that capability.

- The existing server may act as hub and collector simultaneously. Keep the
  existing local service/data/token/8790/nginx deployment working. Register
  remote collectors through the authenticated H5; store remote tokens only in
  private server-side files, never return them or persist them in the browser.
  Explicit configured HTTP(S) base URLs may use private server networks; reject
  embedded credentials/query/fragments and do not follow credential-bearing
  redirects. Do not discover or contact guessed server addresses.
- A single mobile console lists local and registered remote instances, current
  status, connection errors, task/coverage/halts and merge progress. Selecting
  an instance routes its existing configuration, pause/continue, one-probe retry
  and paged histories through authenticated hub APIs. A remote timeout is
  distinguishable from acquisition failure. Mutating remote commands are never
  automatically retried after an ambiguous response. Source controls preserve
  each instance's own rate/cooldown and halt; no automatic IP failover occurs.
- The hub synchronizes local/remote acquired records in the background and on
  explicit sync requests. This transfers collector evidence only and never
  causes source requests. Per-instance immutable export sequence and durable
  cursor make transfers incremental, replayable and safe across restart.
  Raw bodies transfer with verified SHA-256/byte count, original request facts,
  instance/source identity and collection/publication times. Failed transfers
  retain the prior cursor and expose errors, not zero-data success.
- Merged data resides in app data/fleet/collector.db using SimplePostStore's
  original schema. Separate merge ledger keeps per-instance observations,
  source provenance, raw evidence, duplicate relationships, coverage snapshots,
  conflicts and synchronization cursors. It never joins SQLite files by copy
  or overwrites root production/model databases.
- Same (source,source_item_id) acquired on multiple instances is one compatible
  post with retained instance observations. A verified detail body may enrich a
  list-only record; later list-only/missing observations cannot erase a body.
  Genuine empty body remains distinct from missing body. Different identity or
  body facts remain explicit versions/conflicts with a deterministic selected
  projection; never silently blend text, select content by quality, or infer
  full historical/model eligibility. Disconnecting a node retains merged data.
- Verify real authenticated local HTTP servers end-to-end with injected source
  fixtures, remote node lifecycle/configuration/control/history, secret redaction,
  two-node overlapping record merge, raw integrity failures, incremental replay,
  cursor recovery, conflicts, restart and mobile instance switching. No live
  source traffic is needed for implementation verification. Supply exact
  existing-server upgrade and additional-server registration instructions.
