# Source Spec: `eastmoney_guba`

Status: `APPROVED`

Owner: Source Researcher / Phase 1 Research Lead

Observed at: 2026-08-10, Asia/Shanghai

Evidence location: `runs/phase-01-round-01/research-evidence.md`

This approval freezes the Phase 1 standard top-level-post (`post_type=0`) acquisition semantics below. It does not approve alternate post types, reply collection, DataClean implementation, sentiment processing, a storage backend or a second source.

Do not include credentials, cookie values, authorization headers or unnecessary personal data.

## 1. Identity

- source_name: `eastmoney_guba`
- source_type: public investment discussion forum
- source_home: `https://guba.eastmoney.com/`
- purpose: acquire replayable raw facts for standard top-level forum posts observed in a requested A-share stock bar.
- item scope: list entries with source `post_type=0`, followed by their source-owned standard detail surface.
- excluded emitted-item scope: alternate `post_type` values, replies/comments as independent records, user profiles, authenticated/private content, semantic filtering and sentiment. Alternate rows remain in raw list evidence and explicit out-of-scope counters; they are not silently discarded.
- evidence/status: `APPROVED` — `CURRENT SOURCE FACT` shows reproducible list and standard `post_type=0` detail surfaces; alternate types and reply acquisition remain explicitly excluded from emitted items.

## 2. Entry Point

- entry type: server-rendered public HTML containing embedded source JSON.
- list endpoint/page: `https://guba.eastmoney.com/list,{stock_code},f.html` for page 1; `https://guba.eastmoney.com/list,{stock_code},f_{page}.html` for page 2+.
- detail endpoint/page for in-scope standard posts: use the exact observed list link; the normal form is `https://guba.eastmoney.com/news,{canonical_bar_code},{post_id}.html`.
- method: `GET`.
- approved list payload: the `var article_list=<JSON>;` assignment embedded in HTML. Do not derive precise time or identity from display-only table text when the structured object is present.
- approved detail payload: the `var post_article=<JSON>;` assignment embedded in the exact detail HTML.
- current availability: HTTP 200 with usable embedded data was reproduced anonymously for list page 1, page 2 and a standard detail page.
- evidence/status: `APPROVED` — current observations and source-owned `list.js`/`news.js`; internal JSON APIs referenced by those scripts are not approved entry points in this spec.

## 3. Request

- parameters: six-digit requested `stock_code`; integer `page >= 1`.
- required non-secret headers: a descriptive, configurable `User-Agent`; normal HTML `Accept` is allowed. No browser impersonation requirement was established.
- cookies required: `NO` for the observed approved surfaces.
- authentication required: `NO` for the observed approved surfaces.
- secret injection mechanism: none.
- redirects: follow only HTTPS redirects to source-owned Eastmoney hosts; record the final URL.
- evidence/status: `APPROVED` — bounded public observations succeeded without authentication. `UNKNOWN`: whether the source may introduce future cookie or header requirements.

## 4. Pagination

- mechanism: page number encoded in the public route.
- page/cursor start: `1`.
- observed page size: `80` items in each of four observed list pages. Treat source-reported list length as observed, not as a permanent guarantee.
- ordering: `,f` is the source UI's “最新发帖” sort. Use `post_publish_time` for boundary comparisons; never use default “最新评论”/last-update ordering for the Phase 1 incremental path.
- overlap behavior: overlap is permitted and expected. Two IDs overlapped across observed latest-post pages 1 and 2 while new posts arrived.
- page integrity: every requested page produces a page outcome and counts for received, parsed and failed rows. Duplicate IDs across pages are not parse failures.
- stop condition for bounded runs: stop at configured `max_pages`, a valid empty page, or the incremental boundary rule in section 13. A failed/malformed page is not an empty page and must not be used as a normal stop.
- evidence/status: `APPROVED` for routes, start, observed size and moving-page overlap; historical maximum page/depth is `UNKNOWN`.

## 5. Historical Coverage

- earliest observable: `UNKNOWN`; this round did not crawl to the historical tail.
- range limitation: page-number traversal exists and the source reports a large total count, but neither value proves every historical post remains available.
- deletion/expiry behavior: `UNKNOWN`; deleted/inaccessible detail pages must remain observable failures against previously listed IDs.
- Phase 1 guarantee: only the explicitly requested pages and their per-page outcomes are claimed. Do not claim full-history completeness.
- evidence/status: `APPROVED WITH EXPLICIT LIMITATION` — no historical backfill guarantee is authorized.

## 6. Item Identity

- source_item_id source location: decimal `post_id` in an in-scope `article_list.re[*]` item; it must match the detail `post_article.post_id` and the ID in its standard detail URL.
- uniqueness scope/guarantee: unique within `eastmoney_guba` top-level post records for Collector idempotency. This is an evidence-backed operational contract, not a claim of a published website guarantee.
- required format: non-empty base-10 digits; preserve as a string in the common envelope.
- missing-ID behavior: row parse failure; never invent a hash or use title/URL as fallback identity.
- collision behavior: same `(source, source_item_id)` with identical raw content is an overlap/re-observation; different source facts become a new immutable observation/version and raise an identity-content drift metric. They are never silently merged.
- cross-bar behavior: the same ID observed through multiple requested bars remains one source item with multiple observations/associations; requested bar and canonical `stockbar_code` are both retained.
- evidence/status: `APPROVED` — ID agreement was observed across list, URL and detail.

## 7. Fields

The table freezes source-to-raw semantic mapping. The transport and storage representation remain governed by `docs/data-collector/data-contract.md`; the adapter must not silently discard source fields because the common envelope is still provisional.

| Output candidate | Source location | Meaning | Type | Required | Nullable | Missing behavior | Evidence/status |
|---|---|---|---|---|---|---|---|
| `schema_version` | Collector constant | raw envelope/parser contract version | string | yes | no | fail record/run contract | `APPROVED`; exact version constant chosen once in Developer change and fixture-frozen |
| `source` | constant | `eastmoney_guba` | string | yes | no | fail record | `APPROVED` |
| `source_item_id` | list `post_id` | top-level source post identity | digit string | yes | no | row parse failure | `APPROVED` |
| `stock_code` | requested list route | source-explicit bar through which item was observed | string | yes | no | request validation failure | `APPROVED`; not semantic classification |
| `author_id` | list `user_id`; detail `post_user.user_id` | source author identity | string | no | yes | preserve null and increment missing-field metric | `APPROVED` |
| `author_name` | list `user_nickname`; detail `post_user.user_nickname` | source display name at observation | string | no | yes | preserve null | `APPROVED` |
| `title` | list `post_title`; detail `post_title` | source title | string | no | yes | preserve null/empty exactly; do not synthesize | `APPROVED` |
| `content` | detail `post_content` | source body; source-provided empty string is preserved | string | yes for accepted item | no | detail failure makes item incomplete and run partial; never substitute title | `APPROVED` |
| `published_at` | `post_publish_time` | original source publication local time converted per section 8 | timestamp/string | yes for accepted item | no | item parse failure | `APPROVED` |
| `collected_at` | Collector clock | UTC acquisition instant for this observation | UTC timestamp | yes | no | fail observation | repository contract |
| `url` | exact list link resolved to HTTPS | observed/canonical detail surface | string | yes | no | row parse failure | `APPROVED`; preserve alternate host/type links |
| `raw_ref` | Collector raw snapshot manifest | replayable list and, when requested, detail evidence references | string/object | yes | no | run cannot be `SUCCESS` | repository contract + `APPROVED` mapping |
| `source_metadata.requested_bar_code` | request | requested bar association | string | yes | no | request failure | `APPROVED` |
| `source_metadata.canonical_bar_code` | `stockbar_code` or detail `post_guba.stockbar_code` | post's canonical/source bar identity | string | no | yes | preserve null; do not replace with requested bar | `APPROVED` |
| `source_metadata.canonical_bar_name` | `stockbar_name` / detail `post_guba.stockbar_name` | source bar display name | string | no | yes | preserve null | `APPROVED` |
| `source_metadata.post_type` | `post_type` | source-defined post type; exactly `0` for emitted Phase 1 items | integer | yes | no | nonzero values remain raw out-of-scope rows, not accepted items | `APPROVED`; structural scope, not a quality label |
| `source_metadata.post_state` | `post_state` | source-defined state | integer | no | yes | preserve null | `APPROVED` |
| `source_metadata.post_top_status` | `post_top_status` | source-defined pinned/top state | integer | no | yes | preserve null | `APPROVED` |
| `source_metadata.last_updated_at` | `post_last_time` | source last activity/update fact | timestamp/string | no | yes | preserve null; never copy to `published_at` | `APPROVED` |
| `source_metadata.display_time` | `post_display_time` | source display-time fact | timestamp/string | no | yes | preserve null | `APPROVED` |
| `source_metadata.read_count` | `post_click_count` | source observation-time click/read count | integer | no | yes | preserve null, not zero | `APPROVED` |
| `source_metadata.reply_count` | `post_comment_count` | source observation-time comment count | integer | no | yes | preserve null, not zero | `APPROVED` |
| `source_metadata.like_count` | detail `post_like_count` | source observation-time like count | integer | no | yes | preserve null, not zero | `APPROVED` |
| `source_metadata.forward_count` | `post_forward_count` | source observation-time forward count | integer | no | yes | preserve null, not zero | `APPROVED` |
| `source_metadata.source_post_id` | `post_source_id` / detail `source_post_id` | source reference/repost identity when present | string/integer | no | yes | preserve null/empty exactly | `APPROVED` |
| `source_metadata.source_times_raw` | raw time fields | exact source strings before conversion | object | yes | no | parse failure | `APPROVED` for auditability |
| `source_metadata.extra` | remaining item/detail object | source fields not promoted above | object | yes | no | retain empty object if none | `APPROVED`; sanitize only credentials/unnecessary profile detail, never content facts |

Field mismatch rule: when list and detail disagree on ID, author, canonical bar, title or publish time, retain both raw snapshots, reject the combined accepted item, and record `SPEC_MISMATCH`/parse failure. Mutable engagement and last-update values may differ by observation time; retain both observations rather than forcing equality.

## 8. Time Semantics

- publish time source and meaning: `post_publish_time`, original publication time on the observed source surface.
- update time source and meaning: `post_last_time`, last activity/update time; it is separate metadata and never substitutes for publication time.
- display time: `post_display_time`, retained separately because it can differ for alternate post types.
- source timezone: Asia/Shanghai / fixed `UTC+08:00` for the observed surface.
- source format observed: `YYYY-MM-DD HH:MM:SS` without an inline offset.
- ambiguous/missing time behavior: invalid or missing publication time is an item parse failure. Invalid/missing update/display time remains null with a field error; it does not invalidate a source item that otherwise has valid publication time.
- conversion rule: parse strictly as a valid Asia/Shanghai civil time, preserve the raw string, and serialize the common `published_at` using the repository's selected timezone-aware format. Do not use host-local timezone or infer a year from table display text.
- evidence/status: `APPROVED` — separate exact fields and `+08:00` server alignment reproduced. DST is not applied in Asia/Shanghai.

## 9. Error Behavior

Terminal outcomes for this source are frozen as follows:

- `SUCCESS`: at least one new accepted in-scope item and every claimed request/page/row/detail completed or was explicitly in-scope/out-of-scope accounted for without failure.
- `NO_NEW_DATA`: the incremental boundary completed, no new accepted in-scope ID exists, and no request/page/row/detail failure occurred.
- `PARTIAL_COLLECTION`: some source evidence was acquired but any claimed page, in-scope row or required detail failed, or the run hit its coverage cap before the confirmation boundary.
- `COLLECTION_FAILED`: no trustworthy claimed collection window was established after retries.
- `SPEC_MISMATCH`: current source structure or semantics contradict the approved spec; preserve raw evidence and stop rather than guessing.
- `CANCELLED`: an explicit external cancellation stopped work; never map cancellation to success/no-data.

- timeout/network/DNS/TLS: page/detail request failure; retry under section 10. Exhaustion is `COLLECTION_FAILED` if no required page was acquired, otherwise `PARTIAL_COLLECTION`.
- HTTP 429: retryable rate-limit failure; honor numeric `Retry-After` when safe, stop after budget, and expose 429 count. Never report no data.
- HTTP 403 or CAPTCHA/WAF body: access failure, not an empty page. One conservative delayed retry is allowed; repeated block stops the run as failed/partial.
- HTTP 5xx: retryable within budget; exhaustion failed/partial.
- other non-2xx: non-success page; classify explicitly and do not parse as empty.
- invalid body: missing/invalid `article_list`, non-success `rc`, malformed JSON, missing `re`, or structurally impossible fields are parse/source failures.
- partial body/row: count `records_received`, `records_parsed`, `records_failed`; any failed required row prevents `SUCCESS`.
- empty success: only valid when HTTP succeeds, embedded object is structurally valid with source success semantics, and `re` is an empty list. It may produce `NO_NEW_DATA` only when no new valid IDs exist and no request/page/record failure occurred.
- detail failure for an in-scope row: accepted list identity remains in raw evidence, but required content acquisition is incomplete; run is `PARTIAL_COLLECTION` unless all candidate details fail, in which case `COLLECTION_FAILED`. An explicitly counted alternate-type row is not a detail failure because it is outside the approved emitted-item scope.
- evidence/status: `APPROVED` policy; exact live 403/429/5xx bodies are not established and require synthetic fixtures plus future captured sanitized fixtures.

## 10. Retry Policy

- retryable: timeout, connection reset, 429, 5xx and one suspected transient WAF/CAPTCHA response.
- non_retryable: invalid request stock code, redirect off allowlisted source hosts, deterministic schema mismatch, missing identity, repeated access-control block, and most other 4xx.
- maximum attempts: three total attempts for timeout/429/5xx; two total for access-block responses.
- backoff: exponential delays with jitter, bounded and observable; honor a safe `Retry-After` without exceeding run budget.
- page rule: retry the same page/detail only; never skip forward and later claim complete success.
- evidence/status: `APPROVED ENGINEERING POLICY` inferred from failure risk and legacy history; not represented as a source guarantee.

## 11. Rate Limiting

- known limit: `UNKNOWN`; no official numeric limit was established.
- minimum default interval: 3 seconds between requests to the approved source, configurable upward but not below 2.5 seconds without a new evidence-backed spec change.
- concurrency: one request in flight per source adapter; no parallel detail fan-out in Phase 1.
- response to rate limiting: increase delay/backoff and stop within retry budget; never rotate accounts, steal cookies or bypass CAPTCHA.
- evidence/status: `APPROVED CONSERVATIVE POLICY` — the precise threshold is an inference, not a current source fact.

## 12. Abnormal Cases

- pinned posts: preserve `post_top_status`; allow overlap; do not drop pinned content.
- cross-bar items within `post_type=0`: preserve requested bar, canonical bar and exact URL; do not silently rewrite association.
- deleted posts: preserve prior raw observation; current missing/failed detail is an explicit failure/state observation, not deletion proof unless the source says so.
- anonymous/missing author: keep nullable author fields; never generate an author identity or hash a display name.
- reposts/alternate post types: preserve the complete page in raw evidence and count every nonzero `post_type` as an explicit out-of-scope source row. Do not emit it under this spec, fetch it through an unverified parser or classify it by quality. Adding an alternate type requires a spec change with detail evidence.
- missing fields: follow section 7; source numeric zero remains zero, missing remains null.
- duplicate items/pages: deduplicate accepted emission by `(source, source_item_id)` while retaining page/observation provenance and metrics.
- structure changes: missing assignments, changed types or semantic mismatch produce explicit schema/parse failure and retained raw evidence.
- content filtering: forbidden in adapter. The `post_type=0` boundary is a frozen source-object scope, not a content decision. Within that scope there is no advertisement, spam, length, language, quality, author-value or sentiment filtering.
- evidence/status: `APPROVED`; alternate types and cross-bar rows were observed, deletion behavior remains `UNKNOWN`.

## 13. Incremental Strategy

- cursor/timestamp/page boundary: no opaque cursor is available. Begin at page 1 in latest-post ordering every run and traverse sequentially.
- stable ordering assumption: do not assume immutable page membership or globally monotonic numeric IDs. Use valid `post_publish_time` plus observed IDs for stopping and idempotency.
- overlap window: always re-fetch at least two pages for an incremental run. Continue until one full successfully parsed page contains only IDs already seen with publication times at or before the committed watermark; then fetch one additional confirmation page before stopping. `max_pages` remains a hard coverage cap.
- high-water commit: advance the committed watermark only after all required pages/details in the claimed window succeed. A partial/failed run must not advance it.
- late-arriving/update behavior: a known ID with changed raw facts is a new observation/version. Engagement and `post_last_time` are mutable snapshots; original `published_at` is immutable unless a source correction is captured as drift.
- idempotency key: `(source='eastmoney_guba', source_item_id)` for logical identity; observation/raw snapshot identity additionally includes collection/version provenance.
- coverage statement: report pages requested/succeeded/failed, source rows received, rows in scope/out of scope/failed, details requested/succeeded/failed, first/last publish time and stop reason. Hitting `max_pages` before the confirmation boundary is `PARTIAL_COLLECTION`, not success.
- evidence/status: `APPROVED ENGINEERING POLICY` based on reproduced moving-page overlap.

## 14. Evidence

- reproduction steps:
  1. `GET https://guba.eastmoney.com/list,601012,f.html` with a descriptive User-Agent.
  2. Verify HTTP success and parse the exact JSON assigned to `article_list` without executing page JavaScript.
  3. `GET https://guba.eastmoney.com/list,601012,f_2.html`; compare IDs and source times to demonstrate page movement/overlap.
  4. Resolve one exact standard list link and verify detail `post_article.post_id`, author/bar identity, `post_publish_time`, `post_last_time` and body.
- sanitized request sample/reference: the URLs and non-secret method above; no cookies, authorization or credential values.
- sanitized response sample/reference: structural facts, byte sizes and SHA-256 values in `runs/phase-01-round-01/research-evidence.md`. Live bodies are not committed.
- observations: list and detail returned HTTP 200; latest pages contained 80 items each; two IDs overlapped in sequential observations; exact publish and last-update fields were distinct.
- evidence limitations: one stock, a short observation window, no load test, no forced 403/429/5xx, no historical-tail crawl, no verified reply response.

## 15. Acceptance Criteria

- [x] entry point behavior reproduced
- [x] identity behavior established for top-level posts
- [x] fields and time semantics established for approved scope
- [x] pagination and honest stopping policy established
- [x] errors/retries and partial outcome policy established
- [x] sanitized list/detail/error/overlap fixture plan established
- [x] source-to-raw contract mapping approved for top-level posts
- [x] runtime outcomes approved
- [x] unresolved non-blocking limitations listed

Developer acceptance must include deterministic offline fixtures/tests for: page 1 and page 2 overlap, exact ID/detail agreement, cross-bar standard posts, explicitly counted nonzero alternate post types, missing ID, malformed/missing embedded JSON, publish versus last-update time, nullable author, missing counts versus numeric zero, empty valid list, first-page failure, later-page failure, detail failure, retry exhaustion, `max_pages` boundary and idempotent replay. No live network test is an acceptance dependency.

## Open Questions / Blocks

- Non-blocking for approved scope: official numeric rate limit, historical tail/deletion behavior and full availability SLA.
- Blocked outside approved scope: replies/comments as independent records require a separate spec change with a successfully reproduced response, identity, pagination and failure contract.
- Blocked outside Source Spec: exact Collector → DataClean transport/storage remains a repository-level contract decision and must not be invented by the adapter.

## Change History

| Date | Change | Evidence | Author |
|---|---|---|---|
| 2026-08-10 | Approved Phase 1 top-level-post source semantics and explicit exclusions | `runs/phase-01-round-01/research-evidence.md` | Source Researcher / Phase 1 Research Lead |
