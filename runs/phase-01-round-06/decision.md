# Phase 1 Round 06 Decision — Historical Refresh Scope

## Decision

`SPEC_CORRECTION: APPROVED`

Phase 1 prioritizes reliable acquisition of new discussion content and honest
incremental boundaries. It does not become a historical mutation archive.

## Old rule

The prior frozen wording made any different facts for the same
`source_item_id` a new immutable observation/version without distinguishing
whether Phase 1 had actively acquired the historical detail. Read counts,
reply counts, `post_last_time` and edited body text therefore implied a need to
re-fetch every known old item.

## Why Round 5 exposed the problem

The Tester correctly applied the then-current wording. For a known ID with
`published_at <= committed watermark`, the implementation stopped before detail
acquisition, so a mutable-fact cross-condition returned `NO_NEW_DATA` without a
version. This was a real mismatch with the old spec, not a Tester error.

## Business value versus cost

For the current sentiment pipeline, new post bodies are the primary acquisition
value. Historical read/reply/like counters and post-publication edits are not
currently consumed as approved sentiment inputs. Re-fetching all old details
would add requests, anti-bot exposure, raw storage and observation/version
growth, plus incremental state and test complexity. The current evidence does
not establish enough business value to pay that cost in Phase 1.

## New frozen rule

1. Unknown IDs are eligible for list/detail acquisition.
2. IDs with valid `published_at > committed watermark` remain eligible even if
   previously seen; they must not be suppressed solely by ID membership.
3. A known ID with valid `published_at <= committed watermark` may confirm the
   incremental boundary without a historical detail fetch. Phase 1 does not
   guarantee detection of its later engagement changes, `post_last_time`
   changes or body edits.
4. If source-item/detail facts are actually acquired in an authorized Phase 1
   path, changed facts must not be silently merged or overwritten; they produce
   a new observation/version and drift evidence.
5. Boundary-page raw evidence remains retained. A boundary-page reappearance
   alone does not create a historical detail-refresh obligation.

## Explicitly out of scope

- Continuous old-post recrawl.
- All-history mutable snapshot tracking.
- Historical content-edit detection as a Phase 1 guarantee.
- A concrete recent-window size or refresh schedule.

## Future extension

A bounded recent-window refresh may be proposed later only with business
evidence, rate/operations approval and a new explicit scope. No window is
designed or implemented here.

## Round 5 reclassification

`SPEC_CORRECTED`

The Round 5 Tester correctly exposed a mismatch between the implementation and
the then-current specification. Product/spec review determined that the broad
historical refresh requirement was unnecessary for Phase 1, so the acceptance
requirement was narrowed rather than treating the Tester as wrong.

