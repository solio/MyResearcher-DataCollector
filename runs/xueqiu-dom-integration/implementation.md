# Xueqiu DOM integration implementation report

## Source evidence used

The implementation follows the already archived Expert evidence in commit
`f5f155ff73d7d37927dc296d4765839b858adf0a` and `experts/xueqiu-live-access/`:
the stock page is `https://xueqiu.com/S/{symbol}`, posts are
`article.timeline__item`, pagination is page-control/ID based while the URL
stays stable, and list text beginning with `修改于` is not publication time.

## Components

- `sources/xueqiu/dom_transport.py`: managed-Chromium page ownership, async
  post wait, DOM extraction, active-page/ID progression, and
  `window.SNOWMAN_STATUS` detail timestamp observation. Modified-post detail
  lookup now creates a temporary page in the same browser context, closes it
  in `finally`, and leaves the main list page at its current page/ID sequence.
- `sources/xueqiu/dom_backfill.py`: modified-post resolution no longer calls
  `restore_page`; it performs a non-navigating main-page state assertion and
  writes resume rows only when the shared plan proves a frozen, resumable
  range.
- `sources/xueqiu/dom_parser.py`: deterministic DOM field and time parsing;
  modified display times remain unresolved until detail lookup.
- `sources/xueqiu/dom_backfill.py`: sequential range traversal, selective
  detail resolution, page transaction/upsert/resume/anchor durability, and
  shared coverage semantics.
- `cli/main.py`: `backfill --source xueqiu` now selects the DOM transport while
  retaining the existing Eastmoney path and plan-only boundary.
- `simple_store.py`: optional `created_at` input lets the shared posts store
  preserve source creation time while retaining idempotent `(source,
  source_item_id)` upsert behavior.

The pre-existing JSON/browser-observed Xueqiu path remains available for its
existing contract and tests; it was not deleted or rewritten.

## Safety boundary

The new transport does not construct the old discussion API URL, generate
challenge parameters, export cookies, or persist credentials. It uses the
existing managed Chromium runtime and only accepts the `managed-chromium`
acquisition mode. Pacing is sequential and bounded to a random 3–10 seconds.

## Live state

No live smoke was executed in this correction run, as required by the current
scope. The prior attempted smoke failed during source acquisition before any
post was read; its temporary profile/data directory was removed. No real source
response, user browser profile, credential, or production database was read or
written.
