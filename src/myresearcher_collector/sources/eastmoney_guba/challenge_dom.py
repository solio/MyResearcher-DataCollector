"""One definition of "is this page behind a challenge?" for the browser surfaces.

WHY THIS IS A SHARED MODULE
---------------------------
The challenge the operator was looking at on 2026-09-23 was an **overlay**:
a slider modal rendered on top of a fully loaded, normal-looking page, with the
underlying `<title>` and the embedded `post_article` payload left intact
(`runtime/diagnostics/eastmoney-20260813T132249.png`). Two consequences:

* `parser.is_access_block_page` cannot see it -- that function is an *asset*
  signature (`<title>身份核实</title>` plus `em_capt.js` / `validate.js` /
  `emcaptcha` / `fd_guba_validate`), which describes the bare initial shell, not a
  later client-side injection;
* `parse_detail_page` then **succeeds**, so "the content parsed" is not evidence
  that no challenge occurred.

The overlay only exists in the **live DOM**, so the detector has to read the
rendered page. That code was first written inside `scripts/ops/referer_probe.py`,
where a self-test validated it -- but a self-test that validates a *copy* proves
nothing about what the collector runs. Everything is therefore defined here once,
and both the probe and the collector import it.

TWO TIERS, RECORDED SEPARATELY
------------------------------
`structural` reasons are hard evidence -- a visible captcha container, a challenge
`<title>`. `text` reasons are the rendered visible words (`滑块`, `拼图`, ...),
which is what catches the 2026-08-13 overlay but could in principle appear in
ordinary prose. Callers should treat any reason as a block (fail closed) while
**keeping the tier visible** so a text-only hit can be reported as suspected
rather than confirmed.

"Could in principle appear in ordinary prose" stopped being hypothetical on
2026-09-28: it happened, and the prose tier is now the **conjunction of at least
two** tokens. See `challenge_reasons` -- a lone `拼图` was photographed inside a
normal post body, and the block it caused would have ended the run.

A CHALLENGE *ASSET* IS NOT A CHALLENGE (learned 2026-09-23, the hard way)
------------------------------------------------------------------------
The first revision of this module fired `dom_asset:` on a challenge marker
found anywhere in `document.documentElement.outerHTML`. That was wrong, and it
was wrong in the most expensive direction: it made a healthy page look blocked.

`em_capt.js` is a plain `<script src="//cfgpassport2.eastmoney.com/captcha/
scripts/em_capt.js">` in the `<head>` of **every** page template -- the site
ships the captcha so it can pop the overlay on demand. Measured over the
captured corpus (`data/raw/eastmoney_guba/*.body`):

    bodies containing em_capt          117 / 117
    of which confirmed-normal pages      5 /   5   (full `post_article` payload)
    bodies containing 验证码/滑块/拼图    0 / 117

So the asset tier flagged 100% of pages, and an enrich smoke run halted on its
very first candidate -- while the response bytes were a perfectly good article.
`parser.is_access_block_page` has never had this bug because it requires a
challenge `<title>` **and** a marker; the DOM detector must mirror that
conjunction:

    dom_asset:<token>   only ever reported as CORROBORATION, once a challenge
                        title or a visible overlay has already fired.

The asset is kept in the output because "the title said 身份核实 and the shell
carried fd_guba_validate" is useful evidence. It just never decides on its own.

A LAID-OUT OVERLAY IS NOT A VISIBLE ONE (learned 2026-09-28, the same way)
-----------------------------------------------------------------------
`STRUCTURAL_SELECTORS` used to be judged by `getBoundingClientRect()` alone --
"has area, is not `display:none`, not `visibility:hidden`, not `opacity:0`". That
test cannot see **occlusion**, and the site ships its captcha iframe on ordinary
pages: two managed-chromium detail pages were photographed while the selector
`iframe[src*=captcha]` reported "visible", and the reader was looking at the
Eastmoney **APP-download promo modal** on top of it
(`runtime/diagnostics/eastmoney-20260928T091508.png`, `...091721.png`). The one
veto that fired on a genuine slider (`...091536.png`) produced the *same* reason
string, so the two are indistinguishable from the reasons alone.

The consequence is not cosmetic: a manual-verification wait now holds the run
still while it believes a captcha is on screen, so a false positive burns the
full timeout on an advertisement and can halt a job.

Visibility is therefore decided by hit-testing the element's centre
(`document.elementFromPoint`) and graded:

    visible_overlays:  the reader could actually be looking at it  -> gates
    covered_overlays:  laid out but something is in front of it    -> evidence

Only the first gates. The second is recorded so the next occurrence can be told
apart from a real overlay **without** a screenshot.
"""

from __future__ import annotations

import json

# `<title>` values that mean "challenge page".
CHALLENGE_TITLES = ("身份核实", "访问验证", "安全验证", "人机验证")

# Challenge assets. These appear in every known variant of the strict shell.
STRUCTURAL_TOKENS = (
    "emcaptcha", "fd_guba_validate", "em_capt.js", "validate.js",
    "geetest", "nc_1_n1z",
)

# Containers that a challenge overlay is rendered into. Visibility is checked in
# the browser so a hidden template in the page source does not count.
STRUCTURAL_SELECTORS = (
    "#emcaptcha", "[id*=captcha]", "[class*=captcha]", "[class*=verify]",
    "iframe[src*=captcha]", ".geetest_panel", ".nc-container",
)

# Rendered visible text. `滑块`/`拼图` are the 2026-08-13 overlay's own words
# (拖动下方滑块完成拼图) and are deliberately included: no asset marker matches
# that overlay, which is exactly why it went unnoticed for six weeks.
TEXT_TOKENS = (
    "验证码", "身份核实", "人机验证", "安全验证", "访问验证",
    "滑块", "拼图", "请拖动", "请完成", "验证错误",
)

# Title substrings that are suspicious even if not an exact CHALLENGE_TITLES hit.
# The length guard exists because a *detail* page's `<title>` is the article
# headline plus the bar and the site name (the collector itself only enriches
# titles of length >= 40), so a stock-forum post about "验证" would otherwise be
# reported as a challenge. Shell titles are short: the observed one is 4 chars.
TITLE_SUBSTRINGS = ("身份核实", "验证", "人机验证")
TITLE_CONTAINS_MAX_LEN = 30

DOM_CHALLENGE_JS = """
() => {
  const title = document.title || '';
  const html = document.documentElement ? document.documentElement.outerHTML : '';
  const text = (document.body ? document.body.innerText : '') || '';
  const attrHay = (location.href + '\\n' + html).toLowerCase();
  const textHay = (location.href + '\\n' + title + '\\n' + text).toLowerCase();
  const structuralTokens = %s.filter(t => attrHay.includes(t.toLowerCase()));
  const textTokens = %s.filter(t => textHay.includes(t.toLowerCase()));
  const visible = [];
  const covered = [];
  for (const sel of %s) {
    for (const el of document.querySelectorAll(sel)) {
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      if (!(rect.width > 0 && rect.height > 0 &&
            style.visibility !== 'hidden' && style.display !== 'none' &&
            style.opacity !== '0')) continue;
      // OCCLUSION, not just geometry (added 2026-09-28).
      // The site ships a captcha iframe on ordinary pages, so "an overlay exists
      // and is laid out" is NOT "the reader is looking at a challenge". Measured
      // on managed-chromium detail pages: getBoundingClientRect passed while the
      // APP-download promo modal sat on top of the captcha iframe
      // (runtime/diagnostics/eastmoney-20260928T091508.png, ...091721.png), and a
      // 180s manual-verification wait then stalled on an advertisement -- 2 of 3
      // vetoes. Hit-test the centre: the element a click would actually reach.
      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height / 2;
      let topmost = true;
      if (cx >= 0 && cy >= 0 && cx <= window.innerWidth && cy <= window.innerHeight) {
        const hit = document.elementFromPoint(cx, cy);
        // `hit === el` is the overlay itself (an iframe answer comes back as the
        // iframe). `el.contains(hit)` is its own content; `hit.contains(el)` is a
        // wrapper that captured the hit. Anything else is another layer in front.
        topmost = !hit || hit === el || el.contains(hit) || hit.contains(el);
      }
      if (topmost) { visible.push(sel); }
      else { covered.push(sel); }
      break;
    }
  }
  return {url: location.href, title: title, ready: document.readyState,
          structural_tokens: structuralTokens, text_tokens: textTokens,
          visible_overlays: visible, covered_overlays: covered,
          visible_text_len: text.length,
          visible_text_head: text.replace(/\\s+/g, ' ').trim().slice(0, 160)};
}
""" % (
    json.dumps(list(STRUCTURAL_TOKENS), ensure_ascii=False),
    json.dumps(list(TEXT_TOKENS), ensure_ascii=False),
    json.dumps(list(STRUCTURAL_SELECTORS), ensure_ascii=False),
)


def challenge_reasons(dom: dict, *, include_text: bool = True) -> list[str]:
    """Turn a DOM probe result into a list of reasons. Empty list = looks fine.

    Reason strings are prefixed with their tier so a caller can separate
    confirmed from suspected without re-deriving anything:

        ``visible_overlay:`` / ``title:`` / ``title_contains:``  -> structural
        ``dom_asset:``                                           -> structural
                                                                    (corroborating)
        ``visible_text:``                                        -> text

    Only a title or a visible overlay can *start* a block. Everything else --
    assets, occluded overlays, a lone rendered word -- is appended for evidence
    once something has already fired (see the module docstring: a lone
    `em_capt.js` is on every page, and `拼图` is ordinary prose). A block can also
    be started by **two or more** rendered challenge words together, which is the
    real prompt (`拖动下方滑块完成拼图`) and not something prose produces.
    """
    if not isinstance(dom, dict):
        return []
    reasons: list[str] = []

    title = str(dom.get("title") or "").strip()
    gated = False
    if title in CHALLENGE_TITLES:
        reasons.append(f"title:{title}")
        gated = True
    elif (
        len(title) <= TITLE_CONTAINS_MAX_LEN
        and any(token in title for token in TITLE_SUBSTRINGS)
    ):
        reasons.append(f"title_contains:{title[:24]}")
        gated = True

    overlays = list(dom.get("visible_overlays") or [])
    if overlays:
        reasons.extend(f"visible_overlay:{selector}" for selector in overlays)
        gated = True

    if gated:
        reasons.extend(
            f"dom_asset:{token}" for token in (dom.get("structural_tokens") or [])
        )

    # THE TEXT TIER NEEDS A CONJUNCTION TOO (2026-09-28).
    #
    # A single rendered word is not a challenge. `拼图` is an ordinary Chinese
    # word -- "…是整个过程里非常关键的一块拼图" -- and 股吧 posts are full of
    # prose like that. The block path holds the run still for the entire 180s
    # window waiting for a challenge that is not there, the word never
    # disappears (it is in the article), so the wait times out into
    # `access_block`, which the driver treats as fail-closed and uses to end the
    # job. **Measured on a real run 2026-09-28 13:44-13:47: one prose word cost
    # 180s and ended the run with 1666 candidates still outstanding**
    # (trace `runtime/enrich-runs/601012.err`,
    # screenshot `runtime/diagnostics/eastmoney-20260928T134457.png`).
    #
    # Why two tokens and not zero: the genuine prompt is 「拖动下方滑块完成拼图」
    # and carries BOTH 滑块 and 拼图 (see the sliders photographed in
    # `runtime/diagnostics/eastmoney-20260928T091536.png`). Requiring both keeps
    # the 2026-08-13 overlay form detectable -- it had no structural signal, which
    # is why this tier exists -- while a lone word can no longer gate.
    #
    # EVIDENCE DISCIPLINE, because this comment was wrong once already: the first
    # version cited "15 of 16 text-only blocks carried both words", read off the
    # ledger. Those 15 rows all have `source_item_id = "1"` and are **test-fixture
    # rows from 2026-09-23**, written into the production ledger before the
    # fixture was given its own log path. On real traffic the text tier has fired
    # exactly **once**, and that once was this false positive. Any analysis over
    # `eastmoney-detail-enrichment.jsonl` must filter `source_item_id = "1"`.
    #
    # So the text tier now mirrors `dom_asset`: it needs corroboration. Two
    # distinct tokens gate; one is only appended as evidence once something else
    # has already gated.
    text_tokens = list(dom.get("text_tokens") or []) if include_text else []
    if len(text_tokens) >= 2:
        gated = True
    if gated:
        reasons.extend(f"visible_text:{token}" for token in text_tokens)

    # Overlays that are laid out but OCCLUDED (the site's promo modals sit on top
    # of its own captcha iframe). Never gate on these -- seeing them is exactly
    # what stalled a wait on an advertisement -- but say they were there, so the
    # next occurrence is diagnosable from the ledger alone instead of a screenshot.
    if gated:
        reasons.extend(
            f"covered_overlay:{selector}" for selector in (dom.get("covered_overlays") or [])
        )
    return reasons


def asset_tokens(dom: dict) -> list[str]:
    """Challenge assets seen in the DOM. Diagnostic only -- never a block."""
    if not isinstance(dom, dict):
        return []
    return list(dom.get("structural_tokens") or [])


def structural_reasons(reasons: list[str]) -> list[str]:
    return [r for r in reasons if not r.startswith("visible_text:")]


def text_reasons(reasons: list[str]) -> list[str]:
    return [r for r in reasons if r.startswith("visible_text:")]
