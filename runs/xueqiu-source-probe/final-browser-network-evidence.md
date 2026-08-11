# Xueqiu Final Browser Network Evidence

All values below are sanitized. The automatically appended challenge/signature query value is omitted. No cookie value, credential, header value, full response body or unnecessary profile data is stored.

## Page 1

~~~text
request: GET https://xueqiu.com/query/v1/symbol/search/status.json
parameters: symbol=SH600519, count=10, comment=0, hl=0, source=all, sort=time, page=1, q=, type=11
status: 200
content-type: application/json
resource: XHR
body bytes: 73456
body SHA-256: d6d3c2d74375fed1adfcc1534d057857cbeedd58245b5c965645568920893032
top-level keys: about,count,key,list,maxPage,page,q,query_id,recommend_cards
list item count: 10
pagination keys observed: count,maxPage,page
~~~

Item IDs, in order:

~~~text
404541440, 404540795, 404540751, 404540676, 404539833,
404539753, 404539592, 404539556, 404539216, 404539054
~~~

created_at, in order:

~~~text
1786423476000, 1786423052000, 1786423023000, 1786422964000,
1786422429000, 1786422377000, 1786422276000, 1786422248000,
1786422026000, 1786421939000
~~~

Required field presence:

~~~text
id: YES
description: YES
title: YES
created_at: YES
target: YES
user.id: YES
user.screen_name: YES
fav_count: YES
reply_count: YES
retweet_count: YES
~~~

## Page 2

~~~text
request: GET https://xueqiu.com/query/v1/symbol/search/status.json
parameters: symbol=SH600519, count=10, comment=0, hl=0, source=all, sort=time, page=2, q=, type=11, last_id=404539054
status: 200
content-type: application/json
resource: XHR
body bytes: 76979
body SHA-256: ce7017902c91c53beacbca6bac0c94c04eed225befa043ee92ded68a2ed9e812
top-level keys: about,count,key,list,maxPage,page,q,query_id,recommend_cards
list item count: 10
pagination keys observed: count,maxPage,page
~~~

Item IDs, in order:

~~~text
404538246, 404538137, 404538073, 404538046, 404537743,
404537519, 404536440, 404536338, 404534274, 404533741
~~~

created_at, in order:

~~~text
1786421437000, 1786421398000, 1786421353000, 1786421339000,
1786421157000, 1786421036000, 1786420484000, 1786420442000,
1786419686000, 1786419548000
~~~

Required field presence:

~~~text
id: YES
description: YES
title: YES
created_at: YES
target: YES
user.id: YES
user.screen_name: YES
fav_count: YES
reply_count: YES
retweet_count: YES
~~~

## Repeated Page 1

~~~text
request: GET https://xueqiu.com/query/v1/symbol/search/status.json
parameters: symbol=SH600519, count=10, comment=0, hl=0, source=all, sort=time, page=1, q=, type=11
status: 200
content-type: application/json
body bytes: 73456
body SHA-256: fa9908264b28a7b701eb80b0648c770650e0ba6840c204ea328b521895f57870
list item count: 10
~~~

IDs and created_at matched the first page 1 in this bounded comparison:

~~~text
overlap: 10
new IDs: none
removed IDs: none
newest created_at: unchanged at 1786423476000
~~~

## Derived Pagination Evidence

~~~text
page1/page2 overlap: 0
page2 duplicate count: 0
page2 item IDs all new relative to page1: YES
page2 newest created_at: 1786421437000
page1 newest created_at: 1786423476000
page2 broadly older: YES
~~~

## Access and Safety

~~~text
temporary Chrome profile: YES
anonymous browser page: YES
login submitted: NO
cookie values recorded: NO
credentials recorded: NO
CAPTCHA/WAF bypass: NO
concurrency: 1
bounded page interaction: one click on control 2
~~~

