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
