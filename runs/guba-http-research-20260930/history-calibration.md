# 股吧 HTTP 研究的离线历史校准

日期：2026-09-30（Asia/Shanghai）\
角色：Source Researcher，独立离线证据审计\
范围：只读历史文档、原始 HTML、截图和 JSON/JSONL；不发网络请求，不运行浏览器，不读写生产数据库。本次只新增本文件。

## 结论与本次实验应采用的措辞

历史证据确立了两个相互独立的事实：来源返回的真实 `article_list` / `post_article` 数据可以有效；来源同时也可能要求验证。必须独立记录数据有效性和挑战证据，不能把其中一个当作另一个的判据。

- `HTTP 200` 仅代表状态码；HTTP-200 的“身份核实”壳没有真实列表/详情。
- 正常页自带 `em_capt.js`。模板资源出现只能记录为诊断字段，不能单独判 `access_block`。
- 正文或列表可以在挑战覆盖层出现时仍可解析。解析成功不证明未发生挑战。
- 本轮纯 HTTP 没有 JavaScript 执行和渲染环境，不能看到脚本后续注入的动态滑块、iframe 内部内容和遮挡关系。“收到的原始响应未发现挑战证据”是可核验结论；“渲染后无验证码”“永久免验证码”不是。
- 原始响应全局出现“验证”“滑块”“拼图”也不能直接证明挑战。这些词可能属于帖子正文、标题、脚本字符串、注释或隐藏模板。应记录命中位置和上下文，区分明确验证壳、明确原始挑战标记、疑似证据、未发现证据；原始字节的元素存在不等于用户可见。

生产 SOURCE_SPEC 仍规定既有浏览器入口，本次 scope 明确授权隔离 HTTP 研究。这些校准事实不会自行改变生产入口。

## 已读契约与审计方式

依 AGENTS.md 顺序读 `AGENTS.md`、产品目标、协作契约、数据契约、实现计划、本 run `scope.md`、`specs/eastmoney_guba.md`。本 run 在审计开始时只有 scope，尚无 current handoff。继续读了指定历史：`experts/eastmoney-live-access/README.md`、`CURL-CFFI-AUDIT.md`、`PROMPT-ENGINEERING-REVIEW.md`、`runs/phase-01-round-01/research-evidence.md`、decision log D-016/D-017/D-024/D-025。

离线执行两类检查：

1. Python 标准库逐一扫描现存 `data/raw/eastmoney_guba/*.body`，计算 SHA-256，读取标题、模板资源、嵌入 payload 与源 `post_content` 字符数。
2. `python3 -B` 只导入确定性 parser/challenge functions，对 5 份真实详情、验证壳 fixture 及通过 AST 提取自 `referer_probe_selftest.py` 的 F1/F2/F3 做解析；对已有 `runtime/referer-probe-selftest.json` 的 DOM 记录重放 `challenge_reasons`。没有执行 selftest 脚本、启动浏览器、写入 pycache 或重新渲染。该重放检查的是当前判定逻辑，DOM 可见性的直接观察来自历史截图和历史 selftest 记录。

## D-016：真实数据与挑战覆盖层可并存

`docs/data-collector/decision-log.md:153` 开始的 D-016 把 2026-09-23 referer probe v1/v2 的 `blocked=0` 改成 `blocked=unknown`。其原因是当时使用的壳判定和 payload 解析器都不能观察渲染后的滑块，用户看到滑块时 probe 仍报告 clean。

直接查看保存截图 `runtime/diagnostics/eastmoney-20260813T132249.png`：中央确有“拖动下方滑块完成拼图”，后面仍能看到正常股吧列表和行情栏。截图大小 364169 bytes，SHA-256：

`918a5b107071e739ad154f3a85537e22b739a5e6fdc5cf0f69ac2effb3b2ece5`

边界必须保留：D-016 明确该截图对应列表 `list,601888,f_51.html`；它不是详情页的同形覆盖层证据。`challenge_dom.py` 模块说明和旧 selftest 的开头有把截图与 `post_article` 描述联在一起的宽泛叙述，不能据此扩大真实观察范围。真实截图证明“正常页面后景与可见挑战同时存在”；详情 payload 与挑战并存的确定性机制由合成 F2/F3 证明，不等于本轮观察到真实详情 overlay。

## D-017：正常模板资源造成反向误报

`docs/data-collector/decision-log.md:161` 开始的 D-017 记录了把 `em_capt.js` 单独当挑战后，正常页 100% 被误报的失败。2026-09-30 对当前本机 corpus 的直接复核与该历史分母一致：

| 当前离线 corpus 事实 | 数量 |
|---|---:|
| `.body` 文件 | 117 |
| 包含 `em_capt.js` | 117/117 |
| 有嵌入 `article_list` | 112/117 |
| 有真实可解析 `post_article` | 5/117 |
| 5 份详情严格解析成功 | 5/5 |
| 出现 `验证码`、`滑块` 或 `拼图` 的响应文件 | 0/117 |
| 标题为已知验证壳标题的文件 | 0/117 |

这 117 是内容寻址的唯一历史 body 文件数，不是请求次数，也不是本次 HTTP 成功率。保存 body 的获取方式与时间必须通过原请求 metadata 确认；不能把既有 browser/DOM body 的存在冒充 curl 当前成功。

五份详情都 `is_access_block_page=False`、`parse_detail_page=PASS`，且真实 `post_content` 非空：

| `data/raw/eastmoney_guba/` 下文件（文件名也是复核所得 SHA-256） | bytes | post_id | 源正文字符数 |
|---|---:|---:|---:|
| `0b3cb7de28c4382cb39cd33af4310e7f4ad95c3b88b720215cc5ba623980ad1d.body` | 167321 | 1740355068 | 205 |
| `16bf5012078a4e314f51d812c89110ab42bc160569c115ef824d9f0e6129107f.body` | 162883 | 1740355704 | 202 |
| `5ed65c66aa079477d618379ba02fbb51adfd4a68ab93e1a60bb5d861094843bb.body` | 161395 | 1740355613 | 179 |
| `5ff4471e60ceb16934ed693442abfab988e2b5090483ec2ef8d021f33dfaaee6.body` | 163906 | 1740321941 | 331 |
| `9caa2466a4bad7e00f8fcb1bc2bef676a85e347e89eceb8b383de51ce4bd4518.body` | 158518 | 1740355242 | 201 |

一个正常列表负对照是 `data/raw/eastmoney_guba/0500aef7b343ba922ecf8360c3bc8d5ee3914983b31323bec66683e8243f6d03.body`，317881 bytes，同名 SHA-256；标题是正常隆基绿能股吧页，同时含 `em_capt.js` 和 `article_list`。

## 现存身份核实 fixture 与缺失的历史真实壳

现存 `tests/fixtures/eastmoney_guba/verification_page.html` 是 README 明示的合成裁剪 fixture，不是完整真实 HTTP body。431 bytes，SHA-256：

`a7bcecc4cf9752d656c4d03166ead733bbba5a3b00d0e5968c7b3be6845db78b`

它有 `<title>身份核实</title>`、`fd_guba_validate` 资源路径、`em_capt.js` 和 `validate.js`，无 `article_list` / `post_article`，当前 `is_access_block_page=True`。适合固定“标题 + 来源特有 marker”的壳正对照。

08-11 的真实 HTTP-200 壳由 `runs/backfill-live-01/inspection.md:51` 记录：2834 bytes，SHA-256 `d9bc3154679106ea3fe92b69042d743009a13a75813d660405fc2868a6174f5a`；README 记录 direct curl 复现同壳。其原目录 `data/live-backfill-eastmoney-601012/` 在本次审计时已经不存在，未能重新读取真实原始 body。它只能列为“历史文档记录”，不能说本次重放了这份真壳，不能以 431-byte 合成 fixture 替代它而不标注。

## F1/F2/F3 的离线复核

`scripts/ops/referer_probe_selftest.py` 定义忠实的正常模板负对照（带 `em_capt.js`）和两种带挑战覆盖层的正对照。脚本本身 20766 bytes，SHA-256：

`73ab120a0db4a42e7a5257d4a7eba4935f31eea638a24e6098c5624508203f28`

现存 `runtime/referer-probe-selftest.json` 为 3035 bytes，SHA-256：

`914e5d0603569934f581a24eccbfe9a0e046fb82ba444e13b3a55c7a5c91d8a0`

本次从脚本 AST 提取字符串构造等价 HTML，不执行脚本，结果如下。derived hash 是本次构造的 UTF-8 HTML hash，不是历史浏览器实际抓取 body 的 hash。

| 对照 | 原始壳 detector | 严格详情解析 | 对已有 DOM 记录的当前重放 | derived bytes / SHA-256 |
|---|---|---|---|---|
| F1 正常模板，无 overlay | False | PASS，21 字符正文 | 无挑战理由；match=True | 1366 / `d40c6a3df927c0a3c334c38bccef66bb7d8c49653d3dc26e8defb8c06ebe0080` |
| F2 captcha 类名 + 滑块文字 | False | PASS，21 字符正文 | `visible_overlay:[class*=captcha]`、asset 佐证、滑块/拼图；match=True | 1566 / `682702ec29898e95daed33fda6d0904b2669b268047b33cd1cf43351307fe5f9` |
| F3 无关类名，仅滑块文字 | False | PASS，21 字符正文 | `visible_text:滑块`、`visible_text:拼图`；match=True | 1551 / `caf48974f739c7931ab336bf0e17f53797ab6772721dec6c3558011d296df4c7` |

F2/F3 的 `content_ok=True` 不是 clean 判定；它们正是检测“真实可解析内容和挑战并存”的回归对照。已有记录从本地 fixture 的渲染产生，本次未重新执行渲染。

当前离线判定代码快照：`src/myresearcher_collector/sources/eastmoney_guba/parser.py` SHA-256 `1c6b4d0d03c13c8b856c61f42ca0a391e4b6c25677dad1cce55872c2d2fe64ae`；`challenge_dom.py` SHA-256 `fef91f0cc737890d5a4aaebc55bce64b233db99dbcc21e0ab8cf1129a3583001`。本次只读使用，未修改。

## 2026-09-24 的真实挑战，按事件和表面区分

直接核对 `runtime/logs/eastmoney-detail-enrichment.jsonl`，排除 `source_item_id="1"` 的 fixture 污染。不能用整天 event 数充当某次请求分母：当天非 fixture 57 行 = 54 success + 2 access_block + 1 manual_verification_resumed。

- D-024：2026-09-24T04:58:12.870Z（北京时间 12:58:12.870），第 8454 行，post ID `1777358839`。带 `list_page:title:身份核实`、`list_page:visible_overlay:#emcaptcha` 等前缀，指向 enrich 的列表页前置访问。它不是该 post 真实详情被返回的证据。
- D-025：2026-09-24T05:48:11.041Z（北京时间 13:48:11.041），第 8456 行，post ID `1777161492`。带 `title:身份核实`、`visible_overlay:#emcaptcha`、`dom_asset:emcaptcha` 等，没有 `list_page:` 前缀；该 run 的配置是 `detail_referer=0`，故为直接详情表面的验证记录。
- 同 post 在 05:48:26.078Z（13:48:26.078）第 8457 行出现 `manual_verification_resumed`。这是人工辅助事件，不能计为匿名纯 HTTP 成功。本次不读取 DB；D-025 记录其后取得 768 字符真正文，是历史 DB 检查结论，本轮没有重验正文。
- 精确窗口 05:48:00Z–05:52:30Z 为 49 行 = 47 success + 1 access_block + 1 manual_verification_resumed，与 driver log 的 47/47 filled 一致。事件行不等于 source 请求行。

第 8456 行（不含换行）SHA-256 `216f1fb73f33a568797b28e3563fcca36375c8adc1ba6f0b21eb7d513cea0105`；第 8457 行（不含换行）SHA-256 `88a6697cd67832cec701104ce078405b0c3f8709ec7b3da02ae7fb05f82f9a47`。JSONL 在审计时 10237760 bytes，整体 SHA-256 `094cd747ed9cf1a63f5e2b770ecf8caa505ef7a512d0141e47e946ade06297d1`；它仍可能由其他聊天追加，故用行 hash 定位该历史证据。

`runtime/logs/enrich-driver-standalone-20260924T054759Z.log` 为 3383 bytes，SHA-256 `bdb0f477ddfc40dc94eabbfc30864cd5f4e43d532cf6d9a3e9fe622797f8256d`，包含 `RUN_START ... detail_referer=0`、002463 的 `access_blocks=1`、14 股票 `remaining=0` 和 `ALL_DONE`。

另外直接查看 `runtime/diagnostics/eastmoney-20260924T123054.png`，280066 bytes，SHA-256 `a0e1acdb09a4fb1d950c1e1733408266980c8b693a25b46cf42d7a124fe50e78`：也是正常列表后景上可见滑块的真截图。它的文件时间标签与 D-025 05:48Z 不同，不能填补 D-025 所述“本次详情挑战无截图”的证据缺口，也不能将其对应某个 ledger event 而无关联 metadata。

D-025 同日 correction 还明确：用户期望的列表页→JS 导航实际未开启，助手选择了 `detail_referer=0`。因此不能用这次 success 证明用户原想验证的 JS 导航机制有效，更不能反推 Referer、session/IP 或间隔的因果。

## 历史 curl/curl_cffi 结论需要收窄

除读 CURL-CFFI-AUDIT 外，直接复核外部只读仓库 `/Users/mac/Documents/trae_projects/prompt-engineering` 的现存代码和日志：

- `logs/20260629.log:79–110` 有两段明确启用 `curl_cffi (TLS 指纹模拟 chrome120)` 的运行，均 page1 被旧程序判验证码。
- 同日志 `:112–163` 在 23:01:34 起只有普通初始化标记，随后十页报告 730 帖。它说明“没有启用标记的路径也曾报告列表进展”，不构成由 curl_cffi 造成成功的因果证据。没有保存该次逐请求真实 body，也不能由弱日志重建严格 body 验证。
- `guba_scraper.py:151–157` 只有在股票标准链接 `<3` 且出现指定关键词时才判验证；完整 payload/重复列表会漏报。
- `guba_scraper.py:336–381` 的详情处理只判断 HTTP 200，提取显示时间/计数，无 `post_article` 身份校验、无真正文校验，也无身份核实检查；所以其“详情返回字典”并不证明取得正文。
- 当前 SOURCE_SPEC 是 `f.html` 最新发帖顺序，旧程序默认路线和显示表格时间属于另一语义。旧记录不能直接充当本次 `f.html` 顺序覆盖证明。

外部文件当前 hash：`guba_scraper.py`，20769 bytes，SHA-256 `4b72d6af880a689a949b53ff0b802bc98bb4e9af1dd40a862e4f71031f7239a7`；`logs/20260629.log`，204814 bytes，SHA-256 `add351a78d22e4225e506166195fbbed52f5aafbfba338e54ca3ffb1e6d5c8d4`。

因此，历史 evidence 足以否定“HTTP 200 就是详情正文”“TLS impersonation 是成功必要条件”“旧 failed 足以决定今日普通 HTTP 必败”。它不能证明某客户端永远不能或永远能够无人值守访问。本轮 scope 禁止 impersonation，无须再测试该禁止配置。

## 建议给本次 HTTP probe 的确定性边界

建议每个响应独立保存以下字段；名字可按诊断脚本 schema 调整，意义不可合并：

| 维度 | 判据 | 不能推出的结论 |
|---|---|---|
| `http_ok` | 实际状态码、最终 HTTPS 来源 URL | 不能推出真实数据 |
| `raw_challenge_evidence` | 已知验证壳标题与特有 marker；或明确的原始挑战标记/文案位置 | 单 asset、普通正文词、隐藏容器不证明可见挑战 |
| `challenge_visibility` | 纯 HTTP 固定 `not_observed_without_js_rendering` | 不能填 False 或 no-captcha |
| `payload_ok` | 真对象且字段/身份/时间/正文严格成立 | 不能覆盖已有挑战证据 |
| `pagination_progress` | 完整有序 ID 集、页号、publish-time 范围与重叠 | 新 URL/HTTP 200 不保证新页 |
| `detail_identity_ok` | 请求 URL post_id 与响应 post_id 相等，标准 post_type=0，源正文存在 | 标题、计数、动态 error 页不是正文 |

列表还需区分 `rc=1` 下的真空 `re=[]` 和缺 payload/壳/错误码；后者都是失败。详情删除/不可用应以实际最终 URL `error?type=2` 与来源 error shell 另列，不等于验证码，也不能把它计入正常 detail success。详情优先从本次真实列表的类型 0 取样，不从历史死链代替当前详情检验。

强挑战证据优先于 payload success：保留仍有效的 payload 事实，但该路径不计“无已发现挑战的成功”，遇强挑战即停止重复。原始字节未见挑战且 payload 成立，可以报告“本次普通 HTTP 收到真实数据，原始响应未发现明确挑战证据”；应同时保留动态挑战不可观察限制。

后续 root 的新 probe 若沿用代码中的 `challenge_reasons`，需意识到它消费的是浏览器 DOM probe 记录。不能把原始 HTML 的 token 全局扫描结果伪装成 `visible_overlays` / `innerText` 输入。09-28 的模块和测试还记录了普通正文单词“拼图”误报、广告覆盖 captcha iframe 的误报；这进一步要求 HTTP 检查保存原始命中位置而不宣称元素可见。
