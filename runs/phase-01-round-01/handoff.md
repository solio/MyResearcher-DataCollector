# Phase 1 Round 01 Handoff

## Outcome

```text
FIRST_SOURCE = eastmoney_guba
SOURCE_SPEC = APPROVED
SECOND_SOURCE = xueqiu (RESEARCHED / DEFERRED; NOT AUTHORIZED)
ROUND_STATE = SOURCE_SPEC_READY_FOR_DEVELOPER
```

The decision is based on current source behavior, not on assumed content quality. Guba exposed usable anonymous top-level post list/detail facts; Xueqiu's tested anonymous search returned an error and its public stock page returned a WAF challenge.

## Next Role

```text
Developer
```

This handoff names the next role only. The Source Researcher did not execute Developer or Tester work.

## Canonical inputs for the next role

Read in this order:

1. `AGENTS.md`
2. `docs/data-collector/collaboration-contract.md`
3. `docs/data-collector/data-contract.md`
4. `docs/data-collector/runtime-contract.md`
5. `docs/data-collector/source-spec-contract.md`
6. `docs/data-collector/testing-contract.md`
7. `specs/eastmoney_guba.md`
8. `runs/phase-01-round-01/scope.md`
9. `runs/phase-01-round-01/research-evidence.md`
10. `runs/phase-01-round-01/source-comparison.md`
11. `runs/phase-01-round-01/open-questions.md`

## Developer-authorized source scope

- Implement only the isolated `eastmoney_guba` top-level-post behavior described by the approved spec.
- Use the public latest-post HTML route and exact observed item links; parse embedded structured source objects without executing browser JavaScript.
- Preserve requested-bar and canonical-bar identity separately.
- Preserve original body and exact publish/update facts; never use “最后更新” as publication time and never substitute title for missing body.
- Preserve immutable raw list/detail evidence behind a contract-approved `raw_ref` boundary.
- Make page overlap idempotent while retaining observation/page provenance.
- Expose distinct success, no-data, partial, collection-failure and spec-mismatch outcomes with reconciled counters.
- Create small sanitized deterministic fixtures before relying on parser tests. Live network is research evidence, not regression infrastructure.

## Gates before production output

The Source Spec is approved, but repository-wide output decisions remain open:

- DataClean has no executable input schema or entry point.
- raw snapshot retention/manifest format is not frozen.
- common timestamp/version serialization is not frozen.

The Developer may design and implement the isolated source parser/adapter behind repository contracts, but must stop and record a contract blocker before inventing a production DataClean integration, persistence backend or public envelope. If the intended Developer round requires those outputs immediately, obtain the relevant contract decision first.

## Explicitly forbidden

- no Xueqiu adapter, browser automation, authenticated-session handling or WAF/CAPTCHA bypass;
- no reply/comment records until their separate source behavior is approved;
- no direct use of undocumented internal JSON endpoints when the approved spec names public HTML pages;
- no copy/paste migration of legacy `guba_scraper.py` behavior;
- no title filtering, advertising/spam filtering, title-as-body substitution, semantic deduplication, sentiment, author quality or finance/trading logic;
- no credentials, cookies, authorization values or live unsanitized responses in Git;
- no claim of complete history or production scheduling approval.

## Required deterministic acceptance evidence

At minimum, fixtures/tests must cover:

- valid latest-post page and matching detail;
- page 1/page 2 overlap and idempotent logical emission;
- moving page boundary with explicit coverage counters;
- cross-bar standard posts plus explicitly counted/preserved nonzero alternate post-type rows without emitting them under the approved scope;
- missing/non-numeric ID and list/detail ID mismatch;
- precise publication time versus last-update/display time;
- valid numeric zero versus missing engagement fields;
- missing author without generated identity;
- malformed/missing embedded JSON and structurally valid empty list;
- first-page failure, later-page failure, detail failure, retry exhaustion and max-page cutoff;
- no watermark advancement on partial/failure;
- raw replay reference present for every emitted item.

Tester is not authorized by this handoff until Developer completes its own repository handoff.

## Evidence and security note

- DataCollector baseline: `2eb563386bb335918720d3a51e4597507a04a437`.
- Legacy evidence baseline: `d510cc5ddb08215403d932616193af463fb9ffdf`.
- DataClean read-only baseline: `d2696799a930a3c8f0eff5f70723db2b388fb9af`.
- Current public observations occurred on 2026-08-10 with no login/API credential. An anonymous temporary Xueqiu cookie was kept only in `/tmp` during the two-request comparison and then deleted; no cookie value was written to the repository.
- No production code, parser, adapter, runner, DataClean code or test implementation was created in this round.
