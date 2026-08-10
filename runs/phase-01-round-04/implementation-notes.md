# Phase 1 Round 04 Implementation Notes

## Round 3 finding → fix → regression evidence

| Tester finding | Fix | Regression test/result |
|---|---|---|
| 429/5xx used the 403 budget | `_fetch` now keeps `max_attempts` for timeout/429/5xx and applies `access_block_attempts` only to 403 | `test_429_uses_three_attempt_retry_budget`, `test_5xx_uses_three_attempt_retry_budget`, `test_403_keeps_two_attempt_access_block_budget` — passed |
| Detail-only source changes were suppressed | Duplicate IDs outside an old watermark re-acquire detail; merged detail facts are fingerprinted and versioned on drift | `test_detail_content_drift_creates_new_observation_version` — passed |
| Seen IDs newer than watermark stopped the boundary | Publication-time eligibility is evaluated before duplicate suppression; first eligible observation is emitted once | `test_seen_id_newer_than_watermark_is_eligible` — passed |
| Structural mismatch discarded raw bytes | List/detail raw bytes are written to the evidence store immediately after transport and before parsing | `test_first_page_schema_failure_is_spec_mismatch` now asserts one retained raw snapshot — passed |

## Boundary

No production persistence, DataClean envelope, scheduler, new source or source
semantic change was introduced. Round 3 remains immutable historical evidence.

