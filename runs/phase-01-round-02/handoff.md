# Phase 1 Round 02 Developer Handoff

```text
DEVELOPER_ROUND: READY_FOR_TESTER
DEVELOPER_SELF_AUDIT: READY_FOR_TESTER
Next Role: Tester
FIRST_SOURCE: eastmoney_guba
SOURCE_SPEC: APPROVED
SPEC_MISMATCH: NONE OBSERVED
```

Traceability: `runs/phase-01-round-02/spec-implementation-traceability.md`

## Implemented behavior

- `src/myresearcher_collector/sources/eastmoney_guba/parser.py` parses the approved public `article_list` and standard `post_article` embedded JSON without executing JavaScript.
- List routes follow the frozen `latest post` URLs: page 1 `list,{stock_code},f.html`, page 2+ `list,{stock_code},f_{page}.html`.
- Standard `post_type=0` rows require decimal `post_id`, exact source URL, valid source publication time and matching detail identity. Missing identity, malformed embedded JSON, invalid timestamps and list/detail semantic drift fail closed.
- `post_publish_time`, `post_last_time` and `post_display_time` remain separate timezone-aware values using the approved UTC+08:00 source interpretation.
- Requested bar and canonical bar are retained separately. Alternate nonzero post types are retained in fetched page evidence and counted, but not emitted under the approved scope.
- `src/myresearcher_collector/sources/eastmoney_guba/collector.py` provides injectable HTTP transport, retry classification for timeout/429/403/5xx, page/detail counters, overlap idempotency by `(source, source_item_id)`, watermark confirmation, explicit terminal statuses and a developer-only in-memory raw evidence store.
- Source items carry `schema_version`, `source_metadata.extra`, final URL,
  identity-content drift counters and immutable `observation_version` values.
- Redirects are restricted to approved HTTPS Eastmoney hosts; bounded backoff,
  numeric `Retry-After`, minimum request interval, cancellation and partial-run
  watermark protection are enforced at the source-isolated boundary.
- `src/myresearcher_collector/cli/` provides the minimal `eastmoney-guba` command boundary. It returns nonzero for partial/failure/spec-mismatch outcomes and does not implement DataClean persistence.

## Fixtures and tests

Fixtures are synthetic and deterministic under `tests/fixtures/eastmoney_guba/`; they contain no live response, credential, cookie or unnecessary personal data.

Developer tests cover:

- valid list/detail parsing and source time separation;
- empty body preservation without title fallback;
- missing identity, malformed payload, invalid timestamp and list/detail mismatch;
- page overlap and acquisition-level idempotency;
- alternate post-type counting;
- valid empty page → `NO_NEW_DATA`;
- watermark confirmation with no detail request;
- 403, 429, 503 and timeout retry/failure behavior;
- later-page and detail failure → `PARTIAL_COLLECTION`;
- first-page schema drift → `SPEC_MISMATCH`;
- raw evidence references and counter reconciliation.
- cross-bar/nullable/missing-count fields, empty-title preservation and optional
  time field errors;
- retry exhaustion, `Retry-After`, interval bounds, redirect policy,
  cancellation and partial-run watermark protection.

## Exact validation commands

```text
PYTHONPYCACHEPREFIX=/tmp/myresearcher-datacollector-pyc python -m compileall -q src tests
PYTHONDONTWRITEBYTECODE=1 python -m pytest --collect-only -q
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python -m myresearcher_collector.cli --help
```

Observed result at correction handoff: `25 passed`, `25 collected`, compile
succeeded, CLI help succeeded. Pytest may report a non-blocking cache-permission
warning because this checkout's `.pytest_cache` is not writable.

## Changed files

- `README.md`, `tests/README.md`
- `src/myresearcher_collector/__init__.py`
- `src/myresearcher_collector/models/__init__.py`, `models/runtime.py`
- `src/myresearcher_collector/sources/__init__.py`
- `src/myresearcher_collector/sources/eastmoney_guba/__init__.py`, `collector.py`, `parser.py`
- `src/myresearcher_collector/cli/__init__.py`, `cli/__main__.py`, `cli/main.py`
- `tests/fixtures/eastmoney_guba/*`
- `tests/unit/test_eastmoney_guba_parser.py`, `tests/unit/test_eastmoney_guba_collector.py`
- `runs/phase-01-round-02/scope.md`, `status.txt`, `implementation-notes.md`, `handoff.md`

## Known limitations and contract blockers

- OQ-01/OQ-02/OQ-03 remain open: no final DataClean entry point, durable `raw_ref` manifest, persistence backend or public cross-project envelope was invented.
- OQ-04 remains open: no production scheduling/rate approval or live collection claim is made.
- Replies and alternate post types remain outside the approved Source Spec item scope.
- Historical-tail completeness, deletion retention and official rate limits remain unknown.
- This handoff does not claim independent Tester acceptance.

## Security and scope

- No credentials, cookies, authorization headers or live response bodies were added.
- No Xueqiu code, browser automation, WAF/CAPTCHA bypass, DataClean code, database, scheduler, sentiment, finance or trading logic was added.
- No live network request was used by the tests.
