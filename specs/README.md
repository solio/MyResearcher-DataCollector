# Source Specifications

Every production Source Adapter must have one approved same-name SOURCE_SPEC in this directory.

Example future mapping:

```text
specs/example_source.md
        ↕ 1:1
src/myresearcher_collector/sources/example_source/
```

Workflow:

```text
Source Researcher evidence
        ↓
SOURCE_SPEC review
        ↓
Developer implementation
        ↓
Tester verification
```

Use `_template.md`. Do not create a source spec from memory, do not store credentials, and do not treat an unresolved field or behavior as confirmed.

Phase 0 contains no approved concrete source spec.
