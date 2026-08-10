# Phase 1 Round 08 — Independent Re-Test Scope

Role: `Tester`

Baseline under test: `725cf4e1b841599cb9ab28b63c282cb81c8d52b5`.

This run independently re-tests the Round 07 incremental alignment against
the corrected `specs/eastmoney_guba.md`, using synthetic local transports only.
It covers unknown/known IDs on both sides of the committed watermark, mixed
pages, pagination termination, and the existing Phase 1 regression suite.

Forbidden: production implementation changes, SOURCE_SPEC changes, Developer
artifacts changes, network/live smoke, real credentials or real collected data.
