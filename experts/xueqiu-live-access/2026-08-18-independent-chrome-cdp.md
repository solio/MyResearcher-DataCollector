# Xueqiu 独立普通 Chrome + 固定 CDP 实验

日期：2026-08-18  
标的：隆基绿能 `SH601012`  
入口：<https://xueqiu.com/S/SH601012>

> 后续状态更新：下面的 `NOT_DONE` 是本实验完成时的历史状态。同日后续已将该链路
> 接入 production Collector；离线回归已通过，production live CLI 的独立重新验收
> 尚待执行。维护交接见
> [`runs/xueqiu-dom-integration/handoff.md`](../../runs/xueqiu-dom-integration/handoff.md)。

## 1. 结论

```text
EXTERNAL_NORMAL_CHROME = PASS
DEDICATED_PROFILE = PASS
FIXED_LOOPBACK_CDP = PASS
PLAYWRIGHT_LAUNCH_USED = NO
PLAYWRIGHT_LAUNCH_PERSISTENT_CONTEXT_USED = NO
ENTRY_PAGE = PASS (9 posts, stable for 20 seconds)
PAGE_2 = PASS (10 posts, zero overlap with page 1)
DETAIL = PASS (status 405329188, created_at present)
OWNED_CHROME_PID_FRONTMOST = NO
USER_CHROME_BASELINE_FINAL_TAB_MATCH = YES
TRANSIENT_MD5_REDIRECT = YES (entry once, detail once; self-recovered)
GRAPHICAL_CAPTCHA = NO
PRODUCTION_COLLECTOR_INTEGRATION = NOT_DONE_AT_EXPERIMENT_TIME
```

这条技术路线真实可行，并且不依赖 Codex 浏览器。实验脚本由普通 Python 进程启动
本机官方 Chrome，可从 Terminal、launchd 或其它宿主运行。

但结果应准确描述为 `PASS_WITH_TRANSIENT_MD5_REDIRECTS`，不能包装成无条件稳定：
入口和详情各出现一次带 `md5__1038` 的短暂导航，随后自动回到原 URL 并出现正常
公开 DOM；本轮没有无限跳转、图形验证码或人工操作。它证明该链路可以运行，不证明
雪球对 30 股 × 100 天的长任务永远不触发风控。

## 2. 它是否还是 Apple Events 方案

不是。控制面完全不同：

```text
普通 Python 进程
  -> subprocess.Popen 官方 Google Chrome 可执行文件
  -> .runtime/browser-profiles/xueqiu-dedicated
  -> 127.0.0.1:9227 固定专属 CDP 端口
  -> Playwright connect_over_cdp（只附着，不 launch）
  -> 独立 Chrome 的 context / background target
```

Apple Events 仅用于只读遥测：读取 macOS 当前前台进程及 Chrome 活动标签身份；脚本
里没有用 Apple Events 打开 URL、切换窗口、点击、导航或执行页面 JavaScript。

二者差异不是“换一个 API 名字”：

- Apple Events 的 `tell application "Google Chrome"` 是按应用 bundle 寻址，两个
  Chrome 主进程同时存在时可能指向其中任一个窗口；生产现有实现因此不能保证只控制
  Collector 的窗口。
- 固定 CDP 端口只属于本轮启动的 dedicated Chrome。CDP 客户端看不到用户 Chrome
  的 context/page，也无法误关用户 Chrome 的标签。
- AppleScript 遥测即使失败，页面控制仍可继续；CDP 端口或 owned process 不存在时，
  页面控制会 fail closed。

## 3. 找到“不抢焦点”的关键

第一次有效的 CDP 尝试使用 `context.new_page()`，页面访问成功，但创建首个窗口时
Chrome 成为前台。这说明“CDP 附着”本身不会自动保证不抢焦点，`new_page()` 的窗口
创建语义仍会影响桌面。

最终成功变体使用：

1. 启动 Chrome 时带 `--no-startup-window`；
2. 通过浏览器级 CDP 命令执行
   `Target.createTarget({url: marker, background: true})`；
3. 断开并重新 `connect_over_cdp`，让 Playwright 把外部创建的 target 识别为现有
   `Page`；
4. 详情页重复同样的后台 target + reconnect 方法；
5. 清理时不逐个激活或关闭窗口，而是先断开 CDP 客户端，再终止本轮记录的精确
   Chrome PID。

直接在已经附着的 Playwright 连接里调用 `Target.createTarget(background=true)`，
Chromium 会正确创建后台 target，但 Playwright 不会把它即时暴露成 `Page`。重新附着
是为了对象发现，不改变浏览器指纹或页面状态。

## 4. 实际执行过程

实验使用：

- Chrome：`151.0.7922.138`；
- binary：`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`；
- profile：`.runtime/browser-profiles/xueqiu-dedicated`；
- CDP：`http://127.0.0.1:9227`；
- context 数：1；
- `navigator.webdriver=false`；
- UA 为正常 Chrome 151；
- languages：`["zh-CN", "zh"]`；
- plugins：5；
- `window.chrome=true`。

时间线：

1. 入口页约 2.7 秒出现 9 个 `article.timeline__item`；
2. 连续观察 20 秒，共 10 个样本，每次仍为 9 帖，当前页面没有 challenge token；
3. 随机等待 `6.665s`；
4. 点击分页 2，约 0.5 秒后得到 10 个非空帖子 ID，与第一页重叠数为 0；
5. 随机等待 `9.538s`；
6. 后台打开 `405329188` 详情，解析出的 ID 与目标一致，`created_at` 存在；
7. 详情完成后主列表仍为第 2 页且 10 个 ID 完全保持；
8. owned Chrome PID `75613` 正常退出，9227 端口关闭。

本机存在 `ALL_PROXY=socks5h://...` 类型环境时，Playwright 的本地 CDP 客户端曾把
`127.0.0.1` 错送给代理并报“不支持 socks5h”。脚本只对新启动的 Playwright driver
移除代理变量并设置 `NO_PROXY=127.0.0.1,localhost`；Chrome 进程已经在此之前启动，
仍继承原工作站网络环境。这个修复只作用于 loopback 控制连接，不涉及雪球访问规避。

## 5. URL 与导航证据

实际业务 URL：

- 列表入口及 SPA 分页：<https://xueqiu.com/S/SH601012>
- 详情：<https://xueqiu.com/9126453883/405329188>

主 frame 的 redacted 导航链：

```text
https://xueqiu.com/S/SH601012
https://xueqiu.com/S/SH601012?md5__1038=<redacted>
https://xueqiu.com/S/SH601012
https://xueqiu.com/9126453883/405329188
https://xueqiu.com/9126453883/405329188?md5__1038=<redacted>
https://xueqiu.com/9126453883/405329188
```

query value 有意不保存。这里没有修改、生成或重放 `md5__1038`；Chrome 正常运行站点
页面后自行返回原 URL。若后续生产运行出现持续 challenge、空 DOM 或循环次数超过
有界阈值，应明确失败并停止，而不是尝试解验证码。

## 6. 抓到的帖子列表

第一页 9 个 ID：

```text
405401819  hzsunwu             58分钟前·来自雪球
405394855  蝴蝶日记_小蝶       修改于3小时前·来自iPhone
405375333  棋行者               昨天22:03·来自雪球
405373810  PCHDriving           昨天21:50·来自Android
405371704  炼金术士的朝圣       昨天21:34·来自Android
405363152  我的投资札记         昨天20:16·来自Android
405360151  老奈                 昨天19:43·来自iPhone
405346130  珠穆朗玛峰一样高     昨天17:33·来自雪球
405334863  海上骑鲸客灬         昨天16:13·来自雪球
```

第二页 10 个 ID：

```text
405329188  远离夕阳价值陷阱     昨天15:35·来自Android
405327793  酱菜甜菜             昨天15:28·来自雪球
405317396  东亚病股             昨天14:37·来自Android
405276541  Olida                昨天10:47·来自iPhone
405273701  Charley-001          昨天10:36·来自iPhone
405269850  金不换又大了一岁     昨天10:22·来自HarmonyOS
405257742  蝴蝶日记_小蝶       昨天09:44·来自iPhone
405255915  轻身                 昨天09:40·来自Android
405238137  爱英语娃             08-16 23:55·来自iPhone
405235322  奥马哈农夫           修改于昨天08:26·来自Android
```

包含每条帖子 URL、标题、作者和观察时间的结构化证据见
`evidence/independent-chrome-cdp-2026-08-18.json`。

## 7. 焦点与用户 Chrome 证据

采样脚本不含 `activate`、`set active tab`、导航或 `execute javascript`。不保存用户
无关标签的原始 URL/title，只保存 SHA-256 短摘要。

两次互补证据：

- 后台 target 专项预检以 Docker Desktop 为前台；12 秒内 owned Chrome 从未成为
  frontmost，用户 Chrome 的运行前/清理后标签身份一致。
- 最终完整验收开始时用户 Chrome 已在前台；42 个样本里前台进程 PID 始终不是本轮
  owned PID `75613`，运行前/清理后用户 Chrome 的 window/tab/index 及 URL/title
  hash 完全一致。

局限：Apple Events 按 Chrome bundle 寻址，在两个 Chrome 进程共存期间，返回的
中间 window/tab identity 会落到 dedicated Chrome，因此不能用中间 AppleScript
数据逐毫秒证明用户 Chrome 标签状态；可靠边界是 macOS frontmost PID 加上运行前后
用户标签 identity 对照。现有证据支持“不抢用户标签/焦点”，但应在生产集成测试中
继续保留同样 telemetry。

## 8. 可重现实验

前提：macOS、本机官方 Google Chrome、Python 环境已安装项目的 browser extra；
dedicated profile 未被其它进程占用，9227 未被占用。

```bash
cd /Users/mac/Documents/trae_projects/MyResearcher/MyResearcher-DataCollector
python -m pip install -e '.[browser]'
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 \
  python experts/xueqiu-live-access/independent_chrome_cdp_probe.py \
  --port 9227 \
  --observe-seconds 20
```

脚本会：

- 检查固定端口和 profile ownership；
- 只启动精确 binary/profile/port；
- 入口稳定观察 20–30 秒；
- 3–10 秒随机等待后翻第 2 页；
- 再随机等待 3–10 秒后访问一个详情；
- 输出完整 JSON；
- 失败或成功后均终止 owned PID 并确认端口关闭。

程序步骤可以重复执行；由于雪球服务端风控是外部状态，不能诚实保证每次数据结果
必然 PASS。脚本的可重复保证是：成功时验证 ID、页码、详情和焦点；遇到 challenge、
空页、重复页、端口/profile 冲突或详情 ID 不一致时 fail closed，不伪造成功。

屏幕切换本身不影响 CDP 控制；macOS 进入系统睡眠会暂停进程与网络，屏保/锁屏下的
后台限速行为本轮没有验收。因此长回填应显式阻止系统睡眠，不能把本实验外推为睡眠
状态下也可靠。

## 9. 本轮没有做的事情

- 没有修改生产 `create_xueqiu_dom_transport()` 默认路径；
- 没有跑 3/7 天 backfill，更没有跑 30 股 × 100 天；
- 没有读 Cookie、localStorage、sessionStorage、profile 文件或凭据；
- 没有修改 UA、webdriver、TLS、canvas 或其它指纹；
- 没有处理、点击或绕过 CAPTCHA；
- 没有把一次成功写成 unattended production guarantee。

下一步若决定落生产，应把实验 runtime 做成新的明确 acquisition mode，并用单元测试
锁定：固定 loopback port、profile ownership、禁止 `launch*`、background target、
只终止 owned PID、challenge/repeated-ID fail-closed，以及用户 Chrome 前后 identity
不变。该生产改动不在本实验授权范围内。
