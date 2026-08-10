# Tests

- `unit/`: deterministic parser, mapping and core behavior tests.
- `integration/`: bounded adapter/core/storage smoke tests using local fakes or authorized isolated dependencies.
- `fixtures/`: cropped and sanitized source responses plus expected outputs.

Production network access must never be the only test mechanism. See `docs/data-collector/testing-contract.md`.

Phase 0 includes only a package-structure smoke test; no real source behavior is claimed.
