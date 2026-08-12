# Eastmoney Live Access Expert Record

Status: `SOURCE_SEMANTICS: PASS; UNATTENDED_LIVE_ACCESS: BLOCKED`

Observed at: `2026-08-11T09:42:30Z` / `2026-08-11T17:42:30+08:00`

Revalidated at: `2026-08-12` (Asia/Shanghai)

> 2026-08-12 correction: the browser adapter remains a valid implementation
> boundary, but a normal browser context is not a stable access solution.
> Fresh contexts were frequently challenged and graphical verification recurred
> inside one manually verified detail sequence. See
> [2026-08-12-retry-report.md](2026-08-12-retry-report.md). The historical
> one-list/one-detail success below remains evidence of page semantics, not an
> unattended production PASS.

## Problem

The approved Eastmoney list URL returned an HTTP-200 identity-verification
page to the production urllib transport.  Backfill therefore received no
`article_list`.  A current normal Collector probe failed in the same way, so
the defect was not Backfill pagination, storage or URL construction.

## Investigation

1. Historical live commit `1acd107` and evidence commit `7a2db17` used the
   same list URL, urllib opener, `Accept`, no Referer, no CookieJar, redirect
   allowlist, three-second pacing and sequential connection behavior.
2. Backfill used the same first list URL and transport.  Its only request
   difference was the descriptive User-Agent; real A/B probes showed both
   project User-Agents received the same verification response.
3. Current production normal `_fetch` exhausted both access-block attempts.
4. Direct curl reproduced the exact persisted 2834-byte verification body.
5. A normal anonymous browser navigation to the same list URL returned the
   real stock-bar page with `article_list.rc=1` and 80 rows.
6. The same browser context opened one standard `post_type=0` detail URL and
   returned a matching `post_article` for ID `1757445386`.

No CAPTCHA/WAF control was solved.  Browser cookies, storage and credentials
were not inspected or exported.

## Root cause

Eastmoney currently routes plain urllib/curl traffic to access verification
while a normal browser-managed context receives the approved HTML payload.
The exact private risk signal is not observable and is not imitated.

## 2026-08-11 implementation

- Added `EastmoneyBrowserTransport`, a narrow adapter over a caller-owned
  synchronous Playwright-like Page.
- The adapter returns the exact main-document response bytes to the existing
  Collector and strips `Set-Cookie` before metadata leaves the browser.
- Only approved Eastmoney HTTPS hosts are navigable; unsafe requested or final
  URLs fail closed.
- Backfill now accepts an injected `Transport`, allowing a browser host to use
  the same Collector/parser/RawEvidence/SQLite path without rewriting it.
- Updated the SOURCE_SPEC access contract from plain urllib to normal
  browser-managed anonymous HTML navigation.

The 2026-08-12 revalidation narrows the last statement: the supported runtime
is now an explicit experimental/operator-assisted browser host, and unattended
live availability is blocked until a complete normal workload succeeds without
recurring verification.

The standalone CLI now fails closed instead of silently falling back to the
known-blocked urllib path.  A browser-owning host must inject:

```python
transport = EastmoneyBrowserTransport(page)
report = execute_backfill_cli(args, transport=transport)
```

## Real result

List URL:

```text
https://guba.eastmoney.com/list,601012,f.html
```

Captured page title:

```text
隆基绿能(601012)股吧_隆基绿能怎么样_分析讨论社区—东方财富网
```

Detail proof URL:

```text
https://guba.eastmoney.com/news,601012,1757445386.html
```

Result:

```text
list article_list.rc: 1
list rows: 80
in-scope post_type=0 rows: 78
out-of-scope post_type=20 rows: 2
detail post_article: present
detail ID agreement: 1757445386 == 1757445386
```

See [page-evidence.json](page-evidence.json) and [post-list.md](post-list.md).
The full live HTML and post body are deliberately not committed.

## Reproduction package

- [README.zh-CN.md](README.zh-CN.md) is the Chinese entry point covering the
  final result, deterministic checks, non-GUI command, URLs and review verdict.
- [SUCCESS-PLAYBOOK.md](SUCCESS-PLAYBOOK.md) contains the successful lessons,
  exact non-GUI setup/run steps, failure classifications, and the production
  Playwright injection sample.
- [reproduce_headless.py](reproduce_headless.py) is an executable, bounded
  standard-Chrome headless diagnostic. It emits the visited URLs, page
  identity, parsed post list, and strict detail proof as sanitized JSON.
- [HEADLESS-VALIDATION.md](HEADLESS-VALIDATION.md) records the important live
  result: fresh headless Chrome was accepted once and then challenged while
  the browser-managed context still received the real page. Headless is an
  explicit diagnostic/deployment option, not a guaranteed bypass.
- [PROMPT-ENGINEERING-REVIEW.md](PROMPT-ENGINEERING-REVIEW.md) records the
  line-of-approach assessment of the implementation under
  `/Users/mac/Documents/trae_projects/prompt-engineering`, including which
  ideas were adopted and why fingerprint impersonation was rejected.

## Verification

```text
pytest tests/unit/test_eastmoney_browser_transport.py
pytest tests/unit/test_eastmoney_headless_reproduction.py
pytest tests/unit/test_eastmoney_guba_collector.py
pytest tests/unit/test_live_smoke_cli.py
pytest tests/unit/test_backfill_cli.py
```

Final full-suite result: `238 passed, 1 xfailed`.
