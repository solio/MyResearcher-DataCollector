# Xueqiu 生产访问重新定位与原生 Chrome 方案

日期：2026-08-17  
样本：隆基绿能 `SH601012`  
固定入口：<https://xueqiu.com/S/SH601012>

本文记录旧 Expert 结果审计、GOOD/BAD 浏览器环境对照、根因边界、代码改动、
真实 smoke 和仍未完成的验收项。它不把 Codex 浏览器成功解释成 Collector 成功。

## 1. 当前结论

```text
BASELINE_TESTS = PASS
PREVIOUS_EXPERT_RESULT = PARTIALLY_VERIFIED
DISCOVERY_PASS = YES
PLAYWRIGHT_MANAGED_CHROME_COMPATIBLE = NO
EXISTING_USER_CHROME_PAGE1_PAGE2 = PASS
PRODUCTION_BASIC_SMOKE = INCOMPLETE
3_DAY_BACKFILL = NOT_RUN
CURRENT_FINAL_STATUS = DISCOVERY_PASS_PRODUCTION_ENVIRONMENT_NOT_REPRODUCIBLE
```

最重要的因果对照是：同一台 Mac、同一网络、同一个 Chrome 151、同一个
`xueqiu-dedicated` profile 和同一个 URL，普通 Chrome 可以正常显示帖子；关闭后
改由项目的 Playwright `launch_persistent_context` 复用该 profile，页面立即在原 URL
与包含 `md5__1038` 的验证 URL 之间循环，20 秒内始终 `posts=0`。

因此可以确认失败发生在 Playwright 托管浏览器环境与站点验证层之间，不是 DOM
parser、pagination 或 SQLite persistence 先导致的。可观察到 Playwright 环境的
`navigator.webdriver=true`，正常 Chrome 为 `false`；但 Playwright launch flags 和
browser context 也同时变化，不能把唯一触发项武断归因为 `webdriver`。本轮遵守边界，
没有通过修改 webdriver、UA、TLS、canvas 或其它指纹值来验证规避方案。

## 2. 基线与仓库保护

开始前分支为 `main`，HEAD 为：

```text
7465498 fix(xueqiu): isolate modified-post detail navigation
```

开始前已经存在、且本轮没有覆盖或清理的用户改动包括：

- `config/targets.short-term.json`
- `.vscode/`
- `src/.../eastmoney_guba/browser_runtime.py`
- `src/.../xueqiu/dom_transport.py`
- `tests/unit/test_xueqiu_dom.py`
- `tests/unit/test_browser_runtime.py`

调查前基线：

```text
PYTHONDONTWRITEBYTECODE=1 python -m compileall -q src
# exit 0

python -m pytest -q <targeted Xueqiu/runtime tests>
# 71 passed

PYTHONDONTWRITEBYTECODE=1 python -m pytest -q
# 332 passed, 1 xfailed
```

实现原生 Chrome 路径后的当前结果：

```text
PYTHONDONTWRITEBYTECODE=1 python -m compileall -q src
# exit 0

python -m pytest -q <updated targeted tests>
# 68 passed

PYTHONDONTWRITEBYTECODE=1 python -m pytest -q
# 336 passed, 1 xfailed
```

批准的单个 xfail 没有变化。

## 3. 旧 Expert 结果审计

审计对象：`f5f155f73d7d37927dc296d4765839b858adf0a`

### 可验证部分

- `posts-observed-23-pages.json` 有 225 行和 225 个唯一 `status_id`；
- page 1..23 的行数与 manifest 一致，页间没有重复 ID；
- 3 天和 7 天派生文件能从 23 页观察结果复核；
- detail boundary JSON 中的 `created_at`/`edited_at` 与记录一致。

### 不能从旧仓库证据证明的部分

- 旧实验没有保存 browser product/version、profile 类型或完整启动方法；
- 没有保存 `framenavigated` 链，因此不能证明运行期间从未进入 challenge；
- 没有证明一个脱离 Codex 的项目进程可以复现同样环境；
- 旧 README 已明确写出 `CODEX_INDEPENDENT_PROCESS = NOT_TESTED`。

结合本次保留的工具调用记录，可确认旧实验实际使用：

```text
PREVIOUS_EXPERT_BROWSER = Codex built-in in-app browser
PREVIOUS_PROFILE_MODE = UNKNOWN
PREVIOUS_AUTOMATION = Codex browser tool / its Playwright-compatible DOM API
PREVIOUS_EXPERT_RESULT = PARTIALLY_VERIFIED
```

它不是本机普通 Chrome、不是项目 managed Chrome，也不是 Apple Events。旧数据可信，
但旧环境不可直接作为生产交付。

## 4. Controlled environment matrix

所有环境只访问同一 URL 一次并观察约 20–30 秒；没有翻几十页，没有登录，没有自动
处理 CAPTCHA。URL query value 全部 redacted。

### Environment A — Codex built-in browser

```text
browser/runtime: Codex In-app Browser
launch method: Codex browser tool
profile mode: UNKNOWN
goto count: 1
navigation chain: https://xueqiu.com/S/SH601012
md5 loop: NO
challenge: NO
article.timeline__item: 10
POST_DOM_LOADED: true
result: PASS (DISCOVERY ONLY)
```

25 秒采样中 URL 始终不变，页面标题从静态标题更新为实时行情标题。该只读页面执行
环境不暴露 `navigator`，因此 UA/webdriver/languages/plugins 记为 `UNAVAILABLE`，
而不是猜测。

### Environment B — 用户现有正常 Chrome

```text
browser/runtime: Google Chrome 150.0.0.0 (UA observation)
launch method: existing Chrome + Apple Events, temporary window
profile mode: existing user profile
goto count: 1
navigation chain: https://xueqiu.com/S/SH601012
md5 loop: NO
challenge: NO
article.timeline__item: 10
POST_DOM_LOADED: true
navigator.webdriver: false
languages: [en-US, en, ja]
plugins.length: 5
window.chrome: true
result: PASS
```

临时窗口在实验后关闭。没有读取 profile 文件、Cookie value、Cookie name 或 storage。

### Environment C — 项目 managed Chrome + fresh profile

```text
browser/runtime: official Google Chrome launched by Playwright
launch method: playwright.chromium.launch_persistent_context(channel=chrome)
profile mode: fresh per run
goto count: 1
md5 loop: YES
verification/CAPTCHA: YES (operator visual observation)
article.timeline__item: 0
POST_DOM_LOADED: false
result: FAIL
```

两次受限 fresh-profile 探针都卡在 `open_stock`，用户看到无限跳转和验证码。本轮随后
停止重复 fresh-profile 请求。

### Environment D — 普通 Chrome 初始化后复用 dedicated profile

第一阶段以普通 Chrome 151 启动独立 `xueqiu-dedicated` profile，用户确认页面正常；
随后关闭普通 Chrome，项目 Playwright 使用同一目录复用。

```text
browser/runtime: Google Chrome 151.0.0.0
launch method: Playwright persistent context
profile mode: explicit dedicated reuse
goto count: 1
representative navigation loop:
  /S/SH601012
  /S/SH601012?md5__1038=<redacted>
  /S/SH601012
  /S/SH601012?alichlgref=<redacted>&md5__1038=<redacted>
  ...same pair repeated throughout the timeout...
challenge text: [md5__1038, 访问验证]
article.timeline__item: 0
POST_DOM_LOADED: false
navigator.webdriver: true
languages: [zh-CN, zh]
plugins.length: 5
window.chrome: true
result: FAIL
```

这个 A/B 排除了“只需要先人工养好 dedicated profile”的假设。

### 未采集的浏览器状态

Cookie names、Cookie values、localStorage keys、sessionStorage 和 profile/session 文件均
未采集。浏览器控制安全边界禁止读取这些数据；同时，D 环境已经用完全相同的 profile
完成更强的对照，不需要靠导出会话秘密证明差异。

## 5. GOOD vs BAD：只有证据支持的差异

| 事实 | GOOD 普通 Chrome | BAD Playwright Chrome |
|---|---|---|
| 同一机器/网络 | 是 | 是 |
| Chrome 151 + 同一 dedicated profile | 正常（用户确认） | challenge loop |
| 启动控制 | 普通 Chrome | Playwright persistent context |
| `navigator.webdriver` | 正常 Chrome 对照为 `false` | `true` |
| 帖子 DOM | 出现 | 0 |
| `md5__1038` 循环 | 无 | 有 |

确认结论：Playwright 托管 launch/context 与当前 Xueqiu 验证不兼容。  
合理但未单独确认：`webdriver=true` 和/或 Playwright launch/context 特征触发验证。  
仍未知：哪一个单独 flag/property 是唯一触发条件。本轮不会用 spoofing 去回答它。

## 6. 最小生产方案

不继续修补 Playwright 指纹。新的最小路径是：

```text
MyResearcher-DataCollector CLI
    -> macOS osascript / Apple Events
    -> 用户已运行的正常 Google Chrome
    -> Collector 自己创建并记录 tab id
    -> 公开 Xueqiu DOM
    -> 现有 parser / pagination / detail / posts persistence
```

边界：

- 不依赖 Codex；
- 不复制用户 profile；
- 不读取或导出 Cookie/storage；
- 不修改 UA、webdriver 或其它指纹；
- 不登录、不解 CAPTCHA；
- 只关闭 Collector 自己记录的标签；
- 需要 macOS Chrome 已开启“允许 Apple 事件执行 JavaScript”；
- 这是 headed/operator environment，`unattended_production_ready=false`。

实现文件：

- `src/myresearcher_collector/sources/xueqiu/existing_chrome.py`
- `src/myresearcher_collector/sources/xueqiu/dom_transport.py`
- `src/myresearcher_collector/cli/main.py`
- `tests/unit/test_xueqiu_existing_chrome.py`
- `tests/unit/test_backfill_cli.py`

CLI 的 Xueqiu backfill 默认从失败的 `managed-chromium` 改为
`existing-chrome`；仍可显式选择 managed 模式用于诊断，但当前证据判定其不可用。

## 7. 详情页独立 bug

真实原生 Chrome smoke 中，page1/page2 已成功，但详情等待
`window.SNOWMAN_STATUS` 超时。进一步只读检查发现：

```text
detail URL: https://xueqiu.com/1717901414/405215718
title: 正常帖子标题
readyState: complete
challenge: []
window.SNOWMAN_STATUS: undefined
script text: window.SNOWMAN_STATUS = {"id":405215718,...,"created_at":...}
```

所以它不是 WAF，而是实现错误：当前页面保留公开 JSON script，但初始化后不保证全局
变量继续存在。修复后的原生 Chrome adapter 优先读 global；global 不存在时只截取固定
marker 后的 JSON 文本，再用 Python `json.loads` 解析。它不 `eval` 页面脚本。

## 8. Production basic smoke 当前结果

真实项目进程（非 Codex browser）已取得：

```text
stock page loaded: PASS
page1 parsed: PASS, 10 unique IDs
page2 pagination: PASS, 10 unique IDs
page1/page2 overlap: 0
modified post encountered: YES
modified detail before fix: FAIL (root cause found)
modified detail after fix: NOT_EXECUTED (desktop approval channel rejected)
persistence: NOT_RUN
basic smoke result: INCOMPLETE
```

本次 page IDs 已保存于 `evidence/production-reinvestigation.json`。最后一次 fix 后 live
smoke 因桌面审批通道断开而未执行；不能把离线测试替代成 live PASS。

## 9. 3-day backfill

没有执行。按照任务门槛，只有修复后的 detail live smoke 和 page-level persistence 都
PASS，才允许运行 `601012 / 3 days / single browser / random 3–10s`。

## 10. Repository contract 待协调项

仓库中存在本轮之前已经形成的合同冲突：`specs/xueqiu.md` 冻结的是 browser-owned
discussion JSON route，并将 historical full backfill 列为 out of scope；较新的
`runs/xueqiu-dom-integration/scope.md` 又明确授权 Developer 产品化 DOM backfill。
本轮用户任务继续要求验证当前 DOM/backfill 路径，因此这里只做最小 runtime 修复，
没有重写 SOURCE_SPEC，也没有宣称该冲突已经消失。正式合并为长期 production contract
前，应由 Reviewer/Source Researcher 统一 SOURCE_SPEC 与当前 DOM scope。

## 11. 可复现命令

环境矩阵中的 managed Chrome（预期当前失败）：

```bash
PYTHONPATH=src python experts/xueqiu-live-access/managed_environment_probe.py \
  --observe-seconds 20
```

项目原生 Chrome 基础 smoke：

```bash
PYTHONPATH=src python experts/xueqiu-live-access/existing_chrome_smoke.py
```

离线回归：

```bash
PYTHONDONTWRITEBYTECODE=1 python -m compileall -q src
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/unit/test_xueqiu_existing_chrome.py \
  tests/unit/test_xueqiu_dom.py \
  tests/unit/test_browser_runtime.py \
  tests/unit/test_backfill_cli.py \
  tests/integration/test_xueqiu_dom_backfill.py
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q
```

修复后 smoke PASS 才可运行的 3 天命令：

```bash
PYTHONPATH=src python -m myresearcher_collector.cli.main backfill \
  --source xueqiu \
  --stock 601012 \
  --days 3 \
  --data-dir runtime/xueqiu-3day-smoke \
  --acquisition-mode existing-chrome \
  --min-interval 3 \
  --max-interval 10 \
  --confirm-live
```

不要在修复后 smoke 未通过时执行最后一条命令。
