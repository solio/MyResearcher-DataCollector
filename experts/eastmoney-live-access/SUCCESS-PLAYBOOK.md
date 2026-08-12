# Eastmoney Live Access Success Playbook

This records the successful 2026-08-11 page-semantics proof and a reproducible
non-GUI diagnostic. Revalidation on 2026-08-12 proved that the browser transport
is a sound integration boundary but not a stable unattended access solution.
It is intentionally bounded to normal public browser access.

## What the browser adapter fixed—and did not fix

The URL, pagination, parser and Backfill logic were not the original failure.
The same approved URL returned an HTTP-200 identity-verification shell through
plain urllib/curl but returned `article_list` in a normal anonymous browser.

The correct implementation boundary is:

```text
standard browser navigation
  -> exact approved Eastmoney HTTPS page
  -> explicit verification-page classification
  -> existing structured article_list/post_article parser
  -> list/detail identity check
  -> raw evidence and normal Collector storage path
```

Important details:

- Use the approved latest-post URLs: page 1 is
  `https://guba.eastmoney.com/list,601012,f.html`; page 2 is
  `https://guba.eastmoney.com/list,601012,f_2.html`.
- Parse embedded `var article_list=...` and `var post_article=...`; do not infer
  source timestamps from the rendered five-column table.
- Preserve `None` versus numeric zero and do not invent a title or content.
- Keep `post_type=0`; record other types as out-of-scope evidence.
- Require list/detail ID and identity agreement.
- Use sequential navigation, at least 2.5 seconds apart, with a hard page and
  request budget.  Do not rotate UA or randomize pages.
- A verification page, schema mismatch, repeated complete page signature, or
  transport failure is a collection failure—not “no posts”.

## No-extra-Python-package headless diagnostic

Requirements:

- Python 3.11 or newer.
- This repository checkout.
- A normal installed Google Chrome or Chromium.  No Python browser package is
  required.

From `MyResearcher-DataCollector`:

```bash
python experts/eastmoney-live-access/reproduce_headless.py \
  --stock 601012 \
  --pages 1 \
  --with-detail \
  --json-out /tmp/eastmoney-headless.json \
  --markdown-out /tmp/eastmoney-headless-posts.md
```

On a machine where Chrome is not auto-discovered:

```bash
python experts/eastmoney-live-access/reproduce_headless.py \
  --chrome /absolute/path/to/chrome \
  --stock 601012
```

The script launches standard Chrome with `--headless=new --dump-dom` and a
fresh temporary profile.  It recognizes a complete document explicitly and
then terminates the temporary browser process, so it does not depend on each
Chrome version exiting automatically after `--dump-dom`.  It does not set a browser UA, simulate a TLS
fingerprint, use a stealth plugin/proxy, solve a challenge, or read/export
cookies or browser storage.  The default budget is exactly two navigations:
one list and one detail.

`PASS` here means only that this bounded diagnostic was accepted at that time.
It does not promote Eastmoney to unattended-production-ready. The 2026-08-12
matrix includes fresh-context first-page blocks and recurrent graphical
verification after manual verification.

A source-accepted run returns `status: PASS`, all visited URLs, the captured page
title, source counts, the parsed post list, and a strict detail identity proof.
It deliberately does not persist raw HTML or full post bodies.

The failure statuses are designed for automation:

| Status | Meaning | Exit |
| --- | --- | ---: |
| `ENVIRONMENT_ERROR` | Chrome missing or could not start | 2 |
| `TRANSPORT_ERROR` | Browser timeout/no document | 3 |
| `ACCESS_BLOCK` | Eastmoney returned its verification page | 4 |
| `SOURCE_SCHEMA_MISMATCH` | Approved embedded payload changed/failed identity checks | 5 |
| `PAGINATION_NOT_PROGRESSING` | Optional page 2 repeated page 1's full ID sequence | 6 |

No external website can be guaranteed to return the same live data forever.
What is reproducible here is the environment setup, URL semantics, bounded
navigation, parser, proof checks, and unambiguous outcome classification.
On 2026-08-11, fresh headless Chrome first returned the real list page and a
later run returned `ACCESS_BLOCK`, while the browser-managed context still
returned the real page. See `HEADLESS-VALIDATION.md`. Consequently, headless
mode is a useful deployable option only when the source accepts that context;
it is not the production success criterion by itself.

## Bounded Playwright diagnostic sample

This complete sample exercises `EastmoneyBrowserTransport` with
the same two-navigation budget as the successful proof. `headless=True` is the
requested non-GUI option; if it returns a verification page, stop and report
`ACCESS_BLOCK` rather than bypassing the challenge or claiming success:

```python
import json
import time

from playwright.sync_api import sync_playwright

from myresearcher_collector.sources.eastmoney_guba import (
    EastmoneyBrowserTransport,
    EastmoneyGubaCollector,
    parse_detail_page,
    parse_list_page,
)
from myresearcher_collector.sources.eastmoney_guba.parser import (
    is_access_block_page,
    merge_list_and_detail,
)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, channel="chrome")
    context = browser.new_context()  # no UA override, no stored login state
    page = context.new_page()
    transport = EastmoneyBrowserTransport(page)

    list_url = EastmoneyGubaCollector.list_url("601012", 1)
    response = transport.get(list_url, timeout=30)
    if is_access_block_page(response.text):
        raise RuntimeError("ACCESS_BLOCK")
    parsed_page = parse_list_page(response.text, "601012")
    first = parsed_page.rows[0]

    time.sleep(3)
    response = transport.get(first.url, timeout=30)
    if is_access_block_page(response.text):
        raise RuntimeError("ACCESS_BLOCK")
    merged = merge_list_and_detail(first, parse_detail_page(response.text))

    print(json.dumps({
        "status": "PASS",
        "visited_urls": [list_url, first.url],
        "post_count": len(parsed_page.rows),
        "post_list": [
            {"id": row.source_item_id, "title": row.title, "url": row.url}
            for row in parsed_page.rows
        ],
        "detail_id": merged["source_item_id"],
        "detail_content_length": len(merged["content"]),
    }, ensure_ascii=False, indent=2))

    context.close()
    browser.close()
```

Install only when this service form is needed:

```bash
python -m pip install 'playwright>=1.45,<2'
```

`channel="chrome"` uses the installed stable Chrome.  If using Playwright's
bundled Chromium instead, install it explicitly with
`python -m playwright install chromium` and omit `channel="chrome"`.

The adapter accepts only approved Eastmoney HTTPS hosts, rejects unsafe final
URLs, returns the main-document response to the existing Collector, and does
not export `Set-Cookie`.

For the user-executable long-lived host and Collector commands, see
[2026-08-12-retry-report.md](2026-08-12-retry-report.md). Those commands are
explicitly experimental/operator-assisted and fail closed; they are not a
recipe for unattended production.

## Re-validation checklist

1. Run `reproduce_headless.py` with its default one-page/two-request budget.
2. Confirm `status == "PASS"` and `article_list_rc == 1`.
3. Confirm list rows are present and every accepted row is `post_type == 0`.
4. Confirm the detail proof says `list_detail_id_match == true`.
5. Review `visited_urls`; there must be only the generated list URL and the
   first accepted Eastmoney detail URL.
6. For pagination diagnosis only, rerun with `--pages 2 --no-with-detail`.
   The script accesses the approved `f_2` route sequentially and rejects a
   repeated complete signature.
7. Run the offline suite:

```bash
PYTHONPATH=src:. pytest -q
python -m compileall -q \
  src/myresearcher_collector/sources/eastmoney_guba \
  experts/eastmoney-live-access/reproduce_headless.py
```

The already captured successful page evidence and exact 80-row list remain in
`page-evidence.json` and `post-list.md`.

8. Do not start normal collection or Backfill merely because the two-request
   diagnostic passed. First require a complete, representative normal workload
   to finish without human verification. If verification recurs, stop and mark
   live availability blocked.
