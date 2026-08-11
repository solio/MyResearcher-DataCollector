# Phase 2 Round 05 — Deterministic Execution Evidence

All scenarios use temporary directories, local Eastmoney fixtures, a mapping
transport, the real `EastmoneyGubaCollector`, the real `RawEvidenceStore`, and
the real `SQLitePersistence`. Each scenario closes the integration store; the
checks below reopen it.

| Scenario | Collector outcome | Persisted run | Observation | Failure | Checkpoint | Raw verified | Result |
|---|---|---:|---:|---:|---|---|---|
| Happy path: list pages 1–3 plus details | `SUCCESS` | `SUCCESS`; 6 attempts | 2 latest source identities (`1001`, `1002`); 6 evidence links preserve overlap lineage | 0 | created at runtime watermark | all 6 evidence rows; first row SHA/size equals actual `list_page_1.html` bytes | Passed |
| Incremental: committed identities + checkpoint, list pages 1–2 | `NO_NEW_DATA` | `NO_NEW_DATA`; 2 list attempts | no new observation; detail was not requested | 0 | equal frontier accepted, `last_safe_run_id=run-second` | 2 newly captured list responses verified on reopen | Passed |
| Partial: known first page, page 2 HTTP 503 retry exhaustion | `PARTIAL_COLLECTION` | `PARTIAL_COLLECTION`; 4 attempts | no new observation | 1 `http_503` failure linked to final failed attempt and raw evidence | unchanged from first run; no unsafe advance | page 1 plus all 3 actual 503 response bodies; linked failure evidence verifies | Passed |

## Reopened state checks

Happy path queries returned `collection_runs=SUCCESS`, six
`collection_attempts`, six `raw_evidence` rows, two
`source_item_observations`, six `observation_evidence` links and two
`observation_scopes` links for `stock:600001`. The raw directory contained
content-addressed `eastmoney_guba/*.body` files.

The incremental run read the first run's checkpoint and accepted source IDs
from SQLite. Its transport call list contained only the two list URLs; no
detail request was made. Reopened SQLite showed the same watermark and the
second run as `last_safe_run_id`.

The partial run persisted its failure row and its actual response bodies. The
reopened checkpoint tuple exactly matched the pre-run tuple, proving the
unresolved page did not cross the checkpoint boundary.

These are Developer integration results, not Independent Tester acceptance and
not a Phase 2 final pass.
