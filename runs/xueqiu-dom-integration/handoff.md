# Xueqiu DOM integration handoff

Status: `DISCOVERY_PASS_PRODUCTION_ENVIRONMENT_NOT_REPRODUCIBLE`

Next role: Operator-assisted live Tester, then Reviewer

2026-08-17 live re-investigation supersedes the earlier assumption that
Playwright managed Chrome is a usable production runtime.  See
`experts/xueqiu-live-access/2026-08-17-production-reinvestigation.md` and its
redacted JSON evidence.

Confirmed live behavior:

- Codex IAB and the user's normal Chrome load 10 public posts;
- Playwright managed Chrome enters an `md5__1038` loop with both fresh and
  normally initialized dedicated profiles;
- a project-owned Apple Events adapter against normal user Chrome has live
  page1/page2 success (10 + 10 unique IDs, zero overlap);
- the detail page exposes public `SNOWMAN_STATUS` JSON in a script after the
  global is removed; the fallback parser is implemented and offline-tested,
  but its final live retry was rejected by the desktop approval channel;
- persistence and the 3-day backfill have not run.

Current deterministic suite: `336 passed, 1 approved xfail`.

Do not report production PASS yet.  Next execution order is exactly:

1. run `experts/xueqiu-live-access/existing_chrome_smoke.py` with explicit
   desktop approval;
2. only if it passes, perform page-level persistence smoke;
3. only if that passes, run the bounded `601012 --days 3` backfill;
4. reconcile the older JSON-route SOURCE_SPEC with the later DOM integration
   scope before treating this as a frozen long-term production contract.

Read `scope.md`, `implementation.md`, and `test-results.md`. The approved
Expert source evidence is the commit and directory cited in
`implementation.md`; this correction run does not claim a live CLI smoke
result.

Changed production components are limited to the Xueqiu DOM transport/parser,
the unified backfill dispatch, and the shared posts upsert's optional source
creation timestamp. Existing Eastmoney and legacy Xueqiu paths remain intact.

Correction evidence:

- detail timestamp resolution uses a temporary page in the existing browser
  context and never restores/reopens the main list page on the normal path;
- manual unproven starts do not create reusable resume rows;
- exact frozen-range continuation remains resumable;
- full deterministic suite: 328 passed, 1 approved xfail.

Earlier implementation review focus remains:

- verify the managed browser produces the observed public DOM shape;
- independently inspect range/coverage behavior and page-level durability;
- decide whether to authorize a tiny, fresh-profile `SH601012` live smoke.
