# Runtime Contract

## Purpose

Future Collector runs must be observable enough to distinguish source state, transport failure, parse failure and record output without inspecting logs manually.

## Provisional run envelope

| Field | Type | Required | Meaning |
|---|---|---:|---|
| `run_id` | string | yes | unique run identity |
| `source` | string | yes | canonical SOURCE_SPEC name |
| `start_time` | UTC timestamp | yes | run start |
| `end_time` | UTC timestamp | yes | run completion |
| `requests_total` | integer | yes | all request attempts |
| `requests_success` | integer | yes | transport/protocol successes |
| `requests_failed` | integer | yes | failed request attempts |
| `pages_requested` | integer | yes | requested pages/cursors |
| `pages_success` | integer | yes | successfully received pages |
| `pages_failed` | integer | yes | failed pages |
| `records_received` | integer | yes | source items observed before parsing |
| `records_parsed` | integer | yes | records emitted under the raw contract |
| `records_failed` | integer | yes | item parse failures |
| `first_item_time` | timestamp/null | yes | earliest observed source item time if known |
| `last_item_time` | timestamp/null | yes | latest observed source item time if known |
| `status` | enum | yes | explicit terminal outcome |
| `failure_reason` | string/null | yes | machine-/human-readable failure summary |

Field names and serialization remain `PROVISIONAL` until Phase 1; the semantic distinction below is frozen.

## Frozen status distinction

- `NO_NEW_DATA`: acquisition completed according to SOURCE_SPEC and yielded no new eligible raw source item.
- `COLLECTION_FAILED`: acquisition could not establish a successful no-data outcome.

`0 records` alone is never a valid terminal explanation.

Phase 1 must also define success, partial success, parse failure and cancellation vocabulary without collapsing them into these two states.

## Counter invariants

Counters are non-negative. Success plus failure counts must reconcile with totals according to the SOURCE_SPEC's retry semantics. Missing measurements remain unknown rather than silently defaulted when they cannot be established.

## Logging safety

Runtime output must not contain credentials, cookie values, authorization headers, raw secrets or unnecessary personal data. Errors must remain useful without echoing request secrets.
