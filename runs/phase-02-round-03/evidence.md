# Phase 2 Round 03 — Persistence Synthetic Evidence

All evidence below uses temporary directories, synthetic bytes and local
fixtures. No network, credential, real database or production data was used.

| Area | Test | Expected | Actual |
|---|---|---|---|
| schema/migration | `test_fresh_reopen_foreign_keys_and_schema_drift_rejection`, `test_unknown_existing_database_is_rejected` | Fresh DB creates/reopens; unknown/drifted DB fails closed | Passed |
| timestamp/numeric checks | `test_sql_shape_rejects_invalid_timestamp_and_negative_numeric` | Direct SQL rejects invalid timestamp shape and negative retry value | Passed |
| raw publish | `test_raw_publish_reuses_bytes_and_keeps_separate_lineage` | Equal bytes reuse one physical path without temp references | Passed |
| raw collision | `test_raw_collision_and_missing_reference_fail_closed` | Hash/size collision and missing referenced file fail closed | Passed |
| temp failure | `test_raw_temp_write_failure_creates_no_publishable_reference` | Pre-publish write failure leaves no `.partial` reference | Passed |
| orphan after DB failure | `test_orphan_after_sqlite_reference_failure_is_not_success` | Published orphan may remain; no evidence row is committed | Passed |
| observation idempotency/version | `test_observation_idempotency_versioning_scope_and_immutability` | Same fingerprint reuses latest version; changed facts append v2; old row cannot update/delete | Passed |
| request/evidence lineage | `test_lineage_and_physical_dedup_keep_two_evidence_rows` | Physical dedup does not collapse attempts/evidence links | Passed |
| source-agnostic evidence | `test_evidence_roles_are_source_agnostic_single_direct_support_is_allowed` | A direct supporting role is accepted without fabricated list/detail topology | Passed |
| grouped atomic transaction | `test_grouped_transaction_rolls_back_metadata_and_never_reports_success` | Metadata failure rolls back and run remains non-terminal | Passed |
| safe checkpoint | `test_safe_frontier_commits_for_success_no_data_and_partial` | SUCCESS/NO_NEW_DATA/PARTIAL may commit a proven safe frontier | Passed |
| partial without frontier/gap | `test_partial_without_frontier_and_unresolved_gap_do_not_advance` | No safe frontier or unresolved gap leaves checkpoint unchanged | Passed |
| unsafe terminal outcomes | `test_unsafe_terminal_status_does_not_advance_checkpoint` | Failed/spec-mismatch/cancelled cases do not advance | Passed |

The full suite also retained the 33 Phase 1 tests, including the unknown-old
watermark regression and Eastmoney list/detail drift behavior.
