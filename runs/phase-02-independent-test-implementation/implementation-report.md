# Phase 2 Independent Persistence Test Implementation Report

Status: `PRE-INTEGRATION_TEST_RESULT`

Final implementation state: `PERSISTENCE_TEST_IMPLEMENTATION_READY_WITH_PREINTEGRATION_FAILURES`

## Baseline

- Tester worktree: `/Users/mac/Documents/trae_projects/MyResearcher/MyResearcher-DataCollector-tester`
- branch: `phase2-persistence-independent-test`
- frozen baseline: `34c823d113c7d97dfdf4cad64f369183bf420179`
- baseline title: `Phase 2 Round 04 — Persistence Developer Correction Pass`
- frozen Test Plan: `runs/phase-02-test-plan/` (not modified)

The worktree was created clean at the exact baseline before implementation.
Only committed Round 04 persistence interfaces, the frozen contracts and the
frozen Test Plan were read. Developer Round 05 working-tree/branch contents
were not read, searched, imported or executed.

## Tests implemented

New independent test file:

`tests/acceptance/test_persistence_acceptance.py`

The file uses real temporary SQLite databases, real `RawEvidenceStore` files,
SQLite schema inspection, public persistence methods and bounded filesystem/
SQLite fault injection. It does not modify `src/`, migrations, the frozen
Storage Contract, SOURCE_SPEC or `tests/unit/test_storage.py`.

| PST | Independent test mapping |
| --- | ------------------------ |
| PST-001 | `test_pst001_fresh_migration_schema_history_constraints_and_indexes` |
| PST-002 | `test_pst002_current_schema_reopen_is_idempotent_and_preserves_data` |
| PST-003 | `test_pst003_unknown_version_missing_index_and_checksum_fail_closed` |
| PST-004 | `test_pst004_foreign_keys_are_enabled_and_invalid_lineage_cannot_commit` |
| PST-005 | `test_pst005_normal_raw_publish_hash_size_path_and_lineage` |
| PST-006 | `test_pst006_identical_content_dedup_keeps_two_attempt_and_evidence_lineages` |
| PST-007 | `test_pst007_existing_final_mismatch_fails_closed_without_overwrite` |
| PST-008 | `test_pst008_pre_publish_failures_leave_no_final_or_partial_reference` |
| PST-009 | `test_pst009_sqlite_commit_failure_leaves_orphan_only_and_rolls_back_metadata` |
| PST-010 | `test_pst010_missing_and_hash_mismatched_references_fail_closed` |
| PST-011 | `test_pst011_first_observation_has_eastmoney_evidence_and_source_semantics` |
| PST-012 | `test_pst012_identical_reacquisition_keeps_version_and_appends_scope_lineage` |
| PST-013 | `test_pst013_a_to_b_to_a_is_v1_v2_v3_and_history_is_immutable` |
| PST-014 | `test_pst014_retry_success_retains_attempts_and_stable_observation_identity` |
| PST-015 | `test_pst015_retry_exhaustion_preserves_attempts_failure_and_checkpoint` |
| PST-016 | `test_pst016_parse_schema_failure_after_body_retains_raw_and_failure` |
| PST-017 | `test_pst017_success_and_no_new_data_safe_frontier_commit`; negative-control test `test_pst017_zero_observations_without_frontier_cannot_infer_no_new_data` |
| PST-018 | `test_pst018_partial_proven_safe_prefix_advances_exactly_to_prefix` |
| PST-019 | `test_pst019_unsafe_terminal_states_do_not_advance_checkpoint` (four statuses) |
| PST-020 | `test_pst020_unresolved_gap_rejects_candidate_frontier_without_false_success` |
| PST-021 | `test_pst021_checkpoint_component_regression_forward_equal_and_backward` (component-only) |
| PST-022 | `test_pst022_source_agnostic_direct_evidence_role_is_supported` (public direct-response probe) |

Collection produced 26 test cases because PST-017 has two explicit cases and
PST-019 is parameterized across four unsafe statuses.

## Production changes

`None.`

No production code, migration, frozen Test Plan or Developer unit test was
changed.

## Independent pre-integration failure

`PST-017` negative control fails against the frozen contract. Calling the
public `finish_run(..., status="NO_NEW_DATA", safe_frontier=None)` on a
running run with zero observations returns normally and terminalizes the run
as `NO_NEW_DATA`; the test expected a fail-closed rejection with the run still
`RUNNING`. The checkpoint remains absent, but the terminal status is already a
contract violation because zero observations alone are not proof of no data.

This is recorded, not repaired, in the Tester role.
