"""Walk a bar's list pages the way a reader does: by clicking the pager.

WHY
---
Backfill walks `list,<bar>,f.html`, `f_2.html`, ... and fetches each one with
`page.goto`. Every one of those is a **typed** navigation: the browser sends no
`Referer` and `Sec-Fetch-Site: none`. `scripts/ops/referer_probe_selftest.py`
measured exactly that shape as the thing that separates a scripted walk from a
human one (README, arms A/B/C). A reader instead lands on page 1 and clicks
"下一页", and the browser then issues a **same-origin** navigation carrying a
`Referer`.

This module supplies only that click: find the pager anchor the page has already
rendered, check that it points at the page we want, click it.

WHAT IT DOES NOT DO
-------------------
It does not decide policy, does not retry, and does not fall back -- the
transport owns that, so the fallback can be counted. It also does not wait: the
pager is server-rendered (see below), so the anchor is there at
`domcontentloaded` and the caller only has to wait for the navigation.

SERVER-RENDERED PAGER
---------------------
Measured against a captured body
(`data/raw/eastmoney_guba/0500aef7b343ba922ecf8360c3bc8d5ee3914983b31323bec66683e8243f6d03.body`):
`<ul class="paging"><li><a class="nump" href="…/list,601012,f_96.html">` … and
`<a class="nextp" href="…/list,601012,f_99.html">`, alongside a
`跳转至<input name="pager_num">` jump box. The next-page anchor is therefore in
the document the collector has already parsed -- no client-side wait is needed,
and the page walk stays 1:1 with `collector.list_url`'s sequence
(`range(start_page, page_limit + 1)`), which is strictly sequential.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

GUBA_HOST = "guba.eastmoney.com"

# Page 1 is `f.html`; page >= 2 is `f_<n>.html`. Same two shapes the collector
# builds; `test_list_page_url_matches_the_collector_definition` pins them
# together so the two copies cannot drift.
LIST_PATH_RE = re.compile(r"^/list,([^,]+),f(?:_([0-9]+))?\.html$")

# Click the pager anchor whose own href is the page we asked for, and report what
# was done. A click has no return value, so this return value is the **only**
# instrument that can tell "we clicked the right anchor" from "there was nothing
# to click" -- without it a silent fallback to `goto` would be indistinguishable
# from a successful click in every downstream log.
#
# MEASURED, not reasoned (2026-09-23, live 3-page walk on 601012, `backfill
# --list-click-paging --max-pages 3`): the request this click issued carried
#
#     referer:        https://guba.eastmoney.com/list,601012,f.html
#     sec-fetch-site: same-origin
#     sec-fetch-mode: navigate
#     sec-fetch-user: ?1
#
# That is the shape a reader's click produces, and it is the whole point: the same
# page requested by URL carries no `Referer` and `sec-fetch-site: none` (both
# samples are in that run's report). The `?1` is worth noting because it was
# *predicted wrong* here first -- a script-issued `HTMLElement.click()` was
# expected to arrive without `Sec-Fetch-User`. It does not, and the comment now
# records the measurement instead of the guess.
CLICK_NEXT_LIST_PAGE_JS = """
(args) => {
  const seen = [];
  for (const anchor of document.querySelectorAll('a.nextp')) {
    const href = String(anchor.getAttribute('href') || '');
    seen.push(href);
    const match = /\\/list,[^,]+,f(?:_(\\d+))?\\.html/.exec(href);
    if (!match) { continue; }
    const page = match[1] ? parseInt(match[1], 10) : 1;
    if (page !== args.page) { continue; }
    anchor.click();
    return {clicked: true, href: href, requested_page: args.page, seen: seen};
  }
  return {clicked: false, href: null, requested_page: args.page, seen: seen};
}
"""


# The counters a run reports. Kept here, next to the navigation they describe, so
# the transport's counter dict and the run report cannot disagree about the names.
#
#   click            the pager anchor was clicked and the page it opened was served
#   goto             the page was requested by URL instead (`f_<n>.html`)
#   click_no_anchor  no pager anchor on the page pointed at the page asked for
#   click_not_taken  the click was issued but the browser did not navigate
#
# `goto` is counted for list pages whether or not the click scheme is on, so a run
# with the scheme OFF (`click: 0`, `goto: N`) is the negative control that makes a
# run with it ON (`click: N`) a measurement rather than an assumption.
NAVIGATION_COUNTS = ("click", "goto", "click_no_anchor", "click_not_taken")


def navigation_report(
    counts: object, samples: object = None, *, include_samples: bool = True
) -> dict:
    """Normalise a transport's paging counters into the shape the run report uses.

    Every counter is always present, defaulting to 0: a *missing* key reads as
    "not measured" while an explicit 0 reads as "measured, did not happen", and
    only the second one can serve as a negative control.
    """
    source = counts if isinstance(counts, dict) else {}
    report: dict = {
        mode: int(source.get(mode) or 0) for mode in NAVIGATION_COUNTS
    }
    if include_samples:
        raw = samples if isinstance(samples, dict) else {}
        report["samples"] = {
            str(mode): dict(shape)
            for mode, shape in raw.items()
            if isinstance(shape, dict)
        }
    return report


def list_page_parts(url: str) -> tuple[str, int] | None:
    """`(bar, page)` for a bar list URL, else None.

    `None` means "this is not a paginated list URL" -- a detail page, a caifuhao
    URL, another host, or a path shape we do not recognise. Callers must treat it
    as "leave this URL on the direct path".
    """
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.hostname != GUBA_HOST:
        return None
    match = LIST_PATH_RE.match(parsed.path)
    if match is None:
        return None
    return match.group(1), int(match.group(2) or 1)


def list_page_url(bar: str, page: int) -> str:
    """The URL for `page` of `bar`'s list; the same shape `collector.list_url` builds."""
    if page == 1:
        return f"https://{GUBA_HOST}/list,{bar},f.html"
    return f"https://{GUBA_HOST}/list,{bar},f_{page}.html"
