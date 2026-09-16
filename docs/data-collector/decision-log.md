# Decision Log

## D-001

Date: 2026-08-10  
Decision: Use the standalone repository at `/Users/mac/Documents/trae_projects/MyResearcher/MyResearcher-DataCollector` with origin `git@github.com:solio/MyResearcher-DataCollector.git`.  
Reason: The user supplied the canonical local path and dedicated origin; the cloned remote was empty.  
Evidence: User direction, clone result and `git remote -v`.  
Alternatives: Keep the bootstrap as a subtree of the accessible legacy MyResearcher repository. Rejected after the canonical location was clarified.  
Impact: The project has an independent Git history and sits beside MyResearcher-DataClean. Legacy MyResearcher remains read-only evidence only.  
Evidence level: `CONFIRMED — user direction + repository fact`.

## D-002

Date: 2026-08-10  
Decision: Collector owns acquisition, structural parsing, raw traceability and runtime observation only.  
Reason: Cleaning, sentiment, finance and trading responsibilities belong downstream and are explicitly forbidden by the task.  
Evidence: Phase 0 task contract and legacy responsibility inventory.  
Alternatives: Preserve the legacy integrated pipeline as the new architecture. Rejected because it violates the product boundary.  
Impact: New source modules cannot import or implement cleaning, sentiment, investment or Dashboard logic.  
Evidence level: `CONFIRMED — task contract`.

## D-003

Date: 2026-08-10  
Decision: Require one approved same-name SOURCE_SPEC before each production Source Adapter.  
Reason: Source behavior is uncertain and must be resolved through evidence before deterministic implementation.  
Evidence: Phase 0 task contract.  
Alternatives: Implement first and document later; use one universal crawler. Rejected.  
Impact: Phase 0 creates only a template; Phase 1 research precedes implementation.  
Evidence level: `CONFIRMED — task contract`.

## D-004

Date: 2026-08-10  
Decision: Do not select a persistence backend, scheduler or distributed infrastructure in Phase 0.  
Reason: No approved volume, operations or DataClean transport requirement supports such a choice.  
Evidence: DataClean is in bootstrap and has no frozen input entry point or scale evidence.  
Alternatives: SQLite, relational database, document store, queue or object storage. All remain open.  
Impact: `storage/` is a boundary-only package skeleton.  
Evidence level: `CONFIRMED — repository/downstream fact + Phase 0 design decision`.

## D-005

Date: 2026-08-10  
Decision: Freeze acquisition invariants but keep the concrete raw field envelope provisional.  
Reason: DataClean confirms replay/provenance principles but explicitly has no concrete schema; legacy field shapes are inconsistent and cannot be promoted silently.  
Evidence: DataClean state/knowledge files and legacy scraper/database inspection.  
Alternatives: Declare the candidate YAML fields final. Rejected as unsupported.  
Impact: Phase 1 must close the blocking DataClean and first-source schema questions before adapter output is approved.  
Evidence level: `CONFIRMED — downstream/repository fact`; field proposal remains `PROVISIONAL`.

## D-006

Date: 2026-09-16  
Decision: Track the Eastmoney detail-enrichment runbooks as source under `scripts/ops/` (`enrich_all_stocks.sh`, `mop_up.sh`, `check_revisit.py`) instead of leaving them untracked in `runtime/`.  
Reason: `runtime/` is deliberately gitignored as runtime artifacts, but these files are drivers and one read-only guard — reproducible operating logic, not evidence. `AGENTS.md` makes the Git repository the authoritative shared project memory and forbids relying on local/untracked state; an untracked driver cannot be reviewed, diffed, or reproduced by another client. The revisit guard in particular encodes a regression contract (already-marked 404 posts must never be re-requested) that must be durable.  
Evidence: `git ls-files runtime/` returned nothing; both drivers hardcoded an absolute repo path and `check_revisit.py` resolved `parents[1]`, so none could run from a fresh clone.  
Alternatives: `git add -f` under `runtime/` (rejected — defeats the ignore, mixes source with logs/reports/browser profiles); place them in the `scripts/` root (rejected — `scripts/README.md` reserves it for deterministic utilities and these touch network/browser); keep them untracked (rejected — violates D-006's own reason).  
Impact: They move to `scripts/ops/`, each documented in `scripts/ops/README.md`; repo root is now derived from the script location; all outputs continue to land in the gitignored `runtime/` and `data/`. `scripts/README.md` and the root `README.md` layout note point at the new subdirectory.  
Evidence level: `CONFIRMED — repository fact + AGENTS.md collaboration contract`.

## D-007

Date: 2026-09-16  
Decision: Derive the detail-enrichment work list from the database at run time (`scripts/ops/enrich_plan.py`), and make the two concurrent enrichment streams own disjoint stock queues published in a single atomically-replaced `plan.txt`, instead of sharing one list and arbitrating at runtime.  
Reason: Two independent defects surfaced in the same run. (1) `enrich_all_stocks.sh` carried a hardcoded 16-code array frozen on 2026-09-10, still labelled "Ordered by original pending count (desc)"; it made the driver always start on 601888, which read as a bias in the data when 601012 actually held the larger backlog (251 vs 159). (2) `enrich_tail_worker.sh` walked the *same* list from the tail while excluding only the stock the driver was currently holding, leaving the stock the driver was about to take unreserved. Both streams converged on 002028 (tail picked it 05:25:44Z, driver started it 05:29:19Z) and 8 source_item_ids were requested twice. The exclusion rule cannot be repaired: on a shared queue the head→tail and tail→head walks necessarily meet at an unowned point.  
Evidence: `runtime/logs/eastmoney-detail-enrichment.jsonl` — in the ≥05:20Z window, 169 events across 5 run ids with 8 duplicated `source_item_id`s, each appearing once under run id `3cb61f48…` and once under `de83b05a…`; two distinct managed-chromium profile directories (`20260916-052544-661732`, `20260916-052919-566141`) for the same stock; both `002028.json` reports empty because both processes were killed mid-run. No corruption observed (writes are identical-value upserts) and no `database is locked`.  
Alternatives: Keep the hardcoded list and just re-sort it (rejected — it rots again at the next backlog shift, which is exactly what happened here); have the worker exclude the driver's whole remaining queue (rejected — with one shared list that leaves the worker nothing to do); add a runtime claim file with `noclobber` (rejected — still a race against the driver's between-stocks phase, and unnecessary once the queues are disjoint by construction); one orchestrator with a worker pool and a shared rate limiter (deferred — the right end state, but it also has to solve the four concurrency hazards in `scripts/ops/README.md`, which this change deliberately does not).  
Impact: Eligibility SQL now exists in exactly one place (`enrich_plan.py`), so the driver, the worker and ad-hoc queries cannot drift apart. The split is LPT — balanced by pending rows rather than stock count, largest job on the fail-closed stream. `STOCKS="..."` publishes a driver-only plan and makes the worker refuse to start (`TAIL_NO_PLAN`), so a hand-run cannot collide with a background worker. The four concurrency hazards (fail-closed coupling, stream-unaware revisit guard, no WAL/`busy_timeout`, best-effort ledger writes) remain open and are still documented as open.  
Evidence level: `CONFIRMED — repository fact + measured request-log evidence`.
