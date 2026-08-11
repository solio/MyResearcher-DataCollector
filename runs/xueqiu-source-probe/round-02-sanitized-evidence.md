# Xueqiu Source Probe Round 2 — Sanitized Evidence

Round 2 baseline commit: 128d750fe46d4c98e15a8c9012b2f2c9690e15ee
Target: SH600519
Probe time: 2026-08-11T04:28:46Z (UTC)

## Path A

~~~text
request origin: browser context at https://xueqiu.com/S/SH600519
endpoint: https://xueqiu.com/query/v1/symbol/search/status
parameters: symbol=SH600519, count=20, page=1, sort=time
credentials: include
headers: Accept JSON/text, X-Requested-With XMLHttpRequest
result: BROWSER_CORS_BLOCKED
HTTP status: NOT OBSERVED
content-type: NOT OBSERVED
response bytes: NOT OBSERVED
response SHA-256: NOT OBSERVED
JSON: NO READABLE RESPONSE
~~~

No page=2 or repeat page=1 request occurred after the Path A block.

## Path B

~~~text
endpoint: https://api.xueqiu.com/query/v1/symbol/search/status.json
parameters: symbol_id=SH600519, symbol=SH600519, source=user, count=20, page=1, sort=time, comment=0, hl=0
result: BROWSER_CLIENT_BLOCKED
browser error: net::ERR_BLOCKED_BY_CLIENT
HTTP status: NOT OBSERVED
content-type: NOT OBSERVED
response bytes: NOT OBSERVED
response SHA-256: NOT OBSERVED
JSON: NOT OBSERVED
~~~

The browser error was not converted into an API 401/403/WAF claim.

## HTML Path Structural Evidence

Reused only the sanitized Round 1 observation:

~~~text
public HTML stock page: YES
discussion article nodes: 10
stable-looking post URL/id links: YES
visible page controls: 1..5, ..., 100, 下一页
author/content/relative-time labels: YES
login controls visible: YES
login submitted: NO
~~~

No full post content, user profile, cookie value or browser storage was recorded.

## Round 2 Classification

~~~text
Path A: BROWSER_CORS_BLOCKED
Path B: BROWSER_CLIENT_BLOCKED
HTML_BROWSER_PATH_CANDIDATE: YES
production browser requirement: UNRESOLVED
login requirement: UNRESOLVED
JSON response: NO
page overlap: UNRESOLVED
moving-page: UNRESOLVED
~~~

## Safety

No retries, parameter grid, alternate-host loop, CAPTCHA, login, cookie export, WAF bypass, proxy rotation or stealth behavior was used.

