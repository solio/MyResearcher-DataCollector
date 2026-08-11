# Xueqiu Acceptance Matrix

| Case | Frozen behavior exercised |
|---|---|
| XQ-001 | Injectable browser transport with deterministic fake |
| XQ-002 | Offline run does not construct a real browser |
| XQ-003 | Approved JSON route and `page=1` request |
| XQ-004 | Sequential `page` + `last_id` continuity |
| XQ-005 | Sequential-only execution and >=3 second interaction interval |
| XQ-006 | Two-page bootstrap success and newest valid `created_at` frontier |
| XQ-007 | Bootstrap page-1 access/transport failure leaves checkpoint NULL |
| XQ-008 | Bootstrap page-2 failure leaves checkpoint NULL |
| XQ-009 | Required item/schema failure is not success/no-data |
| XQ-010 | 403/challenge is an access failure, never `NO_NEW_DATA` |
| XQ-011 | Prior IDs without checkpoint restart bootstrap at page 1 |
| XQ-012 | Incremental known boundary stops without an extra page |
| XQ-013 | Unknown old ID remains eligible at/before checkpoint |
| XQ-014 | Known historical item is not forcibly refreshed |
| XQ-015 | Authorized source drift creates a new observation version |
| XQ-016 | Coverage cap before boundary is `PARTIAL_COLLECTION` with no frontier |
| XQ-017 | `NO_NEW_DATA` requires checkpoint + known boundary + no new item |
| XQ-018 | Repeated/non-advancing pagination is failure/incomplete, not no-data |
| XQ-019 | Raw response lineage includes source URL and response SHA |
| XQ-020 | Eastmoney regression suite remains explicitly in final execution scope |

The XQ acceptance module has one implementation loader. On the baseline, the
missing production module is reported as `PENDING_IMPLEMENTATION`; after the
Developer exposes the collector, constructor/result mismatches fail tests.
