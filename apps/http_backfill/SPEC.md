# Low-frequency HTTP backfill console — experimental contract

Status: LOCAL IMPLEMENTATION VERIFIED; 60-SECOND LIVE OBSERVATION RUNNING;
REMOTE DEPLOYMENT INSTRUCTIONS PROVIDED; REMOTE EXECUTION NOT PERFORMED.
Date: 2026-09-30, Asia/Shanghai. Role: Developer.

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
- Isolated SQLite and raw directory. Never write `data/collector.db`, model data
  or production coverage. Partial data stays in the experiment and export labels
  its completion/coverage explicitly. There is no automatic production promotion.
- For each list page, retain all source rows; queue all in-window type0 detail
  links before the next list page. Deduplicate source IDs and preserve bar
  associations. No semantic filtering or title substitution for missing content.
- A full job has explicit stocks/date range and per-stock list coverage plus
  per-item detail outcomes. Source exhaustion before requested history means a
  visible coverage gap. Valid empty source body is separate from nonempty body.
- Default app state is paused. User creates/configures and starts the trial.
  One worker process owns the SQLite/queue; process crash preserves evidence and
  pauses on unknown in-flight outcome. A browser merely observes/controls.

## Core API (Python)

`Engine(data_dir, transport=None, clock=None)` with `status()`,
`create_job(config)`, `start()`, `pause()`, `retry()`, `tick()`,
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
