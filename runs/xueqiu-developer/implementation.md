# Xueqiu v0.1 Developer Implementation

## Scope

实现 approved `specs/xueqiu.md` 规定的 A-share、stock-scoped、top-level Xueqiu discussion source boundary。未实现 replies、profiles、news、HK/US、历史全量回填、DataClean 或并发抓取。

## SOURCE_SPEC traceability

- Access: `browser-managed anonymous session`; `XueqiuBrowserTransport` 只观察 browser-owned response，不生成 challenge/signature。
- Symbol: `600519 -> SH600519`、`000001 -> SZ000001`；仅接受 A-share 六位代码。
- Mapping: `list` item 的 `id`, `user.id`, `user.screen_name`, `title`, `description`, `created_at`, `target`, `fav_count`, `reply_count`, `retweet_count` 按冻结映射进入 raw item。
- Content/time: `description` 原样保存；epoch milliseconds 转 canonical UTC；保留 source time provenance。
- Pagination: 顺序 page/last_id chain；重复 ID 去重；不推进或重复页产生 pagination failure。
- Bootstrap: `XUEQIU_BOOTSTRAP_MIN_PAGES=2`；两页完整成功后才建立最大合法 `created_at` frontier。
- Incremental: known historical IDs 可在 boundary 停止；unknown IDs 即使旧于 checkpoint 也 eligible；coverage cap 为 `PARTIAL_COLLECTION` 且不推进 checkpoint。
- Access/schema: 401、403、WAF/challenge、invalid JSON、required-field/schema failure 不转换为 `NO_NEW_DATA`。

## Implemented files

- `src/myresearcher_collector/sources/xueqiu/parser.py`
- `src/myresearcher_collector/sources/xueqiu/collector.py`
- `src/myresearcher_collector/sources/xueqiu/browser_transport.py`
- `src/myresearcher_collector/sources/xueqiu/__init__.py`
- `src/myresearcher_collector/models/runtime.py` / `models/__init__.py`: shared `SourceItem` boundary; existing `GubaSourceItem` remains compatible.
- `src/myresearcher_collector/storage/sqlite_store.py`: type-only source-neutral alignment.
- `src/myresearcher_collector/integration.py`: Xueqiu-specific entry reusing existing `RawEvidenceStore`, `SQLitePersistence`, safe frontier and per-stock checkpoint ownership.
- `src/myresearcher_collector/cli/main.py`: bounded Xueqiu plan-only entry and injected-transport execution seam.
- Browser-observed provenance URLs are allowlisted/redacted before they enter result/evidence metadata; browser-added challenge/signature query values are not retained.

## Safety

No cookies, credentials, authorization headers, challenge/signature values or browser state are persisted. No proxy, CAPTCHA/WAF bypass, stealth or account rotation was added.
