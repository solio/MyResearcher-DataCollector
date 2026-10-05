# Low-frequency HTTP backfill console — experimental contract

## 2026-10-05 cheap stable-frontier checks and bounded recalibration — authorized

Read-only local evidence for 002353 requests 2230..2237 shows generation 145
scanning 140/141 twice with identical IDs, zero drift and zero discoveries.
Eight paced detail tasks plus retries caused the 300-second list-delay trigger;
normal detail work does not itself demonstrate pagination drift. For ordinary
delay/resume/restart/periodic checks, first reread the last acquired forward page.
An exact full ordered match of explicit non-pinned standard IDs and publication
times, valid descending order and a known source count with no decrease permit
continuation at that physical page+1. Record proof only as last_forward_page_stable,
never as two-pass interval reconciliation or complete window coverage.

If that page changes or a baseline is unavailable, retain the response and fall
back within the same generation to the existing fixed-anchor two-pass scan.
Observed drift/count loss, nonstandard/unknown anchors and terminal date/tail
checks still require that scan directly. Preserve the original trigger across
pause/restart so risk recovery cannot become an ordinary cheap check. Successful
checks update the exact frontier request/count/rows and checked time. Previously
started ordinary recovery may use the cheap check on upgrade only without known
drift/fallback. Transient failures retain their target and original paced retry.

Bound successful calibration responses to 64 and complete scan passes to 6 per
recovery; persist counters across resume/restart and fallback. Exhaustion keeps
an explicit gap/error instead of scanning forever. Source challenges, one-shot
probe exceptions and restart cannot silently renew the budget. Only a successful
user-requested one-shot probe after calibration-limit halt authorizes a fresh
budget for subsequent manual continuation; record previous usage and retain gaps.
The response that exhausts navigation budget remains real_data when fully
validated, with calibration_limit recorded separately; retained posts must still
have successful source evidence for compatible projection and exports.
Original pacing, retained raw/observations, window/detail
filters, exports and training data boundaries remain unchanged. H5 shows the
trigger, cheap-check or two-pass mode and budget use without mislabelling proof.

## 2026-10-05 task-window statistics and observation labels — authorized

The user reports a historical task whose seek found its May upper cutoff while
the card still labels September/October prefix observations as actual coverage.
Separate requested_window, post_time_range (current active window), and
navigation_time_range (all successful list observations, including seek and
retained prefix) in status/H5. Existing coverage earliest/latest remain raw
forward/recovery list facts for compatibility, exposed as forward_time_range;
never reinterpret them as window post bounds. A seek can discover valid window
posts before sequential coverage begins; labels must disclose this distinction.

Current job/stock post and detail counts and the active api/posts preview require
current configured stocks, eligible associations and publication epoch within
Shanghai start-of-day and inclusive effective upper cutoff. Recheck dates in
queries, not just stored eligible=1. Source times have second precision; use
integer epochs so end-of-day microsecond rounding cannot admit next midnight.
Operational item times must be complete ISO timestamps with an explicit offset;
naive, missing or invalid values are excluded rather than assumed to be UTC.
Deduplicate the aggregate across stock associations. Preserve historical raw,
posts, flags and all-retained node exports; do not delete or rewrite dates to
make the card match. Report excluded old/invalid flagged records as diagnostics.
Ordinary dispatch skips stale out-of-scope tasks without deleting evidence;
original blocked one-shot probes retain their authorized exception. Older node
APIs lacking these fields remain explicitly labelled with their old list/count
semantics, never silently advertised as current-window post coverage.

## 2026-10-05 historical-window seek and calibration cost — authorized

The user supplies a live request ledger showing a historic-window task still
walking from page 1 and four repeated calibration requests between consecutive
forward pages. Implement date-window entry seeking in this HTTP worker, with
one scheduled source request at a time and durable job/stock seek state. A fresh
head page establishes current source facts; existing local runtime page/time
anchors are hints only. Reuse the existing page_anchor helpers and bracketed
doubling/bisection approach to locate the effective inclusive upper cutoff,
then start sequential forward collection conservatively one page earlier.
Never derive a proof from uniform post density or reported total alone.

Use only source-explicit non-pinned type0 rows for date bounds, validate their
ordering and live bound consistency. Valid empty pages can bound the search,
not prove target-window coverage. Unusable/contradictory bounds or exhausted
probe budget remain visible errors. Seek samples retain raw/request/row/post
facts and eligible detail queues, but do not advance forward-page coverage,
lower-bound confirmations or the continuous collection frontier. Seek work has
priority over sampled detail enrichment until the entry is located. Its requests
obey the same pacing, transient backoff and source block/probe rules. Pause,
restart, config revisions/removal/archive preserve scope and halt evidence.
Old unfinished jobs whose last standard frontier is entirely newer than their
upper cutoff may initialize seek on upgrade, with existing observations retained
and only obsolete operational prefix-navigation superseded. Never migrate past
an unresolved halt or restart live collection automatically.

Remove unconditional two-pass calibration after every forward page. Retain it
for pause/restart, at least five minutes since the last list observation before
another forward dispatch (including substantial detail processing), loss of
forward ID/time progress, source-count decrease, every 25 forward pages and
terminal date/source-tail confirmation. Ordinary consecutive pages can advance
once each; repeated detail batches may still require calibration. Known IDs from
earlier seek samples are not pagination stagnation: compare the adjacent live
frontier rather than all previously observed IDs. Periodic calibration verifies
its actual recent anchor interval only, never the entire preceding segment;
overall history/continuity remains unproven and coverage_complete stays false.
Revalidate the first chosen entry against live dates; a now-too-old entry must
reseek rather than silently miss the requested upper boundary. No new settings,
Compose entry, production-db writes or unpaced synchronous seek loop.

## 2026-10-05 transient network failures — explicitly authorized

TLS handshake/certificate-verification errors, request/connect timeouts, DNS and
connection interruptions are transport failures, not evidence of source access
blocking. Keep strict TLS verification. Classify known curl exit codes and typed
urllib exceptions; do not guess every unknown/configuration error is transient.
Retain failed bytes and request facts without parsing or advancing the target.
During normal running, keep the original target pending and retry it through the
existing scheduler after finish-based exponential backoff: original interval,
twice, four times, etc., capped at 15 minutes but never below the configured
global interval or retained Retry-After. No retry loop inside transport and no
new provider extraction for every origin TLS/read failure. Persist retry kind,
target, request, consecutive count and due time in existing meta/analysis;
successful target completion resets it. H5 distinguishes network retry waiting
from an access block. Manual pause wins over an in-flight transient result;
restart remains safely paused and archived/out-of-scope targets cannot revive.

Observed CAPTCHA/identity verification, source HTTP 401/403/429 and proxy 407
remain protective stops and take precedence even if body acquisition also fails.
A single manual or dynamic-recovery probe stays single: transient failure cannot
clear or replace an existing source halt/evidence or trigger generic retries.
Permanent/configuration, response-structure and local persistence failures remain
visible error stops; they cannot be accepted as acquired data. This amendment
supersedes older blanket transport-failure halt wording, not source-validation
or data-safety invariants. Legacy non-probe halts may be released to paused only
when their retained request identifies a known transient failure, has no source
block status/challenge bytes, and its original target is still in active scope.
Keep all historical request/raw/evidence and cooldown facts; upgrade never
automatically starts a collector.

## 2026-10-04 Qingguo short-effect provider — authorized

The user requests Qingguo support using its official API overview
https://www.qg.net/doc/2145.html. Add configured mode qingguo while preserving
existing mayi/direct/http configuration. Both dynamic providers share source
transport, actual lease-expiry guard, node-wide persisted extraction counters,
rotation, bounded recovery, no-IP direct fallback, source evidence and pacing.
Switching providers does not reset the node's daily extraction/recovery counters.
Require a matching new API URL when switching providers; blank secrets still
preserve the current provider's configuration. No Compose/dependency/migration.

Support the new domestic short-effect JSON GET endpoints share.proxy.qg.net/get
and share.proxy.qg.net/aggregate/get.
Require a single nonempty key and num absent or exactly 1; keep other generated
product/account parameters, including extraction-auth pwd. Do not accept legacy
proxy.qg.net/allocate or long-effect APIs: their contracts differ and long-effect
deadline is release eligibility rather than expiration. Also exclude
overseas.proxy.qg.net/get: short/global-long products share its URL and JSON, so
they cannot be distinguished safely by this configuration. Primary docs:
https://www.qg.net/doc/2255.html, /2713.html, /1839.html, /1863.html, /6637.html.

Accept code=SUCCESS and data=[one candidate]. Connect through HTTP server
host:port, never proxy_ip, which identifies the provider-reported exit. Validate
server, proxy_ip and deadline (YYYY-MM-DD HH:MM:SS), applying the same 30-second
lease margin. The official docs do not specify the naive datetime's timezone;
Asia/Shanghai is the explicit current integration convention, not a live finding.
Qingguo proxy Authkey/Authpwd are supplied through existing username/password,
or both remain blank when the operator configured the provider's IP whitelist.
API pwd remains in the private URL and is not synthesized from proxy credentials.
See https://www.qg.net/doc/1574.html and /2283.html.

Map documented auth/permission codes to manual-rearm provider_auth; invalid
parameters/unknown schema to provider_schema; BALANCE_INSUFFICIENT to a visible
manual-rearm provider_balance. Temporary/no-resource/rate-limit codes use the
existing minimum-five-minute direct fallback. EXTRACT_LIMIT_EXCEEDED can be a
minute or daily quota depending on product, so do not infer next-day recovery
from it; only the node's own daily cap has that fixed meaning. See
https://www.qg.net/doc/1838.html and /2259.html. No account extraction or source
request is needed to implement this provider.

## 2026-10-04 no-IP fallback — explicitly authorized

The user requires mayi extraction limits or unavailable candidates to fall back
automatically to the collector node's original direct transport. This supersedes
the earlier no-automatic-fallback and extraction-budget-halts-collection policy.
Keep configured mayi mode and credentials so a later request can return to mayi.
Existing usable leases are reused; fallback is selected only before any source
or proxy CONNECT attempt, never as a second source attempt in the same tick.

Local extraction cap exhaustion uses direct until the next Shanghai day.
Provider failure/empty candidates, duplicate cooled candidates, or insufficient
expiry use direct and delay the next extraction by at least five minutes (or the
longer configured recovery cooldown). Provider auth/schema errors also use direct,
but suspend extraction until explicit settings save or manual rotation. All
extraction attempts, including failures, still count against the persisted cap.
Storage, configuration races and extraction concurrency errors remain no-send
failures, rather than being masked as provider unavailability.

Public status and each source request record actual direct fallback separately
from configured mayi mode, with sanitized reason and next extraction time. No
proxy credentials are attached to the direct request. Use core.fetch's explicit
environment-proxy bypass. Original global interval, Retry-After, halt evidence,
task/anchor state and source validation apply. A direct fallback source block
stops collection and suspends automatic recovery; only an explicit manual action
may arrange another paced probe. Reaching the IP extraction cap alone does not
prevent an otherwise permitted, bounded recovery probe through direct transport.

## 2026-10-04 compact output toolbar

The user asks to merge database status and selected-node CSV/JSONL exports into
the same row as the isolated-output heading. Use a wrapping toolbar: title,
database state/path/recent sync and current-node exports. Full storage layout,
format and explanatory text remain available in a default-collapsed disclosure;
storage failures stay visibly expanded outside that disclosure. Narrow screens
wrap without horizontal scrolling. Preserve selected-node routing, click-time
identity, all-task export scope, storage-error handling and old-node exports
when storage metadata is absent. This changes only H5 layout/text, not acquisition
or data. No test suite or source request is needed.

## 2026-10-04 source proxy contract — implementation authorized

The user explicitly authorizes implementation after clarifying dynamic mayihttp
IPs, generic reuse of the node's existing local HTTP/mixed proxy, and replacement
of blocked exits. This supersedes the preceding design-only restriction for the
isolated HTTP app. Default direct mode and existing source validation, post
identity, enrichment rules, data/import layout, selected-node control and global
finish-to-next-start pacing remain the contract.

### Selected-node configuration and routing

Provide direct / generic HTTP endpoint / mayi and Qingguo dynamic modes. The hub
configures the selected node through authenticated GET api/proxy, POST
api/proxy/config and POST api/proxy/rotate, with existing UUID verification for
mutations. The node persists settings once in owner-only proxy.json outside job
configurations and exported data. Public status exposes configured flags, leases,
counters and sanitized failures, never username/password or the extraction URL.
Blank credential inputs preserve existing secrets; explicit clear flags delete
them. Save requires no in-flight/queued source request and retains pause. It
must not request either provider or source, clear a halt, or arm background
recovery after the operator paused. Start/continue arms an explicitly configured
recovery policy. No extra Compose file, node SSH setup or global proxy variables.

Only source curl/urllib transport uses the chosen route. Fleet, login, health,
exports and provider extraction retain explicit direct transport. Environment
NO_PROXY must not bypass an explicitly selected source proxy. Both clients
retain the existing headers, TLS checks, request timeout/body bound, and no
transport-level origin redirects or retries. Distinguish proxy authentication,
CONNECT refusal, candidate/provider failure and source blocking. Credentials
must not appear in command arguments, errors, source analysis or exports.
Each source dispatch freezes public route metadata in the durable request
ledger before I/O. Provider failures are recorded as no source dispatch, with
an empty source body; ledger rows are distinct from confirmed source attempts.

The HTTP option accepts a generic HTTP/mixed endpoint and optional Basic proxy
authentication. It does not control Clash's API/secret, subscriptions, selector,
TUN or OS proxy. Local means local to the selected collector, not the hub or
phone. Docker Desktop reaches a host service through host.docker.internal;
native Python uses its host's loopback. Linux bridge requires a reachable host
interface or an explicit host-gateway mapping in the existing Compose entry.
Do not publish the host's proxy port or silently enable host networking. A local
proxy's own rules may select DIRECT; actual exit identity remains unknown.

### Dynamic extraction, rotation and budgets

Use the account-generated mayi HTTP extraction URL on mayihttp.com, with num=1,
type=2 JSON and mode=1 HTTP. HTTPS is preferred; an account-generated HTTP URL is
accepted. Do not hardcode account keys, product lifetime or a purchasing policy.
Accept success=true/data=[one candidate], including the user-provided official
example without a code field. If code is present, require integer 200; an explicit
contradictory or malformed code remains failure. Validate IP/port, optional
user/pass and actual Shanghai expire_time. Missing or insufficient
expiry cannot become an invented lease; select direct fallback under the latest
amendment. A new candidate remains source-
unverified until the existing validators accept real_data or a validated
unavailable-detail response; this dated reachability evidence does not imply
that a detail body exists, that the exit IP caused a block, or future access.

Reuse a lease within its observed lifetime, refreshing before it cannot cover
the source timeout. Optional rotate_seconds (0 disables; positive at least 60)
and rotate_requests (0 disables) choose proactive replacement between requests.
A daily_limit >0 is required for dynamic providers; count every attempted extraction before
network I/O, including failures and duplicates, persist across restart, and use
Shanghai calendar days. No silent per-request extraction and no retry loops
inside fetch. A blocked/cooled or explicitly replaced candidate cannot be
reintroduced as fresh during its exclusion period. Original raw responses,
source halt, Retry-After, queued target, resume anchors and interval are retained.

Manual rotate affects the next request, leaving in-flight evidence on its old
route. In blocked/error state it queues at most one existing paced manual probe;
validated success leaves collection paused for manual continuation. Changing to
direct/local mode is an explicit operator choice; configured dynamic modes also use
the authorized no-IP direct fallback without rewriting its settings.
Changing local upstream then manually rotating allows a new source probe without
assuming the endpoint identifies its external exit.

Optional auto_recover applies to dynamic providers and is off by default. Enabling it
requires explicit recovery_cooldown_seconds >=60, recovery_max_attempts >0 and
the daily extraction cap. After observed challenge/source 403/429, proxy connect
failure, unavailable provider or duplicate cooled candidate, schedule exactly
one existing-target probe after max(global next_due, Retry-After, configured
cooldown). Source validation is required before continuing through the existing
resume calibration. Authentication/schema failures suspend extraction while
using direct; insufficient expiry and exhausted extraction budgets also use the
authorized fallback. Parser/storage failures still require manual handling.
Recovery attempts count consecutive recovery probes, persist across restart and
configuration edits, and reset only after an accepted current-route source
response. Exhaustion suspends automatic recovery until manual handling; midnight
must not silently revoke this suspension. Manual pause/retry, restart, scope
edit/archive, or settings save revoke recovery permission. A control generation
and scope checks before scheduling and after response acceptance prevent an
in-flight automatic probe from resuming after such a control action.

### Evidence boundary

Proxy support does not establish that dynamic/local/direct routes are immune to
Guba challenges, improve long-run access, or explain a server blacklist. Keep
source raw evidence and unknown facts. Existing training imports are unchanged;
proxy settings are not part of posts exports. Verification for this change uses
syntax checks and isolated local replays, not source/provider requests or test
suites, and must not alter currently running jobs to infer proxy efficacy.

## 2026-10-03 selected collector posts download

The user identifies that the local export button ignores the selected control
node. Replace its H5 meaning with export of the currently selected collector.
Freeze the node identity/name at click time and update ordinary link targets
when switching selection; a download completing later still belongs to the
clicked node. Show the selected source in the button explanation and filename.
The separate fleet download remains the hub's already merged dataset.

For remote nodes, provide authenticated hub GET
api/nodes/{alias}/download/posts?scope=local&format=csv|jsonl. The hub uses the
registered private token to request the node's existing local posts download;
the browser talks only to the hub. Keep legacy api/download/posts local/fleet
semantics. Export includes that collector's retained posts across all its tasks,
not only the selected job. It neither requests Guba nor synchronizes data.
Verify registered instance identity, reject redirects, distinguish node auth
failure from hub session expiry, and never fall back to hub/local/fleet data.
Use bounded streaming to a temporary file, validate attachment/content type and
complete length, then send HTTP 200 with hub-generated node-specific filename
and safe origin metadata. The file path has a separate 2 GiB limit and download
deadline from the ordinary bounded JSON client. Existing download-capable nodes
do not require an upgrade. CSV/JSONL contents and training-import compatibility
remain unchanged; no test suites are requested for this correction.

## 2026-10-03 current blocking alert versus retained history

The user requests that old verification responses stop occupying the prominent
pause/evidence card once source access has resumed. Render that card only while
active_halt is set, with blocked/error state fallback for older nodes, excluding
storage-only errors. Prefer the latest blocking evidence over the first record.
An outstanding source halt remains current during a scheduled probe or new task;
later task timestamps alone never clear a halt. Once resolved, move retained
evidence to a default-collapsed entry under task history. Preserve its expanded
state during polling and reset it when switching instances. Source raw evidence,
requests, backend halt/probe behavior and all stored history remain unchanged.
If active_halt names a different cause from retained evidence (for example a
process-interrupted request after an earlier resolved challenge), display the
current halt cause without attributing the old request facts to it; keep the old
record in the collapsed historical entry.
This is a display change; no test suites or source-access attempts are required.

## 2026-10-02 large-gap challenge remains the unresolved objective

The user recalls the same failure in prompt-engineering: curl acquisition can
work initially but historical backfill over a large date gap encounters a
verification challenge. Treat this as user-reported history; compare with retained
logs rather than asserting that date span itself is the server's trigger.
The current list URL carries stock/page, not the local configured date range.
Request volume, duration, page depth, detail mix and recovery traffic therefore
remain separate explanatory candidates. Current 60-second spacing is a trial
setting, not a proven sustainable limit. A H5 console, safe stop, resumable queue
and training import do not establish sustained unattended historical acquisition.
Review already acquired blocking evidence before choosing the next intervention;
this follow-up leaves existing node tasks and source request settings untouched.

## 2026-10-02 explicit import into the training database

The user clarifies that merging means adding collected posts to the existing
repository `data/collector.db` for subsequent training, rather than importing
into the console's fleet store. This explicit instruction supersedes earlier
production-write prohibitions only for the separate operator-invoked importer.
The HTTP worker and fleet synchronizer keep their isolated runtime directories.

Provide one CLI accepting a node evidence ZIP, a local node data directory, or
the existing H5 JSONL export, with the training database as its default target.
Preflight the entire input before target mutation, keep the original 15-column
posts schema and `(source, source_item_id)` identity, and create a SQLite backup
including committed WAL before a write. Insert new posts; supplement NULL fields
and missing bodies on matching identities without replacing existing values.
Different non-NULL identity facts block that post's update; other field conflicts
retain the existing value and record both observations. An acquired empty body
is not NULL. Preserve original source/acquisition times and import time separately.

Keep import receipts, immutable input versions and conflict facts alongside the
training posts, without copying HTTP runtime queues or modifying browser
backfill_resume, backfill_page_anchors or backfill_coverage. A verified ZIP retains
its linked raw responses; a JSONL import retains the original export and explicitly
records that linked raw evidence was not supplied. Partial imports do not claim
complete historical coverage. Repeating the same input is idempotent. Provide a
read-only dry run and concrete remote export/local import commands. The user has
asked not to run test suites: use code review and actual import/receipt checks.

## 2026-10-02 direct local H5 login

The user requires login without nginx and requests an immediate fix without
running test suites. Compose must not force a /collector/ cookie path: default
to / so direct host:port and existing nginx subpaths both retain sessions. Keep
an optional BACKFILL_COOKIE_PATH override, existing authentication/HTTPOnly/
SameSite/Secure behavior, port settings and acquired data. Apply to the user's
current local Docker deployment and check the actual login/session flow only;
do not start source collection or run broad regression suites.

## 2026-10-01 inspect actual source pacing and provide usable exports

The user requests concrete data merging/export instructions and confirmation
of a collector showing 5,274 posts against a configured 60-second interval.
Posts and HTTP attempts are different quantities: a source list response may
contain many posts. Audit actual network-attempt rows, their start/end times,
configured interval and uncertain/incomplete rows; do not infer source pacing
from post counts. Provide a standalone read-only audit of the existing database
without starting a worker and expose a clearly scoped recent audit in H5.
Preserve original-source requests, redirect/probe pacing and all existing data.
Remote server pacing is unverified until its actual ledger is examined.

Retain online hub incremental merge and explain its output files and cursors.
Provide operator-usable manual evidence export/import packages for disconnected
nodes, using one consistent database snapshot, immutable export sequences and
hash-verified raw evidence. Exclude credentials and unrelated runtime tables,
reject unsafe/damaged archives, and import through existing MergeStore identity,
cursor/body/conflict rules. Export never fetches Guba or promotes model eligibility.
Provide authenticated download of local-node and merged compatible posts with
fixed snapshot semantics and without dumping private operational databases.
Verify duplicates/enrichment, raw integrity, idempotent imports, invalid exports,
pacing gaps/unknowns and zero source traffic using offline fixtures.

## 2026-10-01 collector startup without per-node configuration

The user rejects manually configuring deployment switches on every collector.
Provide a standalone node Compose preset usable with one standard command and
no .env: listen on host port 8790, API-only enabled, node automatic merge disabled,
automatic private token and persistent local data. IP/port/token registration
and all acquisition task settings remain only in the hub. The node preset uses
the same Compose project/service/data identity as the existing deployment so
switching roles preserves data; keep the hub's default loopback/H5 deployment.
Optional bind/port overrides remain for existing deployments, without being
required setup. Node port access is governed by routing/security groups.
Old database migration must be able to use this same node preset throughout
build/stop/offline migration/start/health checks, without restoring hub settings.
Validate resolved node/hub Compose defaults and wrapper selection/failure paths;
do not deploy remotely or send live source requests for these checks.

## 2026-10-01 direct IP/port collector registration

The user clarifies that additional collectors have no nginx/domain and may only
be reachable over a private server network. One public H5 on the hub is enough.
The primary registration form accepts a collector IP, port and HTTP/HTTPS scheme
(HTTP by default), plus alias/name/token. The hub constructs the API root and
performs all authenticated control and evidence transfer server-to-server.
The phone never connects to node addresses. Retain an advanced base-URL option
for already registered nginx path prefixes; existing identities/cursors remain.
Validate IPv4/IPv6 and integer ports, reject malformed or ambiguous mixed input,
and preserve existing credential/redirect/error handling.

Compose permits the collector's actual private bind IP and host port without
nginx or domain setup. An optional API-only mode suppresses static H5/login
surfaces on collectors while retaining authenticated control/export and health.
Only the hub needs the existing /collector/ HTTPS route. Document a concrete
node deployment and central-only registration, with security-group access from
the hub to that port. The hub must have a routable connection; the application
does not bypass firewall rules. Verify direct IP/port registration, restart,
remote control, transfer, old URL compatibility and API-only serving offline.

## 2026-10-01 post identity and detail-counter clarification

The user flags misleading counters that present list text and detail bodies as
two independent datasets. One source post remains one posts row, with its title
retained and acquired detail added to content on that same identity. Detail
completion counts are subsets of acquired posts; they are not additional posts.
Keep the established >=40 enrichment policy and storage semantics unchanged.
Rename H5 counters to acquired posts, details added, details awaiting acquisition,
and enrichment not triggered; show subset wording in stock/fleet summaries.
Explain that untriggered short-title posts have retained list records and are
not proof of acquired complete bodies. Verify title retention, stable row count
before/after enrichment and the 293/241/52/0 counter display offline.


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
- Ordinary curl or urllib transport with the explicitly authorized Chrome UA and
  Referer policy below; no browser, browser state export, CAPTCHA solving, proxy
  rotation, TLS impersonation or invented challenge tokens.
- One global source request initiation per >=60 seconds, including redirects,
  failures and manually requested probe; timing persisted across process restarts.
- Preserve raw response bytes/hash and every attempt. Network-level ambiguous
  failures are explicit; HTTP200 is insufficient. Strict decode/parse and
  list/detail identity/publication agreement precede accepting complete data.
- Real challenge and HTTP401/403/429 pause automatically. Unknown/partial
  structures remain error stops; classified transient transport failures use
  the authorized paced network retry policy above. Template captcha script alone is not a challenge.
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
Built-in transport callable `(url, client, referer=None) -> Response(status,
body, headers, url, error)`. Injected transports accepting a `referer` keyword
receive it; legacy two-argument injected transports retain their existing API.
Support is checked before I/O, never by retrying a failed transport call.
Clock returns epoch seconds. `tick` never sleeps and initiates at most one request.

## HTTP request profile — user amendment, 2026-10-02

Deployment correction: the user requires one existing compose.yml entry for
all instances. No separate node Compose preset is required or retained. Existing
.env port/bind and optional API-only settings continue to apply. The old
migration --node flag is accepted only as a compatibility alias for the same
entry, without changing runtime data or requiring node role configuration.

- Both real clients use the same fixed desktop Linux Chrome UA:
  `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36`.
  The Chrome major version comes from the locally installed Chrome 154; the
  reduced version format is fixed and does not rotate during a run.
- List page 1 uses `Referer: https://www.google.com/`. Page N > 1 uses the
  same stock's canonical page N-1 URL, including recovery-page requests.
- A detail task's first request chooses Google with probability 30%, Baidu 10%,
  and a nearby observed list page 60%. These are independent random choices per
  detail task, not guaranteed ratios for a small batch. Prefer the most recent
  validated list observation containing that source ID in the task's job/bar;
  fall back to its retained initial list request, then its queued list page.
- Google and Baidu source values are `https://www.google.com/` and
  `https://www.baidu.com/`, representing origin-only cross-site Referer under
  the browser's default `strict-origin-when-cross-origin` policy. No keyword
  pool or search-engine request is needed for these headers. This is a requested
  header simulation, not evidence that a search was performed. Policy reference:
  https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Referrer-Policy
- Repeated attempts and explicit redirect hops for the same task reuse its
  selected Referer. Old attempts without the current request profile get the
  new headers on their next explicitly authorized request, preserving old facts.
- The reserved request's existing `analysis` JSON records `request_profile`,
  `request_headers`, `referer_source` and `referer_list_request_id` before I/O.
  Outcome parsing retains those facts; request APIs, retained block evidence and
  new immutable export versions expose them. Older rows remain unobserved.
- Database layout, source pacing, challenge stop, manual one-shot retry and
  parser acceptance remain governed by their existing contracts. This amendment
  establishes implementation behavior, not sustained source availability.

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
