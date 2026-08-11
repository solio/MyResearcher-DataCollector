# Developer Handoff

status: `READY_FOR_TEST`
role: `Developer`
source: `xueqiu`

## Handoff

Xueqiu v0.1 source-isolated parser, browser-owned transport boundary, sequential collector, CLI plan-only gate and existing RawEvidence/SQLite persistence path are implemented. Eastmoney behavior remains covered by the full regression suite.

Exact implementation commit is recorded in the final task response and Git history. The next role is **Independent Tester**.

Known limitation: this round intentionally does not launch Chrome or make live Xueqiu requests. A bounded live run must supply a normal browser-managed page/context to `XueqiuBrowserTransport`; the CLI does not silently fall back to plain HTTP.

## Correction

- Incremental success after a known boundary now advances `safe_frontier` to `max(checkpoint, accepted published_at)`; no-new-data retains the committed checkpoint and partial collection remains non-advancing.
- `XueqiuBrowserTransport` validates sanitized response `symbol`, `page`, and page>1 `last_id` continuity while dropping unsafe challenge/signature query fields from provenance.
- Correction regression tests: XQ-021 and XQ-022A-D.
