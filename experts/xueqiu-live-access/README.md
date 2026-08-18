# 雪球页面访问与短窗口 Backfill 实验归档

本目录记录雪球公开页面的项目独立访问实验。它保存已经实际执行的思考、步骤、
页面事实、帖子清单和结论，不代表 Collector 集成已经完成，也不把一次浏览器成功
扩大解释为长期无人值守能力。

## 2026-08-18 独立普通 Chrome + CDP 更新

最新独立进程实验见
[2026-08-18-independent-chrome-cdp.md](2026-08-18-independent-chrome-cdp.md)。

- 普通 Python 进程直接启动官方 Chrome 151；
- 使用 `.runtime/browser-profiles/xueqiu-dedicated` 和固定 loopback 端口 9227；
- Playwright 只执行 `connect_over_cdp`，没有调用 `launch` 或
  `launch_persistent_context`；
- 主页面和详情使用 `Target.createTarget(background=true)`，避免
  `context.new_page()` 拉起前台窗口；
- 隆基入口稳定观察 20 秒，page 1 取得 9 帖，page 2 取得 10 帖且零重复，详情
  `405329188` 成功；
- owned Chrome PID 没有成为 frontmost，用户 Chrome 运行前后标签 identity 一致；
- 入口和详情各观察到一次自动恢复的 `md5__1038` 往返，因此结论是有界
  `PASS_WITH_TRANSIENT_MD5_REDIRECTS`，不是长回填稳定性保证；
- 实验尚未接入 production Collector。

复现与证据：

- [independent_chrome_cdp_probe.py](independent_chrome_cdp_probe.py)
- [independent-chrome-cdp-2026-08-18.json](evidence/independent-chrome-cdp-2026-08-18.json)

## 2026-08-17 生产复查更新

最新复查见
[2026-08-17-production-reinvestigation.md](2026-08-17-production-reinvestigation.md)。

- Codex 内置浏览器和用户正常 Chrome 均能取得 10 条帖子；
- 项目 Playwright managed Chrome 无论 fresh profile，还是复用已由普通 Chrome
  成功初始化的 dedicated profile，都会进入 `md5__1038` challenge loop；
- 已增加不依赖 Codex 的 `existing-chrome` Apple Events 路径，真实 page1/page2
  各读到 10 个唯一 ID 且 overlap=0；
- 详情页另有独立 bug：公开 `SNOWMAN_STATUS` JSON 仍在 script 中，但 global 已被
  清除；修复已实现并通过离线测试，修复后的最终 live smoke 尚未获准执行；
- 因此当前仍不能宣称 production basic smoke 或 3-day backfill PASS。

复现代码：

- [managed_environment_probe.py](managed_environment_probe.py)
- [existing_chrome_smoke.py](existing_chrome_smoke.py)
- [production-reinvestigation.json](evidence/production-reinvestigation.json)

## 2026-08-16 历史结论

2026-08-16，以隆基绿能 `SH601012` 为样本，在浏览器页面中按“新帖”顺序、
随机间隔 3–10 秒连续翻到第 23 页：

- 共观察 225 行、225 个唯一 `status_id`，未出现重复 ID；
- 最近 3 个自然日窗口取得 49 条帖子，边界位于第 5 页；
- 最近 7 个自然日窗口取得 217 条帖子，边界位于第 23 页；
- 抽样详情页均能取得正文和公开嵌入状态；
- 未观察到验证码、安全验证、重复页或正文登录阻断；
- 列表中的“修改于”不是原始发布时间，不能直接作为 coverage 边界。

当时状态：

```text
XUEQIU_SH601012_PAGE_ACCESS = PASS
XUEQIU_SH601012_PAGINATION_23_PAGES = PASS
XUEQIU_SH601012_BACKFILL_3D = PASS (49 posts)
XUEQIU_SH601012_BACKFILL_7D = PASS (217 posts)
DETAIL_PAGE_SAMPLE = PASS
PROJECT_INTEGRATION = NOT_PERFORMED
CODEX_INDEPENDENT_PROCESS = NOT_TESTED
```

## 目录内容

- [2026-08-16-longi-backfill-3d-7d.md](2026-08-16-longi-backfill-3d-7d.md)：
  本次思考、执行、发现和结论的完整记录。
- [evidence/manifest.json](evidence/manifest.json)：实验摘要、边界时间校验和详情抽样 URL。
- [evidence/posts-backfill-3d.json](evidence/posts-backfill-3d.json)：最近 3 个自然日的 49 条帖子。
- [evidence/posts-backfill-7d.json](evidence/posts-backfill-7d.json)：最近 7 个自然日的 217 条帖子。
- [evidence/posts-observed-23-pages.json](evidence/posts-observed-23-pages.json)：23 页全部 225 条观察记录。
- [2026-08-18-independent-chrome-cdp.md](2026-08-18-independent-chrome-cdp.md)：
  脱离 Codex 的普通 Chrome + 固定 CDP 入口、分页、详情和焦点实验。
- [evidence/independent-chrome-cdp-2026-08-18.json](evidence/independent-chrome-cdp-2026-08-18.json)：
  本次 URL、帖子 ID/作者/时间、详情和焦点摘要。

## 证据边界

“PASS”只证明 2026-08-16 这次已执行的浏览器上下文和有限访问预算。没有证明：

- 普通 HTTP 客户端或无头浏览器具有相同访问能力；
- 2026-08-18 的独立进程有界成功可以外推为更长 backfill 或无人值守稳定性；
- 30 股 × 100 天能够稳定运行；
- 每条记录都已访问详情并取得精确 `created_at`；
- Snowball/Xueqiu 的长期页面结构、页大小或匿名访问策略不会变化。

归档中的 `standalone` 若出现在 2026-08-18 以前的描述中，只表示“不接入
DataCollector 的独立实验”。只有 2026-08-18 的 external Chrome + fixed CDP
实验实际证明了脱离 Codex 的有界进程运行；它仍不等于 production integration。
