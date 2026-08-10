# Phase 1 Source Comparison

Decision date: 2026-08-10

Decision scope: the first minimal end-to-end Collector engineering loop, not permanent source priority.

## Comparison

| Criterion | Eastmoney Guba | Xueqiu | Evidence class / engineering consequence |
|---|---|---|---|
| Current accessibility | Public list and detail HTML returned usable source data without authentication in bounded observations. | Search JSON returned HTTP 400/error `400016`; stock page returned a WAF challenge shell. | `CURRENT SOURCE FACT` — Guba is presently reproducible; Xueqiu is not anonymously reproducible. |
| Authentication dependency | None observed for the approved top-level post surfaces. | Anonymous and homepage-cookie attempts both failed; authenticated behavior was not tested. | `CURRENT SOURCE FACT`; Xueqiu auth requirement remains `UNKNOWN`, but anonymous acquisition is currently blocked. |
| Anti-bot risk | Legacy false positives and mitigations exist; no block occurred in the bounded current sample. | Current public page is behind an active JavaScript WAF challenge. | `LEGACY FACT` + `CURRENT SOURCE FACT`; both require observability, Xueqiu has the stronger reproduced barrier. |
| Pagination clarity | Explicit page-number routes, observed page size 80 and “latest post” sort route. Two-page overlap was reproduced while the head changed. | Legacy search used page numbers, but no current successful response established their behavior. | Guba supports a testable overlap policy; Xueqiu pagination is `UNKNOWN`. |
| Historical availability | Large source-reported count and multiple pages exist, but earliest accessible date and maximum depth are not established. | Not established. | Both have unknown long-tail history; Guba at least exposes a traversable surface. |
| Incremental reliability | Latest-post ordering exposes publish and last-update separately; overlap plus ID idempotency can be specified. Moving page boundaries still prevent a completeness guarantee without coverage metrics. | Cannot currently establish a usable ordering or cursor. | `CURRENT SOURCE FACT` + `INFERENCE`; Guba can support an honest bounded incremental contract. |
| Field completeness | List metadata exposes IDs, author, precise times, counts, type/state and bar identity; standard `post_type=0` detail exposes body and additional metadata. Replies and alternate post types are retained in list raw evidence but not approved as emitted Phase 1 items. | Legacy code expected rich status data, but no current response was available to confirm it. | Guba standard top-level posts are sufficiently complete for Phase 1; Xueqiu is `UNKNOWN` today. |
| Item identity stability | Numeric post ID is repeated in list metadata, URL and detail metadata. | Legacy output discarded source identity; current identity could not be observed. | Guba has a reproducible identity candidate. |
| Timestamp reliability | Exact publish, last-update and display fields are separate; `+08:00` alignment was observed. | Legacy used host-local conversion; current source timestamp semantics were not observed. | Guba prevents the known “last active = published” error. |
| Raw traceability | Canonical detail URL and retained HTML snapshot can trace every accepted top-level item. | No currently usable public item URL/body pair was established. | Guba has a concrete replay path. |
| Fixture feasibility | Small list/detail HTML fixtures can be sanitized while preserving embedded JSON and malformed/partial cases. | A WAF challenge/error fixture is feasible, but no successful item fixture is currently evidence-backed. | Guba can support positive and negative deterministic tests now. |
| Testability | Page overlap, missing ID, invalid embedded JSON, detail mismatch and partial page/detail failure are locally modelable. | Only failure-path behavior can be modeled from current evidence. | Guba can exercise the full Research → Spec → Develop → Fixture → Test loop. |
| Long-term maintenance risk | Medium: undocumented HTML/embedded JSON and anti-bot exposure, but clear source-owned fields and public routes. | High: current WAF/session barrier, unverified auth lifecycle and no successful current schema. | `INFERENCE` based on observed access surfaces. |
| Value to MyResearcher | High-volume retail discussion and objective engagement/author metadata; known legacy value. | Potentially differentiated authors/status types and engagement, but current availability is unproven. | Both remain strategic; Phase 1 choice is an engineering sequencing decision. |

## Four-question result

### Eastmoney Guba

1. `ACCESSIBILITY`: **YES, bounded public top-level post acquisition reproduced.**
2. `COMPLETENESS`: **SUFFICIENT FOR STANDARD TOP-LEVEL POST PHASE 1**, using list metadata plus `post_type=0` detail body. Replies, alternate post types and guaranteed deletion history are outside the emitted-item scope but remain visible in raw page evidence and coverage metrics.
3. `RELIABILITY`: **BOUNDED, NOT GUARANTEED**. Page movement and possible anti-bot failures require overlap and explicit `PARTIAL_COLLECTION`/`COLLECTION_FAILED` outcomes.
4. `TRACEABILITY`: **YES**, through numeric post ID, canonical URL and retained raw list/detail snapshots.

### Xueqiu

1. `ACCESSIBILITY`: **NO FOR THE TESTED ANONYMOUS MODEL**.
2. `COMPLETENESS`: **CANNOT DETERMINE** from a current successful response.
3. `RELIABILITY`: **CANNOT ESTABLISH**; an active WAF/session dependency was reproduced.
4. `TRACEABILITY`: **CANNOT ESTABLISH** for a successful item in this round.

## Decision

```text
FIRST_SOURCE = eastmoney_guba
```

Rationale:

- **CURRENT SOURCE FACT** — Guba currently exposes top-level post identity, author, precise publish/update time, engagement, bar identity and detail content through public source-owned pages.
- **CURRENT SOURCE FACT** — Xueqiu did not yield a successful anonymous item response even after initializing an anonymous homepage session, and its stock page returned a WAF challenge.
- **INFERENCE** — Guba is the only candidate in this round that can validate the complete engineering sequence without introducing authenticated-session handling or access-control work.
- **INFERENCE** — choosing Guba first does not rank its content above Xueqiu. It minimizes uncertainty for the first Collector contract and leaves Xueqiu as the explicitly deferred second source.

## Second source state

```text
xueqiu = RESEARCHED / DEFERRED
SOURCE_SPEC = BLOCKED FOR DEVELOPMENT
```

The blocking evidence is a missing current successful, authorized acquisition model. No Xueqiu production adapter is authorized. A future Source Research round should investigate a reasonable authenticated-session or supported public access model without storing credentials in Git or bypassing WAF/CAPTCHA.
