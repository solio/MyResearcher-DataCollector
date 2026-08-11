# MyResearcher-DataCollector —— 雪球实时来源探测与 SOURCE_SPEC 冻结证据

## 角色

你是 **Source Researcher（来源研究员）**。

这是一个有明确边界的来源证据任务。

你不是生产代码 Developer。

不得实现 `src/myresearcher_collector/sources/xueqiu/`。

不得修改 persistence（持久化）。

不得把 Playwright/Selenium 增加为项目依赖。

## 目标

通过一次小范围 live probe（实时来源探测）解决 Xueqiu SOURCE_SPEC 的剩余不确定性：

1. 匿名的普通 HTTP session 能否获得可用的雪球 cookie/session？
2. 个股讨论 endpoint 当前是否能为 `SH600519` 返回有效 JSON？
3. 原始响应中实际存在哪些字段？
4. 页码分页是否正常推进？
5. page 1 与 page 2 之间存在什么 overlap/movement（重叠/移动）？
6. 是否确实需要 browser context（浏览器上下文）？
7. 是否确实需要 authenticated login（登录认证）？

结果将供 Reviewer 冻结 `specs/xueqiu.md` 使用。

---

## 开始前先读

阅读：

```text
AGENTS.md
docs/data-collector/product-goal.md
docs/data-collector/collaboration-contract.md
docs/data-collector/data-contract.md
docs/data-collector/runtime-contract.md
specs/eastmoney_guba.md
```

仓库合同仍然具有最高权威。

---

## 当前研究候选

主要的讨论 endpoint 候选：

```text
https://xueqiu.com/query/v1/symbol/search/status
```

候选请求参数：

```text
symbol=SH600519
count=20
page=1
sort=time
```

已知的外部实现证据提示可能存在：

```text
需要 cookie/session
页码分页
稳定的 item id
按最新时间排序
可能返回 401 / 403 / WAF challenge
```

在本地复现之前，不得把以上内容当作已批准的来源事实。

---

## 阶段 A —— 先尝试普通 HTTP session

先使用最简单的可用方式。

优先使用现有操作系统工具或 Python 标准库。

不得增加项目依赖。

候选方法：

```text
cookie jar
→ GET https://xueqiu.com/
  或 https://xueqiu.com/S/SH600519
→ 只检查 cookie 名称
→ 使用同一个 cookie jar 调用讨论 endpoint
```

可以使用：

```text
curl
Python urllib
http.cookiejar
```

不得记录 cookie 值。

不得把 cookie 值打印到终端 artifact（证据文件）中。

允许记录的证据：

```text
cookie name present: YES/NO
xq_a_token present: YES/NO
HTTP status
content-type
response byte count
脱敏后的 JSON 字段名
item IDs
created_at values
```

---

## 阶段 B —— page 1 / page 2 证据

如果普通 HTTP 能取得可用 JSON，则按顺序获取：

```text
page=1
等待 >= 3 秒
page=2
```

使用：

```text
count=20
sort=time
```

对每一页记录：

```text
HTTP status
content-type
item count
有序 item IDs
有序 created_at 时间戳
created_at 的最小值/最大值
```

然后计算：

```text
page1/page2 的 ID overlap（重叠）
duplicate count（重复数量）
分页是否推进
时间戳是否总体向更早时间移动
```

不得假设排序完全不可变。

---

## 阶段 C —— moving page 观察

在再次保守等待之后重新获取：

```text
page=1
```

比较两次 page-1 观察结果：

```text
new IDs
removed/moved IDs
overlap count
ordering movement
newest created_at
```

这是未来 incremental/bootstrap policy（增量/引导策略）的证据。

暂时不要发明该策略。

---

## 阶段 D —— 浏览器需求分类

如果普通 HTTP 失败，必须先对确切失败进行分类，再尝试其他方法：

```text
HTTP_401
HTTP_403
COOKIE_INVALID
WAF_CHALLENGE
CAPTCHA
HTML_INSTEAD_OF_JSON
DNS/TLS
OTHER
```

如果不增加项目依赖、且本机已有可用的普通浏览器 session，可以使用**一次**有边界的对照测试。

不得仅为本次探测把 Playwright 安装到项目中。

不得自动化登录凭证。

不得绕过 CAPTCHA/WAF。

如果出现 CAPTCHA 或明确的风控挑战：

**立即停止。**

记录：

```text
ACCESS_BLOCKED_BY_SOURCE
```

不得尝试 stealth、代理轮换、cookie 窃取、账号轮换或反检测。

---

## 登录分类

最终访问分类必须区分：

```text
ANONYMOUS_HTTP_WORKS
ANONYMOUS_BROWSER_COOKIE_WORKS
LOGIN_SESSION_REQUIRED
ACCESS_BLOCKED_OR_UNRESOLVED
```

不得仅因第三方实现使用已登录浏览器，就推断必须登录。

---

## 原始字段证据

对于成功的 JSON，记录多个 item 实际观察到的字段名。

特别检查：

```text
id
description
title
created_at
user.id
user.screen_name

fav_count
reply_count
retweet_count

target
```

实际缺失的字段必须保持缺失。

不得发明 fallback 值。

不得提交完整用户 profile 或不必要的个人信息。

---

## 内容规则

对 `description` 不做清洗：

```text
description
```

如果其中含 HTML，记录这一事实。

对于 Collector contract（采集器合同），最终 Collector 必须保留来源材料；DataClean 负责语义/文本清洗。

---

## Symbol 规则

探测：

```text
SH600519
```

只有在需要确认 symbol 前缀行为时，才可额外探测一个深圳股票，例如：

```text
SZ000001
```

本轮不得扩展到港股/美股支持。

本轮只允许 A 股。

---

## 安全 / 负载

严格限制范围。

不得并发。

不得进行负载测试。

最小请求间隔：

```text
>= 3 秒
```

目标是让 Xueqiu 总请求数保持很小。

不得进行历史数据爬取。

不得爬取回复/评论下的评论。

---

## 证据 artifact

创建：

```text
runs/xueqiu-source-probe/
```

至少包含：

```text
probe.md
sanitized-evidence.md
handoff.md
```

不得提交含有不必要用户数据的完整实时响应 body。

尽可能使用 hash 和结构摘要。

---

## SOURCE_SPEC 候选

只根据复现证据，更新或创建：

```text
specs/xueqiu.md
```

但状态必须保持：

```text
CANDIDATE
```

不得标记为 APPROVED。

应包含：

```text
scope（范围）
entry point（入口）
request parameters（请求参数）
cookie/auth behavior（cookie/认证行为）
pagination（分页）
identity（身份/标识）
fields（字段）
time semantics（时间语义）
error behavior（错误行为）
rate policy（速率策略）
observed overlap/movement（观察到的重叠/移动）
unresolved facts（未解决事实）
```

不得机械复制 Eastmoney 的 bootstrap/incremental policy。

---

## 最终结果

最终只能输出以下两者之一：

```text
XUEQIU_SOURCE_PROBE: READY_FOR_REVIEW
```

或：

```text
XUEQIU_SOURCE_PROBE: BLOCKED
```

报告：

```text
plain HTTP usable: YES/NO
browser required: YES/NO/UNRESOLVED
login required: YES/NO/UNRESOLVED
endpoint:
response shape:
pagination advanced:
page overlap:
moving-page evidence:
major risk:
candidate spec:
```

## 下一角色

Reviewer（审阅者）。

不得开始生产 Xueqiu Collector 实现。

---

## 翻译与待确认事项

以下内容在原文中本身也是待探测或待决策事项，本译文没有擅自确定：

1. **“usable cookie/session”**：暂按“cookie/session 能被后续讨论 endpoint 接受并返回可用 JSON”理解；仅拿到 cookie 名称不等于 session 可用。
2. **“ordinary HTTP session”**：暂按“无登录、无人工认证、使用普通 HTTP 客户端和 cookie jar 的会话”理解。
3. **“browser context”**：暂按“已有浏览器运行环境及其会话上下文”理解，不等同于允许安装或引入 Playwright/Selenium。
4. **“moving page / ordering movement”**：原文没有规定精确阈值；仅要求记录两次 page 1 的 ID、时间和顺序变化，由证据决定是否发生移动。
5. **“login required”**：只有在匿名 HTTP 与允许的一次匿名浏览器 cookie 对照均不能获得可用结果，且证据明确指向登录 session 时，才能归类为 `LOGIN_SESSION_REQUIRED`；不能由第三方代码单独推断。
6. **`description` 的 HTML**：原文要求在 Collector 边界保留原始内容，不在本探测任务中清洗；这不是对 DataClean 清洗规则的最终定义。
7. **“response shape” 和 candidate spec**：必须等成功的实时 JSON 证据产生后填写；本译文不替代 `specs/xueqiu.md`，也不构成 APPROVED SOURCE_SPEC。


