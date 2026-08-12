# Eastmoney Existing-User Chrome Bridge — Architecture Finding

## 状态

```text
SUPERSEDED: HTTP-ONLY TRANSPORT ASSUMPTION
```

Reviewer superseded this handoff's production-blocking conclusion. Its field
capability finding remains valid: Apple Events does not provide HTTP status,
headers, or exact network response bytes. The corrected contract preserves the
exact DOM snapshot bytes consumed by the parser as `browser_dom_snapshot`
RawEvidence, with HTTP-only metadata truthfully `NULL`.

本 finding 基于 Expert 已通过的 commit：

```text
8a3b644933a08deb633b1ee7183b4dd4aa065c2a
```

该 commit 已证明 existing-user Chrome + macOS Apple Events 可以在有限导航中
访问 Eastmoney list/detail 页面，但没有证明它可以作为当前 Collector 的
transport-grade response source。

## 字段能力矩阵

| `EastmoneyBrowserResponse` 字段 | Apple Events / 页面脚本当前能力 | 结论 |
|---|---|---|
| `status_code` | 不可取得；页面脚本没有主文档 HTTP response 对象 | BLOCKED |
| `body` | 只能取得 `document.documentElement.innerHTML` | 不是 exact/main-document response bytes |
| `headers` | 不可取得；AppleScript `execute javascript` 不暴露 response headers | BLOCKED |
| `final_url` | 可以读取 `location.href` / 页面 URL | 只能作为页面位置事实，不能证明最终 HTTP response URL |

## 为什么不能静默降级

当前 RawEvidence contract 保存 transport response 的 provenance、响应 bytes、
HTTP metadata、SHA 和可 replay body。DOM serialization 可能：

- 丢失原始字节编码、doctype、空白和响应传输边界；
- 反映解析/脚本修改后的 DOM，而不是服务器返回的 main-document body；
- 没有 status、headers、redirect chain 或可靠 final response URL；
- 无法与当前 `EastmoneyBrowserTransport` / persistence evidence 的 response
  语义等价。

因此本轮不允许：

```text
status_code = 200（伪造）
headers = {}（伪造或默认）
DOM HTML = exact raw HTTP body（错误声明）
```

也不允许修改 RawEvidence 历史定义来迁就 Apple Events。

## 已验证路径与未验证路径

已验证：

```text
existing user Chrome
→ macOS Apple Events
→ bounded page navigation
→ DOM inspection
→ article_list / post_article structural checks
```

未验证：

```text
existing user Chrome
→ status_code + exact response bytes + headers + final response URL
→ EastmoneyBrowserResponse
→ existing Collector
→ RawEvidence / SQLite / checkpoint
```

因此以下内容本轮均未执行：

- production transport adapter
- Collector integration
- RawEvidence integration
- SQLite persistence integration
- checkpoint validation
- bounded live backfill smoke

## 安全边界

没有读取或导出 cookie、credentials、Chrome profile、storage、password 或
history；没有自动处理 challenge/CAPTCHA；没有使用新 profile、Codex Browser、
stealth 或 bypass。现有 Chrome 的人工上下文仍由浏览器自己持有。

## 可评估的替代接口

1. **Chrome DevTools Protocol / remote debugging Network domain**：如果用户
   明确启用一个受控的、仅本机的调试接口，且该接口能返回主文档 response
   status、headers、body 和 final URL，可以评估将其包装为现有
   `EastmoneyBrowserResponse`。这需要单独的安全审查；本轮没有启用、读取或
   导出用户 Chrome 的调试端口。
2. **Playwright/CDP 自有 browser context**：现有 `EastmoneyBrowserTransport`
   已能提供完整 response shape，但新 context/profile 在当前环境会触发访问
   verification，且不能假定它等价于 existing-user Chrome。它不是本轮
   existing-user bridge 的替代成功证明。
3. **Apple Events DOM-only adapter**：Reviewer 后续批准为独立 acquisition
   method；保存 parser 实际消费的 DOM snapshot，并明确 provenance，不伪造 HTTP
   metadata。

## 结论与下一步

本文件原结论已被 architecture correction 取代。保留的事实是：

```text
EASTMONEY_EXISTING_CHROME_ACCESS_HYPOTHESIS = PASS
STANDALONE_EXISTING_CHROME_BOUNDED_NAVIGATION = PASS
```

新实现必须独立验证：

```text
DOM_ACQUISITION
COLLECTOR_INTEGRATION
BOUNDED_BACKFILL_SMOKE
```

无需以 CDP/Network response 为前置条件。后续 handoff 以
`runs/eastmoney-dom-acquisition/` 为当前结论。

```text
FULL_BACKFILL_AUTHORIZED = NO
Next Role = Reviewer
```
