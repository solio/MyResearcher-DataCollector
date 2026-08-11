# Xueqiu Source Probe

Status: XUEQIU_SOURCE_PROBE: BLOCKED

## Environment

- Role: Source Researcher / Source Probe Executor
- Probe time: 2026-08-11T04:19:46Z (UTC)
- Repository main SHA: e1dcb36e540ece479efcd26333bdcb5dc6461b0b
- Target: SH600519
- Plain HTTP client: Python standard-library urllib with an in-memory CookieJar
- Browser comparison: one Codex in-app Browser navigation, no login and no interaction
- Project dependencies changed: NONE
- Production files changed: NONE
- Cookie values, authorization values and response bodies were not written to the repository

## Probe Target

Candidate discussion endpoint:

~~~text
https://xueqiu.com/query/v1/symbol/search/status
~~~

Candidate parameters used for the direct request:

~~~text
symbol=SH600519
count=20
page=1
sort=time
~~~

The endpoint and parameters remain research candidates, not approved source facts.

## Request Sequence

1. Fresh anonymous urllib session: GET https://xueqiu.com/
2. The same session was not used for further requests after the homepage returned an explicit WAF challenge.
3. One independent fresh anonymous urllib session: GET the candidate discussion endpoint with page=1.
4. No page=2 or repeated page=1 request was made because the source returned WAF challenge HTML.
5. One bounded browser comparison: open https://xueqiu.com/S/SH600519 and read the visible DOM only. No login, CAPTCHA, click, form submission or cookie inspection.

All HTTP requests were sequential. No concurrency, retry, proxy, rotation, bypass or historical crawl was used.

## Plain HTTP Attempt

The homepage returned:

~~~text
HTTP status: 200
content-type: text/html
response type: WAF_CHALLENGE + HTML_INSTEAD_OF_JSON
response bytes: 110310
response SHA-256: fb13f7cd0b5528dd230f627c9711e177349a9041c971b9fa65843aa3730f8743
~~~

The body was not parsed as source JSON. The response contained an explicit challenge signature (WAF/challenge/Aliyun indicators); no challenge was solved.

## Cookie Observation

The fresh homepage session exposed only the cookie name:

~~~text
Observed cookie names: acw_tc
xq_a_token present: NO
~~~

No cookie value was printed or persisted.

The independent endpoint session also exposed:

~~~text
Observed cookie names: acw_tc
xq_a_token present: NO
~~~

The browser context cookie state was not inspected.

## Endpoint Observation

The candidate endpoint returned:

~~~text
HTTP status: 200
content-type: text/html
response type: WAF_CHALLENGE + HTML_INSTEAD_OF_JSON
response bytes: 110310
response SHA-256: 32c4164aff94c5c923d5ffe42dba6760bb43251bf596cb693668ebf9faa33df7
JSON parse: failed with JSONDecodeError
final URL: unchanged candidate endpoint URL
~~~

No usable JSON response was obtained. Therefore the following are NOT OBSERVED for this endpoint:

- top-level item/list path
- item field keys
- pagination metadata
- count/total fields
- source item IDs from JSON
- created_at values from JSON

This is an access/source failure, not NO_NEW_DATA.

## Pagination Probe

NOT RUN after the WAF challenge. The requested page=2 and the comparison of page=1/page=2 were intentionally not attempted.

- pagination advanced: UNRESOLVED
- page1/page2 overlap: UNRESOLVED
- duplicate count: UNRESOLVED
- timestamp movement toward older items: UNRESOLVED

## Moving Page Probe

NOT RUN because the candidate endpoint was not usable through plain HTTP.

- page1_first vs page1_second: UNRESOLVED
- new/removed/moved IDs: UNRESOLVED
- ordering movement: UNRESOLVED
- newest created_at movement: UNRESOLVED

## Browser Comparison

One bounded navigation to https://xueqiu.com/S/SH600519 rendered a public stock page containing:

- stock title for 贵州茅台 (SH600519);
- visible discussion article elements (10 article nodes in the observed DOM);
- public item links and relative publication labels;
- page controls labelled 1, 2, 3, 4, 5, ..., 100 and 下一页;
- visible login/register controls.

No login was performed and no CAPTCHA/WAF control was solved. Browser cookies and local storage were not inspected. The browser page is HTML/UI evidence, not proof that the candidate JSON endpoint works.

Classification for the observed access paths:

~~~text
plain HTTP usable: NO
browser required: YES for the observed public HTML page; candidate JSON endpoint: UNRESOLVED
login required: UNRESOLVED
final access classification: ACCESS_BLOCKED_OR_UNRESOLVED
~~~

The visible login controls make a mandatory login requirement unproven; an existing browser session cannot be ruled in or out without inspecting credentials/session state, which is prohibited.

## Unresolved Facts

- Whether a normal anonymous browser session can call the candidate JSON endpoint.
- Whether the browser HTML page is backed by a different endpoint or client-side request.
- xq_a_token behavior in a browser context.
- Actual JSON response shape and field mapping.
- Page-number advancement, page overlap and moving-page behavior.
- Whether login is required for the discussion endpoint.
- Historical coverage, stable item identity guarantee, timestamp representation and source rate limit.

## Safety Result

The plain HTTP WAF challenge was recorded and no bypass was attempted. No Playwright/Selenium dependency, browser crawler, credential automation, proxy rotation, account rotation, stealth or fingerprint spoofing was used.

Next role: Reviewer.

