# Backfill 验证报告 — 2026-09-30（DAYS=2，补 9/29–9/30）

> 此为浏览器采集的历史操作验收报告。引用的 `runtime/` 日志、报告、截图和生产数据只在原运行环境留存，不随 Git 分发；本次整理未重新执行采集或复验这些运行结果。

**结论：✅ 全绿。16/16 SUCCESS，无 HALT，`ALL_DONE`，退出码 0，耗时 3m47s。**

| 项 | 值 |
|---|---|
| 命令 | `DAYS=2 zsh scripts/ops/backfill_all_stocks.sh` |
| RUN_START | `2026-09-30T15:50:50Z` days=2 from=none to=2026-09-30 tag=20260930 list_click_paging=0 |
| ALL_DONE | `2026-09-30T15:54:38Z` |
| 窗口（每只） | `from=2026-09-28T16:00:00Z` → `to=<该只结束时刻>` |
| 驱动日志 | `runtime/logs/backfill-driver-20260930.log` |
| 逐只报告 | `runtime/logs/backfill-<stock>-20260930.json` / `.err` |

> 窗口口径：`from=2026-09-28T16:00:00Z` **就是** `2026-09-29 00:00:00 +08:00`（UTC 比北京时间早 8 小时）。
> 所以本轮的 DAYS=2 = 9/29 00:00 → 9/30 23:59（Asia/Shanghai），与预期一致。

---

## ① 逐只 status / stop_reason / pages

16 只**全部** `status=SUCCESS`、`stop_reason=backfill_range_complete`、`range_complete=True`、`failed=0`。

| # | stock | status | stop_reason | pages | received | in_range | out_of_scope | 本run新增行数 |
|---|---|---|---:|---:|---:|---:|---:|---:|
| 1 | 601012 | SUCCESS | backfill_range_complete | 7 | 560 | 432 | 23 | 432 |
| 2 | 002463 | SUCCESS | backfill_range_complete | 6 | 481 | 309 | 54 | 309 |
| 3 | 601888 | SUCCESS | backfill_range_complete | 5 | 400 | 231 | 34 | 231 |
| 4 | 300666 | SUCCESS | backfill_range_complete | 3 | 240 | 83 | 46 | 83 |
| 5 | 300054 | SUCCESS | backfill_range_complete | 3 | 240 | 130 | 34 | 130 |
| 6 | 603039 | SUCCESS | backfill_range_complete | 2 | 160 | 33 | 17 | 33 |
| 7 | 002648 | SUCCESS | backfill_range_complete | 3 | 240 | 142 | 19 | 142 |
| 8 | 002028 | SUCCESS | backfill_range_complete | 3 | 240 | 118 | 16 | 118 |
| 9 | 603179 | SUCCESS | backfill_range_complete | 2 | 160 | 70 | 11 | 70 |
| 10 | 605020 | SUCCESS | backfill_range_complete | 2 | 160 | 60 | 29 | 60 |
| 11 | 600312 | SUCCESS | backfill_range_complete | 3 | 240 | 92 | 32 | 92 |
| 12 | 002891 | SUCCESS | backfill_range_complete | 2 | 160 | 35 | 36 | 35 |
| 13 | 603806 | SUCCESS | backfill_range_complete | 2 | 160 | 29 | 21 | 29 |
| 14 | 688676 | SUCCESS | backfill_range_complete | 2 | 160 | 28 | 38 | 28 |
| 15 | 300487 | SUCCESS | backfill_range_complete | 2 | 160 | 7 | 50 | 7 |
| 16 | 603997 | SUCCESS | backfill_range_complete | 2 | 160 | 8 | 46 | 8 |
| | **合计** | | | **47** | **3921** | **1807** | **506** | **1807** |

**没有一只 COLLECTION_FAILED，没有任何 `access_block` 字样**（16 个 `.err` 里 `access_block|Traceback|manual_verification` 零命中）。

### 恒等式残差（`received - in_range - out_of_scope - failed`）

16 只残差全部非 0（71–135，合计 1608）。**这是预期的、不是漏采**：本轮 16 只的 `stop_reason` **全部**是
`backfill_range_complete`，该分支的停止条件是「整页读到窗口起点之前」（`collector.py:984`），那部分行计入
`received` 但按设计不落库。判据：非零残差**必须**都落在 `backfill_range_complete` 上 → 本轮成立。

## ② stdout `DAYCOUNT BEFORE` → `AFTER`（三天）

| 发布日（北京） | BEFORE | AFTER | 变化 |
|---|---:|---:|---:|
| 2026-09-28 | 965 | 965 | 0 |
| 2026-09-29 | **（不在表里 = 0 条）** | **837** | **+837** |
| 2026-09-30 | **（不在表里 = 0 条）** | **970** | **+970** |

跑之前 BEFORE 列表**止于 09-28**（09-29/09-30 无行）；跑完只多出 09-29、09-30 两天，其余所有日期逐字未变。

## ③ `backfill_coverage` 新增区间

coverage 行数 **32 → 48**（每只股 +1 段）。**16 只全部**新增了一段：
`covered_from = 2026-09-28T16:00:00Z`（= 09-29 00:00 北京时间） → `covered_to = 该只本轮结束时刻`。

⚠️ 注意起点：是 **`2026-09-28T16:00:00Z`**，**不是** `2026-09-29T16:00:00Z`。因为段边界对齐**北京日**，
`2026-09-28T16:00:00Z` 恰为 9/29 00:00 CST（历史各段同规律：基段 `2026-05-03T16:00:00Z` = 05-04 00:00 CST）。

| stock | 新段 covered_from | 新段 covered_to |
|---|---|---|
| 002028 | 2026-09-28T16:00:00Z | 2026-09-30T15:53:19.716694Z |
| 002463 | 2026-09-28T16:00:00Z | 2026-09-30T15:51:34.754257Z |
| 002648 | 2026-09-28T16:00:00Z | 2026-09-30T15:53:02.340087Z |
| 002891 | 2026-09-28T16:00:00Z | 2026-09-30T15:53:59.290298Z |
| 300054 | 2026-09-28T16:00:00Z | 2026-09-30T15:52:46.087075Z |
| 300487 | 2026-09-28T16:00:00Z | 2026-09-30T15:54:26.222258Z |
| 300666 | 2026-09-28T16:00:00Z | 2026-09-30T15:52:31.822736Z |
| 600312 | 2026-09-28T16:00:00Z | 2026-09-30T15:53:45.794319Z |
| 601012 | 2026-09-28T16:00:00Z | 2026-09-30T15:50:51.233442Z |
| 601888 | 2026-09-28T16:00:00Z | 2026-09-30T15:52:06.338575Z |
| 603039 | 2026-09-28T16:00:00Z | 2026-09-30T15:52:58.164017Z |
| 603179 | 2026-09-28T16:00:00Z | 2026-09-30T15:53:29.612665Z |
| 603806 | 2026-09-28T16:00:00Z | 2026-09-30T15:54:06.809360Z |
| 603997 | 2026-09-28T16:00:00Z | 2026-09-30T15:54:32.535084Z |
| 605020 | 2026-09-28T16:00:00Z | 2026-09-30T15:53:36.258894Z |
| 688676 | 2026-09-28T16:00:00Z | 2026-09-30T15:54:17.687342Z |

## ④ posts 总行数增量

**214094 → 215901 = +1807。**

## 交叉对账（三条独立口径全部闭合到 1807）

| 口径 | 09-29 | 09-30 | 合计 |
|---|---:|---:|---:|
| `DAYCOUNT AFTER - BEFORE`（驱动） | +837 | +970 | **1807** |
| `posts.created_at >= RUN_START` 按发布日分桶 | 837 | 970 | **1807** |
| `Σ STAT in_range`（报告） | — | — | **1807** |
| `Σ 逐股 created_at 新增` | — | — | **1807** |

- 原地刷新（`updated_at >= RUN_START AND created_at < RUN_START`）= **0**（本轮只插入，未改老行）。
- 越界检查：本轮新增里 `published_at < 2026-09-29T00:00:00+08:00` = **0**；`> 2026-09-30T23:59:59+08:00` = **0**。
- run 内重复 `source_item_id` = **0**。
- 结构校验（1807 行）：10 位 id **1807/1807**、空标题 **0**、空作者 **0**、guba 主机 **1807/1807**、`+08:00` **1807/1807**。
- 冻结快照 `data/collector.legacy.db` = **8251**（原封不动）。
- `backfill_resume` = **0**（跑前跑后均 0）。

## 运行过程侧记

- **无验证码/无拦截**：16 只共 47 页，`FETCH_OK ... status=200`，**全部 `attempt=1`**（零重试），
  `.err` 里只有 runpy 警告 + `acquisition_mode` + 逐页 FETCH/PACE trace。
- 节奏（`PACE wait_sec`）在 2.93–9.39s 之间浮动，是 longtail 节奏（非固定间隔）。
- 本次 `profile_mode=fresh`（16/16）。`backfill_all_stocks.sh` 里**没有** `PROFILE_DIR`/persistent 逻辑
  （持久 profile 的改动只存在于 `enrich_all_stocks.sh`）→ backfill 每只起新 profile 是它当前的设计行为。
- 未编辑任何脚本。
