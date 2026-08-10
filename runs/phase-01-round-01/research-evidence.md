# Phase 1 Round 01 Research Evidence

Research date: 2026-08-10 (Asia/Shanghai)

Role: Source Researcher / Phase 1 Research Lead

Legacy repository baseline: `d510cc5ddb08215403d932616193af463fb9ffdf`

DataCollector baseline: `2eb563386bb335918720d3a51e4597507a04a437`

DataClean read-only baseline: `d2696799a930a3c8f0eff5f70723db2b388fb9af`

## Evidence labels

- `LEGACY FACT`: reproduced from the legacy MyResearcher repository or its Git history.
- `REPOSITORY FACT`: reproduced from the current DataCollector or DataClean repository contracts/state.
- `CURRENT SOURCE FACT`: reproduced from a bounded public request or source-owned page/script on 2026-08-10.
- `INFERENCE`: an engineering conclusion derived from stated facts.
- `UNKNOWN`: not established by the available evidence.

No login, account credential, API key, CAPTCHA bypass or authenticated cookie was used. Temporary anonymous response bodies were inspected outside the repository; no live body or cookie is retained in Git.

## Eastmoney Guba

### Legacy implementation and failure history

- **LEGACY FACT** — `guba_scraper.py` requests `https://guba.eastmoney.com/list,{stock_code}_{page}.html`, parses a five-column table and writes the fifth column, labelled “最后更新”, into `post_time`.
- **LEGACY FACT** — the parser reads the author cell into `td_author` but does not place author ID or author name in its returned item.
- **LEGACY FACT** — list items are reduced to title, URL, empty content, source labels, reply/read counts and `post_time`; `searcher.py` later substitutes title for missing content. This mixes collection with downstream convenience behavior and loses source facts.
- **LEGACY FACT** — title-based advertising/noise rules run inside the scraper. Those semantic/content-quality filters are outside the new Collector boundary and must not migrate.
- **LEGACY FACT** — a failed page is retried three times and then stops the crawl, while the method returns accumulated items without a partial-collection outcome.
- **LEGACY FACT** — commit `2a06b5e` records a false anti-bot classification caused by the word “验证” in a normal post title; the later check requires CAPTCHA markers plus fewer than three matching links.
- **LEGACY FACT** — commit `aa5c11b` added rotating User-Agent strings, jitter and longer crawl limits in response to anti-bot concerns. These are historical mitigations, not proof of a current source requirement.
- **LEGACY FACT** — `thoughts/20260514-情绪模型与股吧优化.md` reports one historical run as “764帖→80条有效”. This is an unverified historical note, not a current coverage measurement; no tracked raw fixture supports it.

### Current public observations

- **CURRENT SOURCE FACT** — unauthenticated `GET https://guba.eastmoney.com/list,601012_1.html` returned HTTP 200 and a 138,753-byte HTML page at 2026-08-10 10:30:30 +08:00. Body SHA-256: `4145d11a083e0f00dfe2c58e3c16072e6ddf82f8bb76b618a5f9ce72a104179b`.
- **CURRENT SOURCE FACT** — the default list visibly labels its columns 阅读、评论、标题、作者、最后更新. It is therefore incorrect to interpret the fifth column as original publication time.
- **CURRENT SOURCE FACT** — the default first page contained 80 distinct `data-postid` values, 80 source author profile links, 65 `/news,601012,...` links, 12 `/news,<other-code>,...` links and three non-`news` items. A requested bar page can contain cross-bar and alternate post types; requested-bar membership and canonical post bar are separate facts.
- **CURRENT SOURCE FACT** — unauthenticated `GET https://guba.eastmoney.com/list,601012,f.html` returned HTTP 200 and embeds `var article_list=...` as JSON. The observation contained 80 distinct items and exact `post_publish_time`, `post_last_time`, `post_display_time`, `post_id`, `post_type`, counts, author identity and bar identity fields. Body SHA-256: `8b0142306d6b9b1e5a5dde16a0c93cf72bd67a5504d794f7ceb12ef10cbe199f`.
- **CURRENT SOURCE FACT** — the corresponding second page is `https://guba.eastmoney.com/list,601012,f_2.html`; it returned 80 distinct items. Page 1 and page 2 shared two IDs in observations about two minutes apart, while reported total count changed from 662,734 to 662,736. Page-number crawling can overlap while the head changes.
- **CURRENT SOURCE FACT** — source-owned `list.js` maps `,f` to “最新发帖”, uses page number and a page size of 80 in the observed page, and references `webarticlelist/api/Article/Articlelist` with `code`, `type`, `p`, `ps` and `sorttype` parameters. The HTML route is the selected Phase 1 entry; the internal API is evidence, not the approved entry point.
- **CURRENT SOURCE FACT** — unauthenticated `GET https://guba.eastmoney.com/news,601012,1756523996.html` returned HTTP 200 and embeds `var post_article=...`. The object provides stable-looking numeric `post_id`, full body, author ID/name, canonical bar identity, publish/last/display times, state/type/top flags, engagement counts and source metadata. Body SHA-256: `43776328bd4f6ac1429c7ed5412c466db2d8dc6aa446b376e9f21546774da1ac`.
- **CURRENT SOURCE FACT** — for that detail observation, the displayed timestamp matched `post_publish_time`; the embedded `post_last_time` is separately named. The page response time in UTC and embedded server time with `+08:00` aligned with the displayed local timestamps, supporting Asia/Shanghai (`UTC+08:00`) interpretation for this source surface.
- **CURRENT SOURCE FACT** — source-owned `news.js` references first-level and child reply fields including `reply_id`, `reply_user.user_id`, `reply_publish_time`, `reply_text`, `reply_like_count` and source-reply identity, plus page parameters `p` and `ps`. An attempted public reply request did not return the expected reply collection, so live reply acquisition is not established.
- **CURRENT SOURCE FACT** — the bounded requests above did not require an authenticated session and did not encounter 403, 429, 5xx or CAPTCHA. This does not prove those failures cannot occur.

### Engineering conclusions

- **INFERENCE** — top-level post IDs are suitable Phase 1 `source_item_id` candidates because the same numeric ID appears in list metadata, canonical URL and detail metadata. Site-wide uniqueness is strongly indicated but not formally documented; the contract scopes uniqueness to `eastmoney_guba` top-level posts and fails closed on missing/non-numeric IDs.
- **INFERENCE** — latest-post ordering is safer for incremental acquisition than the default latest-comment ordering, but page-number mutation still requires overlap, ID-based idempotency and explicit partial coverage reporting.
- **INFERENCE** — exact source snapshots must be retained through `raw_ref`; source HTML contains richer fields than the common candidate envelope and must remain replayable.
- **UNKNOWN** — official rate limits, maximum useful historical depth, deletion retention and guaranteed ordering under heavy write traffic are not published or established by this round.
- **UNKNOWN** — operationally reliable reply acquisition and reply pagination remain unverified. Replies are therefore excluded from the Phase 1 approved item scope rather than represented as top-level posts.

## Xueqiu

### Legacy implementation and failure history

- **LEGACY FACT** — `xueqiu_scraper.py` calls `https://xueqiu.com/statuses/search.json` after a best-effort homepage request intended to initialize cookies.
- **LEGACY FACT** — the legacy search sends `count=20`, page number, stock code as `q`, `sort=time` and `comment=0`, and stops after at most four pages for recent search.
- **LEGACY FACT** — non-200, request exceptions, JSON errors and missing keys all break the loop and return accumulated items, often an empty list. The caller cannot distinguish source failure from no new data or a partial collection.
- **LEGACY FACT** — commit `7c42e28` explicitly says Xueqiu has WAF protection plus login validation, may block direct API calls, and should be treated as best-effort. Commit `aa5c11b` logs an empty result as either WAF blocking or no discussion, preserving the ambiguity rather than diagnosing it.
- **LEGACY FACT** — the scraper uses local `datetime.fromtimestamp` without an explicit source timezone and omits source item ID and author identity from its output, despite the endpoint response being expected to contain richer status data.
- **LEGACY FACT** — no tracked deterministic Xueqiu fixture or dedicated direct-scraper regression test was found. No tracked HTML/JSON sample proves a successful legacy response.

### Current public observations

- **CURRENT SOURCE FACT** — an unauthenticated request to `/statuses/search.json?count=20&page=1&q=601012&sort=time&comment=0` returned HTTP 400, a JSON error with code `400016`, and no list.
- **CURRENT SOURCE FACT** — requesting the public homepage first and replaying only its anonymous temporary cookie produced the same HTTP 400/error-code outcome. No authenticated cookie was used or retained.
- **CURRENT SOURCE FACT** — unauthenticated `GET https://xueqiu.com/S/SH601012` returned HTTP 200 but the 110,310-byte body was an Aliyun WAF JavaScript challenge shell, not stock or discussion data. A 200 status is therefore not evidence of usable collection.
- **CURRENT SOURCE FACT** — the current observations do not establish any usable anonymous item fields, item identity, pagination, historical depth or timestamp semantics.

### Failure classification and engineering conclusions

- **INFERENCE** — the reproduced current failure is not evidence that the endpoint path is invalid: it returns structured source-specific error JSON. The supported classification is access/session/WAF dependency; whether ordinary authenticated sessions are sufficient is `UNKNOWN`.
- **INFERENCE** — a source that currently requires browser challenge execution or an authenticated session has higher operational and security complexity than the current Phase 1 should introduce.
- **UNKNOWN** — login-cookie lifetime, account/IP sensitivity, current status schema, stock timeline pagination, historical depth and long-term endpoint stability.
- **UNKNOWN** — whether an officially supported or reasonably stable public discussion API exists for this Collector use case.

## DataClean boundary evidence

- **LEGACY FACT** — legacy collection code performs title filtering, title-as-body substitution, cross-source fallback and semantic deduplication around acquisition.
- **REPOSITORY FACT** — `docs/data-collector/data-contract.md` keeps content-quality filtering, semantic duplication and sentiment outside Collector while requiring replayable raw evidence and distinct failure/no-data/partial outcomes.
- **REPOSITORY FACT** — DataClean remains `PROJECT_BOOTSTRAP` with `NO_ACTIVE_ROUND` and no data-reading/cleaning capability at the read-only baseline above; it exposes principles but no executable Collector input schema or entry point.
- **INFERENCE** — the new adapter may deduplicate identical `(source, source_item_id)` observations caused by page overlap, but must not remove advertisements, low-value posts, repeated prose or disagreeable content. Those are DataClean/measurement concerns.

## Evidence limitations

- Observations are a bounded point-in-time sample, not a load test or availability SLA.
- No authenticated source path was tested.
- No attempt was made to bypass WAF/CAPTCHA or infer protected request signatures.
- Live response bodies remain temporary research material and are not fixtures. Developer must create small, sanitized deterministic fixtures from the documented structures before tests can run.
