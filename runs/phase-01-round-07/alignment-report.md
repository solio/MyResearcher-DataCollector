# Phase 1 Round 07 — Developer Alignment Report

## Decision

`DEV_ALIGNMENT: READY_FOR_RETEST`

The latest frozen `specs/eastmoney_guba.md` revision is the approved Round 06
correction. It removes any Phase 1 obligation to detail-fetch known IDs at or
before the committed watermark solely to discover mutable historical facts.

## Production code change required?

`YES`

`collector.py::collect` previously suppressed every row with
`published_at <= watermark`, including an unknown `source_item_id`. The code
now suppresses only an already-known historical ID. An unknown ID remains
eligible for detail acquisition, as required by the corrected SOURCE_SPEC.

## Tests changed?

- Updated `test_watermark_confirmation_returns_no_new_data_without_detail_requests`
  to identify every fixture row as already known; this keeps its boundary test
  aligned with the corrected known-ID rule.
- Added `test_unknown_id_at_or_before_watermark_is_still_eligible`, proving an
  unknown old ID is acquired rather than converted to `NO_NEW_DATA`.

## Old-SPEC expectations removed/updated?

No test is retained that requires historical known IDs at or before the
watermark to be detail-fetched for mutable-fact discovery. Round 5's historical
`OFFLINE_TEST_FAIL` and evidence are unchanged and remain the correct record of
the prior specification. The traceability document now states that drift is
required when facts are actually acquired, while historical refresh itself is
out of scope.

## Valid regressions preserved?

Yes. The existing deterministic coverage remains for 429/5xx three-attempt
budgets, 403 two-attempt budget, seen ID newer than watermark, authorized
detail-fact drift/versioning, schema-mismatch raw evidence retention, partial
watermark protection, and the broader Phase 1 acceptance set.

## Current implementation matches corrected SOURCE_SPEC?

`YES`, within the deterministic source-isolated boundary. Known historical IDs
may confirm the boundary without detail refresh; unknown IDs and seen IDs newer
than the watermark remain eligible; changed facts observed in an authorized
acquisition are versioned rather than silently merged.

## Remaining blockers?

No alignment blocker. OQ-01/OQ-02/OQ-03/OQ-04 remain unchanged and outside
this source-isolated round: durable persistence/raw manifest, DataClean
transport/envelope, operational scheduling and related contract decisions.
Historical tail/deletion behavior and an official numeric rate limit remain
non-blocking limitations. No live smoke was run.
