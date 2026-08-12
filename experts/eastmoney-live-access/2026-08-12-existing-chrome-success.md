# Existing-user Chrome standalone success（2026-08-12）

## 结果

一个完全独立于 Codex 浏览器的本地 Python 进程，成功控制用户已经运行的日常
Google Chrome 会话，在专用标签页中按随机 3–10 秒间隔完成：

```text
601012 page1
  -> wait 9.993s -> page2
  -> wait 7.404s -> page2 first standard detail
  -> wait 3.014s -> page3
  -> wait 6.913s -> page3 first standard detail
```

最终状态：`PASS`。运行期间没有出现身份核实或图形验证码，也不需要人工操作。

这证明以下路径在当前 macOS 用户会话上可行：

```text
standalone Python process
  -> macOS Apple Events
  -> already-running user's Google Chrome
  -> dedicated normal tab
  -> article_list / post_article validation
```

它没有使用 Codex in-app Browser，没有启动新 Chrome profile，没有读取或复制 Chrome
profile 文件，也没有读取/导出 Cookie、Storage、密码或历史记录。现有 Chrome 自己
继续持有其正常用户上下文。

## 实际响应证明

| 步骤 | 随机等待 | URL | 结构化结果 |
| --- | ---: | --- | --- |
| page1 | — | `https://guba.eastmoney.com/list,601012,f.html` | `rc=1`；80 行；80 标准帖 |
| page2 | 9.993s | `https://guba.eastmoney.com/list,601012,f_2.html` | `rc=1`；80 行；77 标准帖；3 范围外 |
| detail | 7.404s | `https://guba.eastmoney.com/news,601012,1757706220.html` | ID 严格一致；`post_type=0`；正文长度 75 |
| page3 | 3.014s | `https://guba.eastmoney.com/list,601012,f_3.html` | `rc=1`；80 行；75 标准帖；5 范围外 |
| detail | 6.913s | `https://guba.eastmoney.com/news,601012,1757531792.html` | ID 严格一致；`post_type=0`；正文长度 4 |

脱敏机器输出位于本机：

```text
/tmp/eastmoney-existing-chrome-result-20260812-final2.json
```

## 可复现命令

前置条件：

1. macOS 上日常 Google Chrome 已运行；
2. Chrome `显示 → 开发者 → 允许 Apple 事件中的 JavaScript` 已勾选；
3. 启动脚本的终端/Python 已获得 macOS Automation 权限；只有需要 UI 检查菜单时
   才需要 Accessibility 权限；
4. 不要同时关闭脚本创建的专用标签页。

```bash
cd /Users/mac/Documents/trae_projects/MyResearcher/MyResearcher-DataCollector
PYTHONPATH=src python \
  experts/eastmoney-live-access/standalone_existing_chrome_navigation.py \
  --stock 601012 \
  --pages 3 \
  --min-delay 3 \
  --max-delay 10 \
  --operator-wait 180 \
  --load-timeout 30 \
  --result /tmp/eastmoney-existing-chrome-result.json
```

脚本文件：
[standalone_existing_chrome_navigation.py](standalone_existing_chrome_navigation.py)。

## 当前边界

这次成功消除了两个疑问：无需 Codex token 逐页操作，也无需新建独立 Chrome
profile。它尚未证明 30 股 × 100 天的长负载稳定性，也尚未接入 Collector 的
RawEvidence、SQLite、checkpoint 和 Backfill 状态机。

因此当前状态应区分为：

```text
STANDALONE_EXISTING_CHROME_BOUNDED_NAVIGATION = PASS
CODEX_RUNTIME_DEPENDENCY = NONE
NEW_PROFILE_REQUIRED = NO
NORMAL_FULL_PAGE_DETAIL_WORKLOAD = NOT_YET_VALIDATED
30_STOCK_100_DAY_BACKFILL = NOT_YET_VALIDATED
```

下一阶段应该将同一 Apple Events Chrome tab 适配成项目 `Transport`，先做一页
全部标准详情的 normal collection，再逐步扩大到分页/Backfill。任何验证码仍须
`ACCESS_BLOCK`/暂停，不能自动处理或误报为空数据。
