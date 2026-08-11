# Xueqiu Final Feasibility Handoff

XUEQIU_FINAL_FEASIBILITY:
JSON_API_READY_FOR_SPEC_APPROVAL

actual browser network request:
GET https://xueqiu.com/query/v1/symbol/search/status.json
with symbol=SH600519, count=10, comment=0, hl=0, source=all, sort=time, page=1, q=, type=11
and page=2 plus last_id=404539054

JSON available: YES

identity:
item.id observed non-empty and unique within both pages; page1/page2 IDs are distinct; repeated page1 IDs are stable in the bounded comparison
candidate mapping: source_item_id = str(item.id)

content:
description and title fields observed; content values were not committed; HTML representation of description remains UNRESOLVED

timestamp:
created_at observed as numeric Unix epoch milliseconds; page2 is broadly older than page1

pagination:
page parameter advanced from 1 to 2; page2 also used last_id=404539054; top-level list and count/maxPage/page keys observed

moving-page:
after at least 3 seconds and reload, page1 overlap was 10/10; no new or removed IDs; newest created_at unchanged

anonymous usable:
YES for the observed temporary Chrome browser Network path

login required:
NO observed for this path; no login was submitted

HTML fallback:
NOT NEEDED for this gate; public HTML remains a non-production fallback candidate only

candidate spec:
specs/xueqiu.md (Status: CANDIDATE)

production files modified:
NONE

Remaining spec decisions:

- Reviewer must decide whether the browser-managed Network path is acceptable for a production Collector.
- Bootstrap and incremental policies remain UNRESOLVED; do not copy Eastmoney semantics.
- Description HTML preservation needs a future raw-value check if required.
- Cookie names/session transport and challenge parameter handling require a reviewed access contract; no cookie value is retained.

Next Role: Reviewer

