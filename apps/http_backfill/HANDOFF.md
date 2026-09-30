# HTTP idle-time backfill — handoff

Human requirement: run slow ordinary HTTP collection unattended, initially one
source request per minute; observe time-to-challenge, useful body throughput and
recovery; view/pause/one-probe retry from a phone; isolate incomplete data from
the existing model/collector database. Prior arbitrary page/sample targets are
superseded by this requirement.

Implementation: SPEC.md, core.py, server.py, static/, deployment templates and
README.zh-CN.md. All are confined to this app; production src/parser is imported
read-only and no production DB/config/source contract is changed.

Validation: 35 offline core/API tests pass; real static assets served; JS syntax
passes; Docker Compose config validates. Mobile 390x844 viewport has no horizontal
overflow; login/status/config/logs verified on an offline fixture, no source traffic.
Linux amd64 Docker image built using the same private Python base as labelapp.
Disposable container smoke check passed: curl installed, health/static assets,
paused initial state, authenticated status and Secure/HttpOnly cookie with
/collector/ path. No job was started in that container. Remote nginx/systemd
execution has not been performed or verified here.

Local service: http://127.0.0.1:8790/ . Real trial started on 2026-09-30
20:00:59 Asia/Shanghai, curl, 601012, global finish-to-next-start >=60 seconds.
Window 2025-09-30 to 2026-09-30 is a persistent trial work queue, not an already
completed one-year dataset. Effective upper cutoff is frozen at job creation.
Data and token live under app data/ (gitignored); token not in logs/repository.
`data/pilot-observation.json` is a dated initial observation; SQLite is the live
source of truth. Read API status/requests for current outcomes.

Do not infer sustained availability from a few successful requests. HTTP does
not execute JavaScript. Moving pages while details are fetched can leave history
unverified; outputs remain observed_pages_only, dataset_complete=false and
model_database_eligible=false. No automatic production export/promotion exists.
Any challenge/403/429/schema/framing/transport issue stops automatic requests.
Single retry respects due/Retry-After and returns paused even on success.

User requested code submission and server/nginx instructions using the existing
labelapp domain. README.zh-CN.md provides Git/Compose/login/update commands and
the /collector/ route on testapi.zuzurent.com.cn, reusing the private Python base
image and service-side build strategy. No remote SSH account or checkout path
was supplied; do not guess them. Preserve the labelapp namespace/port/data.
Remote deployment remains unexecuted; use an independent /collector/ route and
data volume when following the instructions.
Do not copy laptop partial progress into the production DB.
