# Phase 1 Round 07 — Developer Alignment Scope

## Role

Developer alignment pass after the approved Phase 1 Round 06 source-spec
correction. This round aligns the existing eastmoney_guba implementation,
deterministic tests and traceability with the corrected incremental semantics.

## In scope

- preserve the corrected distinction between unknown IDs, newer seen IDs and
  known historical IDs at or before the committed watermark;
- retain authorized-acquisition drift/version behavior and existing regression
  coverage;
- update only directly conflicting Developer traceability evidence;
- request an independent Tester re-test.

## Out of scope

No historical refresh machinery, source research, live smoke, Xueqiu,
DataClean integration, production persistence, scheduler, sentiment, finance,
trading or unrelated refactoring.

Round 5 artifacts remain immutable historical evidence.
