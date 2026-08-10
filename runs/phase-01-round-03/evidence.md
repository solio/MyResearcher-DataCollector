# Phase 1 Round 03 — Failure Evidence

The following probes were executed with `PYTHONPATH=src` and only the tracked
synthetic fixtures under `tests/fixtures/eastmoney_guba/`.

## 1. Retry budget defect

Reproduction: configure a fake first-page transport to return three consecutive
HTTP 429 responses, then call `collect("600001", max_pages=1)`. The result was:

```text
retry_budget 429 COLLECTION_FAILED 2 2
```

The same probe with three HTTP 503 responses returned:

```text
retry_budget 503 COLLECTION_FAILED 2 2
```

`specs/eastmoney_guba.md` section 10 requires three total attempts for 429 and
5xx, with two total attempts reserved for access-block 403 responses.

Classification: `IMPLEMENTATION_DEFECT`

Implementation evidence: `src/myresearcher_collector/sources/eastmoney_guba/collector.py::_fetch`
initializes the retry budget from `access_block_attempts` for every status.

## 2. Detail identity/content drift defect

Reproduction: serve the same list row on pages 1 and 2, return the original
`detail_1001.html` on the first detail request and a local body-only mutation on
the second. The result was:

```text
detail_drift PARTIAL_COLLECTION 0 1 1 1
```

Meaning: `identity_content_drifts=0`, one detail request, one emitted item and
the second detail was never fetched. A changed source fact for the same ID must
be retained as a new immutable observation/version, not silently skipped.

Classification: `IMPLEMENTATION_DEFECT`

Implementation evidence: `collector.py::collect` suppresses an already-seen ID
before fetching its detail; `_row_fingerprint` only compares list-row facts.

## 3. Incremental watermark boundary defect

Reproduction: provide `existing_ids={"1001"}` and a watermark earlier than the
fixture row's publication time, with two repeated pages. The result was:

```text
watermark_newer_seen NO_NEW_DATA watermark_confirmed 0 0
```

The row's `post_publish_time` is newer than the supplied watermark, so it cannot
count as an old-at-watermark page. The implementation skips it solely because
the ID is already in `seen_ids`.

Classification: `IMPLEMENTATION_DEFECT`

Implementation evidence: `collector.py::collect` checks `already_seen` before
the publication-time eligibility branch.

## 4. Raw evidence retention on schema mismatch

Reproduction: provide `malformed_page.html` as the first page and an
`InMemoryRawEvidenceStore`. The result was:

```text
schema_raw SPEC_MISMATCH 0 1
```

The malformed response is the evidence of the source-structure mismatch, but no
raw snapshot is stored before parsing returns `SPEC_MISMATCH`. The approved
spec requires source evidence to remain retained for structural failures.

Classification: `IMPLEMENTATION_DEFECT`

Implementation evidence: `collector.py::collect` calls `evidence_store.put` for
successful parsed pages only; detail/page raw bytes are not stored on mismatch
paths.

## Scope classification

No source fact conflict was observed, so this is not `SPEC_MISMATCH`. No
contract blocker was needed to reproduce the failures. These are Developer
implementation defects and must be routed back to Developer.

