# Review of the prompt-engineering Guba implementation

Reviewed source tree:

```text
/Users/mac/Documents/trae_projects/prompt-engineering
```

## Decision

The implementation contains useful operational hypotheses, but it is not a
drop-in solution for MyResearcher-DataCollector.  Adopt the bounded request
budget and explicit verification/repeated-page diagnostics.  Do not adopt
`curl_cffi` browser impersonation, UA rotation, randomized page order, or the
table-based data model.

Standard Chrome/Chromium headless is the only accepted non-GUI experiment in
this boundary. It has the deployment benefit the user identified while
remaining a real browser runtime rather than synthesizing one part of a
browser fingerprint. Live validation also showed that a fresh headless profile
can be challenged even while the browser-managed context succeeds, so it must
not be described as the more reliable access path.

## Finding-by-finding assessment

| Other implementation | Assessment here | Decision |
| --- | --- | --- |
| `curl_cffi_requests.Session()` plus `impersonate = "chrome120"` in `guba_scraper.py` | Non-GUI and may technically receive a page, but it deliberately imitates Chrome's TLS/browser fingerprint. This conflicts with this investigation's no-fingerprint-spoofing boundary and is fragile when source checks change. | Reject |
| `list,{code}_{page}.html` | Different route and ordering semantics from the approved latest-post contract `list,{code},f.html` / `f_2.html`. Its history estimator assumes last-reply ordering. | Reject as replacement |
| BeautifulSoup parsing of the rendered five-column table | Loses the authoritative structured timestamps/nullable counters and relies on display-time inference. | Reject |
| Missing/invalid counters become zero | Violates the required distinction between absent and source-reported zero. | Reject |
| Synthetic fallback title `股吧帖子` and title-as-content substitution | Invents source data. | Reject |
| Spam keyword filtering (`张楠`, `加微信`, etc.) | Subjective content cleaning belongs outside the raw collector. | Reject |
| Page cap and slow pacing | Useful safety control when fixed, sequential, and contract-driven. | Adopt concept |
| Detect verification responses | Correct direction, but checking only when fewer than three links can miss abnormal payloads. Use the source-specific verification title+marker classifier before parsing. | Adopt stronger form |
| Detect identical page content (“tarpit”) | Useful diagnostic. Compare complete ordered source-ID signatures and classify failure; do not silently accept repeated pages. | Adopt concept |
| Random page order and rotating UA | Changes identity/traffic shape to avoid detection and breaks sequential coverage semantics. | Reject |
| “page 7 always CAPTCHA”, “about 30 requests causes tarpit”, “IP is the only cause” | These are comments/anecdotes, not committed structured evidence for this environment. `git blame` traces the comments to commit `aa5c11b3` on 2026-06-29, which also differs from the supplied summary's 2026-06-18 date. | Treat as unverified hypothesis |
| API routes are all dead | Not required for the approved HTML source and not established by reusable current evidence in that repository. | Do not rely on claim |

## What was reused

The resulting implementation uses these general lessons without copying its
evasion behavior:

- non-GUI browser operation;
- explicit, small request budget;
- minimum pacing;
- verification pages are collection failures;
- optional full ordered-ID signature comparison for page 1 versus page 2;
- no claim that HTTP 200 means valid data.

## Why headless Chrome is the better fit

`curl_cffi` supplies a browser-like network fingerprint but is not a browser:
it does not give the browser-owned navigation boundary now required by the
SOURCE_SPEC. Standard Chrome headless is still non-GUI, can run on servers, and
can be owned either through the no-extra-Python-package `--dump-dom` diagnostic or
through Playwright for production injection.  The same strict parser then
validates `article_list`, `post_article`, URLs, timestamps, nullable counters,
post type, and list/detail identity.

This does not claim a private Eastmoney risk rule or promise that a clean
headless profile will be accepted. Any verification response is
reported as `ACCESS_BLOCK`, never bypassed and never converted to “no data”.
