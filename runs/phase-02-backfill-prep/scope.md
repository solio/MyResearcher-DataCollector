# Phase 2 Backfill v0.1 — Developer Scope

This run implements only deterministic single-stock Backfill orchestration for
the approved Eastmoney source. It reuses the existing Collector, Parser,
RawEvidence and SQLite observation boundary. Forward checkpoint advancement,
schema changes, resume cursors, live batch execution and Xueqiu live host work
are out of scope.

The inclusive range is `[from_time, to_time]`. A backfill traverses sequential
pages from newest to oldest and only stops after a complete successful page is
older than `from_time`. `to_time` items are retained as list evidence but do
not trigger detail acquisition.
