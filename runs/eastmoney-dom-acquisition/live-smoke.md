# Eastmoney DOM Acquisition — Bounded Live Smoke

Date: 2026-08-12 Asia/Shanghai

```text
symbol: 601012
window: 2026-08-12 09:59:00 .. 10:02:00 Asia/Shanghai
max list pages: 3
acquisition: existing-user Chrome DOM snapshot through Apple Events
```

The initial run retained one real list DOM and correctly stopped as
`SPEC_MISMATCH` because the production parser required a server-fixture-only
`data-postid` attribute. The captured DOM had a strict standard href and the
same `article_list.post_id`; the production parser was corrected to accept the
ID from either source while retaining allowlist and exact-ID validation.

Corrected bounded run:

```text
status: PARTIAL_COLLECTION
stop_reason: max_pages_reached
list pages: 3
source rows received: 240
detail pages / records persisted: 7
request/parse/detail failures: 0
checkpoint before/after: NULL / NULL
```

The partial result is intentional: the three-page cap did not prove the older
historical range boundary, so the run did not claim completeness or advance a
checkpoint.

Same-window rerun:

```text
records_new: 0
records_existing: 2
records_versioned: 5
checkpoint before/after: NULL / NULL
```

The five new immutable observations were explained by real source drift: each
item's `read_count` increased by one while its content and source timestamps
remained stable. This is observation versioning, not a duplicate logical item.

Evidence audit across all three live attempts:

```text
RawEvidence rows/files: 21 / 21
browser_dom_snapshot provenance: 21
non-null HTTP status: 0
non-null content type: 0
SHA/byte-size mismatch: 0
logical items: 7
immutable observations: 12
observation_evidence links: 28
checkpoint rows for stock:601012: 0
```

No cookie, profile, credential, password, history, or browser storage was read
or exported. No CAPTCHA/challenge automation or bypass was attempted.
