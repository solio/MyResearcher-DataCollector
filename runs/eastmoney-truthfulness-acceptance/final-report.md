# Eastmoney Independent Acceptance — Truthfulness Architecture

## TESTED_COMMIT

`1a12a0df6090adb5d8be293b3a6e3f74673f02eb`

## ACCEPTANCE_CONTRACT_VERSION

- HTTP-only assumption removed: **YES**
- HTTP path remains response-grade strict: **PASS**
- DOM-only path accepted only with explicit provenance/evidence fidelity: **NOT IMPLEMENTED in tested commit**

## STATIC_REVIEW

The approved spec and the existing Chrome bridge handoff were reviewed. The current
repository has `EastmoneyBrowserTransport`/socket transport that returns exact main
document response bytes plus observed status/headers/final URL. It has no
`browser_dom_snapshot` acquisition type, capture-method field, or DOM-to-RawEvidence
integration path. The existing Apple Events script proves bounded DOM navigation only;
it is explicitly not transport-grade and must not be upgraded silently.

## INDEPENDENT_TESTS_ADDED

`tests/integration/test_eastmoney_truthfulness_reviewer.py`

The tests independently verify HTTP evidence fidelity, raw hash/size and lineage,
invalid-document versus access-block classification, and page-gap checkpoint safety.

## LIVE_ACCEPTANCE

- symbol: not executed through Collector; existing bounded Chrome evidence is for `601012`
- window: existing evidence covered page 1 → page 2/detail → page 3/detail
- list pages: bounded navigation evidence PASS, Collector integration NOT READY
- detail pages: bounded navigation evidence PASS, Collector integration NOT READY
- records: existing DOM report only; no persisted RawEvidence/SQLite run
- evidence: DOM snapshot evidence is not a current production artifact

## RESULTS

| Gate | Result |
|---|---|
| Acquisition truthfulness | BLOCK — no DOM acquisition path to test |
| Provenance | PASS for HTTP; BLOCK for DOM integration |
| No fake HTTP metadata | PASS in HTTP path; no DOM implementation present |
| Evidence fidelity | PASS for HTTP bytes; DOM path NOT READY |
| Source validation | PASS for HTTP parser and access-block fixtures |
| Raw Evidence / persistence / raw_ref | PASS for HTTP path |
| Idempotency | PASS in existing Backfill acceptance |
| Checkpoint / safe frontier | PASS; independent page-gap test passed |
| Failure classification | PASS; invalid document, access block, and page failure remain distinct |
| HTTP path regression | PASS |
| Bounded live Backfill | NOT AUTHORIZED / NOT EXECUTED |

## FINDINGS

### BLOCKING_REQUIREMENT

Provide an approved existing-Chrome acquisition adapter that honestly declares
`browser_dom_snapshot` (or equivalent), persists the exact parser-consumed snapshot
bytes, records observed URL and unavailable HTTP fields as NULL/absent, and integrates
through RawEvidence → persistence → raw_ref without fabricating status, headers, or
network response identity.

### OBSERVED_BEHAVIOR

The tested commit contains only the response-grade transport and a standalone Apple
Events DOM experiment. The experiment cannot provide status/headers/exact network
bytes and is not wired into Collector or SQLite.

### EXPECTED_BEHAVIOR

DOM acquisition must be a separately labeled, replayable evidence method, with source
payload validation, access-block detection, persisted evidence fidelity, and complete
lineage.

### MINIMAL_CORRECTION_SCOPE

Add the DOM transport/provenance contract and integration tests/implementation, then
rerun this acceptance and the bounded live run under Reviewer authorization. Do not
weaken the HTTP RawEvidence contract.

## VERDICT

`BLOCK`

## FULL_BACKFILL_RECOMMENDATION

`NOT_AUTHORIZED`

Next Role: `Developer Correction`
