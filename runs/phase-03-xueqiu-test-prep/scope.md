# Xueqiu Independent Acceptance Preparation Scope

## Baseline

`743ed2f42c411cc335aa0e114044cf6cdfa97134`

## Role

Independent Tester.

## Objective

Prepare deterministic offline acceptance for the approved `specs/xueqiu.md`
contract. No live browser, network, cookie, token, signature or production
implementation is required for Stage A.

## Boundaries

Allowed changes are limited to `tests/`, sanitized `tests/fixtures/` and this
Tester handoff artifact. Production source, persistence, CLI and the approved
SOURCE_SPEC are unchanged.

## Stage B gate

Execution waits for `XUEQIU_DEV: READY_FOR_TEST` and an exact Developer commit.
