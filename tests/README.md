# Tests

- `unit/`: deterministic parser, mapping and core behavior tests.
- `integration/`: bounded adapter/core/storage smoke tests using local fakes or authorized isolated dependencies.
- `fixtures/`: cropped and sanitized source responses plus expected outputs.

Production network access must never be the only test mechanism. See `docs/data-collector/testing-contract.md`.

Phase 1 Round 2 includes offline parser/collector behavior tests for the approved Eastmoney Guba source. Tests remain independent of production network availability.
