# `curl_cffi` 方案独立复核

日期：2026-08-12

范围：只读审查 `/Users/mac/Documents/trae_projects/prompt-engineering` 的股吧实现和
历史日志；没有修改该仓库，也没有在已有证据充分后继续联网请求。

## 结论

`curl_cffi` 最多改变一次请求的 TLS/HTTP 指纹，不能提供浏览器 JavaScript runtime、
完整 JS 指纹或稳定的东方财富风控状态。现有项目证据无法证明成功由它带来，反而有
明确的反例。因此它不能成为 MyResearcher 的可持续无人值守访问方案。

此外，本次任务禁止 TLS/浏览器指纹模拟，所以即便它偶发改善放行率，也不在批准的
实现边界内。

## 代码和日志证据

1. `prompt-engineering/config.py:93-97`：`GUBA_USE_CURL_CFFI` 默认 `false`；
   `.env.example` 没有公开该开关，当前审查环境也没有安装包。
2. `prompt-engineering/guba_scraper.py:54-64`：启用后只固定
   `impersonate="chrome120"`，不是真浏览器。
3. `prompt-engineering/logs/20260629.log:79-92`：明确启用 `curl_cffi` 后，601012
   page1 立即验证码。
4. `prompt-engineering/logs/20260629.log:94-110`：第二次明确启用，page1 仍立即
   验证码。
5. `prompt-engineering/logs/20260629.log:112-163`：数分钟后一次没有启用标记的
   普通会话反而连续完成 page1–10，共 730 帖。成功和 TLS impersonation 没有
   建立因果。
6. `prompt-engineering/logs/20260610.log:12-52`：普通路径曾完成 601012 十页；
   `logs/20260612.log:12-16` 同一路径又在 page1 验证；同日下午 603039 又完成
   十页（`logs/20260612.log:243-283`）。更符合动态风险状态，而非单一 TLS 根因。
7. `prompt-engineering/logs/20260612.log:693-707`：page1 解析 77 帖，page2–4 也各
   解析 77 帖但新增始终为 0，强烈表明重复完整 ID 集/tarpit。
8. `prompt-engineering/guba_scraper.py:151-157`：只在链接少于 3 且出现验证码词时
   拦截；完整重复页会被当成功。`backfill.py:249-266` 还会增加成功页计数。
9. `prompt-engineering/guba_scraper.py:336-381`：详情只检查 HTTP 200，不识别
   `身份核实`，不校验请求 post ID 与页面 post ID，不验证正文。因此间歇验证码会
   被静默当成详情成功，缺失阅读/评论计数还会表现为 0。

## 官方能力限制

- [`curl_cffi` FAQ：正确 impersonate 后仍可能被检测](https://curl-cffi.readthedocs.io/en/latest/impersonate/faq.html#i-m-still-being-detected-even-if-i-impersonated-correctly)
  说明 JA3/Akamai 指纹并不完整，其他字段以及工具行为本身仍可被识别。
- [`curl_cffi` FAQ：不能改变 JavaScript 指纹](https://curl-cffi.readthedocs.io/en/latest/impersonate/faq.html#can-i-change-javascript-fingerprints-with-this-library)
  说明底层没有浏览器或 JS runtime。

这些官方说明与本轮真实浏览器观察一致：风险挑战可以发生在 page1，也可以在同一
已人工验证会话的任意详情请求重新发生；只调整 TLS 指纹不能形成稳定会话承诺。

## 与 MyResearcher 契约不兼容

- 该实现使用 `list,{code}_{page}.html`（`guba_scraper.py:130`），本项目批准的
  最新发帖排序是 `list,{code},f.html` / `f_2.html`。
- 五列表格解析推断年份并把缺失计数转成 0，没有保留嵌入 `article_list` 的权威
  原始字段语义。
- 详情不提取/校验 `post_article` 正文；上层 `searcher.py:663` 还会用标题代替
  正文。
- 随机页序、轮换 UA 和稀疏抽样不能证明顺序覆盖，也不符合当前 Source Spec。

## 可以吸收的改进

以下只能提高“失败识别和证据质量”，不能提高为稳定访问解法：

1. 在解析前组合验证标题、来源验证 marker 和必要结构，验证页立即 fail closed。
2. 保存每页完整有序帖子 ID 签名；不同页签名完全相同则
   `PAGINATION_NOT_PROGRESSING`。
3. 详情严格校验请求 ID、页面 `post_article.post_id`、正文结构和最终 URL。
4. 保存状态码、最终 URL、原始 body hash、访问序号、实际 transport 和失败类型。
5. 禁止缺包时静默降级；遇验证停止，不轮换 UA、不随机翻页、不连续轰击。

MyResearcher 当前严格解析器、RawEvidence 和 browser socket 边界已经覆盖这些
fail-closed 原则；其作用是保证“失败不是无数据”，不是绕过来源验证。
