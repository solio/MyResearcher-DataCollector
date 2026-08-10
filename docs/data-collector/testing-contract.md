# Testing Contract

## Minimum adapter gate

Every Source Adapter must pass:

```text
SOURCE_SPEC validation
        +
parser unit tests
        +
sanitized fixture regression
        +
pagination tests
        +
error-behavior tests
        +
integration smoke test
```

## Deterministic boundary

Tests prefer cropped fixtures, golden outputs, snapshots, local fakes and exact assertions. Production network availability must never be the only test path. Unit and fixture tests must not access the network.

## Required behavior classes

- normal, empty, malformed and partial responses;
- explicit collection failure versus `NO_NEW_DATA`;
- source identity duplicate and collision cases;
- pagination ordering, stop boundary and overlap;
- timeout, retry limit, 403, 429 and 5xx according to SOURCE_SPEC;
- missing fields, anonymous/deleted/pinned/reposted items where applicable;
- incremental acquisition and idempotent replay;
- schema and provenance version output.

## Fixture rules

- retain the smallest structure needed to reproduce parsing behavior;
- remove credentials, cookies, tokens and unnecessary personal data;
- document source, observation date, sanitization and expected output;
- do not hand-edit fixtures in a way that invents source behavior;
- fixture updates require an explanation of source or contract change.

## Tester result

The Tester reports only `TEST_PASS` or `TEST_FAIL` with evidence. Test pass proves implementation conformance within the tested boundary; it does not prove source stability, production coverage, data quality or downstream research value.

## Phase 0 baseline

Phase 0 contains directories and rules only. Zero collected business tests is acceptable; syntax/project configuration errors are not.
