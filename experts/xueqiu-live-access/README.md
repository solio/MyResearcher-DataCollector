# 雪球页面访问与短窗口 Backfill 实验归档

本目录记录雪球公开页面的项目独立访问实验。它保存已经实际执行的思考、步骤、
页面事实、帖子清单和结论，不代表 Collector 集成已经完成，也不把一次浏览器成功
扩大解释为长期无人值守能力。

## 当前结论

2026-08-16，以隆基绿能 `SH601012` 为样本，在浏览器页面中按“新帖”顺序、
随机间隔 3–10 秒连续翻到第 23 页：

- 共观察 225 行、225 个唯一 `status_id`，未出现重复 ID；
- 最近 3 个自然日窗口取得 49 条帖子，边界位于第 5 页；
- 最近 7 个自然日窗口取得 217 条帖子，边界位于第 23 页；
- 抽样详情页均能取得正文和公开嵌入状态；
- 未观察到验证码、安全验证、重复页或正文登录阻断；
- 列表中的“修改于”不是原始发布时间，不能直接作为 coverage 边界。

当前状态：

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

## 证据边界

“PASS”只证明 2026-08-16 这次已执行的浏览器上下文和有限访问预算。没有证明：

- 普通 HTTP 客户端或无头浏览器具有相同访问能力；
- 一个完全脱离 Codex 的独立进程已经可用；
- 30 股 × 100 天能够稳定运行；
- 每条记录都已访问详情并取得精确 `created_at`；
- Snowball/Xueqiu 的长期页面结构、页大小或匿名访问策略不会变化。

归档中的 `standalone` 若出现在旧实验描述中，只表示“不接入 DataCollector 的独立
实验”，不能解释为“已证明脱离 Codex 运行”。本目录统一使用“项目独立实验”表述。
