# Pre-Integration Test Results

These are Round 04 component results only. They are not final Phase 2
acceptance and must not be reported as `PERSISTENCE_TEST_PASS`.

## Acceptance command

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/acceptance/test_persistence_acceptance.py -q
```

Result: exit code `1`; 26 cases collected, 25 passed, 1 failed.

## Full offline suite command

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q
```

Result: exit code `1`; 81 cases executed, 80 passed, 1 failed, 0 skipped.
The same single PST-017 failure is the only failure in the full suite.

## Scenario table

| PST | Result | Evidence | Final acceptance ready? |
| --- | ------ | -------- | ------------------------ |
| PST-001 | PASS | Fresh migration/schema/history/index assertions passed. | Yes, pending Round 05 integration gate |
| PST-002 | PASS | Reopen schema/data snapshot remained identical. | Yes, pending Round 05 integration gate |
| PST-003 | PASS | Version, missing-index and checksum drift all failed closed. | Yes, pending Round 05 integration gate |
| PST-004 | PASS | FK mode was enabled and orphan insert was rejected. | Yes, pending Round 05 integration gate |
| PST-005 | PASS | Real raw bytes/hash/size/path and run/attempt lineage verified. | Yes, pending Round 05 integration gate |
| PST-006 | PASS | One physical body shared by two evidence/attempt histories. | Yes, pending Round 05 integration gate |
| PST-007 | PASS | Mismatched existing final path was not overwritten or referenced. | Yes, pending Round 05 integration gate |
| PST-008 | PASS | Pre-publish temp failure left no final body or `.partial` reference. | Yes, pending Round 05 integration gate |
| PST-009 | PASS | SQLite commit failures left orphan-only/raw state and rolled back metadata. | Yes, pending Round 05 integration gate |
| PST-010 | PASS | Missing and mutated referenced bodies failed integrity checks. | Yes, pending Round 05 integration gate |
| PST-011 | PASS | Eastmoney list/detail roles, NULL/0 and separate timestamps verified. | Yes, pending Round 05 integration gate |
| PST-012 | PASS | Identical facts remained version 1 while lineage/scopes appended idempotently. | Yes, pending Round 05 integration gate |
| PST-013 | PASS | `A -> B -> A` produced immutable versions 1/2/3. | Yes, pending Round 05 integration gate |
| PST-014 | PASS | Timeout/429/success attempts and stable caller-visible IDs retained. | Yes, pending Round 05 integration gate |
| PST-015 | PASS | Exhaustion retained attempts/failure and left checkpoint unchanged. | Yes, pending Round 05 integration gate |
| PST-016 | PASS | Acquired body remained linked to schema failure; no observation/no-data. | Yes, pending Round 05 integration gate |
| PST-017 | FAIL | Safe SUCCESS/NO_NEW_DATA frontier passed; zero-observation/no-frontier negative control terminalized `NO_NEW_DATA`. | No — pre-integration failure recorded |
| PST-018 | PASS | Partial run advanced exactly to declared safe prefix. | Yes, pending Round 05 integration gate |
| PST-019 | PASS | Partial-without-frontier, failed, spec-mismatch and cancelled states did not advance. | Yes, pending Round 05 integration gate |
| PST-020 | PASS | Candidate with explicit unresolved gap did not advance or report success/no-data. | Yes, pending Round 05 integration gate |
| PST-021 | WAITING_FOR_INTEGRATION_COMMIT | Checkpoint forward/equal/backward component regression passed; unknown-old Collector integration seam is not in Round 04. | No — execute after Round 05 |
| PST-022 | STRUCTURAL_PASS | Existing public direct evidence role accepted without fabricated list/detail evidence. | Yes, pending Round 05 integration gate |

## Failure detail

PST-017 expected `PersistenceError` and a still-`RUNNING` run for a caller
with zero observations and no proven frontier. Actual Round 04 behavior was a
normal return with terminal `NO_NEW_DATA`, no checkpoint and no `watermark_after`.
The checkpoint non-advancement is correct; the status distinction is not.
