# 股吧无浏览器 HTTP 访问研究：可获取，但批量已真实阻断，回补未完成

## 当前优先结论（覆盖下方旧预检结论）

2026-09-30 用户两次纠正后，本支线按实际部署的16股、2026-05-04起的日期窗口与全部待补正文验收，不再以固定页数或抽样数结案。下方11列表/9详情仅是预检历史。

扩展诊断在北京时间17:07:31–17:12:09运行：78次HTTP尝试，77个真实有效列表（601012连续30页、600519连续30页、300750连续17页），6170行观察、6157不同ID。第78请求 `https://guba.eastmoney.com/list,300750,f_18.html` 返回HTTP200/身份核实壳，raw SHA256为 `d9bc3154679106ea3fe92b69042d743009a13a75813d660405fc2868a6174f5a`，4分37.179秒后首次明确阻断。全轮立即停机，详情尚未开始，扩展新增完整正文为0，**不能称批量PASS或回补完成**。

证据：[workload/summary.json](workload/summary.json)、[workload/halt.json](workload/halt.json)、`workload/requests.jsonl`（[归档证据](EVIDENCE-ARCHIVE.md)）、`workload/list-observations.jsonl`（[归档证据](EVIDENCE-ARCHIVE.md)）。普通HTTP列表/详情的有限可取性已成立，当前持续无验证码完成真实回补未成立。随后已完成真实库只读缺口盘点（见 `backfill/inventory.json`），并按最新闲时低频长任务需求实现 `apps/http_backfill/`；其后续状态见应用 SPEC/HANDOFF。此历史诊断没有因代码更新而变成已完成回补，也没有重试挑战或换股/客户端规避。

2026-10-01 整理：完整实验包已移至仓库外，逐文件核验大小/SHA-256；Git 保留结论和小型摘要。本文所有 raw、JSONL、逐请求文件与脚本路径均指归档包，见 [EVIDENCE-ARCHIVE.md](EVIDENCE-ARCHIVE.md)。下方预检状态和在线复现命令是历史记录，不代表当前执行授权或放行。

## 以下为已保留的预检报告（不是当前业务验收）

研究日期：2026-09-30，Asia/Shanghai。角色：Source Researcher。状态：`RESEARCH_COMPLETE — HTML LIST/DETAIL BOUNDED HTTP PASS; LONG-TERM UNATTENDED NOT VALIDATED`。

## 直接回答

1. **能否请求列表：本次可以。** 普通 curl 和 Python 标准库 urllib 获得真实 `article_list`，列表 **11/11 成功**，覆盖 `601012` 的第 1–3 页和 `600519` 的第 1–2 页；5 组相邻分页都有不同 ID 集和 77–80 个新 ID，未出现同一完整页反复充当后续页。
2. **能否请求详情：本次可以。** 使用本次真实列表 `post_type=0` 的精确标准链接，详情 **9/9 成功**，共 **6 篇不同帖子**。请求 URL、响应 `post_id`、列表 ID、作者、canonical bar、标题和原始发布时间严格一致；真实 `post_content` 为 8、15、29、315、328、347 源字符，其中 3 篇长正文明确异于标题，未使用标题补正文。
3. **能否无需验证码处理重复执行：本次有限重复可以。** 独立 curl 进程、独立 urllib 进程，以及同一普通 urllib 临时匿名会话均成功。原始响应没有身份核实壳或明确的静态挑战覆盖层；无需浏览器驱动、浏览器预热、浏览器 Cookie 导出、人工验证、代理轮换或指纹伪装。**这不证明长期无人值守永远没有验证码，也不证明执行页面 JavaScript 后不会出现动态滑块。**

这些是 2026-09-30 当前环境的实测事实，不能用 2026-08/09 的阻断记录否定，也不能反向将旧阻断视为误报。本轮为什么相同公共 HTML 现在可访问，具体风险信号或来源路由原因仍未知。

## 实验环境、范围与分母

来源请求时间：北京时间 **15:59:06.732723–16:21:48.365796**（UTC 07:59:06.732723–08:21:48.365796），约 22 分 42 秒。只在本 run 写研究文件；未改生产 `src/`、SOURCE_SPEC、配置或契约，未读写生产 DB，未启动生产采集/训练链。

- 客户端：系统 `curl 8.7.1`，普通 libcurl/SecureTransport/LibreSSL；urllib 使用每请求记录中的 Python 版本，未安装新 HTTP 库。
- 固定公开研究 User-Agent：`MyResearcher-HTTP-Research/20260930 (anonymous bounded public-source study)`，没有伪装浏览器身份。固定 Accept，个别后续请求使用真实前置列表 URL 作为普通 Referer；没有 `Sec-Fetch-*` 或 TLS/浏览器指纹仿造。
- 首次沙箱请求 DNS 失败，使用环境要求的网络权限后得到真实来源响应。**24 次客户端尝试 = 1 次环境失败 + 23 次到达来源请求**，环境失败不计来源获取失败。脚本保守地把环境尝试也算入 40 次全局上限，当时计数 24、余 16；此预算现已过时，实际来源请求仅 23。
- 一次只发一个请求，实际最小“前次结束到后次开始”间隔 **3.102150 秒**。无自动重试、无并发。每个路径组遇明确挑战立即停止后续同组请求。此次未触发挑战停机，在线矩阵完成后主动结束。

| 请求目标 | curl | urllib | 本次真实数据结论 |
|---|---:|---:|---|
| 公开桌面 HTML 列表 | 6/6 | 5/5 | **11/11**，真实列表 |
| 公开标准 HTML 详情 | 6/6 | 3/3 | **9/9**，严格匹配真实正文 |
| 来源公开 `list.js` / `news.js` | 下载 2/2 | 未测试 | 资源读取成功，不算帖子数据 |
| 脚本实际引用的列表 POST API | 0/1 | 未测试 | 返回非帖子 JSON，帖子获取失败 |
| 沙箱 DNS 尝试 | 环境失败 1 次 | — | 未到达来源，不进入以上分母 |

因此来源层 HTTP 200 为 23/23，但帖子 HTML 数据成功为 **20/20**；若纳入一个 API 候选，数据目标成功为 **20/21**。不能宣称“23/23 都是数据成功”。明确原始挑战证据是 0/20 帖子 HTML 响应；这是 HTTP 原始响应的观测，不是 DOM 渲染后的挑战统计。

## 先校准，再联网

[history-calibration.md](history-calibration.md) 和 [calibration.json](calibration.json) 记录离线校准及原始出处。

- 现存历史 117 份 `.body` 全部带 `em_capt.js`，包含 112 份真实列表和 5 份真实详情。因此该资源单独出现不能判验证码。
- 身份核实 title + 来源验证资源的壳会被识别；HTTP 200 壳没有帖子 payload，不能当空列表或成功。
- 合成的 payload + 强静态挑战覆盖层会同时得到 `data_ok=true` 和 `raw_challenge_evidence=true`，最终按挑战阻断处理；正常模板、普通标题含“验证”、正文含“拼图”、隐藏模板和脚本内挑战文字不误报。
- **31/31 必需离线控制通过**，5 个历史详情与 112 个历史列表解析通过。离线检查只证明分类/结构判定，不冒充本次线上成功。
- D-016/D-017 和 2026-09-24 的真实挑战证据已复核。保存截图显示动态滑块可以覆盖正常列表，保留 payload 不等于没有挑战。历史真实 2834-byte 身份核实响应目录现已缺失，其 SHA 只可引用历史记录；现存 431-byte 壳 fixture 明确是合成样本。

本次分类器只分析响应字节中的 title、资源、静态覆盖层和明确指令，不执行 JS/CSS。纯指令文本仅记疑似，强静态覆盖层/壳才提供明确挑战证据。每请求保留当时 detector SHA256；[final-detector-replay.json](final-detector-replay.json) 用最终版本离线重放所有响应，数据/挑战判定无变化。

## 列表、分页与排序

本次坚持 `list,{code},f.html` / `f_2.html` / `f_3.html`，来源当前脚本明确把 `sorttype=0` 标为“最新发帖”；默认 `list,{code}.html` 的 `sorttype=1` 是“最新评论”，不能替代原始发帖时间排序。代码出处见 [online-script-discovery.md](online-script-discovery.md)。

每个列表校验：HTTP 200、`article_list.rc` 严格整数 1、`re` 数组、所有 ID/类型/原始时间、同页 ID 唯一、type0 精确的 HTTPS 来源详情链接。81/84 行的当前第一页并未被错误压到 80：来源有置顶/非标准类型附加项，冻结 spec 中 80 是历史观测值。非 type0 行完整保留在 raw 和显式计数。

| 路径/客户端 | 请求序号 | 两页行数 | 重叠 ID 数 | 后页新 ID 数 | 分页结果 |
|---|---|---:|---:|---:|---|
| 601012 curl p1 → p2 | 2 → 4 | 81 → 80 | 3 | 77 | 进展 |
| 601012 curl p2 → p3 | 4 → 18 | 80 → 80 | 2 | 78 | 进展 |
| 600519 curl p1 → p2 | 5 → 10 | 84 → 80 | 2 | 78 | 进展 |
| 601012 urllib 会话 p1 → p2 | 11 → 13 | 81 → 80 | 1 | 79 | 进展 |
| 600519 urllib 会话 p1 → p2 | 14 → 16 | 84 → 80 | 0 | 80 | 进展 |

[pagination.json](pagination.json) 给出完整重叠 ID、有序 ID 签名、全部行与 type0 的时间边界。置顶/非 type0 旧条目可令全页最早时间早于后页，不能仅看全页最早时间推断分页错误；本次用 ID 进展和精确 type0 时间核对。

本次新增研究数据：**892 条列表观察**（含有限重复），**403 个不同源 ID**，其中 **368 个不同 type0 ID**。观察类型计数为 type0=818、type1=11、type20=60、type3=3；818+11+60+3=892。这些是获取边界与源类型，不是数据质量或投资语义分类。

## 详情与重复性

详情先从 seq2 当前列表选择 seq3，再扩至第二页、第二只股票和临时匿名会话。本次所有详情均携带相应已保存的本次列表候选用于严格比对。`post_last_time`/阅读评论等可变观察字段会变化，均独立保留，不代替 `post_publish_time`。

| 不同帖子 ID | canonical bar | 源 `post_content` 字符数 | 正文与标题相同 | 获取序号 |
|---|---|---:|---|---|
| 1779352996 | 601012 | 8 | 是，来源确有该正文 | 3 |
| 1779250198 | 601012 | 29 | 是，来源确有该正文 | 8 |
| 1779356899 | 600519 | 328 | 否 | 9、15、22、24 |
| 1779359910 | 601012 | 315 | 否 | 12 |
| 1779156897 | 601012 | 347 | 否 | 20 |
| 1779352981 | 600519 | 15 | 是，来源确有该正文 | 21 |

字符数包括来源正文中的 HTML，未做清洗。短正文恰等于标题不是用标题生成：JSON 原始 `post_content` 字段及独立页面正文容器均有证据；315/328/347 字符的正文进一步确认获取到了正文结构。

- 同一 600519 帖子 `1779356899` 经 curl、urllib 会话、独立 urllib、独立 curl 共 4 次，正文 SHA256 全为 `88bc43cb31e935e808bebe73ebda590d1563609d6771e29e49ee0132e2ea6637`，每次仍验证真实列表元数据/身份。页面整体 SHA 可因动态计数和页面字段变化而不同，不要求字节完全相同。
- 601012 同一第一页经 seq2 curl、seq11 会话、seq19 独立 urllib、seq23 独立 curl 均成功。首次到会话之间有新帖子到达；seq11/19/23 的有序 ID 相同属同页重复成功，与不同页完全相同的分页陷阱不同。
- urllib 临时匿名会话在同一进程完成 6 请求（两只股票各列表、详情、p2），结束 CookieJar=0；独立 curl/urllib 也无 Cookie。此次数据获取不依赖浏览器保存状态。

[repeatability.json](repeatability.json)、[session/session-summary.json](session/session-summary.json) 和 `detail-observations.jsonl`（[归档证据](EVIDENCE-ARCHIVE.md)） 可重放以上观察。6 篇不同完整正文只占 368 个 type0 列表 ID 的一部分；其余列表条目尚无完整正文，不能称已完成整页/全部帖子采集。

## 来源 API 与移动入口研究

先从历史保留来源文档取得真实 `list.js` / `news.js` 地址，再用本次 curl 下载核对完整脚本。地址、原始字节偏移、SHA 和精确源码在 [offline-entry-discovery.md](offline-entry-discovery.md)、[online-script-discovery.md](online-script-discovery.md)。未臆造接口、签名或挑战参数。

当前 list.js 确实构造普通表单 POST：

```text
https://guba.eastmoney.com/api/getData?code=601012&path=webarticlelist/api/Article/Articlelist
param = code=601012&type=0&p=1&ps=80&sorttype=0
plat = Web; path = webarticlelist/api/Article/Articlelist
env = 2; origin = 空; version = 2022; product = Guba
```

精确表单保存在 `api-form-601012-p1.json`（[归档证据](EVIDENCE-ARCHIVE.md)），整体 `param` 外层 form-urlencode。seq17 返回 HTTP 200 / 230 bytes，却是 `re:true`、4 个只含 `security/star` 的 `result` 对象，缺失 `rc/post_id`，`re` 也不是帖子数组。该候选本次 **0/1**，分类 `unexpected_non_post_json`，不是 CAPTCHA、空帖子列表或数据成功。独立复核未发现 helper、方法、字段或编码误读；无证据支持猜新参数/盲目重试。原始 `json_received_unvalidated` 记录保留，最终重放给出派生分类。

来源 helper 使用 `withCredentials=true`，浏览器可能附 Cookie；本次普通无 Cookie 的 POST 与来源浏览器 XHR 的差异尚未全面测试，不能据此确认失败原因或永久失效。现代 `WebArticleList`、HighQuality 列表等不同 UI 分支未测试，不能合并成全量 latest-post 列表。

news.js 使用服务端已有 `window.post_article`；没有发现匿名标准正文读 API。`GetPostEditText` 属修改权限流程，回复 API 不能替代帖子正文。117 份历史文档没有非空 mobile-agent 入口；当前页面还引用 `h5Adaptation.js?r=6`，本轮未抓取该脚本/展开移动路由，因为普通 HTML 主目标已成功。**“本轮未发现/未测试”不等于移动页面或详情 API 不存在。**

## 吞吐观察与无人值守限制

23 来源响应总计 **3,184,050 bytes**。单请求耗时最小 0.139338 秒，中位数 0.249360 秒，最大 0.422733 秒。urllib 6 请求会话的窗口为 17.142752 秒，按“首请求开始到末请求结束”计算约 21.00 请求/分钟；窗口边界效应、3.1 秒等待和内容量共同影响该数值，**它不是可持续速率、安全频率或负载测试结论**。列表之后每篇详情另占请求预算，整页 70–80 篇正文的耗时不能用一页列表耗时替代。

curl seq5 指定 `--http2`，但 guba 实际协商仍为 **HTTP/1.1**；两份静态 JS 实际 HTTP/2。因此本轮没有建立 guba HTTP/2 与 HTTP/1.1 的有效 A/B 差异，也不能把成功归因于 HTTP/2。普通 curl 自动/明确 HTTP/1.1 和 urllib 均已成功；urllib 实际线上协议未保留，应记为 unknown，不能由配置选项声称它的实际协议。

未测试：长时间/跨日运行、完整页所有详情、历史尾部、持续高频/并发、其他机器与网络、429/403/5xx、真实删除重定向、动态 JS 渲染挑战、未安装 requests/httpx、移动页面、其他 API 分支。删除 URL 的 `302 → /error?type=2` 已在历史合同中记录，但本次没有发生，分母为 0；不得将历史删除失败混入当前详情成功率。程序对检测到的明确原始响应挑战可停止并留证；动态挑战不能由纯 HTTP 检测，长期可用性仍需独立验收。

## 历史可重放文件与命令（需完整归档）

入口：[handoff.md](handoff.md)。全请求台账：`requests.jsonl`（[归档证据](EVIDENCE-ARCHIVE.md)），包含每请求 UTC/北京时间、方法、URL/最终 URL、非秘密配置、客户端版本、状态、耗时、bytes、SHA、当时 detector SHA、理由和 raw ref；每请求也有独立 JSON。易筛选表：`request-matrix.tsv`（[归档证据](EVIDENCE-ARCHIVE.md)）；统计：[summary.json](summary.json)。

数据：`list-observations.jsonl`（[归档证据](EVIDENCE-ARCHIVE.md)） 保留全部 892 源行及原始时间/类型/来源；`detail-observations.jsonl`（[归档证据](EVIDENCE-ARCHIVE.md)） 保留 9 个正文观察、6 个不同 ID、作者、时间及原始响应引用。均标 `research_only=true`，未接入 DB/训练。

原始实体在归档包的 `live/*.body` 和 `session/*.body`，按仓库既有 `*.body` 策略不进入 Git；初始预检的 24 份响应仍完整保留并经 hash 核对。异机交接必须显式带上完整归档和哈希清单，否则只能复核结构化摘要，无法重放原始响应；没有将历史 DOM serialization 冒充本次 HTTP 原始实体。本次整理只提交结论与摘要。

历史零网络核验命令，需在完整归档实验包目录执行，并具备校准引用的历史 corpus：

```bash
python3 -B calibration.py
python3 -B summarize.py
```

两个命令已执行且 exit 0：31/31 控制通过，24/24 响应 SHA/bytes 与台账匹配、分页进展及最小间隔核验无问题。独立审计另从原始 HTML 用独立 JSON 提取与锚点核验，不依赖请求脚本结论，结果见 [independent-evidence-audit.md](independent-evidence-audit.md)。

历史 HTTP 复现示例（仅保留为实验记录，旧预算不是当前在线授权；不得据此继续已阻断任务）：

```bash
python3 -B probe.py 'https://guba.eastmoney.com/list,601012,f.html' \
  --out reproduction --label replay-list --group desktop-list --kind list
python3 -B probe.py 'https://guba.eastmoney.com/news,600519,1779356899.html' \
  --out reproduction --label replay-detail --group desktop-detail --kind detail \
  --expected expected-600519-p1-0.json
python3 -B probe.py --anonymous-session
```

新采集应先取当次列表，保存候选再请求详情；以上固定 ID 是重放本次严格已知样本，不是永久可访问承诺。程序串行设计，禁止多个研究进程并发绕过预算；可重复的会话脚本仍受同一台账和挑战停机约束。

## 历史预检接入建议与后续验收

以下是旧预检时点的建议，当前闲时一分钟一次长任务、实现及验收以
`apps/http_backfill/SPEC.md` 和 `HANDOFF.md` 为准；旧建议不能解除本轮阻断。

本轮足以提出 **普通匿名 HTTP HTML 作为待验收生产 transport 候选**；没有授权把冻结 SOURCE_SPEC 直接改成该入口，也没有改生产代码。

1. Source Researcher 提交版本化 access amendment：保留既有 `article_list/post_article`、时间/身份/type0/分页语义，新增普通 HTTP 可用证据、HTTP 与动态 DOM 的可观测区别，明确永久免验证码不可承诺。只有正式批准后 Developer 才接入。
2. Developer 使用普通 urllib 或已批准标准 HTTP 库封装窄 transport，固定公开研究/产品 User-Agent、无浏览器预热/身份轮换；保留 HTTPS 来源 allowlist、逐跳最终 URL、状态、hash、raw ref、最少 3 秒串行间隔和全局预算。使用现有严格 parser，不把 80 固定成必须的行数；页签名进展与明确壳/静态挑战先检查，失败仍按原 partial/failed 语义处理。
3. 按隔离运行验收一页全部 type0 详情，再做两只股票的三页完整窗口，累计预算应提前给出（80 行页面按来源实际 type0 数算详情成本）。每段遇明确挑战立即停止，不重复轰击，不写 checkpoint 为成功。接口候选本轮失败，不作为 fallback。
4. Tester 独立检查 raw 与 list/detail 身份、所有类型计数、分页新 ID、正文原始字段、失败分母和没有人工介入。再进行至少跨日/不同时间窗的有预算无人值守实验，记录成功率、首个失败位置、覆盖度和实际吞吐；通过前不宣称长期稳定或安全频率。
5. 需要静态回归 fixture 时从本 run 做必要脱敏、保留正常 em_capt 模板和 payload+挑战正例。后续 DataClean/训练使用数据之前，先完成其独立输入契约、正文覆盖和 raw retention 验收，不在 Collector 做内容价值/情绪过滤。

本次发现的可用路径已经有当前实测、有限重复、离线回放和独立审计支持。下一步是上述 access amendment 与受预算的完整窗口验收；本支线的网络研究在当前证据闭环后结束。
