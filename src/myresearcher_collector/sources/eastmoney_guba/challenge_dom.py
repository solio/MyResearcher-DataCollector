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
  for (const sel of %s) {
    for (const el of document.querySelectorAll(sel)) {
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      if (rect.width > 0 && rect.height > 0 &&
          style.visibility !== 'hidden' && style.display !== 'none' &&
          style.opacity !== '0') {
        visible.push(sel);
        break;
      }
    }
  }
  return {url: location.href, title: title, ready: document.readyState,
          structural_tokens: structuralTokens, text_tokens: textTokens,
          visible_overlays: visible, visible_text_len: text.length,
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

    Only a title or a visible overlay can *start* a block; assets are appended
    for evidence once one of those already fired (see the module docstring --
    a lone `em_capt.js` is on every page and must not decide anything).
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

    if include_text:
        reasons.extend(f"visible_text:{token}" for token in (dom.get("text_tokens") or []))
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
