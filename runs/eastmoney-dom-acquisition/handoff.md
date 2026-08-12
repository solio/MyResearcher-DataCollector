# Eastmoney Existing-Chrome DOM Acquisition — Developer Handoff

Status: `READY_FOR_INDEPENDENT_TESTER`

The HTTP-only response assumption is superseded. `AcquiredDocument` preserves
the exact evidence bytes consumed by the parser, observed URL, capture method,
time, and only metadata actually observed. Existing Chrome records
`browser_dom_snapshot` with nullable HTTP metadata. The schema is unchanged;
`raw_evidence.evidence_kind` stores role plus capture method.

Offline tests and bounded live results are recorded in `test-results.md` and
`live-smoke.md`. The bounded run proved real DOM list/detail acquisition,
production parsing, exact evidence persistence, observation linkage, truthful
partial/checkpoint behavior, and explainable rerun versioning. Full Backfill
remains unauthorized.
