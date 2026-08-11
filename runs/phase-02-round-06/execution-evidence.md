# Phase 2 Final Offline Execution Evidence

## Required cross-gap scenario

Test:

`tests/acceptance/test_persistence_integration_acceptance.py::test_pst020_partial_detail_failure_cannot_cross_unresolved_mid_page_item`

Fixture:

- prior checkpoint `T0 = 2026-08-10T00:00:00.000000Z`;
- page 1 item A has source-local publish time `2026-08-10 10:00:00`, i.e.
  `T1 = 2026-08-10T02:00:00.000000Z`; detail succeeds;
- page 2 distinct item B has source-local publish time `2026-08-10 09:00:00`,
  i.e. `Tmid = 2026-08-10T01:00:00.000000Z`; B is eligible/new;
- B detail returns HTTP 503 three times and exhausts retry budget.

Expected:

```text
runtime must not declare a frontier crossing B
checkpoint must remain T0
run must remain PARTIAL_COLLECTION with no watermark_after
```

Actual deterministic evidence:

```text
runtime_safe_frontier = 2026-08-10 10:00:00+08:00  # UTC T1
persisted_checkpoint = ('2026-08-10T02:00:00.000000Z', 'cross-gap-integration')
run_state = ('PARTIAL_COLLECTION', '2026-08-10T02:00:00.000000Z')
```

The runtime declaration and committed checkpoint both cross unresolved B.
This is an implementation defect in the Round 05 Correction integration
behavior, not a test-plan ambiguity and not a live-source issue.

## Other final results

| Area | Result |
| --- | --- |
| Persistence acceptance PST-001..PST-016 | PASS |
| PST-017 runtime mapping | PASS with `TEST_PLAN_MAPPING_NOTE`; direct negative control xfailed |
| PST-018/PST-019 checkpoint component cases | PASS |
| PST-021 unknown-old-ID real integration | PASS |
| PST-022 source-agnostic evidence | PASS |
| Required PST-020 Collector cross-gap case | FAIL — `IMPLEMENTATION_DEFECT` |

No production fix was made by the Tester.
