# Product Goal

## Mission

MyResearcher-DataCollector provides stable, traceable and repeatable acquisition of external raw data for the MyResearcher data pipeline.

It answers:

> Did we reliably and completely bring external data back, with enough evidence to replay and diagnose the acquisition?

Evidence level: `CONFIRMED — task contract`.

## Correctness priorities

1. Never represent collection failure as no data.
2. Never silently lose acquired data.
3. Never silently misparse a source field.
4. Preserve the original source and acquisition trace.
5. Make coverage and failures observable.
6. Detect source-structure changes.
7. Optimize throughput and performance only after correctness.

## In scope

- fetch external responses through a source-specific adapter;
- parse source structure deterministically;
- map source fields into a versioned raw-record boundary;
- preserve provenance or a durable raw reference;
- expose collection statistics and explicit outcomes;
- hand normalized raw records to DataClean.

## Out of scope

- content quality, filtering or deduplication decisions;
- sentiment, stance, behavior-intent or financial semantics;
- investment classification, scoring or signals;
- backtesting or trading decisions;
- dashboards and research reports;
- infrastructure without evidence of need.

## Long-term success

A source is successful only when its behavior is documented by evidence, its adapter conforms to a frozen SOURCE_SPEC, deterministic fixtures protect parsing behavior, failures are distinguishable from no new data, and every emitted record remains traceable to its acquisition.
