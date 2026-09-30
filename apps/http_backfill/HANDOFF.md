# HTTP idle-time backfill — handoff

Date: 2026-10-01, Asia/Shanghai. Version: http-backfill.v2.

The user has deployed v1 on one server and explicitly authorized fixing stock
configuration editing/deletion and improving pause/recovery, challenge handling
and continuous list/detail work. This release targets that single server.
Multi-node scheduling, one central console and data merge remain future work.

Implementation is confined to this app plus a root Docker context allowlist.
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

## Validation

85 offline Engine, recovery, lifecycle and HTTP tests pass, including real Engine
behind authenticated APIs, insertion/deletion drift, long detail processing,
challenge during recovery, probes after archive, atomic edit rollback on damaged
evidence, legacy migration and restart/cooldown persistence. JavaScript syntax
and Docker Compose configuration pass.

A backup clone of the paused v1 real trial database migrated successfully:
179 requests, 229 posts, 240 row observations, 3 page observations and 179 raw
files remained unchanged; every recorded raw SHA-256 matched. Edit/archive and
reopen preserved data and instance ID. No source request was sent by this check.
The original trial directory was not migrated or edited by this verification.

Actual 390x844 browser verification used an isolated fake-response Engine without
a source worker. Saving interval=90, removing a stock and archiving a job all
produced the intended paused state; document scroll width equals viewport width.
The confirmation explains retained data and halted-state behavior.

Linux amd64 rebuild retained the base/apt instructions and reused the cached
curl/CA installation layer. The Docker context excludes data, credentials, Git
and unrelated research runs. Disposable container checks cover health,
authenticated v2 status, edit/remove/archive and Secure/HttpOnly /collector/
cookie path, with zero source attempts.

## Deployment and evidence limits

The user confirmed their v1 server build/deployment. v2 remote upgrade is not
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
No new live source traffic was required for v2 verification.

All results remain observed_pages_only, coverage_complete=false,
dataset_complete=false and model_database_eligible=false. Local reconciliation
does not prove deleted/unavailable or never-observed history complete. A queue
ending does not establish a one-year dataset. Keep partial data in this isolated
research store and evaluate future long trials using the actual request ledger.
