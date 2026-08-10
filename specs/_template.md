# Source Spec: `<source-name>`

Status: `DRAFT | APPROVED | BLOCKED | SUPERSEDED`  
Owner:  
Observed at:  
Evidence location:  

Do not include credentials, cookie values, authorization headers or unnecessary personal data.

## 1. Identity

- source_name:
- source_type:
- source_home:
- purpose:
- evidence/status:

## 2. Entry Point

- entry type:
- endpoint/page:
- method:
- current availability:
- evidence/status:

## 3. Request

- parameters:
- required non-secret headers:
- cookies required: `YES | NO | UNKNOWN`
- authentication required: `YES | NO | UNKNOWN`
- secret injection mechanism (name only, never value):
- evidence/status:

## 4. Pagination

- mechanism:
- page/cursor start:
- page size:
- ordering:
- overlap behavior:
- stop condition:
- evidence/status:

## 5. Historical Coverage

- earliest observable:
- range limitation:
- deletion/expiry behavior:
- evidence/status:

## 6. Item Identity

- source_item_id source location:
- uniqueness scope/guarantee:
- missing-ID behavior:
- collision behavior:
- evidence/status:

## 7. Fields

| Output candidate | Source location | Meaning | Type | Required | Nullable | Missing behavior | Evidence/status |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

## 8. Time Semantics

- publish time source and meaning:
- update time source and meaning:
- source timezone:
- ambiguous/missing time behavior:
- conversion rule:
- evidence/status:

## 9. Error Behavior

- timeout:
- HTTP 429:
- HTTP 403:
- HTTP 5xx:
- invalid body:
- partial body:
- empty success:
- evidence/status:

## 10. Retry Policy

- retryable:
- non_retryable:
- maximum attempts:
- backoff:
- evidence/status:

## 11. Rate Limiting

- known limit:
- recommended interval:
- concurrency:
- evidence/status:

## 12. Abnormal Cases

- pinned posts:
- deleted posts:
- anonymous posts:
- reposts:
- missing fields:
- duplicate items/pages:
- structure changes:
- evidence/status:

## 13. Incremental Strategy

- cursor/timestamp/page boundary:
- stable ordering assumption:
- overlap window:
- late-arriving/update behavior:
- idempotency key:
- evidence/status:

## 14. Evidence

- reproduction steps:
- sanitized request sample/reference:
- sanitized response sample/reference:
- observations:
- evidence limitations:

## 15. Acceptance Criteria

- [ ] entry point behavior reproduced
- [ ] identity behavior established
- [ ] fields and time semantics established
- [ ] pagination and stopping established
- [ ] errors/retries established
- [ ] sanitized fixtures planned
- [ ] raw contract mapping approved
- [ ] runtime outcomes approved
- [ ] unresolved blockers listed

## Open Questions / Blocks

-

## Change History

| Date | Change | Evidence | Author |
|---|---|---|---|
| | | | |
