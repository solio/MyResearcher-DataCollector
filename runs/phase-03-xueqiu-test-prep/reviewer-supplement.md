# Reviewer Supplement — XQ-021 / XQ-022

## Scope

This supplement adds only XQ-021 and XQ-022A/B/C/D/E to the existing XQ-001
through XQ-020 preparation. The frozen source of truth remains
`specs/xueqiu.md` (`APPROVED`).

## XQ-021

`tests/integration/test_xueqiu_supplement.py` uses
`execute_and_persist_xueqiu_collection` with real `SQLitePersistence` and
`RawEvidenceStore`. It proves a new item at `T1 > T0` advances the persisted
checkpoint to `T1`, persists the new observation, and retains a negative control
where a known boundary with no new item remains `NO_NEW_DATA` at `T0`.

## XQ-022

The same module drives production `XueqiuBrowserTransport` with a minimal fake
Playwright-like page implementing `expect_response`, `goto`, pagination lookup,
click, response URL/body/status/headers. It covers page 1, page 2 + `last_id`,
wrong `last_id`, wrong page/symbol, and redaction of a synthetic
`challenge_secret=DO_NOT_PERSIST` value from persisted provenance.

## Stage A evidence

- XQ-021 prepared: YES
- XQ-022 prepared: YES
- Production files modified by Tester: NONE
- Live browser/network used: NO
- Current committed Developer baseline execution:
  - XQ-001 through XQ-020: `20 passed`
  - XQ-021/XQ-022 supplement: `4 passed, 4 expected failures`
  - XQ-021 expected failure: runtime/persisted frontier remains `T0` instead of
    advancing to accepted new item `T1`
  - XQ-022C/D expected failures: committed BrowserTransport accepts wrong
    `last_id`, page or symbol response URLs instead of raising pagination
    failure
  - Eastmoney targeted regression: `29 passed`
- These are pre-correction evidence only; Tester did not alter production.

## Waiting for

`XUEQIU_DEV_CORRECTION: READY_FOR_TEST` and its exact commit SHA.
