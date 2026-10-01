# Handoff — 股吧无浏览器 HTTP 独立研究

## Execution Identity

client: Codex desktop\
model: 基于 GPT-6；本支线不宣称未提供的具体部署 ID\
role: Source Researcher

## Current State

phase: 独立来源研究，非生产实现阶段\
round: `guba-http-research-20260930`\
status: `RESEARCH_BLOCKED_AT_REQUEST_78; BACKFILL_INCOMPLETE; LONG_TERM_UNATTENDED_NOT_VALIDATED`

当前扩展诊断已在第 78 次请求因真实身份核实壳停止，77 次有效列表、扩展正文 0，回补未完成。早期 11 列表/9 次详情仅证明有限样本可取性；不能把旧预检 PASS 当作当前批量或长期可用性通过。后续每分钟一次闲时长任务及手机主控已独立实现于 `apps/http_backfill/`，最新实现/测试/部署状态以应用 SPEC/HANDOFF 为准。

完整实验包已移到仓库外并逐文件验 SHA-256；本交接下方的脚本、逐请求元数据、JSONL/raw 路径均为**历史归档包路径**，见 [EVIDENCE-ARCHIVE.md](EVIDENCE-ARCHIVE.md)。Git checkout 只携带结论与小型摘要。以下 Completed 与 Exact Offline Commands 是旧预检历史，不是新的在线执行指令。

## Read Before Continuing

按 `AGENTS.md` 要求的顺序读取产品、协作、数据、实现契约，然后本 run `scope.md`、`specs/eastmoney_guba.md`、本文件、[REPORT.zh-CN.md](REPORT.zh-CN.md)。历史误判与原始证据限制见 [history-calibration.md](history-calibration.md)；当前独立复核见 [independent-evidence-audit.md](independent-evidence-audit.md)。

## Completed

- 离线校准 31/31 必需控制 PASS；正常 em_capt 模板、身份核实壳、payload 与静态挑战共存、正文/标题验证词、隐藏模板、错误字段/链接/类型/重复 ID 均覆盖。历史 112 列表和 5 真实详情 replay PASS。
- 24 客户端尝试 = 1 sandbox DNS 环境失败 + 23 到达来源请求；无浏览器、人工验证、代理/身份轮换、TLS/浏览器指纹伪装或生产链执行。
- HTML 列表 **11/11**（curl6、urllib5），详情 **9/9**（curl6、urllib3）；两股票 601012/600519，3页/2页，5组分页均进展；最小间隔3.102150秒。
- curl独立进程、urllib独立进程和一次6请求临时匿名会话通过；CookieJar结束为空。6不同正文，其中315/328/347源字符长正文与标题不同，328字符正文跨4次请求hash一致。
- 2公开脚本下载2/2；准确源码引用的POST列表API本次0/1，返回非帖子security/star JSON，非挑战、不猜参数重试。移动/其他API分支未展开。
- 每请求保存时间、非秘密请求配置、客户端版本、状态、最终URL、耗时、bytes、SHA、当时detector SHA、分类理由及响应实体。所有24 raw hash/bytes与ledger、单次metadata匹配；独立audit PASS。
- 研究数据落地892源列表行观察、403唯一ID（368唯一type0候选），9正文观察/6唯一正文。未写生产DB、未接入DataClean/训练。

## Decisions Made

- 旧HTTP阻断记录不外推为2026-09-30的不可访问结论；当前有限成功也不外推永久免验证码/安全频率。
- HTTP200/JSON可解析不是数据成功，脚本读取不进入帖子数据成功分母。HTML数据20/20；含API候选数据20/21；23/23只是来源HTTP200。
- 将“原始响应中无明确挑战证据”和“动态JS渲染挑战未知”并列；不把模板资源当挑战，不把payload存在当无挑战证明。
- 保留来源原始时间/type0与非0、变动更新时间和重叠ID；不固定当前页必须80行，不以全页最早时间替代type0发布时间边界。
- 当前仅提出普通匿名HTML HTTP transport候选；不改冻结spec/主线生产入口，不使用本轮失败API作为fallback。

## Evidence and Changed Files

原研究阶段的文件清单如下；完整生成文件和诊断脚本现位于仓库外归档，Git 只保留结论/摘要。`scope.md` 已保留后续人类澄清，不再以固定页数验收。历史主要文件：

- `REPORT.zh-CN.md`、`handoff.md`、`status.txt`：结论、限制、接入建议与继续入口。
- `probe.py`：普通curl/urllib请求、共享40尝试上限、3.1秒间隔、逐跳allowlist与挑战stop、结构/身份/body校验。单进程串行设计；禁止多个研究进程并发调用。
- `anonymous_session.py`：临时匿名urllib CookieJar进程，6请求实验；通过 `python3 -B probe.py --anonymous-session` 运行。
- `calibration.py`/`calibration.json`：零网络离线正反控制和历史corpus校准。
- `summarize.py`：零网络raw完整性/最终detector重放、分页/重复性统计和研究数据导出。
- `requests.jsonl`、`request-matrix.tsv`、`live/*.json`、`session/*.json`：完整台账与单次元数据；`expected-*.json`/`session/expected-*.json`为严格本次列表对照，`api-form-601012-p1.json`为源码实际表单。
- `live/*.body`、`session/*.body`：24原始实体（seq1为空环境响应），保留在本地归档。**仓库既有 `*.body` 策略默认忽略Git，不是source fixture；异机复制run必须显式包括body，否则原始回放不完整。raw 不进入 Git；结论和摘要在本次整理中提交。**
- `summary.json`、`pagination.json`、`repeatability.json`、`final-detector-replay.json`：统一分母与派生验证。
- `list-observations.jsonl`、`detail-observations.jsonl`：原始源行与真实正文研究观察，均含 `research_only=true` 和原始响应引用。368列表候选不等于368完整正文。
- `history-calibration.md`、`offline-entry-discovery.md`、`online-script-discovery.md`、`independent-evidence-audit.md`：历史/来源脚本/独立证据复核。

## Exact Offline Commands

在完整本地归档实验包目录（所需历史 corpus 另见报告；不是单独 Git checkout）：

```bash
python3 -B calibration.py
python3 -B summarize.py
```

两命令已exit0。它们不联网，不运行浏览器，不读DB。现场逐请求的时间、程序版本和结果已经保留；低频在线复现命令见REPORT，该 40 次上限及余 16 是旧预检时点记录，不是当前可继续联网的预算；第 78 次真实阻断事实优先。

## Open Questions / Known Limitations

- 为什么当前普通HTTP放行而历史被挡，私有风险信号未知；无因果A/B证据。
- 动态JS挑战不可观察；当前20 HTML无明确原始挑战证据不等于浏览器永无滑块。
- 只6不同完整正文，未完成整页所有type0详情、跨日/长期无人值守或历史尾部覆盖；本文没有可持续频率承诺。
- HTTP2强制选项在guba仍返回1.1，仅两静态JS是2；urllib实际协议记录缺失，应保持unknown。
- API17非帖子JSON原因未知，构造已按源码核查；移动/其他API/requests/httpx未测试。
- 本次没有删除重定向、429/403/5xx、其他环境/网络或并发实验；不得借历史样本填当前成功率。
- 原始body为本地留存，跨机器交接必须显式运输并验hash；既有历史DOM/body只用于校准，不冒充本次HTTP成功。

## Historical Next Role / Next Action

以下为旧预检提出的接入建议，当前实际工作以应用 SPEC/HANDOFF 为准，不能据此重启已阻断的历史诊断。

Source Researcher先提交access amendment，列明当前HTTP证据和上述观测边界；正式批准后交Developer添加普通匿名HTML transport，并保持既有strict parser/RawEvidence/partial语义。

Developer最小可评审实现及Tester下一验收：固定普通公开UA、HTTPS来源allowlist、逐跳元数据、≥3秒串行间隔、有预算、明确挑战stop，type0详情严格URL/ID/作者/bar/title/publish/content一致，分页有序ID与新ID进展；先隔离验收一整页所有type0详情，再两股票三页完整窗口，之后跨日有预算无人值守。遇挑战停止/partial，不推进成功checkpoint，不用API/浏览器状态自动补救。生产接入必须先满足冻结spec的修订流程，不能由本研究handoff自动授权。

## Do Not

- 不从研究成功直接启动生产采集、写DB、更新checkpoint或接训练。
- 不承诺永久免验证码，不以HTTP200/模板资源/可解析payload替代严格证据。
- 不解验证码/构造挑战参数/轮换代理身份/TLS或浏览器指纹伪装，不借浏览器预热或人工验证算本目标成功。
- 不修改冻结SOURCE_SPEC/契约和生产src，除非另有明确阶段范围。
- 不丢失失败分母、同页/跨页区别、源原始时间、非0类型或原始响应追溯。
