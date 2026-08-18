# Xueqiu DOM integration scope

Role: Developer

This run productizes the Expert-verified public stock-page DOM path for
`source=xueqiu` under the existing backfill command. It is limited to A-share
top-level stock discussions, sequential page traversal, selective detail time
resolution, and the existing `posts`/simple backfill state.

In scope:

- process-isolated normal Chrome + fixed loopback CDP lifecycle and public DOM observation;
- `status_id`/author/content/time/detail URL extraction;
- active-page and ID-sequence pagination checks;
- created-time range filtering and page-level durable upserts;
- deterministic offline tests.

Out of scope: replies, profiles, search, hot feed, HK/US markets, batch
orchestration, DataClean, credential/session export, private APIs, CAPTCHA or
proxy work, and a live batch run. Playwright-managed launch/persistent context
and Apple Events control of the user's Chrome are legacy diagnostics, not the
default production runtime.
