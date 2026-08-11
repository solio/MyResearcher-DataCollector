# Xueqiu Final Browser Network Probe

XUEQIU_FINAL_FEASIBILITY:
JSON_API_READY_FOR_SPEC_APPROVAL

## Environment

- Role: Source Researcher / Source Probe Executor
- Probe time: 2026-08-11T04:48:14Z (UTC)
- Repository baseline: 4a0ae28a27015f756610ac3a7a24326232124e16
- Browser: temporary local Google Chrome headless profile over CDP
- Target page: https://xueqiu.com/S/SH600519
- Login performed: NO
- Cookie values or credentials persisted: NONE
- Production files modified: NONE

## Actual Browser Network Request

The page's own Network traffic revealed the actual discussion request:

~~~text
host: xueqiu.com
path: /query/v1/symbol/search/status.json
method: GET
resource type: XHR
content-type: application/json
initiator: script
~~~

Observed non-secret parameters:

~~~text
symbol=SH600519
count=10
comment=0
hl=0
source=all
sort=time
page=1
q=
type=11
~~~

The browser automatically appended a challenge/signature parameter. Its name and value are omitted from repository evidence. No cookie/header value was recorded.

## Bounded Flow

1. Load stock page and observe the initial page=1 status JSON.
2. Click the visible page control labelled 2 once.
3. Observe the page=2 request.
4. Wait at least 3 seconds.
5. Reload the same stock page and observe a second page=1 request.

The browser DOM after the flow still showed 10 discussion article nodes, page controls 1, 2, 3, 4, 5, ..., 100 and 下一页, and a visible login control. No login or CAPTCHA interaction occurred.

## JSON Response Shape

For page 1, page 2 and the repeated page 1:

~~~text
HTTP status: 200
content-type: application/json
top-level keys: about, count, key, list, maxPage, page, q, query_id, recommend_cards
item path: list
item count: 10
~~~

Observed item fields include:

~~~text
id
description
title
created_at
target
user.id
user.screen_name
fav_count
reply_count
retweet_count
~~~

Additional observed item fields and nested user keys are retained in the sanitized evidence. No item content, author value or full profile was committed.

## Identity and Time

- Ten non-empty item IDs were observed on each page.
- IDs were unique within each observed page.
- page 1 and page 2 had no overlap in this bounded observation; page 2 IDs were different and older.
- The repeated page 1 contained the same ten IDs as the first page 1.
- created_at values were numeric Unix epoch milliseconds.
- page 2 newest created_at was older than page 1 newest created_at.
- Description HTML representation was not copied or normalized; whether description contains HTML remains UNRESOLVED.

## Pagination and Moving Page

~~~text
page 1 request:
  page=1

page 2 request:
  page=2
  last_id=404539054

page1/page2 overlap: 0
page2 duplicate count: 0
pagination advanced: YES
page2 broadly older than page1: YES

page1 first vs reload:
  overlap: 10
  new IDs: none
  removed IDs: none
  newest created_at changed: NO
~~~

This is evidence only. Bootstrap and incremental algorithms remain unfrozen.

## Access Decision

~~~text
anonymous usable: YES for the observed browser Network path
login required: NO observed for this path
browser/session requirement: browser-managed session/context required for the observed path; exact production mechanism remains for Reviewer
JSON available: YES
HTML fallback: NOT NEEDED for this gate
~~~

The earlier plain-HTTP WAF result and in-app-browser CORS/client blocks remain environment-specific limitations; they do not negate this real Chrome Network observation.

Next Role: Reviewer.

