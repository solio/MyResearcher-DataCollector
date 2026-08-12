# 东方财富股吧访问问题：成功经验与复现入口

## 最终结论

本次真正定位到的根因不是 Backfill、翻页 URL 或解析器，而是访问上下文：
相同公开列表 URL 经普通 urllib/curl 得到 HTTP 200 的身份验证壳，经正常的
浏览器托管上下文则得到含 `article_list` 的真实页面。

成功修复是新增 `EastmoneyBrowserTransport`，由宿主持有正常浏览器 Page，
Transport 只访问批准的东方财富 HTTPS 域名，再把主文档原始响应交给现有严格
解析器和持久化链路。它不读取或导出 Cookie/Storage，不解验证码，也不模拟
TLS 指纹。

已成功抓到的真实结果：

- 列表 URL：`https://guba.eastmoney.com/list,601012,f.html`
- 页面 `article_list.rc=1`
- 共 80 行：78 行标准帖子 `post_type=0`，2 行范围外类型 `post_type=20`
- 详情 URL：`https://guba.eastmoney.com/news,601012,1757445386.html`
- 列表/详情 ID 严格一致，详情正文长度 316 字符
- 完整脱敏帖子列表见 `post-list.md`

## 一定能重复执行的验证

外部网站是否放行某个新会话无法永久保证；可以确定复现的是代码、URL 语义、
严格解析、访问预算和失败分类。先运行不依赖外网的确定性验证：

```bash
cd /Users/mac/Documents/trae_projects/MyResearcher/MyResearcher-DataCollector
PYTHONPATH=src:. pytest -q
python -m compileall -q \
  src/myresearcher_collector/sources/eastmoney_guba \
  experts/eastmoney-live-access/reproduce_headless.py
```

本次完整结果见下文 Verification；以后测试数可能随项目演进增加，但不能有
新的失败。测试使用固定 HTML fixture，可确定复现 URL、验证页识别、
`article_list`/`post_article` 解析、空值与零值、列表/详情一致性和 Transport
域名边界。

## 无 GUI 在线诊断

机器只需 Python 3.11+ 和已安装的 Chrome/Chromium，不需要 Python Playwright：

```bash
python experts/eastmoney-live-access/reproduce_headless.py \
  --stock 601012 \
  --pages 1 \
  --with-detail \
  --json-out /tmp/eastmoney-headless.json \
  --markdown-out /tmp/eastmoney-headless-posts.md
```

它默认只做两次导航（1 个列表 + 1 个详情），间隔至少 3 秒；输出访问过的 URL、
页面标题、帖子 list 和详情 ID 证明。它不会改 UA、轮换指纹、使用代理、解验证
或读取浏览器 Cookie/Storage。

输出状态是稳定契约：

- `PASS`：来源放行，列表和首条详情均通过严格解析；
- `ACCESS_BLOCK`：来源返回身份验证页；
- `SOURCE_SCHEMA_MISMATCH`：来源结构变化或列表/详情不一致；
- `PAGINATION_NOT_PROGRESSING`：可选第 2 页完整 ID 序列重复；
- `TRANSPORT_ERROR` / `ENVIRONMENT_ERROR`：浏览器或环境失败。

2026-08-11 实测中，全新 Chrome headless 曾成功返回真实页面，随后又收到
`ACCESS_BLOCK`；与此同时，已有浏览器托管上下文仍能得到真实页面。因此无 GUI
是部署选项，不是“必定绕过验证”的保证。完整记录见 `HEADLESS-VALIDATION.md`。

## 对另一个项目代码的结论

`/Users/mac/Documents/trae_projects/prompt-engineering` 中值得吸收的是：小请求预算、
慢速访问、识别验证页、识别重复页，不能只看 HTTP 200。

本项目不采用以下做法：

- `curl_cffi` 的 `impersonate="chrome120"`：属于 TLS/浏览器指纹模拟，与本任务
  边界冲突；
- `list,{code}_{page}.html`：和本项目批准的最新帖 `f.html` / `f_2.html` 排序
  语义不同；
- 随机页序、轮换 UA：改变流量特征且破坏顺序覆盖；
- 五列表格时间推断、缺失计数改为 0、虚构标题/正文、主观垃圾词过滤：均破坏
  原始数据契约；
- “第 7 页必验证”“30 次必 tarpit”“只能换 IP”：该仓库只有注释性经验，
  没有足以在当前环境确认这些阈值的结构化证据。

逐项代码评审见 `PROMPT-ENGINEERING-REVIEW.md`；成功步骤和完整 Playwright
样例见 `SUCCESS-PLAYBOOK.md`；原始定位记录见 `README.md`。

## Verification

本次最终验证结果：

```text
238 passed, 1 xfailed
compileall: PASS
git diff --check: PASS
experts 必需文件: 全部存在
既有成功证据: article_list.rc=1，80 行帖子记录，详情 ID=1757445386
```

其中新增的无头复现脚本测试为 `4 passed`，覆盖成功 list+detail、验证页失败
分类、重复翻页拒绝和 CLI 输入边界。
