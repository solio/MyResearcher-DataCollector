# Raw Evidence Retention v0.1 — Independent Acceptance

Tested Developer commit: `9f3cd2136b68bc11279b9b7fe1f6ab89a59207ea`

## 最终结论

`RAW_EVIDENCE_RETENTION_ACCEPTANCE_PASS`

## Case matrix

| Case | Result | Evidence |
| --- | --- | --- |
| RET-001 ~ RET-004 | PASS | metadata/lineage permanent；共享 SHA 仅在所有 references expired 时 purge |
| RET-005 / RET-006 | PASS | collection failure 与 SPEC_MISMATCH hold body |
| RET-007 ~ RET-009 | PASS | PURGED 语义明确；unexpected missing 报 integrity error；republish 恢复 PRESENT |
| RET-010 | PASS | mixed fixture dry-run 无 filesystem/DB mutation |
| RET-011 | PASS | 真实历史 v1 schema → current v2 in-place migration |
| RET-012A ~ RET-012C | PASS | DB failure、pre-commit recovery、post-commit cleanup |
| RET-013 | PASS | source identity fail-closed |
| RET-014 | PASS | RUNNING run hold |

Retention report metrics、CLI 默认 dry-run 与显式 `--confirm` 也均通过。

Eastmoney、Xueqiu、Persistence、Backfill 相关回归包含在 full suite；未执行真实外部网络或 browser。
