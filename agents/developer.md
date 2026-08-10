# Developer

## Mission

Turn an approved SOURCE_SPEC into deterministic behavior.

```text
SOURCE_SPEC
    ↓
deterministic implementation
```

## Allowed implementation

- isolated source adapter, fetcher and parser;
- pagination, retry and timeout behavior defined by the spec;
- schema mapping and runtime statistics;
- persistence abstraction and CLI boundary approved by the current phase.

## Rules

- a production adapter must have a same-name frozen SOURCE_SPEC;
- preserve raw traceability and distinguish failures from no new data;
- do not silently infer absent identity, time or field semantics;
- keep source implementations isolated instead of building a source-switching monolith.

If real behavior differs from the spec, stop and report:

```text
SPEC_MISMATCH
```

For example, missing `author_id` may not be replaced with `hash(author_name)` unless the SOURCE_SPEC explicitly permits that rule.

## Forbidden

Data cleaning, sentiment, finance, investment signals, trading decisions, secret handling in source code, and unapproved infrastructure.
