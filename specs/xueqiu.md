# Source Spec: xueqiu

Status: CANDIDATE

Owner: Source Researcher / Xueqiu Source Probe
Observed at: 2026-08-11 UTC
Evidence location: runs/xueqiu-source-probe/probe.md and runs/xueqiu-source-probe/sanitized-evidence.md

This is an evidence-backed candidate only. It is not approved and does not authorize a production Xueqiu adapter.

## 1. Identity and Scope

- source_name: xueqiu
- source_type: stock-scoped investment discussion source
- source_home: https://xueqiu.com/
- candidate scope: A-share stock-scoped top-level discussion posts
- observed primary symbol: SH600519
- out of scope: post replies, user profiles, news, announcements, hot feed, HK/US markets, ETF/fund/index expansion and historical full backfill
- status: CANDIDATE; current live evidence is blocked/unresolved

## 2. Entry Point

- observed public entry page: https://xueqiu.com/S/SH600519
- candidate discussion API: https://xueqiu.com/query/v1/symbol/search/status
- method: GET
- candidate query parameters used: symbol=SH600519, count=20, page=1, sort=time
- direct HTTP observation: HTTP 200 text/html WAF challenge, not JSON
- browser observation: public stock/discussion HTML rendered once; the underlying discussion request was not identified
- approved entry point: UNRESOLVED

## 3. Request and Access Contract

- non-secret headers used: descriptive User-Agent, Accept: application/json,text/plain,*/*, Referer: stock page
- direct HTTP cookie names: acw_tc only
- xq_a_token in direct HTTP: NO
- browser cookie/session requirement: UNRESOLVED; browser cookie state was not inspected
- browser requirement: YES for the observed public HTML rendering; requirement for the candidate JSON endpoint is UNRESOLVED
- login requirement: UNRESOLVED
- credentials, authorization headers and cookie values: not used or retained
- redirects: final URL remained on xueqiu.com in both direct HTTP responses

## 4. Pagination

- candidate page parameter: page
- candidate count parameter: count=20
- candidate sort parameter: sort=time
- page=1/page=2 JSON advancement: UNRESOLVED; page=2 was not requested after WAF challenge
- page1/page2 overlap: UNRESOLVED
- duplicate behavior: UNRESOLVED
- moving-page observation: UNRESOLVED
- page controls 1..100 and 下一页 were visible in one browser-rendered HTML page, but no page navigation was performed
- bootstrap policy: UNRESOLVED
- incremental policy: UNRESOLVED
- no Eastmoney page/bootstrap/confirmation semantics are copied here

## 5. Historical Coverage

- earliest observable item: UNRESOLVED
- historical full backfill: OUT OF SCOPE
- current evidence proves only one bounded browser page rendering, not historical availability or completeness

## 6. Item Identity

- candidate source identity: item.id if confirmed in a successful JSON response
- current identity evidence: NOT OBSERVED in JSON
- URL/path IDs visible in browser HTML are not promoted to the JSON contract
- source_item_id mapping: UNRESOLVED
- no fallback hash/title/URL identity is authorized

## 7. Fields

Only fields returned by a successful JSON response may be promoted. Current candidate mapping:

| Candidate field | Observed source field | Status |
|---|---|---|
| source | constant xueqiu | CANDIDATE |
| stock_code | symbol/request scope | UNRESOLVED |
| source_item_id | item.id | UNRESOLVED |
| author_id | user.id | UNRESOLVED |
| author_name | user.screen_name | UNRESOLVED |
| title | title | UNRESOLVED |
| content | description | UNRESOLVED |
| published_at | created_at | UNRESOLVED |
| url | item URL/target | UNRESOLVED |
| like_count | fav_count | UNRESOLVED |
| reply_count | reply_count | UNRESOLVED |
| forward_count | retweet_count | UNRESOLVED |
| raw_ref | retained raw response reference | CANDIDATE repository contract |
| source_metadata | remaining observed source fields | UNRESOLVED |

Required JSON field checks id, description, title, created_at, target, user.id, user.screen_name, fav_count, reply_count and retweet_count: NOT OBSERVED.

## 8. Time Semantics

- candidate source field: created_at
- representation: UNRESOLVED; no JSON value was observed
- publication versus update meaning: UNRESOLVED
- timezone: UNRESOLVED
- browser relative labels are not accepted as source timestamps
- no timestamp conversion or fallback is authorized

## 9. Error and Access-Failure Behavior

Observed:

- HTTP 200 with text/html WAF challenge body
- JSON decoding failure
- direct response type: WAF_CHALLENGE / HTML_INSTEAD_OF_JSON
- cookie name acw_tc without xq_a_token

These outcomes are access/source failures, never NO_NEW_DATA.

Required classifications 401, 403, CAPTCHA, invalid session and non-2xx behavior: UNRESOLVED in this probe unless separately observed.

A future adapter must distinguish access failure, transport failure, invalid body and no-data only after a reviewed spec.

## 10. Retry Policy

- source retryability: UNRESOLVED
- probe policy used: no retry after explicit WAF challenge
- no retry, bypass, proxy rotation or challenge solving is approved by this candidate
- any future retry policy requires fresh evidence and Reviewer approval

## 11. Rate Limiting

- probe concurrency: 1
- probe interval policy: at least 3 seconds when sequential page probing is possible
- source numeric rate limit: UNRESOLVED
- conservative candidate policy: concurrency=1 and minimum interval >=3 seconds
- this is a probe safety policy, not a source guarantee

## 12. Abnormal Cases

- WAF challenge HTML: access failure, not empty data
- CAPTCHA: UNRESOLVED; if observed, stop and classify source access as blocked
- login page/controls: visible in browser, but login requirement remains UNRESOLVED
- missing fields: remain NOT OBSERVED/nullable until JSON evidence exists
- replies, user profiles, alternate markets and other non-scope surfaces: out of scope
- no content cleaning, spam filtering, emoji removal or semantic normalization is authorized

## 13. Incremental and Bootstrap Strategy

- bootstrap policy: UNRESOLVED
- incremental policy: UNRESOLVED
- page overlap and moving-page evidence are missing because plain HTTP was blocked
- do not copy Eastmoney BOOTSTRAP_MIN_PAGES, confirmation-page rules, watermark semantics or safe-frontier semantics
- Reviewer must freeze any future policy only after Xueqiu-specific page and moving-page evidence

## 14. Content Rule

Collector must preserve acquired source material. If a future successful JSON response shows description containing HTML, the source representation must be retained; semantic cleaning belongs to MyResearcher-DataClean. No HTML stripping, emoji removal, spam filtering, sentiment cleaning or text normalization is authorized in the source adapter.

## 15. Evidence and Reproduction

- Python stdlib anonymous homepage request:
  - 200 text/html, 110310 bytes, SHA-256 fb13f7cd0b5528dd230f627c9711e177349a9041c971b9fa65843aa3730f8743
  - cookie name acw_tc; xq_a_token absent
- Python stdlib anonymous candidate endpoint request:
  - 200 text/html, 110310 bytes, SHA-256 32c4164aff94c5c923d5ffe42dba6760bb43251bf596cb693668ebf9faa33df7
  - cookie name acw_tc; xq_a_token absent
- browser comparison:
  - one navigation to https://xueqiu.com/S/SH600519
  - public stock/discussion HTML visible; 10 article nodes and page controls observed
  - no login, CAPTCHA or WAF challenge interaction
- full response bodies and cookie values are intentionally not stored

## 16. Candidate Acceptance Criteria

Before any production adapter is authorized, Reviewer must require:

1. a successful authorized response or an explicitly accepted blocked-source decision;
2. confirmed JSON top-level item path and pagination fields;
3. observed item identity and field mapping;
4. verified created_at representation and timezone semantics;
5. page=1/page=2 overlap and moving-page evidence;
6. explicit access/login/browser classification;
7. sanitized fixtures and deterministic tests;
8. a reviewed transition from CANDIDATE to APPROVED.

Until then, no Xueqiu Collector implementation may be started.

## 17. Round 2 Browser/API Access Resolution

Round 2 tested only the two requested browser/API paths and did not repeat the
known anonymous plain-HTTP WAF requests.

### Path A: browser-context same-origin fetch

- Browser origin page: https://xueqiu.com/S/SH600519
- Request: GET https://xueqiu.com/query/v1/symbol/search/status
- Parameters: symbol=SH600519, count=20, page=1, sort=time
- credentials: include
- headers: Accept JSON/text and X-Requested-With XMLHttpRequest
- result: BROWSER_CORS_BLOCKED
- readable JSON: NO
- page=2 and repeat page=1: NOT RUN because page=1 JSON was unavailable

The browser page itself rendered public HTML, but the browser context could not
return the same-origin fetch result to the probe. This does not establish the
source endpoint response shape.

### Path B: api.xueqiu.com

- URL: https://api.xueqiu.com/query/v1/symbol/search/status.json
- Parameters: symbol_id=SH600519, symbol=SH600519, source=user, count=20,
  page=1, sort=time, comment=0, hl=0
- result: BROWSER_CLIENT_BLOCKED
- browser error: net::ERR_BLOCKED_BY_CLIENT
- source HTTP status/content-type/body: NOT OBSERVED

The browser-client error is not interpreted as an API 401, 403 or WAF response.
No parameter grid or alternate-host loop was attempted.

### Round 2 access decision

- public HTML browser path: HTML_BROWSER_PATH_CANDIDATE
- usable JSON collection path: NO
- production browser requirement: UNRESOLVED
- login requirement: UNRESOLVED
- API session requirement: UNRESOLVED
- JSON top-level list path and field mapping: UNRESOLVED
- page overlap and moving-page behavior: UNRESOLVED

The HTML browser path has visible discussion nodes, post URL/id links and page
controls, but it is not approved for a production Collector and no browser
crawler is authorized. Round 2 therefore leaves this candidate spec in
CANDIDATE status and routes the route decision to Reviewer.

Round 2 evidence: runs/xueqiu-source-probe/round-02-probe.md and
runs/xueqiu-source-probe/round-02-sanitized-evidence.md
