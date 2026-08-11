# Xueqiu SOURCE_SPEC 定稿交接

## 结论

```text
Source Research: PASS
Final feasibility: JSON_API_READY_FOR_SPEC_APPROVAL
SOURCE_SPEC: APPROVED
```

## 已冻结内容

- Approved access：`browser-managed anonymous JSON Network path`
- Browser-managed context：required
- Login：approved observed path 不需要登录
- Scope：A-share stock-scoped top-level discussion posts
- Identity：`source_item_id = str(item.id)`；`id` 缺失或非法是 schema/item failure
- Response item path：`list`
- Pagination：sequential、concurrency=1、沿 `page` + `last_id` continuity
- Rate policy：minimum request/page interaction interval `>= 3 seconds`
- Bootstrap：`XUEQIU_BOOTSTRAP_MIN_PAGES = 2`
- Incremental：从 page 1 开始，使用 `KNOWN_BOUNDARY_REACHED` 作为安全停止边界
- Coverage cap：`max_pages`；达到 cap 且未到边界时为 `PARTIAL_COLLECTION`，不推进 checkpoint
- Raw evidence：保留 response bytes/equivalent、request provenance、source URL、collection time、SHA
- Challenge/signature：`BROWSER_OWNED`，不逆向、不保存、不绕过

## 依据

Reviewer 已通过 exact commit：

```text
41a2e3bd11883438410e0b0a5e043a64aa22c3fa
```

Final Browser Network 证据位于：

- `runs/xueqiu-source-probe/final-browser-network-probe.md`
- `runs/xueqiu-source-probe/final-browser-network-evidence.md`
- `runs/xueqiu-source-probe/final-handoff.md`

早期 plain-HTTP WAF 和探测环境阻断仅作为历史记录，不覆盖 Final Gate。

## 本轮边界

```text
Production files modified: NONE
```

本轮仅重写 `specs/xueqiu.md` 并新增本 handoff；未访问 Xueqiu，未实现
production adapter，未修改 `src/`、`tests/`、Persistence、Eastmoney 或
Batch。

## 下一角色

```text
Next Role: Developer
```
