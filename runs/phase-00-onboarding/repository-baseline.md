# Repository Baseline

## 1. Observation point

- Date: `2026-08-10`
- Local project path: `/Users/mac/Documents/trae_projects/MyResearcher/MyResearcher-DataCollector`
- Origin: `git@github.com:solio/MyResearcher-DataCollector.git`
- Branch: unborn `main`
- Commit: none; the remote repository was empty when cloned
- Tracked files before bootstrap: 0
- Worktree immediately after the empty clone: clean

Evidence level: `CONFIRMED — repository fact` (`git clone` warning, `git status`, `git remote -v`).

## 2. Classification

Repository state: `EMPTY`.

Reason: the dedicated origin had no commits or tracked files. No DataCollector package, contracts, roles, phase evidence or pyproject existed before bootstrap.

Evidence level: `CONFIRMED — repository fact`.

## 3. Accessible legacy evidence

An independently accessible legacy MyResearcher repository at `/Users/mac/Documents/trae_projects/prompt-engineering` was inspected read-only at commit `d510cc5ddb08215403d932616193af463fb9ffdf`. It uses root-level Python modules and `requirements.txt`, not a `src/` package or `pyproject.toml`. Its README describes a stock-research assistant rather than a standalone acquisition boundary.

Observed modules include:

- collection/search: `searcher.py`, `guba_scraper.py`, `xueqiu_scraper.py`, `quant_scraper.py`;
- orchestration/persistence: `researcher.py`, `scheduler.py`, `database.py`, `backfill.py`;
- downstream or out-of-scope behavior: `content_cleaner.py`, `emotion.py`, `emotion_v2.py`, `emotion_v3.py`, `score_tracer.py`, `dashboard.py`;
- legacy diagnostic/test scripts under `tests/` and `tools/`.

Observed dependencies include `requests`, `schedule`, `curl_cffi`, and `python-json-logger`.

Evidence level: `CONFIRMED — accessible legacy repository fact` (`README.md`, `requirements.txt`, tracked paths).

## 4. Existing collectors and record shapes

The legacy repository contains real collection implementations for Eastmoney Guba and Xueqiu, plus search and quantitative acquisition code. These are evidence and potential migration inputs only; Phase 0 does not copy, refactor, or endorse them as the new Collector implementation.

Observed forum dictionaries use a non-frozen subset of:

- `title`, `url`, `content`;
- `source_type`, `source`;
- `reply_count`, `like_count`, `read_count`;
- `post_time`.

Identity and time behavior are inconsistent across paths: URL is used as a legacy uniqueness key; author identity is absent from the observed forum outputs; some fallback parser paths omit counts and time; local `datetime.fromtimestamp`/formatted strings are used without a frozen timezone contract.

Evidence level: `CONFIRMED — repository fact` (`guba_scraper.py`, `xueqiu_scraper.py`, `database.py`).

## 5. Existing persistence and output

The legacy application writes Markdown and formerly documented JSON outputs, and currently persists integrated research results into SQLite tables. `news_items` stores URL, title, content, source, source type, publication/post time and engagement counters; URL is unique. The same persistence layer also stores research runs, analysis, emotion fields and result relationships.

This is not an approved Collector → DataClean contract because collection facts and downstream analysis are coupled, missing values may be collapsed to empty strings or zero, and no raw payload/provenance envelope is frozen.

Evidence level: `CONFIRMED — repository fact` (`README.md`, `database.py`, `researcher.py`).

No database file or runtime output was opened during this inspection.

## 6. Existing responsibility violations relative to the new project

The accessible legacy application combines acquisition with:

- content cleaning/filtering;
- sentiment scoring;
- investment analysis;
- result rewriting and deduplication;
- scheduling and Dashboard presentation.

Those are existing facts, not deletion targets. The new project must isolate collection and must not import these responsibilities into its contract.

Evidence level: `CONFIRMED — repository fact`.

## 7. Downstream DataClean inspection

An accessible sibling project exists at `../MyResearcher/MyResearcher-DataClean`.

Observed state:

- project status is `PROJECT_BOOTSTRAP`;
- current round is `NO_ACTIVE_ROUND`;
- capability ledger contains governance only and explicitly says data reading/cleaning capabilities do not yet exist;
- DataClean data knowledge lists a unified RAW schema, provenance, timestamp semantics and concrete field contract as provisional/open questions;
- no `src/` or `tests/` implementation was present at the inspected paths.

Therefore DataClean currently provides principles—immutable/replayable RAW data and provenance expectations—but no confirmed executable input schema or entry point that Collector can freeze against.

Evidence level: `CONFIRMED — downstream repository fact` (`AGENTS.md`, `docs/state/`, `docs/knowledge/data.md`, `docs/state/capability-ledger.md`).

## 8. Bootstrap placement decision

The new project is a standalone Git repository at `/Users/mac/Documents/trae_projects/MyResearcher/MyResearcher-DataCollector`, cloned from the user-specified empty origin. It is a sibling of the accessible `MyResearcher-DataClean` project and is not nested in the legacy evidence repository.

Evidence level: `CONFIRMED — user direction + repository fact`, recorded in the project decision log.
