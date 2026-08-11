# Sanitized Xueqiu Probe Evidence

Probe SHA: e1dcb36e540ece479efcd26333bdcb5dc6461b0b
Probe target: SH600519
Probe time: 2026-08-11T04:19:46Z

## Anonymous HTTP

| Request | HTTP | Content-Type | Type | Bytes | SHA-256 | Cookie names | xq_a_token |
|---|---:|---|---|---:|---|---|---|
| GET https://xueqiu.com/ | 200 | text/html | WAF_CHALLENGE / HTML_INSTEAD_OF_JSON | 110310 | fb13f7cd0b5528dd230f627c9711e177349a9041c971b9fa65843aa3730f8743 | acw_tc | NO |
| GET https://xueqiu.com/query/v1/symbol/search/status?symbol=SH600519&count=20&page=1&sort=time | 200 | text/html | WAF_CHALLENGE / HTML_INSTEAD_OF_JSON | 110310 | 32c4164aff94c5c923d5ffe42dba6760bb43251bf596cb693668ebf9faa33df7 | acw_tc | NO |

The second request used a fresh independent anonymous HTTP session. No cookie value was captured.

## JSON Field Evidence

No valid JSON was returned by the candidate endpoint.

~~~text
id: NOT OBSERVED
description: NOT OBSERVED
title: NOT OBSERVED
created_at: NOT OBSERVED
target: NOT OBSERVED
user.id: NOT OBSERVED
user.screen_name: NOT OBSERVED
fav_count: NOT OBSERVED
reply_count: NOT OBSERVED
retweet_count: NOT OBSERVED
top-level list/items path: NOT OBSERVED
pagination fields: NOT OBSERVED
count/total fields: NOT OBSERVED
~~~

No fallback values were constructed.

## Browser DOM Structural Evidence

One bounded browser navigation to the public stock page rendered HTML/UI content:

~~~text
URL: https://xueqiu.com/S/SH600519
public stock page: YES
discussion article nodes observed: 10
page controls observed: 1,2,3,4,5,...,100,下一页
login controls visible: YES
login submitted: NO
CAPTCHA/WAF challenge solved: NO
~~~

No author names, post bodies, cookie values or complete user profiles were recorded.

## Pagination and Moving Page

~~~text
page=1 HTTP JSON: NOT AVAILABLE
page=2 HTTP JSON: NOT REQUESTED after WAF challenge
page1/page2 overlap: UNRESOLVED
moving page: UNRESOLVED
~~~

## Classification

~~~text
plain HTTP usable: NO
browser required: YES for observed public HTML; JSON endpoint requirement UNRESOLVED
login required: UNRESOLVED
final access classification: ACCESS_BLOCKED_OR_UNRESOLVED
~~~

## Evidence Handling

Only status, content type, byte counts, hashes, cookie names and structural summaries are retained. Full WAF/HTML bodies and any cookie values are intentionally not committed.

