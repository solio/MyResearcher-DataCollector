# Phase 1 Round 08 — Independent Evidence

All probes use in-memory `MappingTransport`, generated deterministic HTML and
the existing local fixtures. No network request was made.

## Scenario evidence

- `test_unknown_id_at_or_before_watermark_is_still_eligible`: an unknown ID
  published before the watermark requested detail and emitted one observation.
- `test_watermark_confirmation_returns_no_new_data_without_detail_requests`:
  known old IDs did not request detail and reached `watermark_confirmed`.
- `test_seen_id_newer_than_watermark_is_eligible`: a known newer ID requested
  detail and emitted an observation.
- `test_mixed_incremental_page_handles_known_and_unknown_old_and_new_ids`:
  one page containing known-old, unknown-old, known-newer and unknown-newer IDs
  skipped only known-old and acquired the other three.
- `test_historical_known_first_page_does_not_stop_before_unknown_second_page`:
  a known-old first page did not stop before an unknown-old second-page item;
  the later empty page terminated normally.

## Regression evidence

The complete deterministic suite includes retry budgets, rate limiting,
redirect boundary, raw evidence on schema mismatch, partial outcomes,
watermark safety, drift/versioning, duplicate handling and parser contracts.

No implementation failure, source-spec conflict or environment blocker was
observed.
