# Xueqiu DOM integration handoff

Status: `IMPLEMENTATION_PASS`

Next role: Independent Tester / reviewer

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

Required review focus:

- verify the managed browser produces the observed public DOM shape;
- independently inspect range/coverage behavior and page-level durability;
- decide whether to authorize a tiny, fresh-profile `SH601012` live smoke.
