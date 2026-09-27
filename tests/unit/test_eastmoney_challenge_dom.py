"""Deterministic tests for the shared challenge detector."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from myresearcher_collector.sources.eastmoney_guba import parser
from myresearcher_collector.sources.eastmoney_guba.challenge_dom import (
    CHALLENGE_TITLES,
    DOM_CHALLENGE_JS,
    STRUCTURAL_TOKENS,
    TEXT_TOKENS,
    TITLE_CONTAINS_MAX_LEN,
    asset_tokens,
    challenge_reasons,
    structural_reasons,
    text_reasons,
)

CORPUS = Path(__file__).resolve().parents[2] / "data" / "raw" / "eastmoney_guba"


def _dom(**overrides) -> dict:
    dom = {
        "url": "https://guba.eastmoney.com/news,601012,1.html",
        "title": "某个帖子标题",
        "text_tokens": [],
        "structural_tokens": [],
        "visible_overlays": [],
        "visible_text_head": "",
    }
    dom.update(overrides)
    return dom


def _dom_from_bytes(html: str) -> dict:
    """Approximate what DOM_CHALLENGE_JS would report for a served page.

    Substring scans over the raw body stand in for the scan over
    `document.documentElement.outerHTML`; for the tokens in play the two agree,
    because every marker involved is literal markup that survives parsing.
    """
    title_match = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    hay = html.lower()
    return {
        "title": (title_match.group(1).strip() if title_match else ""),
        "structural_tokens": [t for t in STRUCTURAL_TOKENS if t.lower() in hay],
        "text_tokens": [t for t in TEXT_TOKENS if t in html],
        "visible_overlays": [],
    }


def test_a_clean_page_has_no_reasons() -> None:
    assert challenge_reasons(_dom()) == []


def test_a_visible_overlay_container_is_a_structural_reason() -> None:
    reasons = challenge_reasons(_dom(visible_overlays=["[class*=captcha]"]))
    assert reasons == ["visible_overlay:[class*=captcha]"]
    assert structural_reasons(reasons) == reasons
    assert text_reasons(reasons) == []


def test_a_challenge_asset_alone_is_not_a_block() -> None:
    """The false positive that halted an enrich run on candidate #1.

    Every page ships `<script src=".../em_capt.js">` so the overlay can be
    raised on demand. Firing on it flagged the whole corpus.
    """
    dom = _dom(structural_tokens=["em_capt.js"])
    assert challenge_reasons(dom) == []
    assert asset_tokens(dom) == ["em_capt.js"], "still visible as diagnostics"


def test_a_challenge_asset_corroborates_a_title_but_does_not_decide() -> None:
    reasons = challenge_reasons(
        _dom(title="身份核实", structural_tokens=["fd_guba_validate", "em_capt.js"])
    )
    assert reasons == [
        "title:身份核实",
        "dom_asset:fd_guba_validate",
        "dom_asset:em_capt.js",
    ]


def test_a_challenge_asset_corroborates_a_visible_overlay() -> None:
    reasons = challenge_reasons(
        _dom(visible_overlays=["#emcaptcha"], structural_tokens=["emcaptcha"])
    )
    assert reasons == ["visible_overlay:#emcaptcha", "dom_asset:emcaptcha"]


@pytest.mark.parametrize("title", CHALLENGE_TITLES)
def test_a_challenge_title_is_a_structural_reason(title: str) -> None:
    assert challenge_reasons(_dom(title=title)) == [f"title:{title}"]


def test_a_short_title_merely_containing_a_challenge_word_is_flagged() -> None:
    assert challenge_reasons(_dom(title="请完成验证")) == ["title_contains:请完成验证"]


def test_a_headline_containing_a_challenge_word_is_not_flagged() -> None:
    """A detail page's title is the article headline; a post about 验证 is not a block."""
    headline = "今天验证了一下这个套利逻辑到底成不成立，先说结论_某股(600000)股吧_东方财富网股吧"
    assert len(headline) > TITLE_CONTAINS_MAX_LEN
    assert challenge_reasons(_dom(title=headline)) == []


def test_the_2026_08_13_overlay_is_caught_by_visible_text_alone() -> None:
    """The regression this detector exists for.

    The overlay's own words are 拖动下方滑块完成拼图, it carries no challenge
    asset and does not change the title, so it is only visible in the rendered
    text. `滑块`/`拼图` must therefore be in the token list -- they are absent
    from `parser._ACCESS_MARKERS`, which is why the old oracle missed it.
    """
    reasons = challenge_reasons(_dom(text_tokens=["滑块", "拼图"]))
    assert reasons == ["visible_text:滑块", "visible_text:拼图"]
    assert structural_reasons(reasons) == []
    assert text_reasons(reasons) == reasons


def test_text_tokens_are_in_the_list_that_catches_the_overlay() -> None:
    assert "滑块" in TEXT_TOKENS
    assert "拼图" in TEXT_TOKENS


def test_text_reasons_can_be_excluded_for_structural_only_callers() -> None:
    dom = _dom(title="身份核实", structural_tokens=["emcaptcha"], text_tokens=["滑块"])
    assert challenge_reasons(dom, include_text=False) == [
        "title:身份核实",
        "dom_asset:emcaptcha",
    ]


def test_a_missing_or_broken_probe_result_is_not_a_block() -> None:
    assert challenge_reasons({}) == []
    assert challenge_reasons(None) == []  # type: ignore[arg-type]


def test_the_browser_probe_is_valid_javascript() -> None:
    assert DOM_CHALLENGE_JS.strip().startswith("() =>")
    assert "innerText" in DOM_CHALLENGE_JS, "visible text is the point"
    assert "getBoundingClientRect" in DOM_CHALLENGE_JS, "hidden templates must not count"


@pytest.mark.skipif(not CORPUS.exists(), reason="captured corpus is not in the repo")
def test_the_captcha_asset_is_on_every_real_page_so_it_cannot_decide() -> None:
    """Positive control against the corpus, not against a fixture I wrote.

    If this ever fails because the marker *disappeared*, the corroboration rule
    is no longer what keeps the detector honest -- re-measure before relaxing it.
    """
    bodies = sorted(CORPUS.glob("*.body"))
    assert bodies, "expected captured responses"
    with_asset = 0
    for path in bodies:
        html = path.read_text(encoding="utf-8", errors="replace")
        if "em_capt.js" in html.lower():
            with_asset += 1
    assert with_asset == len(bodies), (
        "em_capt.js is the reason a lone asset must not decide; "
        f"it was found on {with_asset}/{len(bodies)} bodies"
    )


@pytest.mark.skipif(not CORPUS.exists(), reason="captured corpus is not in the repo")
def test_real_normal_detail_pages_are_never_reported_as_blocked() -> None:
    """The end-to-end control: real bytes in, no block out."""
    normals = [
        p
        for p in sorted(CORPUS.glob("*.body"))
        if "var post_article=" in p.read_text(encoding="utf-8", errors="replace")
    ]
    assert normals, "expected at least one normal detail page in the corpus"
    for path in normals:
        html = path.read_text(encoding="utf-8", errors="replace")
        assert parser.is_access_block_page(html) is False, path.name
        reasons = challenge_reasons(_dom_from_bytes(html))
        assert reasons == [], f"{path.name}: {reasons}"
        assert asset_tokens(_dom_from_bytes(html)), (
            f"{path.name}: expected the captcha asset to be present -- if it is "
            "gone this test no longer exercises the false positive"
        )
