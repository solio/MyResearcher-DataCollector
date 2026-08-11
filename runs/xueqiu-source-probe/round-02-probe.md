# Xueqiu Source Probe Round 2

Status: XUEQIU_SOURCE_PROBE_R2: BLOCKED

## 基线

- Round 1 status: XUEQIU_SOURCE_PROBE_R1: PASS_AS_BLOCKED_EVIDENCE
- Round 1 commit: 128d750fe46d4c98e15a8c9012b2f2c9690e15ee
- Round 2 probe time: 2026-08-11T04:28:46Z (UTC)
- Target: SH600519
- Production files modified: NONE
- Xueqiu cookies, credentials and response bodies: not persisted

Round 2 did not repeat the known anonymous plain-HTTP WAF paths. It tested the browser/API access paths only.

## Path A — Browser Context Fetch

The stock page was opened in a new Codex in-app Browser tab:

~~~text
https://xueqiu.com/S/SH600519
~~~

From that browser context, one same-origin fetch was attempted with:

~~~text
GET https://xueqiu.com/query/v1/symbol/search/status
symbol=SH600519
count=20
page=1
sort=time
credentials=include
Accept: application/json, text/plain, */*
X-Requested-With: XMLHttpRequest
~~~

Result:

~~~text
Path A: BROWSER_CORS_BLOCKED
page=1 JSON: NOT AVAILABLE
page=2: NOT REQUESTED
repeat page=1: NOT REQUESTED
~~~

The browser context could not return the fetch result to the probe (TypeError/CORS-style browser failure). Because page 1 did not yield readable JSON, no retry loop or pagination request was made.

## Path B — api.xueqiu.com

One browser navigation was attempted using the external API host and the candidate parameter set:

~~~text
https://api.xueqiu.com/query/v1/symbol/search/status.json?symbol_id=SH600519&symbol=SH600519&source=user&count=20&page=1&sort=time&comment=0&hl=0
~~~

Result:

~~~text
browser client error: net::ERR_BLOCKED_BY_CLIENT
source HTTP status: NOT OBSERVED
content-type: NOT OBSERVED
JSON body: NOT OBSERVED
~~~

This is a browser-client/network-surface block, not evidence that api.xueqiu.com itself returned 401, 403 or WAF. No alternative host/parameter loop was attempted.

## HTML Browser Path

Round 1 already observed one public HTML page in a browser context:

- public stock/discussion page rendered for SH600519;
- 10 discussion article nodes were visible;
- stable-looking public post URL/id links were visible;
- page controls 1, 2, 3, 4, 5, ..., 100 and 下一页 were visible;
- author, content and relative time labels were visible;
- login controls were visible and no login was performed.

Round 2 did not click page controls or start an HTML crawler. This is recorded only as:

~~~text
HTML_BROWSER_PATH_CANDIDATE
~~~

It is not a production route approval.

## Access Classification

~~~text
Path A: BROWSER_CORS_BLOCKED
Path B: BROWSER_CLIENT_BLOCKED
usable collection path: HTML_BROWSER_PATH_CANDIDATE (candidate only)
browser/session requirement: public HTML works in a browser context; production browser requirement UNRESOLVED
login requirement: UNRESOLVED
JSON response: NO
pagination evidence: HTML controls only; JSON page advancement UNRESOLVED
~~~

## Unresolved Facts

- Whether a browser context with a different allowed network surface can read the candidate JSON endpoint.
- Whether api.xueqiu.com is source-available outside the current browser client.
- Whether the public HTML page is backed by a stable production-accessible request path.
- xq_a_token behavior in browser-managed storage.
- JSON top-level list path, field mapping, item identity and created_at semantics.
- JSON page overlap and moving-page behavior.
- Login requirement for any future approved access path.

No CAPTCHA was solved, no login was automated, and no WAF/CORS/client block was bypassed.

Next Role: Reviewer.

