# 东方财富 Live Access 重试报告（2026-08-12）

> 后续更新：同日已经找到并实测通过一个脱离 Codex 的有界路径——独立 Python
> 进程通过 macOS Apple Events 控制用户已经运行的日常 Google Chrome 会话，按
> 随机 3–10 秒完成 page1、page2+详情、page3+详情，全部严格解析通过且未出现
> 验证。见 [2026-08-12-existing-chrome-success.md](2026-08-12-existing-chrome-success.md)。
> 本报告下文的 `UNATTENDED_LIVE_ACCESS = BLOCKED` 是该成功之前针对新 profile /
> 独立 Playwright host 的阶段性结论；当前修订为“有界独立路径 PASS，批量 Backfill
> 尚未验证”。

## 结论

页面 URL、`article_list` / `post_article` 数据语义和严格解析器均已再次验证；
但没有找到可以持续、无人值守运行的东方财富股吧访问方式。

决定性证据不是“全新浏览器打不开”，而是：一个由操作者人工完成图形验证、
并保持同一 Chrome profile/context/page 的长生命周期会话，在真实详情序列中仍会
间歇性再次返回 `身份核实`。因此当前状态必须是：

```text
SOURCE_SEMANTICS = PASS
BROWSER_HOST_INTEGRATION = IMPLEMENTED
OPERATOR_ASSISTED_EXPERIMENT = PARTIAL
UNATTENDED_LIVE_ACCESS = BLOCKED
NORMAL_COLLECTION = NOT_PASSED
601012_7_DAY_BACKFILL = NOT_ATTEMPTED
```

没有破解、读取或提交验证码；没有查看或导出 Cookie/Storage；没有代理、账号池、
UA 轮换、随机翻页或 TLS 指纹模拟。图形验证只由用户在可见浏览器中手工完成。

## 连续生命周期测试矩阵

| 环境/上下文 | 顺序 | 结果 | 能证明什么 |
| --- | --- | --- | --- |
| 已建立的 Codex in-app Browser 同一上下文 | page1 → detail1 → page2 → detail2，导航间隔 ≥3 秒 | PASS；两个列表各 80 行，详情 ID 均一致；后续同上下文复查 page1 仍 PASS | 特定已建立上下文可以阶段性连续访问 |
| 新建 Playwright persistent Chrome profile，第一次进程 | 同上 | PASS | 新 profile 并非永远首屏失败 |
| 同一 persistent profile 关闭后重启 | page1 | `ACCESS_BLOCK` | 保存 profile 不能保证下次可用 |
| 全新 Playwright headless Chrome | page1 | 多次 `ACCESS_BLOCK` | 无 GUI 不是稳定解法 |
| 全新 Playwright headful Chrome | page1 | `ACCESS_BLOCK` | 问题不只由 headless 引起 |
| 外部用户启动 Chrome，Playwright 仅经本地 CDP 连接（headless/headful） | page1 | 均 `ACCESS_BLOCK` | 问题不只由 Playwright `launch()` 引起 |
| 新上下文先访问股吧首页再访问列表 | 首页 200 → page1 | 首页真实、列表 `ACCESS_BLOCK` | 首页预热不足以建立可用列表会话 |
| 长生命周期 headful host，用户手工完成验证 | preflight page1 | READY；80 行，72 个标准帖、8 个范围外行 | 人工验证可以暂时恢复列表访问 |
| 上述同一 host 的真实 normal Collector | page1 → sequential details | 31 个唯一详情正文可严格解析；序列中出现验证壳，用户稍后又看到图形验证码，运行中止 | 人工验证不是一次性门槛；详情序列会再次挑战 |

不能从这些观察反推东方财富的私有风控规则、固定请求阈值或 IP 封禁规则。能确认的
只有响应行为和当前生产可用性结论。

## 操作者协助的真实运行证据

使用的列表 URL：

```text
https://guba.eastmoney.com/list,601012,f.html
```

原始响应目录（本机临时调查证据，不提交 live body）：

```text
/tmp/eastmoney-normal-operator-20260812/raw/eastmoney_guba/
```

按 SHA-256 内容寻址后的唯一 body 统计：

| 类型 | 唯一 body 数 | 严格解析结果 |
| --- | ---: | --- |
| list | 1 | `article_list.rc=1`；72 个 `post_type=0`；8 个范围外类型 |
| detail | 31 | 全部有 `post_article`，ID/URL 可对应，正文可解析 |
| identity verification | 1 | 2834 bytes；标题 `身份核实`；按 `ACCESS_BLOCK` 处理 |

“1 个唯一验证 body”不等于只发生过一次验证请求：RawEvidence 以内容哈希去重，
相同验证壳的多次响应会落到同一个文件。列表位置 13–15 没有形成可解析的详情 body；
随后位置 16–34 又成功。用户又观察到后续图形验证码后，人工中止运行。

这次中止还暴露了一个独立的取消可靠性问题：SQLite run
`2e6cfd76ef274838b1f9009fd67dc27b` 留在 `RUNNING`，而已写入磁盘的 body 没有在
同一事务中登记为 attempt/evidence。它不改变来源访问根因，但该目录不能作为正式
成功结果或继续增量的 checkpoint。

完整页面帖子列表、31 个成功详情 URL 和未完成位置见
[2026-08-12-post-list.md](2026-08-12-post-list.md)。

## 用户可执行的实验命令

### 1. 安装本项目和普通 Playwright 依赖

```bash
cd /Users/mac/Documents/trae_projects/MyResearcher/MyResearcher-DataCollector
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[browser]'
```

`channel=chrome` 使用本机已安装 Chrome，不需要下载 Playwright Chromium。

### 2. 先做完全离线的 plan

```bash
.venv/bin/myresearcher-collector eastmoney-guba-live-smoke 601012 \
  --data-dir /tmp/eastmoney-live-plan \
  --max-pages 1 \
  --plan-only
```

输出会明确包含：

```json
{
  "source_access": "BROWSER_HOST_EXPERIMENTAL_AVAILABILITY_BLOCKED",
  "unattended_production_ready": false
}
```

### 3. 启动可见的长生命周期 host

终端 A：

```bash
.venv/bin/myresearcher-collector eastmoney-browser-host \
  --headful \
  --operator-wait-seconds 180 \
  --preflight-stock 601012 \
  --min-interval 3 \
  --socket /tmp/myresearcher-eastmoney-browser.sock \
  --profile-dir .runtime/eastmoney-browser-profile
```

如果出现图形验证，只能由操作者自己完成。host 不读取、识别或提交挑战。只有严格
解析到真实 page1 后才输出 `READY`；新会话直接放行时也仍标记为实验模式。

### 4. 运行一个有界 normal smoke

终端 B（使用新的空目录）：

```bash
.venv/bin/myresearcher-collector eastmoney-guba-live-smoke 601012 \
  --data-dir /tmp/eastmoney-live-smoke-601012 \
  --max-pages 1 \
  --timeout 30 \
  --min-interval 3 \
  --browser-socket /tmp/myresearcher-eastmoney-browser.sock \
  --confirm-live
```

该命令是真实 Collector/RawEvidence/SQLite 路径，不是 GUI 点击脚本。任何验证页都
是 `ACCESS_BLOCK`/失败，不会变成空列表或零计数。由于 2026-08-12 已证明挑战会
重复出现，当前只能把此命令用于有操作者在场的低频诊断，不能部署为无人值守任务。

### 5. Backfill 门禁

只有完整 normal workload 在代表性请求规模下不需要人工验证并得到成功状态后，
才允许执行 Backfill。当前门禁未通过，因此没有运行原计划的 601012 七日 Backfill。
第一个阻断证据是同一人工验证会话的详情序列再次出现验证，而不是历史分页算法。

## 实现要点和样例代码位置

- `src/myresearcher_collector/sources/eastmoney_guba/browser_transport.py`
  - 只允许 `guba.eastmoney.com` / `caifuhao.eastmoney.com` 的 HTTPS URL；
  - 返回 exact main-document body；去除 `Set-Cookie`；
  - 本地 Unix socket 失败时 fail closed，不回退 urllib。
- `src/myresearcher_collector/sources/eastmoney_guba/browser_host.py`
  - 一个 persistent Chrome context/page；
  - owner-only `0600` Unix socket；
  - host 层全局串行且导航间隔不低于 2.5 秒；
  - preflight 必须严格解析 page1 才 `READY`；
  - 可见模式只等待操作者手工验证，不处理挑战；
  - CLI 中止造成客户端断开时忽略 `BrokenPipe`，host 可继续服务。
- `src/myresearcher_collector/cli/main.py`
  - 所有 Eastmoney live 命令显式连接 browser socket；
  - plan 明确 `unattended_production_ready=false`；
  - 不再默认使用已知会进入验证的 urllib transport。

最小直接注入样例仍见 [SUCCESS-PLAYBOOK.md](SUCCESS-PLAYBOOK.md)，无额外 Python
浏览器包的两请求诊断见 [reproduce_headless.py](reproduce_headless.py)。它们能
确定复现 URL、解析和失败分类，不能保证外部网站放行。

## `curl_cffi` 复核摘要

独立 subagent 只读审查了 `/Users/mac/Documents/trae_projects/prompt-engineering`，
没有继续进行网络压力测试。历史日志直接反驳“`curl_cffi` 是成功前提”：两次明确
启用它的 601012 page1 都立即验证码，几分钟后没有启用标记的普通路径反而连续
page1–10 成功。详情实现还会把 HTTP-200 验证页静默当成功且把缺失计数变成 0。

完整文件/行号、官方能力限制和兼容性分析见
[CURL-CFFI-AUDIT.md](CURL-CFFI-AUDIT.md)。可借鉴的只有严格验证页识别、完整
ID 签名重复检测和原始证据记录；这些只能改善失败分类，不能解决稳定访问。

## 确定性复现

```bash
cd /Users/mac/Documents/trae_projects/MyResearcher/MyResearcher-DataCollector
PYTHONPATH=src:. pytest -q
python -m compileall -q \
  src/myresearcher_collector/sources/eastmoney_guba \
  experts/eastmoney-live-access/reproduce_headless.py
git diff --check
```

离线 suite 验证的是 URL 生成、验证壳分类、严格列表/详情解析、列表/详情 ID 一致、
重复分页拒绝、socket 协议、URL 边界、header 脱敏、host preflight cache 和 CLI
fail-closed 行为。外部网站的实时放行不是确定性测试的组成部分。
