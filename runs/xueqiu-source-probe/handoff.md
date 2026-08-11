# Xueqiu Source Probe Handoff

Status: XUEQIU_SOURCE_PROBE: BLOCKED

Commit SHA: e1dcb36e540ece479efcd26333bdcb5dc6461b0b

plain HTTP usable: NO

browser required: YES for the observed public HTML page; candidate JSON endpoint UNRESOLVED

login required: UNRESOLVED

endpoint: https://xueqiu.com/query/v1/symbol/search/status

response shape: direct HTTP returned 200 text/html WAF challenge, not JSON; JSON shape UNRESOLVED

xq_a_token observed: NO in both anonymous urllib sessions; browser cookie state not inspected

pagination advanced: UNRESOLVED

page1/page2 overlap: UNRESOLVED

moving-page observed: INCONCLUSIVE / NOT RUN after WAF challenge

incremental feasibility: UNRESOLVED; no JSON item/time evidence and no page movement evidence

major risks:

- Current plain HTTP access is blocked by a WAF HTML challenge despite HTTP 200.
- The browser rendered public HTML, but the candidate JSON endpoint was not proven in browser.
- Login requirement and browser session/cookie behavior remain unresolved.
- No source field mapping or time semantics can be frozen from this probe.
- CAPTCHA/WAF bypass is explicitly out of scope.

candidate spec: specs/xueqiu.md

production files modified: NONE

Next Role: Reviewer

