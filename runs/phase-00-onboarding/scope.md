# Phase 0 Scope

## GOAL

建立 `MyResearcher-DataCollector` 项目基线，使项目边界、角色、目录、契约、Source Spec 机制、测试骨架与 Phase 管理证据可被新成员独立理解和审计。

## ALLOWED

- repository inspection
- read-only inspection of accessible downstream DataClean documentation
- directory bootstrap
- documentation
- three agent role definitions
- contracts
- SOURCE_SPEC template
- test skeleton
- Python package skeleton
- structural and offline checks

## FORBIDDEN

- production crawler implementation
- real source integration or network probing
- external API calls
- credentials, cookies, or tokens
- sentiment logic
- data-clean logic or quality decisions
- investment or trading logic
- database selection or schema deployment
- infrastructure expansion
- Phase 1 implementation

## Evidence rule

关键判断必须标注为以下之一：

- `CONFIRMED — repository fact`：可由当前仓库或可访问的 DataClean 文件直接复核。
- `CONFIRMED — task contract`：来自本次 Phase 0 明确要求。
- `PROVISIONAL`：有局部证据但尚未冻结。
- `OPEN QUESTION`：证据不足，禁止用推断补齐。

优先级：冻结 contract > 当前 phase scope > SOURCE_SPEC > Agent 推断。

## Exit boundary

Phase 0 仅在全部验收项成立时写入 `PHASE_0_PASS`；否则写入 `PHASE_0_BLOCKED`。无论结果如何，本轮都不得开始 Phase 1。
