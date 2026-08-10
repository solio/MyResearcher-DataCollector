# Phase 1 Round 04 Developer Handoff

## Execution Identity

client: Codex
model: GPT-5
role: Developer

## Current State

phase: Phase 1
round: Round 04
status: `DEVELOPER_FIX: READY_FOR_RETEST`

## Read Before Continuing

- `AGENTS.md`
- `specs/eastmoney_guba.md`
- `runs/phase-01-round-02/handoff.md`
- `runs/phase-01-round-02/spec-implementation-traceability.md`
- `runs/phase-01-round-03/scope.md`
- `runs/phase-01-round-03/test-report.md`
- `runs/phase-01-round-03/evidence.md`
- `runs/phase-01-round-03/handoff.md`
- `runs/phase-01-round-03/status.txt`

## Round 3 findings fixed

- Timeout/429/5xx now use three total attempts; 403 access blocks use two.
- Duplicate source IDs re-acquire detail when eligible, fingerprint detail
  facts and emit a new immutable observation version on content drift.
- Already-seen IDs are checked against valid `post_publish_time` and the
  committed watermark before boundary confirmation.
- Page/detail raw bytes are retained before parsing, including mismatch paths.

## Regression tests added

- `test_429_uses_three_attempt_retry_budget`
- `test_5xx_uses_three_attempt_retry_budget`
- `test_403_keeps_two_attempt_access_block_budget`
- `test_detail_content_drift_creates_new_observation_version`
- `test_seen_id_newer_than_watermark_is_eligible`
- `test_first_page_schema_failure_is_spec_mismatch` now asserts raw retention.

## Validation

```text
git diff --check                         # exit 0
python -m compileall -q src tests         # exit 0 with PYCACHEPREFIX
pytest --collect-only -q                  # 30 collected
pytest -q                                 # 30 passed
```

No network or live source smoke was used. The pytest cache warning is
non-blocking and caused only by the checkout's unwritable `.pytest_cache`.

## Changed production components

- `src/myresearcher_collector/sources/eastmoney_guba/collector.py`
- `tests/unit/test_eastmoney_guba_collector.py`
- Developer traceability/notes updated; Round 3 artifacts were not changed.

## Remaining limitations and OQ-01..04

- Durable raw persistence/manifest, DataClean transport/envelope and production
  operational approval remain OQ-01/OQ-02/OQ-03/OQ-04.
- Historical tail, deletion behavior and official rate limits remain unknown.
- No Xueqiu, scheduler, sentiment, finance or trading work was performed.

## Next Role

Tester

## Next Action

Run a new independent Tester round against the four Round 3 reproductions and
the full deterministic suite. Do not claim `TEST_PASS` from this handoff.

