# 雪球隆基绿能 Backfill 3/7 天项目独立实验（2026-08-16）

## 1. 实验目的

本轮不从现有项目实现出发，也不修改 Collector、Backfill、SOURCE_SPEC、数据库或
Transport。目标是先回答一个来源事实问题：

> 在当前可用浏览器上下文中，雪球隆基绿能“新帖”页面能否按正常浏览节奏连续
> 翻页，并分别穿过最近 3 个自然日和最近 7 个自然日的时间边界？

实验股票：

```text
隆基绿能
SH601012
```

入口 URL：

<https://xueqiu.com/S/SH601012>

实验时区：`Asia/Shanghai`。

窗口定义采用“包含今天的自然日窗口”：

```text
3 天：2026-08-14 00:00:00 +08:00 → 2026-08-16 实验时刻
7 天：2026-08-10 00:00:00 +08:00 → 2026-08-16 实验时刻
```

## 2. 实验前思考

### 2.1 先验证页面，不先假定 API

本轮只验证浏览器实际可见页面：

- 不调用现有项目的雪球代码；
- 不扫描或猜测 API 参数；
- 不读取 Cookie、Local Storage、Profile、密码或浏览历史；
- 不把 HTTP 200、页面标题或登录入口单独当作帖子访问成功；
- 只有页面中出现可解析帖子节点才算列表成功。

### 2.2 使用来源自己的“新帖”顺序

列表文章节点的 `analytics-data` 实际显示：

```text
sub_tab = 新帖
order = time
```

因此实验顺着来源页面自己的新帖分页向旧时间移动，不随机跳页，也不把“热帖”或
其它排序混入 Backfill。

### 2.3 先找边界页，再计算窗口内帖子

实验停止条件不是固定页数，而是：

1. 逐页访问；
2. 记录每个 `status_id`、作者、列表时间、详情 URL 和正文摘要；
3. 直到页面确实出现早于窗口下界的帖子；
4. 保留边界页中的窗口内记录，排除边界外记录。

### 2.4 正常浏览节奏

每次翻页或详情导航前随机等待 3–10 秒。访问是单线程、顺序执行，没有并发请求、
刷新循环、UA 轮换、指纹模拟、代理轮换或验证码处理。

## 3. 实际执行过程

### 3.1 打开股票页

打开：

<https://xueqiu.com/S/SH601012>

页面正常显示隆基绿能行情和讨论区。首次 DOM 快照时异步帖子尚未出现；等待约 5 秒
后，`.status-list` 中出现真实帖子列表。

页面同时显示“登录”和“立即登录/注册”入口，但这只是页面 UI，不是访问阻断：
帖子列表、详情正文和评论仍可读取。

### 3.2 识别帖子结构

列表中每条帖子对应：

```text
article.timeline__item
```

实际可取得的主要字段：

```text
status_id   = a.date-and-source[data-id]
author      = a.user-name
detail URL  = a.date-and-source.href
time text   = a.date-and-source.innerText
content     = .timeline__item__content
```

详情 URL 形态实际为：

```text
https://xueqiu.com/{user_id}/{status_id}
```

### 3.3 顺序翻页

使用页面自身分页控件，从第 1 页连续访问到第 23 页。股票页 URL 在客户端分页时保持：

```text
https://xueqiu.com/S/SH601012
```

因此不能仅通过地址栏 URL 判断当前页码，必须检查分页控件的 active 页和帖子 ID。

每页实际帖子数：

| 页码 | 条数 | 页码 | 条数 | 页码 | 条数 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 10 | 9 | 10 | 17 | 10 |
| 2 | 10 | 10 | 10 | 18 | 10 |
| 3 | 10 | 11 | 10 | 19 | 10 |
| 4 | 10 | 12 | 10 | 20 | 9 |
| 5 | 10 | 13 | 10 | 21 | 10 |
| 6 | 8 | 14 | 9 | 22 | 10 |
| 7 | 9 | 15 | 10 | 23 | 10 |
| 8 | 10 | 16 | 10 |  |  |

合计：

```text
页面：23
行数：225
唯一 status_id：225
重复 status_id：0
```

页大小不是固定 10。第 6、7、14、20 页少于 10 条，但仍能正常进入下一页；因此
“少于 10 条”不能直接当成历史尾部。

## 4. 3 天边界结果

3 天下界是：

```text
2026-08-14 00:00:00 +08:00
```

第 5 页穿过该边界。列表依次显示：

| 位置 | status_id | 列表时间 |
| ---: | --- | --- |
| 1 | `405032576` | 修改于 08-14 11:28 |
| 2 | `405026255` | 08-14 11:02 |
| 3 | `405022982` | 08-14 10:51 |
| 4 | `405015598` | 08-14 10:27 |
| 5 | `405014354` | 08-14 10:23 |
| 6 | `404997527` | 08-14 09:37 |
| 7 | `404988719` | 08-14 08:54 |
| 8 | `404986364` | 08-14 08:32 |
| 9 | `404968916` | 08-14 00:22 |
| 10 | `404958103` | 08-13 22:28（边界外） |

所以：

```text
第 1–4 页：40 条
第 5 页窗口内：9 条
最近 3 个自然日：49 条
```

下边界内最后一条：

<https://xueqiu.com/3764202289/404968916>

下边界外第一条：

<https://xueqiu.com/9988065465/404958103>

完整清单见 [evidence/posts-backfill-3d.json](evidence/posts-backfill-3d.json)。

## 5. 7 天边界结果

7 天下界是：

```text
2026-08-10 00:00:00 +08:00
```

第 23 页穿过该边界。前 3 条为：

| 位置 | status_id | 列表时间 | 归属 |
| ---: | --- | --- | --- |
| 1 | `404327209` | 修改于 08-10 06:05 | 窗口内 |
| 2 | `404324859` | 08-10 00:45 | 窗口内 |
| 3 | `404321950` | 08-09 23:15 | 边界外 |

所以：

```text
第 1–22 页：215 条
第 23 页窗口内：2 条
最近 7 个自然日：217 条
```

边界帖子：

- <https://xueqiu.com/1088846097/404327209>
- <https://xueqiu.com/7851919725/404324859>
- <https://xueqiu.com/1302611845/404321950>（边界外第一条）

完整清单见 [evidence/posts-backfill-7d.json](evidence/posts-backfill-7d.json)。

## 6. 详情页抽样

抽样访问：

1. 最新长文：<https://xueqiu.com/6861928576/405225405>
2. 中段帖子：<https://xueqiu.com/1873655483/404791394>
3. 七天边界外侧长文：<https://xueqiu.com/6213802785/404301590>

三者均满足：

- 最终 URL 中 `status_id` 与列表一致；
- 页面标题、作者、时间、正文和评论可见；
- 未出现验证码或安全验证；
- 没有因未登录而隐藏正文。

详情页公开脚本还包含：

```text
window.SNOWMAN_STATUS
```

其中可以观察 `id`、`created_at`、`edited_at`、`target` 等公开页面状态。本轮只读取
与列表 ID 和时间边界有关的字段，没有读取浏览器身份数据。

## 7. 关键发现：列表“修改于”不能当发布时间

雪球列表对未编辑帖子显示发布时间，对编辑过的帖子显示“修改于”。这会造成旧页中
出现一个看起来比相邻帖子更新的时间。

### 例 1：旧帖子在第二天被编辑

帖子：<https://xueqiu.com/3439104632/404908180>

```text
列表显示：修改于 08-14 16:57
created_at：2026-08-13 15:56:44 +08:00
edited_at： 2026-08-14 16:57:14 +08:00
```

它不属于从 `08-14 00:00` 开始的 3 天创建时间窗口。若只过滤列表显示时间，会把
3 天结果从正确的 49 条错误算成 50 条。

### 例 2：创建和编辑均在窗口内

帖子：<https://xueqiu.com/1978396034/405032576>

```text
created_at：2026-08-14 11:26:05 +08:00
edited_at： 2026-08-14 11:28:47 +08:00
```

这条应计入 3 天窗口。

### 例 3：七天边界编辑帖

帖子：<https://xueqiu.com/1088846097/404327209>

```text
created_at：2026-08-10 06:03:16 +08:00
edited_at： 2026-08-10 06:05:43 +08:00
```

这条确实属于 7 天窗口。

结论：

```text
列表显示时间 = published/created time 或 edited time 的混合展示
严格创建时间 = 详情公开状态 created_at
```

不能把所有 `time_text_observed` 静默解释为 `published_at`。

## 8. 其它来源行为发现

### 8.1 异步加载

初始 DOM 中可能没有帖子，约 5 秒后才出现 `.status-list`。页面标题或行情出现不等于
讨论流已经加载成功。

### 8.2 客户端分页

翻页时地址栏 URL 不变。必须验证 active 页码和帖子 ID 序列，不能用 URL 变化作为
分页进展证明。

### 8.3 页面行数可变

观察到 8、9、10 条三种页大小。不能用 `rows < 10` 判定空页或历史尾部。

### 8.4 登录 UI 不等于访问失败

页面一直存在登录入口，但列表和详情正文仍可见。访问失败应根据帖子结构或正文是否
存在判断，而不是搜索到“登录”二字就失败。

### 8.5 本轮未出现访问阻断

连续 23 页和多个详情导航中，没有观察到：

- 图形验证码；
- 安全验证；
- 身份核实壳；
- 重复整页；
- 分页停滞；
- 详情 ID 错配。

这只描述本次有限实验，不能转换为永久来源保证。

## 9. 结论

```text
XUEQIU_SH601012_PAGE_ACCESS
PASS

XUEQIU_SH601012_PAGINATION_23_PAGES
PASS

XUEQIU_SH601012_BACKFILL_3D
PASS — 49 posts

XUEQIU_SH601012_BACKFILL_7D
PASS — 217 posts

DETAIL_PAGE_ACCESS
PASS

CAPTCHA_OR_CHALLENGE
NOT OBSERVED

DUPLICATE_PAGE_OR_ID
NOT OBSERVED

PROJECT_INTEGRATION
NOT PERFORMED

CODEX_INDEPENDENT_PROCESS
NOT TESTED
```

“项目独立实验”表示本次没有调用或修改 DataCollector 实现。实际浏览器控制仍由本次
Codex 会话执行，因此不能写成“完全脱离 Codex 的独立进程已经跑通”。

## 10. 归档证据

- [evidence/manifest.json](evidence/manifest.json)
- [evidence/posts-backfill-3d.json](evidence/posts-backfill-3d.json)
- [evidence/posts-backfill-7d.json](evidence/posts-backfill-7d.json)
- [evidence/posts-observed-23-pages.json](evidence/posts-observed-23-pages.json)

帖子 JSON 字段：

```text
page
position
status_id
author
time_text_observed
url
content_excerpt
```

`time_text_observed` 刻意保留来源页面原始展示语义，没有把“修改于”重命名为
`published_at`。
