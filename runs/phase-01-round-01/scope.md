# Phase 1 Round 01 Scope

## Role

Current role: `Source Researcher / Phase 1 Research Lead`.

Role authority is limited by repository contracts. This round does not authorize Developer or Tester work.

## Goal

Research current Eastmoney Guba and Xueqiu source behavior, compare their suitability for the first complete Collector engineering loop, select exactly one `FIRST_SOURCE`, and freeze that source's SOURCE_SPEC only if the evidence is sufficient.

## Allowed

- repository and canonical-contract inspection;
- read-only legacy MyResearcher inspection;
- public external source research and bounded source observations;
- current-source versus legacy behavior comparison;
- source comparison and first-source recommendation;
- SOURCE_SPEC creation for the selected source;
- draft/deferred research state for the second source;
- evidence, open-question and handoff artifacts.

## Forbidden

- production collector, fetcher or parser implementation;
- DataClean implementation or final transport/backend selection;
- sentiment, content-quality, finance or trading logic;
- credentials, cookie values or tokens in Git;
- CAPTCHA bypass, credential theft, aggressive anti-bot bypass or account abuse;
- database, MQ, scheduler, workflow or other infrastructure expansion;
- implementing the second source;
- acting as Developer or Tester;
- entering a later phase or round.

## Evidence taxonomy

- `LEGACY FACT`: directly reproducible from the legacy MyResearcher repository or its history.
- `REPOSITORY FACT`: directly reproducible from the current DataCollector or DataClean repository contracts/state.
- `CURRENT SOURCE FACT`: directly observed from current public source behavior or authoritative current source material, with date and reproduction evidence.
- `INFERENCE`: engineering conclusion derived from stated facts; it must not be presented as source behavior.
- `UNKNOWN`: not verified; remains an open question or blocks spec approval.

## Required outputs

- `research-evidence.md` covering both sources and legacy failure analysis;
- `source-comparison.md` covering accessibility, completeness, reliability and traceability;
- one explicit `FIRST_SOURCE` recommendation;
- `specs/<first-source>.md` with `APPROVED` only if all blocking evidence is sufficient, otherwise `BLOCKED`;
- second-source state: `RESEARCHED`, `DRAFT`, `BLOCKED` or `DEFERRED`;
- `open-questions.md`, `handoff.md` and `status.txt`.

## Exit states

- `SOURCE_SPEC_READY_FOR_DEVELOPER`: first-source spec is frozen and handoff is complete.
- `SOURCE_SPEC_BLOCKED`: evidence required before implementation remains unresolved.

Neither state authorizes implementation in this round.
