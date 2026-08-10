# Architecture

## Phase 0 logical boundary

```text
             CLI / Runner
                  │
                  ▼
             Collector Core
                  │
            Source Adapter
                  │
          ┌───────┴─────────┐
          │                 │
        Fetch             Parse
          │                 │
          └───────┬─────────┘
                  ▼
          Raw Record Model
                  │
                  ▼
              Storage
                  │
                  ▼
             DataClean
```

Evidence level: `CONFIRMED — task contract`.

## Component responsibilities

- CLI / Runner: explicit invocation and run-level outcome; no scheduling design is frozen.
- Collector Core: future lifecycle, retry, collection context and statistics.
- Source Adapter: one isolated implementation of one approved SOURCE_SPEC.
- Fetch: deterministic request construction and transport behavior defined by the source spec.
- Parse: source-response structure to raw fields; no content-quality decision.
- Raw Record Model: versioned acquisition envelope, not a cleaned or semantic record.
- Storage: output boundary only; backend is intentionally undecided.
- DataClean: downstream consumer responsible for cleaning and later semantic stages.

## Dependency direction

Source-specific modules may depend on shared core/model interfaces. Shared core must not depend on a concrete source. Collector must not import DataClean implementation or legacy sentiment/investment modules.

## Not approved in Phase 0

- distributed crawlers or microservices;
- message queues, workflow engines or complex schedulers;
- Redis, Kafka, Celery, Airflow or Kubernetes;
- agent runtime inside production collection;
- a universal `crawler.py` with per-site conditional branches;
- any concrete storage technology.
