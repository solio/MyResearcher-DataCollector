# Phase 0 Report

## 1. Repository Initial State

The dedicated origin `git@github.com:solio/MyResearcher-DataCollector.git` was empty when cloned to `/Users/mac/Documents/trae_projects/MyResearcher/MyResearcher-DataCollector`. The new repository had an unborn `main` branch, no commit and 0 tracked files, so its initial state is `EMPTY`.

An independently accessible legacy MyResearcher repository was inspected read-only as evidence. It is `LEGACY_CODE` with acquisition, persistence, cleaning, sentiment, research and Dashboard modules; it is not the containing repository and was not modified.

Evidence: `repository-baseline.md`.  
Evidence level: `CONFIRMED — repository fact`.

## 2. Created Project Structure

Created the standalone `MyResearcher-DataCollector` project root containing:

- root governance/configuration: `AGENTS.md`, `README.md`, `pyproject.toml`, `.gitignore`;
- exactly three roles under `agents/`;
- eight long-lived documents under `docs/data-collector/`;
- SOURCE_SPEC rules and `_template.md` under `specs/`;
- `src/myresearcher_collector/` with only `core`, `sources`, `models`, `storage` and `cli` package skeletons;
- unit, integration and fixture test boundaries;
- phase evidence under `runs/phase-00-onboarding/`;
- a reserved `scripts/` boundary.

Structural check: 31 mandatory pre-report files present, 0 missing, 0 forbidden infrastructure directories, 0 concrete source specs, 0 production source files.

## 3. Confirmed Product Boundary

Collector owns fetch, structural parse, raw structural mapping, traceability and runtime observation. It hands normalized raw records to DataClean.

Collector does not own cleaning, content quality, deduplication decisions, sentiment/stance, finance judgments, signals, backtests, trading decisions or Dashboard behavior.

Evidence level: `CONFIRMED — task contract`.

## 4. Collector → DataClean Contract Status

Status: `PARTIALLY_FROZEN`.

Frozen invariants include replayability, provenance, explicit failure/no-data separation, preservation of missingness, distinct publish/collection time semantics, and versioned deterministic behavior.

The concrete raw field envelope remains provisional. The accessible DataClean project is `PROJECT_BOOTSTRAP`, has `NO_ACTIVE_ROUND`, has no data capability beyond governance, and explicitly lists its unified RAW schema and time semantics as open questions. No executable DataClean input entry point, required-field schema, identity rule or serialization format is currently confirmed.

This does not block Phase 0, but Q-001/Q-005/Q-007 block approval of Phase 1 adapter output.

Evidence level: `CONFIRMED — downstream repository fact`; candidate fields are `PROVISIONAL`.

## 5. Agent Roles

Created only:

- Source Researcher: source evidence and SOURCE_SPEC;
- Developer: deterministic implementation of an approved spec, with `SPEC_MISMATCH` blocking;
- Tester: deterministic conformance and fixture evidence, reporting `TEST_PASS`/`TEST_FAIL`.

No finance, sentiment, architecture, reviewer, product or gate role was added.

## 6. Source Spec Mechanism

Every future production adapter requires a same-name approved `specs/<source-name>.md`. The template covers identity, entry point, request, pagination, history, fields, time, errors, retry, rate limit, abnormal cases, incremental behavior, evidence and acceptance criteria.

Phase 0 contains only `specs/README.md` and `specs/_template.md`; no Guba, Xueqiu or other concrete spec was created.

## 7. Testing Baseline

Created unit, integration and sanitized fixture boundaries and documented deterministic fixture/golden-output rules. Credentials, cookies, tokens and unnecessary personal data are forbidden in fixtures.

One Phase 0 structural smoke test verifies that the six approved package namespaces import. It is not evidence of source behavior.

Actual result: `1 passed`.

## 8. Runtime Baseline

The provisional run envelope covers run/source/time, request/page/record counters, item-time coverage, status and failure reason.

The semantic distinction between `NO_NEW_DATA` and `COLLECTION_FAILED` is frozen. `0 records` alone is never a terminal explanation. The complete enum and retry-aware counter reconciliation remain Phase 1 work.

## 9. Existing Code Findings

The separately accessible legacy MyResearcher contains real Guba and Xueqiu collectors, search/quant acquisition, SQLite persistence and scheduling. Observed records commonly contain title, URL, content, source/source type, engagement counts and post time.

These implementations lack a new-project SOURCE_SPEC and consistent frozen identity/time/raw-provenance contract. They are evidence and possible references only, not approved Collector code. No legacy file was changed or copied.

## 10. Responsibility Violations

Relative to the new boundary, the separately accessible legacy application couples collection with content cleaning, result filtering/deduplication, sentiment, investment analysis, rewriting, scheduling and Dashboard output.

These are recorded existing facts. Phase 0 did not delete or refactor them. The standalone repository prevents these responsibilities from becoming the new project contract.

## 11. Risks

- DataClean input compatibility is not yet concrete.
- No first source has been selected or researched against current behavior.
- Legacy URL/time/missing-value assumptions may be unsafe to reuse.
- Raw evidence retention and persistence/transport are undecided.
- Authentication, terms, rate limits and source stability require source-specific evidence.
- The dedicated origin has no baseline commit yet; commit/push policy remains a separate explicit action.

## 12. Open Questions

Ten questions are recorded in `open-questions.md`, covering DataClean schema/entry, first source, raw retention, fallback identity, time format, stock association, version fields, legal/auth/rate constraints, legacy reuse and runtime outcomes.

None block Phase 0. Q-001 and Q-002 are explicit Phase 1 start/output gates; other questions block only the relevant source or implementation decision.

## 13. Phase 1 Proposed Scope

Proposed—not started and not authorized:

```text
ONE SOURCE
ONE SOURCE_SPEC
ONE ISOLATED ADAPTER
ONE FROZEN RAW CONTRACT
SANITIZED REAL FIXTURES
DETERMINISTIC TESTS
ONE CLI RUN LOOP
```

Before development, an explicit Phase 1 scope must select the source, freeze DataClean compatibility, approve source identity/time/error behavior and define exact acceptance evidence.

## Verification Evidence

All commands were run from `MyResearcher-DataCollector/` unless noted:

| Check | Result |
|---|---|
| initial repository check | empty origin, unborn `main`, 0 tracked files before bootstrap |
| `git diff --check` | exit 0, no whitespace errors |
| `python -m compileall -q src tests` | exit 0 |
| `python -m pytest --collect-only -q` | exit 0, 1 structural test collected |
| `python -m pytest -q` | exit 0, 1 passed |
| required-path/TOML check | 31 required files present; TOML valid |
| forbidden directory check | 0 |
| concrete SOURCE_SPEC check | 0 |
| production source-file check | 0 |

The only network operation was the user-authorized SSH `git clone` of the dedicated empty origin. No source/runtime network or external data API was called. No SSH key content, credential, runtime database, real output or DataClean data was read or modified. No production crawler, cleaning, sentiment, finance, trading, infrastructure or Phase 1 behavior was implemented.

Phase 0 verdict: `PHASE_0_PASS`.
