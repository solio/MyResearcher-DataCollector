# Raw Evidence Retention v0.1 — Developer Handoff

Status: `RAW_EVIDENCE_RETENTION_DEV: READY_FOR_TEST`

Schema migration is in place and backward-compatible with v1 data. The default
body retention is seven days. Metadata, observations, and failure-linked bodies
are retained; only eligible physical body files are purged after explicit
confirmation. Independent Tester should run the RET acceptance checks and review
the migration fixture and CLI dry-run/confirm boundary.

Real Xueqiu network: not executed.

Next role: Independent Tester.
