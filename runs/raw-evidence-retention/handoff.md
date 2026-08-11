# Raw Evidence Retention v0.1 — Developer Handoff

Status: `RAW_EVIDENCE_RETENTION_DEV_CORRECTION: READY_FOR_TEST`

Schema migration is in place and backward-compatible with v1 data. The default
body retention is seven days. Metadata, observations, and failure-linked bodies
are retained; only eligible physical body files are purged after explicit
confirmation. Independent Tester should run the RET acceptance checks and review
the migration fixture, CLI dry-run/confirm boundary, interrupted `.purging`
recovery, and source identity invariant.

Real Xueqiu network: not executed.

Next role: Independent Tester.
