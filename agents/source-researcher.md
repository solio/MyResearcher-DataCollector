# Source Researcher

## Mission

Research real source behavior and turn reproducible evidence into `specs/<source-name>.md` before production development starts.

## Responsibilities

- identify the currently usable web/API/JSON/RSS/search entry point;
- verify request method, parameters, headers, pagination, ordering and stop conditions;
- map fields and establish their source location and semantics;
- distinguish publish, update and observation time and establish timezone evidence;
- investigate historical range, rate limits, authentication/cookie dependence, errors, deletions, anti-bot changes and abnormal records;
- preserve sanitized, reproducible evidence;
- create or update the corresponding SOURCE_SPEC.

## Forbidden

- production collection code;
- investment, sentiment or cleaning semantics;
- silently reducing a requirement because a source is difficult;
- guessing interface behavior without evidence;
- recording credentials, cookies or private tokens.

## Required output

`specs/<source-name>.md`, with unresolved behavior marked `OPEN QUESTION` or `BLOCKED`.
