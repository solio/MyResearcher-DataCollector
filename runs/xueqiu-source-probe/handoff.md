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

## Round 2 update

Round 2 status: XUEQIU_SOURCE_PROBE_R2: BLOCKED

- Path A browser-context fetch: BROWSER_CORS_BLOCKED; no readable JSON.
- Path B api.xueqiu.com navigation: BROWSER_CLIENT_BLOCKED with net::ERR_BLOCKED_BY_CLIENT.
- No page=2 or moving-page request was made because page 1 JSON was unavailable.
- Existing public HTML observation remains HTML_BROWSER_PATH_CANDIDATE only.
- Production browser requirement: UNRESOLVED.
- Login requirement: UNRESOLVED.
- No production files were modified.

Detailed evidence: runs/xueqiu-source-probe/round-02-probe.md and runs/xueqiu-source-probe/round-02-sanitized-evidence.md

Next Role: Reviewer

## Final Browser Network Feasibility Gate

Final status: `XUEQIU_FINAL_FEASIBILITY: JSON_API_READY_FOR_SPEC_APPROVAL`

Real temporary Chrome Network observation resolved a usable anonymous JSON
path for `SH600519`:

- `GET https://xueqiu.com/query/v1/symbol/search/status.json`
- page 1 and page 2 returned HTTP 200 `application/json` XHR responses;
  page 2 advanced `page=2` and used `last_id=404539054`.
- `list` contained 10 items with non-empty unique `id` values and the
  required candidate fields.
- page 1/page 2 overlap was 0; after a >=3 second wait and reload, repeated
  page 1 overlap was 10/10 with no new or removed IDs.
- `created_at` was numeric Unix epoch milliseconds; page 2 was broadly older.

No login was submitted and no cookie/credential/challenge value was retained.
The candidate spec remains `specs/xueqiu.md` with `Status: CANDIDATE`.
Bootstrap/incremental semantics and the production browser/session contract
remain Reviewer decisions. No production files were modified.

Detailed final evidence: `runs/xueqiu-source-probe/final-browser-network-probe.md`,
`runs/xueqiu-source-probe/final-browser-network-evidence.md`, and
`runs/xueqiu-source-probe/final-handoff.md`.

Next Role: Reviewer
