# HTTP idle-time backfill — handoff

Date: 2026-10-04, Asia/Shanghai. Version: http-backfill.v5.

## Latest research: task routing through the existing local Clash port

The user asks whether different collector tasks can select different existing
Clash nodes while all use mixed port 7890. This is feasibility research; task
mapping, controller writes, live Clash configuration changes and core migration
have not been implemented or performed.

Read-only local evidence on 2026-10-04: Clash for Windows runs Premium
2022.07.07, not Mihomo. Runtime uses mixed-port 7890 and rule mode; Proxy is a
Selector with 99 choices (choices may include groups). The current controller
is loopback port 49961, a runtime snapshot rather than a stable deployment
contract. The existing collector Docker container can GET its version through
host.docker.internal. Runtime rules include GEOIP,CN,DIRECT and final MATCH,Proxy;
no Guba/Eastmoney-specific rule was found. Sending a request to 7890 therefore
does not itself prove a proxied exit. No Guba traffic was sent to check its
actual route.

An isolated configuration parser invocation of the installed core, using a
removed temporary directory and no listening ports, accepts MATCH,DIRECT but
rejects IN-USER,collector-task,DIRECT with "unsupported rule type IN-USER".
Modern Mihomo documents IN-USER for inbound authenticated username routing:
https://wiki.metacubex.one/config/rules/ . Its controller documents selector
changes with PUT /proxies/{group} and a name payload:
https://wiki.metacubex.one/api/ . New-core capabilities must not be assumed for
this installed older core.

Current collector configuration is node-wide; Engine passes URL/client/Referer
to one ProxyManager, without job/stock route selection. Curl and urllib already
support HTTP proxy authentication, but use the same configured credentials.
One instance currently accepts one unfinished backfill job; it can contain
multiple stocks and dispatches source requests serially.

Two candidate designs, neither yet implemented: (1) retain the old core, route
Guba to a dedicated collector Selector, and select its mapped node before each
serial source request; selection is shared state, including other traffic using
that group/rule, and independent workers can race. Do not change the everyday
Proxy group or claim concurrent isolation. (2) migrate to a core supporting
IN-USER, give each job/stock a distinct inbound proxy identity and map it to a
fixed node/group through the same 7890 port; authentication and existing browser
access must be handled in the migration rather than silently changed. HTTPS
CONNECT reveals the target host, not the encrypted stock URL, so host rules
alone cannot distinguish two Guba stock tasks. Preserve source pacing, halt
semantics and list/detail route affinity. Node names do not prove distinct exit
IPs. Persist intended selection and observed route evidence if implemented.

## Latest addition: Qingguo short-effect dynamic HTTP provider

The user authorizes adding Qingguo through the official API overview. The new
qingguo mode reuses ProxyManager transport/expiry/budget/fallback/recovery and the
same node configuration UI. Only URL validation, provider response/error parsing,
and mode recognition differ. Primary evidence and supported short-effect new API
endpoints are recorded in SPEC and README. Connect to server, retain proxy_ip as
provider-reported exit, and parse deadline using the explicit Asia/Shanghai
integration convention (official docs omit timezone). Proxy Authkey/Authpwd use
the existing private username/password; extraction API pwd stays in its URL.

Old allocate, long-effect and overseas products are excluded. Domestic long uses
longterm.proxy.qg.net/get; global long and short share overseas.proxy.qg.net/get
and the same JSON, so the final allowlist is domestic share.proxy.qg.net only.
The deadline distinction and primary evidence are recorded in SPEC.
The shared node daily cap is preserved when switching provider; a matching new
API is required. Both dynamic modes keep no-IP direct fallback and blocked-direct
suspension. Qingguo balance/auth/schema errors stop extraction until explicit
re-arm while direct remains available. EXTRACT_LIMIT_EXCEEDED is a temporary
fallback, not an assumed daily reset, because the code also means minute quota.

Files: proxy.py, Engine dynamic-mode recognition, static app/index and
SPEC/README/HANDOFF. No dependency, Compose entry, storage migration or automatic
provider switching is introduced. Python AST/JavaScript syntax, diff whitespace,
independent source review and isolated offline replays passed. Replays covered
official Qingguo parsing, server/exit separation, source-vs-API auth isolation,
secret redaction, cap/restart fallback, balance suspension, product-quota cooldown,
Mayi no-code response compatibility and provider-switch counter preservation.
The final domestic-only allowlist was verified after discovering global long and
short share an endpoint. An Engine replay confirmed Qingguo bounded fresh-IP
recovery at 60 seconds and direct fallback 429 stopping automatic retries, with
three mocked source requests accurately counted. No test suite, paid extraction, Guba request,
training import or running-collector change. Operators update nodes and the H5
host using the existing git pull / compose rebuild command, then configure the
selected node once. Existing direct/HTTP/mayi configurations remain compatible.

## Latest change: no-IP / extraction-cap direct fallback

The user explicitly requires returning to the node's original direct exit when
mayi cannot provide an IP. This supersedes older no-fallback and extraction-cap
halt statements below. Configured mayi remains saved. proxy.py selects direct
before any proxy/source attempt for local cap, provider failure/empty candidate,
insufficient expiry or cooled duplicate; auth/schema failure also selects direct
but pauses extraction until settings save/manual rotate. Local cap waits until
the next Shanghai day; transient failures wait at least 300 seconds or the
longer configured recovery cooldown, retaining counters across restart. Legacy
proxy.json files need no migration. Storage/config/concurrency errors still
prevent dispatch. Neither a proxy CONNECT attempt nor a source attempt is retried
through direct in the same fetch.

Status includes effective_mode and sanitized fallback state; request analysis
records actual direct mode, configured mayi and fallback reason. H5 displays the
direct fallback explicitly. Direct uses existing curl noproxy / urllib no-proxy
transport and does not change host VPN/TUN/routing. Pacing, original targets,
source validators and evidence remain. Engine allows an otherwise permitted
bounded recovery probe despite exhausted extraction cap, but suspends automatic
recovery if fallback direct encounters source verification/403/429. Explicit
manual retry permits one original paced probe; it does not arm auto recovery or
clear the original halt/evidence before source validation. Manual rotate or a
configuration save can also prepare the next request. Provider auth/schema
extraction suspension is re-armed only by rotate/save, not ordinary manual retry.

Source/response changes are limited to proxy.py and Engine proxy scheduling;
static app/index and SPEC/README/HANDOFF record the operator behavior. Verification
passed Python AST/JavaScript syntax, diff whitespace, independent source review
and isolated temporary-directory replays: cap/restart/day rollover, five-minute
no-IP backoff, restored mayi candidate, auth extraction suspension/manual save,
direct block suspension, generation race, no-send fallback ledger failure and
storage lockout. An Engine replay verifies direct 429 stops automatic recovery,
manual retry retains original evidence and the 120-second Retry-After floor,
and only its two mocked source dispatches count as source requests. These checks
use mocked transport and removed temporary state;
no test suite, supplier/source network, training import or live-task change.
Update mayi collector nodes and the H5 host using the existing git pull / compose
rebuild command; the existing local container is not automatically restarted.

## Latest correction: official mayi success JSON without code

The user provided the supplier's API example with success=true, message and a
single candidate containing ip/port/expire_time/user/pass, but no code field.
The previous implementation incorrectly required code=200 and would reject
this successful envelope. The parser now accepts absent code, while an explicit
code still must be integer 200. All single-candidate, authentication and actual
expiry checks remain. SPEC and the deployment guide record this compatibility
correction and panel instructions: change num=2 to num=1, retain the generated
product/lifetime parameters, let candidate user/pass supply authentication,
and use zero proactive-rotation thresholds when only expiry renewal is wanted.

The user's example was explicitly non-live/unfunded; no account key, full
account API URL, candidate credentials or IP were saved in repository artifacts.
No supplier extraction or Guba request was made for this change. Python AST
syntax, diff whitespace and independent read-only review passed; no test suite,
real data import or collection-control action was performed. The running local
Docker container remains on its existing image until the operator updates it.
Collector nodes using mayi need the existing git pull / compose rebuild command
to run the corrected parser; no database or proxy-config migration is needed.

## Latest H5 layout: compact output toolbar

The user asks to place database status and selected-node exports on the same row
as the isolated-output heading. The static H5 toolbar now shows title, database
state/path/brief sync and current-node CSV/JSONL links together, wrapping on
narrow screens. Full path, timestamp/format and storage explanation are in a
default-collapsed database disclosure. Storage errors remain always visible
outside it. Download routing, all-task node export scope and frozen click-time
identity remain; exports are outside the metadata-dependent hidden element.
Switching nodes closes the disclosure and removes stale storage errors.

Only static index/app/styles and documentation changed. JavaScript syntax,
unique HTML IDs, diff whitespace and independent source review passed; no test
suite or origin request was used. The existing local Docker container received
only these three static assets, with no restart or collection-control action.
Public localhost asset bytes match the workspace, healthz returns 200, and the
container remains healthy. Refresh the local page to see it. The image can be
rebuilt normally from this commit; remote H5 hosts use the existing update
command. Collector nodes need no backend update for this layout change.

## Latest change: dynamic mayi and generic local source proxy

The user authorized implementation after clarifying dynamic mayi IPs, local
HTTP/mixed proxy reuse, arbitrary replacement of blocked candidates, and
selected-node central configuration. Developer work by Codex updates the frozen
SPEC and implements proxy.py, Engine scheduling/ledger integration, authenticated
node/fleet API routes and the existing mobile console. No extra deployment file,
package/apt dependency or storage migration is introduced. Other browser/backfill
working-tree changes are unrelated and must remain outside this commit.

Default direct mode remains. Settings save pauses/waits and remains suspended;
start/continue arms explicitly configured mayi recovery. Source-only curl and
urllib support explicit HTTP/mixed endpoints, Basic proxy auth, environment
bypass prevention and separate CONNECT/auth/provider failures. Public route
metadata is recorded before dispatch; no-origin provider/CONNECT failures are
not counted as source requests. Existing source validators, raw evidence, post
identity, enrichment threshold, training imports and global pacing remain.

Mayi uses the account-generated num=1/type=2/mode=1 JSON extraction URL, actual
Shanghai expire_time and one candidate at a time. Reuse a lease, refresh before
expiry, or rotate by explicit elapsed/count thresholds. Daily extraction cap is
mandatory and durable; failed extraction/duplicates count. Optional auto recovery
is off by default, requires explicit cooldown >=60 and max consecutive attempts
>0, and schedules at most one paced existing-target probe after source/proxy
block. Validated success permits existing resume calibration, not a direct jump
to an assumed page. Authentication/schema/expiry/storage errors are manual.
Exhaustion suspends automatic recovery across midnight/restart; manual controls
and scope changes revoke permission. Current-route source success resets the
consecutive recovery counter. In-flight pause/rotate cannot reactivate a revoked
control generation. Old/archived scope probes never auto-resume collection.

Node runtime proxy.json is owner-only 0600; credentials/API are not returned,
exported, included in jobs or process arguments. Blank fields preserve secrets,
explicit clear flags delete. Settings are managed once on the selected collector;
fleet/health/login/export routes remain direct. Generic local proxies do not
control Clash or OS/TUN/routing. Local port and actual external IP differ.

Read-only local checks before implementation found macOS proxy port 7890 and
container host.docker.internal:7890 TCP reachable. TCP reachability does not prove
proxy authentication, exit identity or Guba availability. New code has not been
enabled on the live collector or deployed to remote nodes in this change. No
source/provider network requests, real data import, live-job change or test suite
was used for implementation verification. Required nodes and main H5 host update
through existing git pull && docker compose up -d --build; startup retains the
existing safe pause/block rule. Remote nodes need this code to use source proxies.

Verification completed so far: Python AST/syntax and diff checks; isolated
temporary-directory Engine replays confirm Retry-After floor, one validated
recovery with anchor calibration, manual pause plus config save staying stopped,
recovery budget exhaustion remaining suspended after day rollover, private API
absence from public status, 0600 file mode, and provider failure creating no
source dispatch/count. Final module/UI syntax and diff checks passed. Independent source review
confirmed source validation, CONNECT isolation, pause/scope generation, budget
suspension and send-time lease expiry checks. A real loopback-only denial server
received exactly one authenticated CONNECT from each of curl and urllib; both
classified 407 as proxy_auth with no origin dispatch/body. Module-level isolated
replays also covered documented JSON/Shanghai expiry, secret redaction, durable
budgets, cooling duplicates, late responses after rotate, extraction/control
races, save rollback/lockout, provider interruption and pre-send expiry. No
verification files were added to the repository.

## Latest correction: download follows the selected collector

The user identifies that the H5 local-post download always uses the hub's own
database. The selected-collector button now routes remote downloads through
authenticated api/nodes/{alias}/download/posts at the hub and keeps fleet export
separate. The clicked node is frozen for URL, filename and completion/error
messages; changing selection updates ordinary link targets. Remote downloads use
the node's existing local download API and private registry token, with UUID
verification, no redirects or fallback, bounded file streaming and complete
attachment validation before browser HTTP 200. Node auth errors do not expire
the hub browser session. Data format, source acquisition and queues are unchanged.
SPEC and README describe the new meaning. Python/JavaScript syntax and diff
whitespace checks passed, with independent source review of both routing and
download validation. Existing live localhost CSV and JSONL downloads each
contained 5,609 posts and passed file/type/count validation. The new authenticated
api/nodes/local/download/posts route also returned a verified 5,609-post JSONL;
served H5 assets match the workspace. This local hub has no registered remote
nodes, so live remote relay remains unverified; no fake registrations or test
suites were created/run, and no Guba requests were used as verification.

For local deployment, pause was requested and request_inflight=false confirmed,
then only fleet.py/server.py and the two H5 assets were copied into the existing
container and it was reloaded. The previously running job 3 for 002353 retained
its exact configuration and resumed at the existing 60-second interval with no
active halt. No root training database or unrelated source changes were deployed.
The container's writable files are updated; a normal image rebuild from this
commit makes the same change durable across container replacement. Remote
deployment uses the original git pull / docker compose up -d --build command
on the central H5 host. Nodes already serving api/download/posts need no upgrade.

## Latest correction: resolved block evidence leaves the alert card

The user rejects a permanent prominent historical CAPTCHA alert after later
source acquisition. The H5 card now follows active_halt, with blocked/error
fallback for older nodes and separate storage-error handling. It prefers the
latest blocking evidence; an unresolved halt stays visible even during a queued
probe. Resolved evidence appears only in a default-collapsed entry under task
history. Polling preserves an explicitly expanded entry; switching instances
hides, clears and folds it before loading the new instance. If a restart/anchor
halt differs from the retained evidence kind, its current reason is shown with
unknown request facts; the old response stays historical. No raw responses,
request history, data or backend halt/probe rules are changed. Changed app.js,
index.html and SPEC.md. JavaScript syntax and git diff whitespace checks passed,
with independent read-only review of source/probe/storage and node-switch cases.
The two static assets were replaced in the existing local container; direct
localhost responses match their workspace SHA256 and use Cache-Control no-store.
Container ID, start time and restart count remained unchanged. Refresh the local
page to load this display change. No test suites or source requests were used for
verification. Remote controls need only the updated central H5 deployment; no
node backend upgrade is required for this change.

## Latest correction: one Compose deployment entry

The user rejects the extra compose.node.yml. That duplicate preset is removed;
every instance uses `git pull && docker compose up -d --build` in the existing
app directory. Export commands also use ordinary docker compose. Existing
compose.yml, service name, ./data mount, .env overrides and server capabilities
remain the shared deployment path. A node does not need its own nginx/domain or
reverse registration; the hub uses its IP/port and generated token.

migrate-storage.sh keeps --node only as an old-command compatibility alias with
an explanatory message; it never references a separate file. Its offline
build/stop/migrate/start/health order remains. For historical nodes that used the
old preset without .env, explicitly retain a routable BACKFILL_BIND_ADDRESS in
the existing .env: the removed preset defaulted to 0.0.0.0, while ordinary Compose
defaults to 127.0.0.1. No database migration is required for this cleanup. The
API_ONLY/SYNC environment options remain optional, not mandatory node setup.
Historical node-preset descriptions later in this handoff are superseded by
this section. No containers or runtime data were modified, and no suites ran.

## Latest change: operator-requested Chrome UA and Referer selection

The user explicitly amended the isolated app's header policy. Both curl and
urllib now use fixed desktop Linux Chrome 154 UA (major version read from local
installed Chrome, reduced UA version form). List page 1 sends Google origin;
page N > 1 sends this bar's page N-1 URL. A detail task's initial attempt chooses
30% Google origin, 10% Baidu origin, 60% its nearest retained validated list
observation (latest in the same job/bar, then original list_request, then queued
list page). Search origins model the default cross-site referrer policy; no
keyword pool/search request/claimed Google or Baidu navigation is needed.

The selection is stored before I/O in existing requests.analysis, including
request_profile/request_headers/referer_source/referer_list_request_id, and
retained through outcome parsing, failed attempts, restart and redirect hops.
Retries reuse the selected Referer; pre-upgrade attempts get the new policy on
their next permitted attempt while retaining old facts. Block evidence and new
immutable export versions retain the headers. H5 request records offer a
collapsible view; old records are not backfilled with invented header facts.
Wire VERSION stays http-backfill.v5 so existing fleet version validation keeps
accepting upgraded nodes; status.http_request_profile.version=chrome-referer.v1
identifies the additive request policy.
No table columns changed; a task-ID request index is created at ordinary schema
initialization. Root training database and unrelated dirty files are untouched.

Changed: core.py, unified_store.py, static/app.js, README.zh-CN.md, SPEC.md and
the run scope. This is an implementation change, not a live sustained-access
result. No source requests or live-worker restart were executed for validation
here, and no test suites were run per the user's standing preference. Python
compile of core.py/unified_store.py, node --check static/app.js and git diff
--check passed; the worker's actual long-run availability remains unverified.
Normal deployment: git pull && docker compose up -d --build on every instance.
All collecting nodes need the new image. Restart remains
paused or source-halted until the operator continues/probes by existing rules.

## Current unresolved goal: sustained backfill of large historical gaps

The user recalls that prompt-engineering also encounters CAPTCHA during large-gap
curl backfill. Current app functionality must not be presented as resolving that
access problem. Stock/page list URLs do not transmit the local date window, so
date gap correlates with volume/duration/depth/detail work but is not an identified
server-side trigger. Fixed >=60-second pacing, failure stop and resumability are
controls whose long-term access effectiveness remains unproven. The 2026-09-30
research ledger stopped on a genuine challenge at request 78 after 77 successful
list requests; this remains distinct from current 60-second app operation.
The previous prompt-engineering audit also records both page-1 failures and
ten-page successes; no universal page/date/request threshold or TLS/IP cause was
established. See experts/eastmoney-live-access/CURL-CFFI-AUDIT.md.
Direct large-gap log: prompt-engineering/logs/20260618.log:114 starts a 60-trading-
day 601012 backfill for 20260222~20260522. Lines 346~366 raise the budget to 50
pages for a target 114 days earlier, receive pages 1~4, then record CAPTCHA at
page 5 after about 5.1 seconds. The outer loop then continues other dates and
repeats first-page attempts. This is the old detector's recorded outcome; the
client's curl_cffi enablement and raw challenge bytes are not established by
that excerpt. It supports failure under historical workload, not a universal
date/page threshold or a conclusion about current 60-second pacing.
The earlier gap-audit turn only read existing evidence and recorded the corrected objective:
no source requests, live task modifications or test suites. The remote blocked
instance and its precise HTTP/challenge response are still needed for diagnosis;
the local node is a separate ongoing acquisition, not evidence of remote recovery.

## Latest correction: merge into the actual training database

The user explicitly names repository-root `data/collector.db` as the merge
destination for later training. This supersedes the old isolation restriction
only for the operator-invoked `training_import.py`; worker/fleet writes retain
their existing isolated paths. SPEC and run scope record this authorization.

The new CLI defaults to that existing training database, accepts `--source-dir`,
`--bundle` or `--jsonl`, and provides `--target-db` and read-only `--dry-run`.
Source directories produce a consistent verified evidence bundle without Engine
construction or source requests. ZIP preflight reuses the existing full hash/raw
and parser validation. JSONL accepts the existing H5 export and explicitly marks
linked raw evidence as absent, retaining the original file. Inputs are staged
before target mutation. Target HTTP/fleet ownership is rejected, including the
separate neighboring fleet `merge.sqlite3` ledger.

The original 15-column posts structure is unchanged. New source IDs insert once;
existing rows only receive missing fields/body, never replacing existing non-NULL
values (including acquired empty bodies). Non-NULL title/author/stock/publication/
URL identity differences block that post's update; body conflicts preserve the
existing body. Acquisition times and import time remain distinct. A single
BEGIN IMMEDIATE transaction includes posts and three collector_import_* tables
for receipts, immutable input versions and conflicts. Before mutation a separate
read connection creates a committed SQLite backup including WAL; the verified
input is durably retained. Repeated identical evidence is idempotent and a no-op
creates no additional backup or receipt. Browser state/coverage is never imported.

Actual local operation (not a fixture): exported a fixed 446-post snapshot with
182 bodies, 200 linked raw responses and 1,064 immutable evidence records to
runtime/imports/local-http-node-20261002.zip. Dry-run then actual import reported:
217 new posts, 165 existing posts supplemented with bodies, 64 unchanged, zero
identity/body conflicts and zero unexported source posts. Training count changed
215,901 -> 216,118; post-import body count is 20,163 (the 165 statistic counts only
existing-post supplementation, not bodies on new rows).

Import ID: 20261002T021128949450Z-179f2a7c. Backup:
data/collector.db.import-backups/20261002T021128949450Z-179f2a7c.db.
Retained evidence:
data/collector.db.imports/inputs/f13655a5730b943915107b4b4c1024bda1602111b793f0c075d29309e47505f9.zip.
The ignored operation receipt/audit is runtime/imports/training-import-20261002.json.
Readonly comparison against that backup confirmed no lost original rows, no
changes to any existing non-NULL field except the intended updated_at, identical
original posts columns and identical content hashes for backfill_resume,
backfill_coverage and backfill_page_anchors. Retained archive SHA matches receipt.
Readonly planning against the same fixed bundle after import found zero new
posts, zero updates and zero new evidence versions, with all 446 posts unchanged.
No test suite was run, following the user's instruction. No worker restart or
source request was made by this import. The user's local collector is continuing
to acquire data; the import is explicitly a fixed snapshot, not a live mirror.
The remote collector's previously mentioned 5,274 posts were not downloaded here.

README gives local direct import, remote ZIP export/scp/import and the convenient
single-console merged JSONL download followed by one local training import.
The existing DataClean read_collector_posts/fresh_pool path already reads this
original posts structure; no training/cleaning run was started.

## Latest fix: local H5 works without nginx

The user explicitly requires direct host:port login and asks not to run test
suites. The local .env now sets API_ONLY=0. Main Compose had forced the cookie
Path to /collector/, so a direct /api/session login succeeded but the browser
did not send that cookie to /api/status. Compose now defaults COOKIE_PATH to /
and supports an optional BACKFILL_COOKIE_PATH override. Existing nginx /collector/
also receives a root-path cookie; server auth/HTTPOnly/SameSite/Secure behavior
is retained. No source scheduler or data changes were needed.

Applied actual local Compose config with up -d --no-build (no apt/image build).
One actual host login flow returned H5 GET /=200, login POST=200 authenticated,
cookie Path=/, and /api/status=200 using only the received cookie. The temporary
verification session was logged out. Source state remains paused with 229 posts
and 179 requests. No broad/unit/browser suite was run for this fix as requested.
README and SPEC document direct login and compatibility with nginx subpaths.

## Latest operation: local Docker startup recovered after legacy migration

The user's locally deployed collector was Restarting(1). Actual Docker inspect
showed the requested 0.0.0.0:8790->8790 PortBindings and the correct bind-mounted
app data, while runtime Ports were empty. Startup logs showed v5's intentional
legacy-layout refusal because this local data still contained experiment.sqlite3.
The .env port settings were effective; no port mapping implementation bug was
found. No native 8790 listener or active worker.lock owner remained.

The root agent stopped this restart loop, used the user's already built image
to run the existing stopped-worker migrator, and restarted the same service
without rebuilding/apt. Migration passed complete ledger/body/raw verification:
179 requests, 229 posts, 176 acquired bodies and all 179 raw files preserved.
Receipt is data/storage-migration.json; backups remain under
data/migration-backups/20261002T013506Z-cb7f3c4320b54fe98a9f407f9e8ed078/.
The runtime legacy file was removed only after validated publication.

Actual host GET http://127.0.0.1:8790/healthz returned 200 and {ok:true}; an
authenticated /api/status returned v5, paused, the preserved counts and a scoped
rate audit with minimum recorded gap 60.00268292427063 seconds. Docker was running,
healthy, Restarting=false, RestartCount=0 with published 0.0.0.0:8790->8790/tcp.
Source request count remained 179. No real source requests, automatic task start,
token output or production-root data access was required.

At the initial recovery the .env API_ONLY=1 and FLEET_SYNC_ENABLED=0 were preserved.
The user subsequently selected local H5/API_ONLY=0 and explicitly requested the
direct-login fix recorded above. README now
puts legacy migration requirements before both hub and node startup commands and
explains the misleading empty runtime Ports during this failure. Existing guard
and migration behavior remain correct; no runtime code change was necessary.

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
No new live source traffic was required for v4 verification. Those clone checks
did not upgrade the original local trial. On 2026-10-02 the user deployed Docker;
the original local data was migrated and the v5 API node recovered as recorded
above. It remains paused with its retained evidence.

All results remain observed_pages_only, coverage_complete=false,
dataset_complete=false and model_database_eligible=false. Local reconciliation
does not prove deleted/unavailable or never-observed history complete. A queue
ending does not establish a one-year dataset. Keep partial data in this isolated
research store and evaluate future long trials using the actual request ledger.
