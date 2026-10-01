# Enrich 验证报告 — 2026-09-30（承接同日 backfill，补 9/29–9/30 新帖正文）

> 此为浏览器采集的历史操作验收报告。引用的 `runtime/` 日志、报告、截图和生产数据只在原运行环境留存，不随 Git 分发；本次整理未重新执行采集或复验这些运行结果。

**结论：✅ 全绿。13/13 只 `requested == success == filled`，`access_blocks=0`、`stopped=False`，
`ALL_DONE`，退出码 0，耗时 30m26s，待办清零 160 → 0。全程零验证码。**

| 项 | 值 |
|---|---|
| 命令 | `zsh scripts/ops/enrich_all_stocks.sh`（全默认参数） |
| RUN_START | `2026-09-30T16:30:35Z` queue=13 stocks pending=160 |
| ALL_DONE | `2026-09-30T17:01:01Z` |
| 驱动日志 | `runtime/logs/enrich-driver-20260930T1630Z.log` |
| 逐只报告 | `runtime/enrich-runs/<stock>.json` / `.err` |
| 起跑前状态 | 上个 backfill `ALL_DONE 2026-09-30T15:54:38Z`，间隔 **≈35 分钟** |

## 生效参数（RUN_START 回显）

```
min_delay=3.0 max_delay=10.0 detail_referer=0 dwell=0-0 pace_model=longtail read_every=20
profile_dir=…/.runtime/browser-profiles/eastmoney-managed-persistent worker=none
acq_mode=managed-chromium profile_busy=no enrich_order=asc challenge_wait=180 challenge_retries=1
```

- **`detail_referer=0`** → 每帖 1 个文档请求（不翻倍）。
- **持久 profile**（不是 fresh）→ 跨轮复用浏览器身份，这是 2026-09-28 定位到的第一杠杆。
- **`pace_model=longtail`** → 每帖间隔长尾（非固定均匀），每 20 帖插一次 30–90s「停下来读」。
- 起跑前 `DRY_RUN=1` 走过一次真实启动路径：`DRY_RUN_OK`，模型 ETA 35m / 13 只，实际 **30m26s**。

## ① 逐只 verdict（13/13 SUCCESS）

| stock | requested | success | filled | failed | access_blocks | stopped | remaining | 耗时 |
|---|---:|---:|---:|---:|---:|---|---:|---|
| 601012 | 41 | 41 | 41 | 0 | 0 | False | 0 | 9m01s |
| 002463 | 30 | 30 | 30 | 0 | 0 | False | 0 | 6m16s |
| 605020 | 21 | 21 | 21 | 0 | 0 | False | 0 | 4m45s |
| 002648 | 18 | 18 | 18 | 0 | 0 | False | 0 | 3m27s |
| 600312 | 14 | 14 | 14 | 0 | 0 | False | 0 | 2m41s |
| 601888 | 11 | 11 | 11 | 0 | 0 | False | 0 | 1m35s |
| 002028 | 8 | 8 | 8 | 0 | 0 | False | 0 | 1m14s |
| 300054 | 7 | 7 | 7 | 0 | 0 | False | 0 | 45s |
| 300666 | 4 | 4 | 4 | 0 | 0 | False | 0 | 21s |
| 688676 | 3 | 3 | 3 | 0 | 0 | False | 0 | 13s |
| 603039 | 1 | 1 | 1 | 0 | 0 | False | 0 | 3s |
| 603179 | 1 | 1 | 1 | 0 | 0 | False | 0 | 2s |
| 603997 | 1 | 1 | 1 | 0 | 0 | False | 0 | 2s |
| **合计** | **160** | **160** | **160** | **0** | **0** | | **0** | **30m26s** |

## ② 三口径对账（全部闭合到 160，无残差）

| 口径 | 值 |
|---|---:|
| ① 本次 13 只报告 `content_filled` 之和 | **160** |
| ② `posts` 里 `content IS NOT NULL AND updated_at >= RUN_START` | **160** |
| ③ jsonl 非夹具增量（按 `result` 分解） | **160**（`success: 160`，**零非 success 行**） |

- 因为 ③ 里 **没有** `access_block` / `manual_verification_resumed` / `fetch_failure` 行，
  三个数**相等**是零拦截跑的预期形态（有拦截时 ③ = ① + 非 success 行数，会不等）。
- ⚠️ **① 必须只看本次队列的 13 个 `<stock>.json`**。`runtime/enrich-runs/` 里还有历史 run 的残留报告
  （`tail-*.json` 与 `PARALLEL-*.json` 是 2026-09-16 的；002891/300487/603806 是 2026-09-28 的）。
  对这 26 个文件朴素 `glob | sum` 会得到 **378** 这个错数；按本次队列 + mtime 过滤后才是 **160**。

## ③ 队列与副作用

- 跑完 `enrich_plan.py list` **为空** → 待办 160 → **0**。
- `REVISIT_CHECK OK: no already-marked post was re-requested`（`ledger_marked=60 preexisting_marks=60 discovered_this_run=0`）。
- 冻结快照 `data/collector.legacy.db` = **8251**（原封不动）。
- 驱动只写 `posts.content`（+ `updated_at`），**不改** `published_at` / 不新增行。

## ④ 无拦截的证据

- 13 个 `.err` 全部对 `WAIT_BLOCKED|WAIT_TIMEOUT|access block|Traceback|LIST_CHALLENGE` **零命中**。
- 13 个报告的 `challenge_windows` 全部 **`[]`** —— 一次挑战窗口都没触发
  （对比：一旦触发挑战，单次等待上限 180s；本跑单只最快 2s、最慢 9m01s，与 180s 量级完全不符）。
- 160 条新 `content` 里含 `身份核实|captcha|滑块|拼图` 的 = **0** 条。
- `content` 长度 40–1132 字节，均值 291 —— 正文量级，不是挑战壳。

## ⑤ 数据真实性抽样

```
1778881400 | 605020 | 请董秘程总工作还是细心点吧…            | 79B
1779167798 | 002463 | 兄弟们，我在这个股票上待了4个月高点150元进的… | 426B
1779236260 | 002028 | 前面两个跌停以后，差不多每8～10个交易日跌10块钱… | 43B
```

**侧记（非缺陷）**：160 条里 **124 条** content 含 HTML 标签（如
`<div class="xeditor_content app_h5_article">…`）。全库已填充 content 的同类占比是 **74.6%**
（14910/19992）→ 这是长期一致的**原始采集行为**，不是本次回归。按 `AGENTS.md`，
"data cleaning or content-quality decisions" 属禁止职责、归 `MyResearcher-DataClean`，此处只做事实记录。

## ⑥ 与已知陷阱的对照

| 已知风险（技能/README） | 本次实测 |
|---|---|
| backfill → enrich 背靠背会在第 ~7 个请求被拦（2026-09-24，间隔 65s） | **未复现**。本轮间隔 ≈35min，160/160 零拦截 |
| 5 流并发时拦截率升到 4.2% 并成簇 | 本轮**单流**，未测多流 |
| 撞到验证码需人在键盘前 180s 内清掉 | 本轮**零挑战**，不需要人工介入 |
| 跑前 `PROFILE_BUSY` 会拒跑 | `profile_busy=no`，正常起跑 |

> 本轮**未**编辑任何脚本、未改任何代码。
