# Source Spec Contract

## Gate

Every production Source Adapter must have one same-name approved SOURCE_SPEC:

```text
Research → SOURCE_SPEC → Develop → Test
```

Implementation before evidence-backed specification is forbidden. A real-behavior mismatch routes back as `SPEC_MISMATCH`.

## Naming and placement

- spec: `specs/<source-name>.md`
- future adapter: `src/myresearcher_collector/sources/<source_name>/`
- one spec maps to one isolated adapter;
- the source name must be stable, lowercase and unambiguous.

## Mandatory sections

1. Identity: source name/type/home and acquisition purpose.
2. Entry Point: page/endpoint, entry type and method.
3. Request: parameters, non-secret headers, cookie/auth requirements.
4. Pagination: mechanism, start, size, ordering and stop condition.
5. Historical Coverage: earliest observable record and limitations.
6. Item Identity: source item ID and evidence for uniqueness.
7. Fields: source location, meaning, required and nullable behavior.
8. Time Semantics: publish/update time and timezone evidence.
9. Error Behavior: timeout, 429, 403, 5xx and invalid body.
10. Retry Policy: retryable and non-retryable outcomes.
11. Rate Limiting: known limit and evidence-backed interval.
12. Abnormal Cases: pinned/deleted/anonymous/reposted/missing records.
13. Incremental Strategy: cursor, timestamp or page-boundary semantics.
14. Evidence: sanitized request/response observations and reproduction steps.
15. Acceptance Criteria: exact implementation and test requirements.

## Evidence requirements

- Evidence must be reproducible, dated and sanitized.
- Unknown behavior is explicitly `OPEN QUESTION`; it is not guessed.
- Credentials, cookie values, tokens and unnecessary personal data are never stored.
- A live request may support research in an authorized later phase but cannot be the only regression test.

## Approval status

Phase 0 provides only `_template.md`; no concrete source spec is approved and no production adapter may be created.
