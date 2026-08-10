# Phase 1 Round 02 Scope

Role: `Developer`

## Goal

Implement the approved `eastmoney_guba` standard top-level-post (`post_type=0`) source behavior as a deterministic, source-isolated adapter with sanitized fixtures, observable runtime outcomes and a minimal CLI boundary.

## Allowed

- `eastmoney_guba` source package, parser, collector and internal source/domain models;
- request/fetch behavior through an injectable transport;
- list/detail parsing, source identity, pagination, overlap idempotency and retry classification;
- in-memory raw evidence abstraction sufficient for deterministic tests;
- sanitized fixtures and Developer-side unit tests;
- minimal CLI needed to exercise the source adapter;
- Round 2 implementation notes, status and Tester handoff.

## Forbidden

- Xueqiu adapter/parser, browser automation, authenticated sessions, WAF/CAPTCHA bypass;
- DataClean implementation, final cross-project transport, production persistence backend or final public envelope;
- content cleaning, title/advertisement filtering, semantic deduplication, sentiment, finance or trading logic;
- new infrastructure, scheduler, database or external service;
- copying legacy `guba_scraper.py` behavior;
- credentials, cookies, authorization values or unsanitized live responses in Git;
- changing `specs/eastmoney_guba.md` source semantics.

## Required source behavior

The implementation must preserve the approved distinction between `post_publish_time`, `post_last_time` and collection time; use source `post_id` as identity; retain requested and canonical bar identity; count and preserve alternate post types in raw page evidence without emitting them under this spec; expose overlap, retries, partial collection and no-data outcomes; and never substitute title for missing body.

## Exit

`DEVELOPER_ROUND: READY_FOR_TESTER` only after deterministic tests and checks pass and `runs/phase-01-round-02/handoff.md` is complete. Any real source contradiction is `SPEC_MISMATCH` and blocks the affected implementation.
