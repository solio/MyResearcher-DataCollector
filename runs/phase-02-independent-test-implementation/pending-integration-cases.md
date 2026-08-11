# Pending Integration Cases

Status: `WAITING_FOR_INTEGRATION_COMMIT`

## PST-021 full regression

The independent component test
`test_pst021_checkpoint_component_regression_forward_equal_and_backward`
already verifies the persistence checkpoint state machine:

```text
T1 -> T2  allowed
T1 -> T1  idempotent
T2 -> T1  rejected for SUCCESS and PARTIAL_COLLECTION
```

The frozen acceptance case still requires the real Collector → Persistence
observable boundary:

```text
unknown source_item_id
+ published_at <= checkpoint watermark
=> must not be suppressed solely by watermark
```

Round 04 has no committed integration API that can express this without
inventing a fake layer. Do not mock or weaken the case. After Round 05 lands,
bind the test to the actual public integration seam and verify the item is
eligible, acquired and persisted with checkpoint state unchanged unless the
runtime declares a valid frontier.

## Other integration-dependent checks

Round 05 must also verify, through its real boundary, that:

- Collector attempt IDs, evidence IDs and observation IDs reach SQLite without
  lineage remapping or content-dedup merging;
- Collector-declared `SafeFrontier` and unresolved gaps are passed to
  persistence without Persistence recomputing pagination or eligibility;
- a Collector parse/transport failure retains the acquired RawEvidence and
  cannot become `NO_NEW_DATA`;
- an accepted Eastmoney observation carries its list/detail evidence roles.

These are not substituted with mocks in this round. The current PST-017
component failure must also be resolved or explicitly reviewed before final
acceptance.
