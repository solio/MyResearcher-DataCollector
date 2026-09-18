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

## D-008

Date: 2026-09-18  
Decision: Add a loud coverage-drift check to `backfill_all_stocks.sh` rather than auto-deriving its stock list, and leave the "which artifact is the stock registry" question open.  
Reason: The 2026-09-18 backfill exposed the same defect class D-007 fixed on the enrichment side: `backfill_all_stocks.sh` carries a literal 16-code array, so a stock pool change would silently drop work. Auto-deriving is not yet safe because no single authoritative registry exists — `backfill_coverage` cannot bootstrap a newly added stock (a new stock has no coverage row yet), and `config/targets.short-term.json` is internally inconsistent (38 codes in `stocks`, 43 in `stock_names`; 002648 / 600312 / 603997 appear only in `stock_names`, which is why D-006's `stock_names` edit was needed). Choosing the wrong source would silently drop a stock from collection, which is a worse failure than a literal list that currently matches reality.  
Evidence: `select stock_code from backfill_coverage` returns exactly the 16 codes in the script's array; `config/targets.short-term.json` was read and the two lists differ in both membership and length.  
Alternatives: Derive from `backfill_coverage` (rejected — cannot bootstrap a new stock, so collection would freeze at today's 16 forever); derive from `posts` (rejected — the 16 are the tracked set, whereas `posts` reflects whatever the last run happened to write, so it cannot detect a *missing* stock, only an extra one); derive from `config/targets.short-term.json` (rejected — the config is self-inconsistent and resolving which of `stocks` / `stock_names` is authoritative is a product decision, not a refactor); warn-only drift check (chosen).  
Impact: every run now prints `DRIFT_CHECK driver_stocks=… coverage_rows=… uncovered_by_driver=…` and emits `WARN_COVERAGE_DRIFT not_in_driver_list=…` when the coverage table knows a stock the driver does not; `DRIFT_CHECK_ONLY=1` runs the checks and exits without collecting. Zero behaviour change to collection itself. Both failure modes were exercised (normal list → no warning; 300487 removed → warning fires).  
Evidence level: `CONFIRMED — repository fact + both branches of the check exercised`.

## D-009

Date: 2026-09-18  
Decision: Give `mop_up.sh` a round-yield measurement and abort on consecutive barren rounds, instead of relying only on a fixed round budget.  
Reason: The wrapper's premise — "a fresh browser session plus a cooldown gives the next round a new chance" — holds for a transient block but not for a source-side rate cap, because a fresh session does not change our IP or reset a server-side counter. On 2026-09-18 the source cut us off after ~70 detail fetches: round 1 yielded 70 rows, then every one of rounds 2–12 yielded exactly **1** row and halted after two instantaneous `access_block` rejections. `MOPUP_MAX_ROUNDS_REACHED` is indistinguishable whether the 12 rounds yielded 70 rows each or 1 row each, so the wrapper could not tell it had stopped making progress and burned 64 minutes for 81 rows.  
Evidence: `runtime/logs/eastmoney-detail-enrichment.jsonl`, window ≥2026-09-18T03:55:54Z — 119 events = 81 `success`, 25 `access_block`, 12 `fetch_failure`, 1 `manual_verification_resumed`. Per-run breakdown: round 1 `{success:70, access_block:3, fetch_failure:1}` over 03:55:56→04:07:09; each of rounds 2–12 `{success:1, access_block:2, fetch_failure:1}` over a ~3.2-minute window. `access_block` responses take 0.20–0.30s (immediate refusal) while the single success per round takes 1.4–1.8s (real page load). Net 47s/row vs the 6.5s/row baseline measured 2026-09-16.  
Alternatives: Raise `MAX_ROUNDS` (rejected — this failure mode does not resolve with more rounds; it likely worsens the cap); raise `COOLDOWN_SECONDS` to e.g. 600 (rejected as the primary fix — it slows the legitimate transient case and still cannot distinguish capped from unlucky); abort on the *first* block (rejected — the driver halts on the first block by design, so that would make `mop_up.sh` a no-op); yield-based barren abort (chosen).  
Impact: every round now logs `MOPUP_HALTED round=… yield=… barren_streak=…`; `MAX_BARREN_ROUNDS` (2) consecutive rounds at or below `MIN_ROUND_YIELD` (1) end the run with `MOPUP_BARREN` and exit 1. Replaying the real 2026-09-18 log, it stops at round 3 instead of 12, saving ~29 minutes and 36 requests. `ALL_DONE` is checked before the yield logic so a legitimately tiny final round is not misread as barren. The residual 192-row backlog is left for a later run at a lower rate; the README documents the block signature and that more retries are counterproductive.  
Evidence level: `CONFIRMED — measured request-log evidence + replay against the real log`.
