# MyResearcher-DataCollector — Backfill v0.1 Independent Acceptance

## Final status

`BACKFILL_OFFLINE_ACCEPTANCE_PASS`

Tested Developer commit:

`b01c608c3497e2ea5bc8f3c4b43f1bbad3e114a2`

Tester correction commit SHA:

`1c3d020`

Eastmoney Backfill: `PASS`

## Acceptance matrix

| Requirement | Result |
|---|---|
| BF-A01 ~ BF-A20 | PASS |
| Forward checkpoint isolation | PASS |
| Fresh NULL checkpoint preserved | PASS |
| In-run overlap dedupe | PASS |
| Detail schema mismatch | PASS |
| Asia/Shanghai range | PASS |
| Counter reconciliation | PASS |
| All-details-failed classification | PASS |
| Idempotency | PASS |
| Observation versioning | PASS |
| RawEvidence compatibility | PASS |
| Existing Eastmoney regression | PASS |
| Xueqiu regression | PASS (offline existing acceptance) |
| Xueqiu Backfill implementation | NOT_READY, explicitly out of scope |
| Retention regression | PASS |
| Full suite | 222 passed, 1 approved xfail, exit 0 |
| Real network | NO |

The corrected BF-A03 executes a successful historical Backfill on a fresh
SQLite/data directory. Direct queries confirm the CollectionRun, RawEvidence
and SourceItemObservation were persisted, while `collector_checkpoints`
remains empty and the subsequent forward plan remains `BOOTSTRAP_PENDING`.
The Backfill path never declared or committed a forward safe frontier. Detail,
failure and raw-evidence counters remained reconciled across success, partial,
failure and schema mismatch outcomes.

## Next Role

`Backfill Live Executor`

Next real validation is Eastmoney, `1 stock × 7 days`. The Independent Tester
did not execute real network activity and did not modify production code.
