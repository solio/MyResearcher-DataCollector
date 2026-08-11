# Eastmoney Backfill Live Round 01 — 交接

## 最终状态

```text
EASTMONEY_BACKFILL_LIVE_01:
BLOCK
```

## 执行摘要

```text
Executed source: eastmoney_guba
Stock: 601012
Range: 2026-08-04T16:00:00Z to 2026-08-11T15:59:59.999999Z
Timezone semantics: Asia/Shanghai calendar range
Real network: YES
HEAD: 1d4d29ba52efa2c601886f28e84c9b5f7110580c
run_id: 3a714f1bbb364565839384eae6c76596
status: SPEC_MISMATCH
stop_reason: schema_mismatch
checkpoint unchanged: YES (NULL -> NULL)
```

第一次列表请求获得 HTTP 200，但响应是 Eastmoney 身份核实 HTML，而不是
批准的 `article_list` 嵌入 JSON。因此按照 frozen Eastmoney SOURCE_SPEC 和
Executor 纪律停止，不能将其解释为无数据，也不能自行调整 parser、解决
验证页、重试或修改 production。

已保留：

- `CollectionRun`、`CollectionAttempt`
- 1 条 list RawEvidence metadata 与 body
- failure row 及 run/attempt/evidence lineage
- checkpoint 前后值（均为 NULL）

未产生 `SourceItemObservation`，因为 page 1 在 schema validation 阶段失败。
没有手工修改 SQLite，没有删除响应，没有保存 cookie、credential 或代理
凭据，也没有执行第二次 Backfill。

## Evidence

- [plan.md](plan.md)
- [execution.md](execution.md)
- [inspection.md](inspection.md)
- isolated data directory（不提交）：`data/live-backfill-eastmoney-601012/`

## 下一角色

```text
Next Role: Reviewer
```
