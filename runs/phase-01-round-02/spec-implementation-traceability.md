# Phase 1 Round 2 — Source Specification Traceability Self-Audit

Audit scope: Developer implementation in this Round only. This document is a
traceability record, not an independent test result and not a Code Review.

Frozen source specification: `specs/eastmoney_guba.md` (`SOURCE_SPEC: APPROVED`).
The statuses below are limited to the project-defined vocabulary. A Developer
test result means only that the Developer believes the mapping is satisfied;
it does not create `TEST_PASS`.

## Entry Point

### Public HTML and embedded assignments

Requirement:
Use the public server-rendered HTML entry point, parse `var article_list=<JSON>`
for list pages and `var post_article=<JSON>` for standard details, without
executing JavaScript.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- `sources/eastmoney_guba/parser.py::_embedded_json`, `parse_list_page`, and
  `parse_detail_page` use `json.JSONDecoder.raw_decode` on the assignments.
- No JavaScript runtime is used.

Test evidence:
- `tests/unit/test_eastmoney_guba_parser.py::test_list_parser_preserves_scope_and_source_times`
- `tests/unit/test_eastmoney_guba_parser.py::test_malformed_embedded_payload_is_schema_mismatch`

Notes:
The fixtures are local deterministic HTML, not live response bodies.

### Routes, method and source host

Requirement:
Use `GET` on page 1 `list,{stock_code},f.html`, page 2+ `list,{stock_code},f_{page}.html`,
and the exact standard detail link. Requests must remain HTTPS and source-owned.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- `sources/eastmoney_guba/collector.py::EastmoneyGubaCollector.list_url`
  constructs the approved routes.
- `UrllibTransport.get` uses `Request(..., method="GET")`.
- `parser.py::_source_url` requires HTTPS and an Eastmoney host allowlist for
  parsed item links.

Test evidence:
- The collector tests use the expected list and detail URLs in their mapping
  transport.
- `test_redirect_final_url_outside_source_boundary_fails_closed` verifies a
  final URL outside the source boundary is rejected.
- `test_collects_pages_details_and_overlap_idempotently` verifies final URL
  recording for an approved HTTPS final URL.

Notes:
`UrllibTransport` validates every redirect and final URL through its source
redirect handler. No live redirect was used.

### Request parameters and headers

Requirement:
Accept a six-digit stock code and page number at least one; send a descriptive
configurable `User-Agent` and normal HTML `Accept`; use no authentication or
cookies.

Status:
`IMPLEMENTED_NOT_DEVELOPER_TESTED`

Implementation evidence:
- `collector.py::collect` validates a six-digit code and `max_pages >= 1`.
- `UrllibTransport.__init__` and `get` set the non-secret headers.
- No cookie or authentication injection exists.

Test evidence:
- No test inspects actual request headers or invalid request rejection.

Notes:
The source spec marks future cookie/header requirements as unknown; no such
requirement is invented here.

## Pagination

### Sequential page traversal and per-page accounting

Requirement:
Start at page 1, traverse page numbers sequentially, produce an outcome and
counts for every requested page, and do not treat a failed page as empty.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- `collector.py::collect` loops `range(1, page_limit + 1)` and updates page,
  request and record counters.
- Fetch and parse failures use explicit failure paths.

Test evidence:
- `test_collects_pages_details_and_overlap_idempotently`
- `test_later_page_failure_is_partial_not_no_data`
- `test_first_page_schema_failure_is_spec_mismatch`

### Stop conditions and max-pages semantics

Requirement:
Stop at configured `max_pages`, a valid empty page, or the approved
incremental boundary. A coverage cap before boundary confirmation is partial.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- `collector.py::collect` uses `max_pages`, `empty_page`, and
  `watermark_confirmed` stop reasons; `max_pages` yields
  `PARTIAL_COLLECTION` unless a valid terminal condition was reached first.

Test evidence:
- `test_valid_empty_page_is_no_new_data`
- `test_timeout_retries_then_succeeds` asserts a max-page cap is partial.
- `test_watermark_confirmation_returns_no_new_data_without_detail_requests`

Notes:
The exact two-page confirmation path is exercised with synthetic overlap and
watermark data; no live moving-page run is claimed.

### Observed page size and historical coverage

Requirement:
Treat the observed page size and historical depth as observations/limitations,
not permanent completeness guarantees.

Status:
`INTENTIONALLY_DEFERRED_BY_APPROVED_SPEC`

Implementation evidence:
- No page-size or full-history claim is encoded in the adapter.

Test evidence:
- No full-history crawl is authorized by the frozen spec.

Notes:
Historical-tail completeness, deletion behavior and availability SLA remain
explicitly unknown in the approved spec.

## Item Identity

### Decimal source identity and list/detail agreement

Requirement:
Use decimal `post_id` as `source_item_id`; require agreement with the detail
payload and URL; reject missing/non-numeric IDs without fallback identity.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- `parser.py::_id`, `_source_url`, `parse_detail_page`, and
  `merge_list_and_detail` enforce the identity checks.

Test evidence:
- `test_missing_identity_is_rejected_without_fallback`
- `test_detail_identity_mismatch_is_rejected`

### Cross-bar identity

Requirement:
Retain requested and canonical bar identities; the same source ID observed via
multiple requested bars remains one logical source item with multiple
associations.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- `GubaListItem`, `GubaDetail`, `GubaSourceItem` retain both bar values.
- `GubaSourceItem.identity_key` is `(source, source_item_id)`.

Test evidence:
- `test_cross_bar_and_nullable_fields_remain_explicit` verifies requested and
  canonical bars remain distinct for the same source row.

### Identity/content drift and immutable observations

Requirement:
An identical re-observation is acquisition-idempotent; changed source facts
for the same ID become a new immutable observation/version and expose an
identity-content drift metric rather than being silently merged.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- `collector.py::collect` compares repeated list observations, increments
  `identity_content_drifts`, retains immutable raw references and emits an
  incremented `observation_version` for changed source facts.
- `GubaSourceItem.identity_key` remains the logical `(source, source_item_id)`;
  versioned observations are not silently merged.

Test evidence:
- `test_collects_pages_details_and_overlap_idempotently` covers identical
  overlap suppression and changed-count drift with versions `[1, 2, 1]`.

Notes:
Cross-run comparison can use the optional `existing_observations` mapping;
durable storage remains outside this source-isolated round.

## Fields

### Required source and observation fields

Requirement:
Emit the required source, source ID, requested bar, content, publication time,
collection time, URL, raw reference and source metadata fields.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- `GubaSourceItem.schema_version` and parser constant `SCHEMA_VERSION` provide
  the frozen raw contract version.
- `parser.py::_source_metadata` places unpromoted source fields in
  `source_metadata.extra`.
- Required promoted fields, source times, raw refs and final URL are present
  on the source item.

Test evidence:
- `test_collects_pages_details_and_overlap_idempotently` checks the schema
  version, final URL and raw refs.
- `test_source_metadata_extra_is_retained` checks unpromoted source fields.

Notes:
No production DataClean envelope is claimed.

### Nullable values, numeric zero and body semantics

Requirement:
Preserve nullable author/title/metadata values, distinguish missing from
numeric zero, preserve an empty source body, and never substitute title for
body.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- `parser.py::_optional_int` distinguishes `0` from `None`.
- `merge_list_and_detail` passes through the detail body without title fallback.
- `parser.py::_optional_text(..., preserve_empty=True)` retains an empty title
  as `""`; nullable author and metadata values remain `None`.

Test evidence:
- `test_list_parser_preserves_scope_and_source_times` checks numeric zero.
- `test_detail_parser_and_merge_preserve_empty_body_without_title_fallback`
  checks empty body behavior.
- `test_cross_bar_and_nullable_fields_remain_explicit` checks nullable author
  and missing count versus numeric zero.
- `test_empty_title_and_optional_invalid_times_are_preserved_as_field_state`
  checks empty title preservation.

Notes:
Title and body are never synthesized from one another.

### Alternate post types and source metadata

Requirement:
Emit only `post_type=0`; retain alternate rows in raw page evidence and
explicit out-of-scope counters; preserve source-defined metadata without
quality classification.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- `parse_list_page` returns nonzero rows in `out_of_scope_rows`.
- `collector.py::collect` counts them and never requests their details.

Test evidence:
- `test_list_parser_preserves_scope_and_source_times`
- `test_collects_pages_details_and_overlap_idempotently`

## Time Semantics

### Publish/update/display separation and timezone

Requirement:
Keep `post_publish_time`, `post_last_time`, and `post_display_time` separate;
interpret source civil time as Asia/Shanghai (`UTC+08:00`); preserve raw strings;
never substitute update/display time for publication time.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- `parser.py::parse_source_time` uses fixed `SHANGHAI` timezone.
- List/detail models and `GubaSourceItem` retain all three values and
  `source_times_raw`.

Test evidence:
- `test_list_parser_preserves_scope_and_source_times` checks distinct values
  and timezone.

### Optional invalid/missing update and display values

Requirement:
Invalid or missing publication time fails the item; invalid/missing optional
update/display fields remain nullable with a field error and do not invalidate
an otherwise valid item.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- `_optional_source_time` records `field_errors` and returns null for malformed
  optional update/display values while required publication time remains strict.

Test evidence:
- `test_invalid_publish_time_is_rejected` checks required publication failure.
- `test_empty_title_and_optional_invalid_times_are_preserved_as_field_state`
  checks nullable optional times and field errors.

Notes:
The raw source time strings remain available in `source_times_raw`.

## Error Behavior and Runtime Outcomes

### Terminal status meanings

Requirement:
Expose `SUCCESS`, `NO_NEW_DATA`, `PARTIAL_COLLECTION`, `COLLECTION_FAILED`,
`SPEC_MISMATCH`, and `CANCELLED` with the frozen meanings.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- `models/runtime.py::CollectionStatus` declares all six values.
- `collector.py::collect` accepts `cancel_check` and returns `CANCELLED` without
  advancing the watermark or issuing a request.

Test evidence:
- Tests cover `SUCCESS`, `NO_NEW_DATA`, `PARTIAL_COLLECTION`, and
  `SPEC_MISMATCH`.
- `test_cancellation_is_explicit_and_does_not_request` covers `CANCELLED`.

Notes:
The callback is an internal source-isolated cancellation boundary; no scheduler
or external orchestration is introduced.

### Empty, malformed and partial bodies

Requirement:
Only a structurally valid source-success empty list can produce no-data;
malformed assignments/rows and incomplete required details must remain visible
as failures and cannot become empty success.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- `parse_list_page` validates `rc`, `re` and embedded JSON.
- `collect` distinguishes empty pages, page failures and detail failures.

Test evidence:
- `test_malformed_embedded_payload_is_schema_mismatch`
- `test_valid_empty_page_is_no_new_data`
- `test_detail_failure_is_partial_and_does_not_emit_title_as_body`

### HTTP and transport failures

Requirement:
Timeout/network/DNS/TLS failures retry; 403 is an access block, 429 is a
rate-limit failure, 5xx is retryable, and other non-2xx responses are explicit
non-success responses rather than empty pages. Exhaustion maps to failed or
partial according to acquired evidence.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- `collector.py::_fetch` classifies timeout/OSError/URLError, 403, 429 and
  status >= 500; `collect` maps first-page versus later/detail exhaustion.

Test evidence:
- Timeout success retry, 429 retry, 403 detail failure and later 503 partial
  tests exist.
- `test_retry_exhaustion_is_collection_failed` covers timeout exhaustion.
- `test_redirect_final_url_outside_source_boundary_fails_closed` covers an
  explicit non-success redirect-policy failure.

### Retry-After, backoff and access-block budget

Requirement:
Honor a safe numeric `Retry-After`, use bounded exponential backoff with
jitter, retry 403/WAF conservatively, and expose the retry behavior.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- `collector.py::_retry_after` accepts bounded numeric `Retry-After` values.
- `_backoff` applies bounded exponential delay plus injected jitter and the
  retry-after floor; request counters expose retry attempts.

Test evidence:
- `test_retry_after_and_bounded_backoff_are_observable` checks Retry-After,
  interval sleep and the backoff bound.
- `test_retry_exhaustion_is_collection_failed` checks exhaustion.

Notes:
The jitter function is injectable for deterministic tests.

### Rate limiting and concurrency

Requirement:
Use at least the approved default interval, one in-flight request per source,
and no account/cookie/CAPTCHA bypass.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- Requests are sequential; `_rate_limit` enforces the configured minimum
  interval, and `CollectorConfig` rejects values below 2.5 seconds.

Test evidence:
- `test_minimum_interval_cannot_be_disabled` checks the lower bound.
- `test_retry_after_and_bounded_backoff_are_observable` checks interval sleep.

Notes:
No account, cookie or CAPTCHA bypass exists.

## Abnormal Cases

### Pinned, cross-bar, missing author, repost and missing values

Requirement:
Preserve pinned state and cross-bar associations; keep missing authors
nullable; retain repost/alternate rows as raw evidence and count them; keep
missing values null and numeric zero as zero.

Status:
`IMPLEMENTED_NOT_DEVELOPER_TESTED`

Implementation evidence:
- Models retain `post_top_status`, requested/canonical bars, author fields and
  source counts; alternate rows are returned separately.
- `_optional_int` preserves numeric zero.

Test evidence:
- Alternate type and zero-count behavior are tested.
- `test_cross_bar_and_nullable_fields_remain_explicit` covers cross-bar,
  missing-author and missing-count behavior.

### Duplicate pages and structure changes

Requirement:
Deduplicate accepted emission by `(source, source_item_id)` while retaining
page/observation provenance; fail explicitly on structural changes and retain
raw evidence.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- `collect` tracks `seen_ids`, duplicate counters and raw page/detail refs.
- Parser raises `GubaSchemaMismatch` for missing/invalid embedded structure.

Test evidence:
- `test_collects_pages_details_and_overlap_idempotently`
- `test_malformed_embedded_payload_is_schema_mismatch`

Notes:
Changed facts are represented by `observation_version` and the drift counter.

### Forbidden content filtering

Requirement:
Do not perform advertisement, spam, length, language, quality, author-value,
sentiment or semantic filtering in the adapter.

Status:
`INTENTIONALLY_DEFERRED_BY_APPROVED_SPEC`

Implementation evidence:
- The source package performs structural scope checks only and contains no
  content-quality or sentiment filter.

Test evidence:
- Scope tests assert post type handling and empty-body preservation.

## Incremental Strategy

### Latest-first traversal, overlap and boundary

Requirement:
Start every incremental run at page 1, traverse sequentially, re-fetch at
least two pages, and stop only after a full old/at-watermark page plus one
confirmation page; `max_pages` remains a hard cap.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- `collect` starts at page 1, uses page-number routes and counts consecutive
  old/seen pages before `watermark_confirmed`.

Test evidence:
- `test_watermark_confirmation_returns_no_new_data_without_detail_requests`
  covers the no-new-data confirmation path.
- `test_partial_run_does_not_advance_watermark` covers partial-run protection.

Notes:
Repeated IDs are compared before duplicate suppression; a changed observation
is versioned and a watermark boundary is not advanced by a partial run.

### High-watermark commit and partial runs

Requirement:
Advance the committed watermark only after every required page/detail in the
claimed window succeeds; partial or failed runs must not advance it.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- `collect` returns the prior watermark for `PARTIAL_COLLECTION`,
  `COLLECTION_FAILED`, `SPEC_MISMATCH` and `CANCELLED`; only successful/no-data
  outcomes compute a candidate watermark.

Test evidence:
- `test_partial_run_does_not_advance_watermark`.

Notes:
Durable committed-watermark persistence remains outside this round.

### Page overlap and replay identity

Requirement:
Use `(source='eastmoney_guba', source_item_id)` for logical acquisition
idempotency while retaining observation/raw provenance.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- `GubaSourceItem.identity_key` and collector `seen_ids` implement the logical
  key; page/detail refs are attached to accepted items.

Test evidence:
- `test_collects_pages_details_and_overlap_idempotently`.

Notes:
Durable observation/version storage remains contract-blocked by OQ-02.

## Raw Evidence / Traceability

### Raw page/detail preservation and references

Requirement:
Preserve raw list/detail responses, expose replayable references and retain
source metadata needed to audit promoted fields.

Status:
`CONTRACT_BLOCKED`

Implementation evidence:
- `InMemoryRawEvidenceStore.put` stores immutable byte copies under SHA-256
  keyed `memory://` references; `collect` attaches list/detail refs.
- `source_times_raw` and selected source metadata are carried forward.

Test evidence:
- `test_collects_pages_details_and_overlap_idempotently` checks five stored
  snapshots and item raw references.

Notes:
The approved spec explicitly leaves durable raw snapshot/manifest format and
DataClean transport to OQ-01/OQ-02/OQ-03. The in-memory store is therefore not
a production persistence claim. Complete `extra` preservation is separately
missing as recorded above.

## Runtime and CLI

### Result counters and serialization

Requirement:
Expose item results, counters, failures, stop reason and watermark without
inventing the final public envelope.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- `models/runtime.py::CollectionResult`, `RuntimeCounters` and
  `GubaSourceItem` provide the source/domain boundary.
- `cli/main.py` serializes the result as JSON.

Test evidence:
- Collector tests assert counters, failures, stop reasons and item identity.
- CLI help was exercised from the checkout.

### CLI exit semantics

Requirement:
The minimal CLI must return zero only for `SUCCESS`/`NO_NEW_DATA` and nonzero
for partial, failed or spec-mismatch outcomes, without DataClean persistence.

Status:
`IMPLEMENTED_NOT_DEVELOPER_TESTED`

Implementation evidence:
- `cli/main.py::main` maps statuses to exit code `0` or `1` and catches invalid
  requests as exit code `2`.

Test evidence:
- Only `--help` and collector-level status paths were exercised; no subprocess
  exit-code matrix exists.

## Acceptance Criteria Matrix

Requirement:
Provide deterministic offline coverage for page overlap, exact ID/detail
agreement, cross-bar posts, alternate post types, missing ID, malformed JSON,
publish/update separation, nullable author, missing-vs-zero counts, valid empty
list, first/later page failure, detail failure, retry exhaustion, max-pages
boundary and idempotent replay.

Status:
`IMPLEMENTED_AND_DEVELOPER_TESTED`

Implementation evidence:
- The parser/collector test modules cover overlap, exact ID/detail agreement,
  cross-bar fields, alternate type, missing ID, malformed JSON, publish/update
  separation, nullable author, missing-vs-zero counts, empty page, later page
  failure, detail failure, retry exhaustion, max-page boundary and replay
  idempotency.

Test evidence:
- `test_cross_bar_and_nullable_fields_remain_explicit`
- `test_retry_exhaustion_is_collection_failed`
- `test_max_pages_is_hard_partial_boundary`

Notes:
All acceptance cases are deterministic and offline. This is Developer evidence,
not independent Tester acceptance.

## Fixture Provenance

The fixture set is synthetic and deterministic. No live response body is being
added by this audit.

| Fixture | Source fact or engineering policy represented | Provenance class | Current validation boundary |
|---|---|---|---|
| `list_page_1.html` | Embedded `article_list`, standard `post_type=0`, source decimal ID, source times, counts including numeric zero, and an alternate nonzero row | `LIVE-EVIDENCE-DERIVED STRUCTURE` with `PURE POLICY SYNTHETIC CASE` values | Structure and scope are synthetic-only; no current live replay dependency |
| `list_page_2.html` | Sequential latest-post page movement and overlapping IDs with a new standard row | `LIVE-EVIDENCE-DERIVED STRUCTURE` plus `PURE POLICY SYNTHETIC CASE` overlap values | Overlap/idempotency is synthetic-only |
| `detail_1001.html`, `detail_1002.html` | Embedded `post_article`, `post_user`, `post_guba`, exact ID/bar/author/time agreement, and body field | `LIVE-EVIDENCE-DERIVED STRUCTURE` with `PURE POLICY SYNTHETIC CASE` content/IDs | Detail merge and time separation are synthetic-only |
| `empty_page.html` | Source-success empty `re` list as the approved empty-page stop shape | `LIVE-EVIDENCE-DERIVED STRUCTURE` plus `PURE POLICY SYNTHETIC CASE` | Empty-page outcome is synthetic-only |
| `malformed_page.html` | Approved fail-closed policy for missing embedded assignment | `PURE POLICY SYNTHETIC CASE` | Schema mismatch is synthetic-only |

No fixture establishes official rate limits, historical completeness, deletion
semantics or durable raw persistence. Redirect policy, Retry-After handling,
cross-bar fields and identity-content drift are validated only with synthetic
transport/fixture data.

## Self-Audit Decision

`DEVELOPER_SELF_AUDIT: READY_FOR_TESTER`

All previously listed REQUIRED gaps have implementation evidence and
deterministic Developer-side evidence. `CONTRACT_BLOCKED` and
`INTENTIONALLY_DEFERRED_BY_APPROVED_SPEC` items remain unchanged.
