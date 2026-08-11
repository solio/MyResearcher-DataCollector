# Xueqiu SOURCE_SPEC

Status: APPROVED

Approved by: Reviewer

Approval basis: commit `41a2e3bd11883438410e0b0a5e043a64aa22c3fa`

本文件是当前唯一生效的 Xueqiu source contract。早期探测过程和原始阻断
结果保留在 `runs/xueqiu-source-probe/`；早期 plain-HTTP WAF 或探测环境
限制不得覆盖 Final Browser Network Gate 已确认的事实。

## 1. Source 与范围

```text
source_name: xueqiu
scope: A-share stock-scoped top-level discussion posts
```

入口页面：`https://xueqiu.com/S/{symbol}`

OUT OF SCOPE：post replies、user profile crawling、news、announcements、
hot feed、HK/US markets、historical full backfill。

## 2. Approved Access Route

生产 v0.1 使用：

```text
browser-managed anonymous session
browser-managed context required: YES
login required: NO for the approved observed path
```

“login required: NO”只表示 Final Gate 观察到的 v0.1 路径无需登录取得
该 JSON；不表示雪球所有接口永远无需登录。

页面自身产生的 discussion request：

```text
GET https://xueqiu.com/query/v1/symbol/search/status.json
```

已观察的非敏感参数：

```text
symbol
count=10
comment=0
hl=0
source=all
sort=time
page
q=
type=11
```

后续页还必须沿 browser/page 实际生成的 pagination chain 传递 `last_id`。
参数顺序和其它由页面运行时产生的参数不得被硬编码为独立 crawler 协议。

## 3. Challenge / Signature 与安全

```text
challenge/signature generation: BROWSER_OWNED
```

Collector 必须让正常 browser/page runtime 自己产生所需 session 和
challenge request state。Developer 不得逆向算法、自己生成或硬编码
signature、保存 signature value、绕过 WAF，或实施 CAPTCHA bypass、proxy
rotation、account rotation、stealth、fingerprint spoofing。

不得保存 cookie values、credentials、authorization headers 或 challenge/
signature values。运行输出也不得泄露这些值。

## 4. Response Shape

```text
top-level item path: list
pagination fields: count, maxPage, page
```

Final Gate 观察到 page 1、page 2 和重复 page 1 均为 HTTP 200
`application/json` XHR，每页 10 items。Observed item fields：

```text
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
```

## 5. Identity 与 Field Mapping

冻结映射：

```text
source           = "xueqiu"
stock_code       = requested A-share stock scope
source_item_id   = str(item.id)
author_id        = item.user.id
author_name      = item.user.screen_name
title            = item.title
content          = item.description
published_at     = item.created_at
url              = item.target
like_count       = item.fav_count
reply_count      = item.reply_count
forward_count    = item.retweet_count
```

`item.id` 必须非空且可转换为字符串；缺失或非法 `id` 是
`schema/item failure`。不得使用 content hash、URL hash 或 author+time
fallback 生成身份。

`title` 在 source 返回 null 或缺失时可 nullable；其余 required source
fields 缺失或结构非法时按 schema/item failure 处理。其它 source fields
按现有 repository contract 保存至 `source_metadata`，不得扩大采集 user
profile。

## 6. Content 与时间

`content = item.description` 按 source 返回值原样保存。即使其中包含
HTML，Collector 也不得 strip HTML、删除 emoji、spam filtering、sentiment
cleaning 或 semantic normalization；这些属于 MyResearcher-DataClean。

```text
published_at source field: created_at
representation: Unix epoch milliseconds
```

Collector 将其转换为 repository canonical timezone-aware timestamp；不得
用 browser relative-time label 替代。原始 publish time、collection time 和
update time（若有）是不同事实，不得互相替代。

## 7. Pagination 与速率

```text
ordering: newest-first
pagination: sequential only
concurrency: 1
minimum request/page interaction interval: >= 3 seconds
```

第一页使用 `page=1`。后续页面必须沿 browser/page 产生的 `page` +
`last_id` continuity 前进；不得丢弃 `last_id`。

若 page 不推进、last_id chain 无效、或整页重复且无法推进，不得解释为
`NO_NEW_DATA`，必须按 source/runtime failure 或 incomplete collection
处理。

## 8. Bootstrap

```text
XUEQIU_BOOTSTRAP_MIN_PAGES = 2
```

Fresh state（`checkpoint == NULL`）进入 `BOOTSTRAP_PENDING`，依次执行：

```text
page1 -> page2 using approved browser pagination
```

两页全部成功，并且所有 required items 已 accounted for、没有 unresolved
access/schema failure，才可产生：

```text
SUCCESS
stop_reason=bootstrap_complete
```

初始 checkpoint 为成功解析且属于 scope 的 bootstrap items 中最大的合法
`created_at`。checkpoint 只是 forward incremental baseline，不是 historical
completeness proof。

若任一 required page、item、access 或 schema step 失败：

```text
checkpoint remains NULL
```

下一次仍从 page 1 重新 Bootstrap。

## 9. Incremental

存在 checkpoint 后进入 `ordinary incremental mode`，从 page 1 开始顺序扫描。

- Unknown ID 始终 eligible；即使 `created_at <= checkpoint` 也不能仅凭
  timestamp suppress。
- Known historical item（known ID 且 `created_at <= checkpoint`）无需为
  mutable metadata 强制 historical refetch。
- 正常 authorized acquisition 观察到 metadata/content drift 时，按
  repository 既有 observation/version contract 处理。

当出现完整 page，且：

```text
all source_item_id already known
AND all created_at <= committed checkpoint
```

即为 `KNOWN_BOUNDARY_REACHED`，可以停止继续向历史扫描。Xueqiu 不复制
Eastmoney confirmation page 或其它 Eastmoney safe-frontier semantics。

## 10. Coverage Cap 与终态

保留 runtime safety cap：`max_pages`。

若达到 `max_pages` 且 `KNOWN_BOUNDARY_REACHED == false`：

```text
PARTIAL_COLLECTION
```

Xueqiu v0.1 下 `PARTIAL_COLLECTION` 不推进 committed checkpoint；第一版
不实现复杂 partial safe-prefix advancement。

只有同时满足以下条件才允许 `NO_NEW_DATA`：

```text
checkpoint exists
AND known boundary safely reached
AND no new accepted source item/observation
```

401、403、WAF challenge、CAPTCHA、browser session failure、invalid JSON、
missing required list、challenge/session failure、pagination failure 或其它
access/schema failure 都不得转换为 `NO_NEW_DATA`。

## 11. Access Failure

以下均为 source/access failure：401、403、WAF challenge、CAPTCHA、browser
session failure、invalid JSON、missing required list、challenge/session failure
和 pagination failure。禁止任何 bypass 或规避行为。Collector 必须区分
access failure、transport failure、schema/item failure、partial collection
和 no-data。

## 12. Raw Evidence 与 Provenance

保存 existing RawEvidence contract 要求的：

```text
response bytes / equivalent captured source response
request provenance
source URL
collection time
SHA
```

Browser transport 不得破坏 replay/audit requirement。原始来源材料必须可追
溯；清洗和语义转换不属于 Collector。

## 13. Evidence 与历史说明

最终依据：

- `runs/xueqiu-source-probe/final-browser-network-probe.md`
- `runs/xueqiu-source-probe/final-browser-network-evidence.md`
- `runs/xueqiu-source-probe/final-handoff.md`
- Reviewer 已审阅 commit `41a2e3bd11883438410e0b0a5e043a64aa22c3fa`

Final Gate 观察到 page 1/page 2 overlap 为 0；page 2 使用 page 2 +
`last_id` 且整体更旧。等待至少 3 秒并 reload 后，重复 page 1 与首次 page 1
为 10/10 overlap，无新增或删除，最新 `created_at` 未改变。

Earlier probe attempts were blocked by plain-HTTP WAF or probe-environment
limitations; see `runs/xueqiu-source-probe/`. 这些历史结果不再作为当前
approved contract 状态。

## 14. Production Boundary

本 SOURCE_SPEC 现在授权 Developer 按本文件实现 Xueqiu v0.1 adapter，但本
次 SOURCE_SPEC finalization 本身不实现 adapter。除本文件和必要 handoff
artifact 外，本轮不得修改 `src/`、`tests/`、Persistence、Eastmoney 或
Batch。
