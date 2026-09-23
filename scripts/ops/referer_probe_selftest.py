#!/usr/bin/env python3
"""Positive/negative controls for the referer probe's block detector.

WHY THIS EXISTS
---------------
`referer_probe.py` v1-v3 reported `blocked=0` on runs where a slider captcha was
almost certainly on screen, because the only oracle was
`parser.is_access_block_page`, an *asset* signature (`<title>身份核实</title>` plus
`em_capt.js`/`validate.js`/`emcaptcha`). The observed block
(`runtime/diagnostics/eastmoney-20260813T132249.png`) is instead an **overlay**
injected on top of a fully loaded, normal-looking page -- underlying title and
`post_article` payload intact -- so both the block check AND the content check
said "fine".

Claiming the v4 detector fixes that is itself a claim that needs a control. This
script builds three local fixtures and asserts the classifier's answer on each,
with no network traffic at all:

  F1  real page, no overlay                 -> NOT blocked, content parses
  F2  real page + overlay whose class contains `captcha` AND slider text
                                            -> blocked (class + text signals)
  F3  real page + overlay with an INNOCENT class name, neutral title, and the
      only distinguishing feature being the visible words
      「拖动下方滑块完成拼图」
                                            -> blocked via `visible_text` ONLY

F3 is the case v3 missed by construction; if F3 does not fire, v4 does not fix
anything and this script fails.

`parse_detail_page` is run on all three so the script also demonstrates why the
content check cannot be used as the block check: it succeeds even on F2/F3.

USAGE
    python scripts/ops/referer_probe_selftest.py [--headful]
Exit 0 = every fixture matched its expected verdict.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))


def _load_probe():
    spec = importlib.util.spec_from_file_location(
        "referer_probe", REPO / "scripts/ops/referer_probe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROBE = _load_probe()

REAL_BODY = """
<div id="pageContent">
  <h1>昨晚美股光伏板块又放大量暴涨3%!逢低买</h1>
  <div class="article-body">正文正文正文正文正文正文正文正文正文正文。</div>
</div>
<!-- The embedded payload marker must have NO space before its equals sign --
     parser._embedded_json searches for the assignment form with the `=` glued
     on. Spacing it out silently makes the fixture unparseable, which would fake
     a "content is broken" result and hide the very thing this fixture exists to
     show. This comment is also deliberately phrased so it does NOT contain that
     literal marker: an earlier version did, and `find()` matched the comment
     instead of the script. -->
<script>
var post_article={"post_id":"1776571734","post_type":0,"post_title":"昨晚美股光伏板块又放大量暴涨3%!逢低买","post_content":"正文正文正文正文正文正文正文正文正文正文。","post_publish_time":"2026-09-23 07:08:18","post_guba":{"stockbar_code":"601012","stockbar_name":"隆基绿能吧"},"post_user":{"user_id":"123","user_nickname":"某人"}};
</script>
"""

# F2: a captcha-looking container. Both the class selector and the visible text
# should fire.
OVERLAY_LOUD = """
<div class="em_captcha_modal" style="position:fixed;left:200px;top:150px;width:360px;height:280px;background:#fff;z-index:9999">
  <p style="font-size:15px">拖动下方滑块完成拼图</p>
</div>
"""

# F3: THE regression fixture. Innocent class, innocent id, no captcha asset, no
# title change -- the ONLY signal is the rendered text. v3 was blind here.
OVERLAY_QUIET = """
<div id="zzz9" style="position:fixed;left:200px;top:150px;width:360px;height:280px;background:#fff;z-index:9999">
  <p style="font-size:15px">拖动下方滑块完成拼图</p>
</div>
"""

TEMPLATE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{title}</title></head>
<body>{body}{overlay}</body></html>
"""

FIXTURES = [
    {
        "name": "F1 真页面（负对照）",
        "title": "昨晚美股光伏板块又放大量暴涨3%!逢低买",
        "overlay": "",
        "expect_blocked": False,
        "expect_text_tokens": [],
    },
    {
        "name": "F2 浮层（类名含 captcha + 滑块文字）",
        "title": "昨晚美股光伏板块又放大量暴涨3%!逢低买",
        "overlay": OVERLAY_LOUD,
        "expect_blocked": True,
        "expect_text_tokens": ["滑块", "拼图"],
    },
    {
        "name": "F3 浮层（类名无关，只有可见文字）",
        "title": "昨晚美股光伏板块又放大量暴涨3%!逢低买",
        "overlay": OVERLAY_QUIET,
        "expect_blocked": True,
        "expect_text_tokens": ["滑块", "拼图"],
    },
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--headful", action="store_true")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    tmp = REPO / "runtime/referer-probe-selftest"
    tmp.mkdir(parents=True, exist_ok=True)
    shots = tmp / "shots"
    shots.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    rows: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headful, channel="chrome")
        page = browser.new_page()
        for index, fixture in enumerate(FIXTURES):
            html_path = tmp / f"f{index}.html"
            html_path.write_text(
                TEMPLATE.format(title=fixture["title"], body=REAL_BODY,
                                overlay=fixture["overlay"]),
                encoding="utf-8",
            )
            page.goto(html_path.as_uri(), wait_until="domcontentloaded", timeout=15000)
            shot = shots / f"f{index}.png"
            page.screenshot(path=str(shot), full_page=False)
            dom = dict(page.evaluate(PROBE.DOM_PROBE_JS))
            html = page.content()

            blocked, reasons = PROBE.classify(html, dom)
            content_ok, why = PROBE.judge_content(html)

            row = {
                "fixture": fixture["name"],
                "expected_blocked": fixture["expect_blocked"],
                "blocked": blocked,
                "block_reasons": reasons,
                "text_tokens": dom.get("text_tokens"),
                "visible_overlays": dom.get("visible_overlays"),
                "content_ok": content_ok,
                "why_not": why,
                "screenshot": str(shot.relative_to(REPO)),
                "screenshot_bytes": shot.stat().st_size if shot.exists() else 0,
                "visible_text_head": dom.get("visible_text_head", "")[:70],
            }
            rows.append(row)

            print(f"\n--- {fixture['name']} ---")
            print(f"  blocked={blocked}  期望={fixture['expect_blocked']}")
            print(f"  block_reasons={reasons}")
            print(f"  text_tokens={dom.get('text_tokens')}  visible_overlays={dom.get('visible_overlays')}")
            print(f"  content_ok={content_ok}  why_not={why}")
            print(f"  截图 {row['screenshot_bytes']} bytes")

            if blocked != fixture["expect_blocked"]:
                failures.append(f"{fixture['name']}: blocked={blocked}, 期望 {fixture['expect_blocked']}")
            for token in fixture["expect_text_tokens"]:
                if token not in (dom.get("text_tokens") or []):
                    failures.append(f"{fixture['name']}: 可见文字标记里缺 {token}")
            if row["screenshot_bytes"] < 1000:
                failures.append(f"{fixture['name']}: 截图疑似为空 ({row['screenshot_bytes']} bytes)")
            # Every fixture carries a well-formed post_article, so the content
            # check MUST succeed everywhere -- including F2/F3 where the page is
            # blocked. That equality (blocked AND content_ok) is the finding.
            if not content_ok:
                failures.append(
                    f"{fixture['name']}: content_ok=False ({why}) -- fixture 不合法，"
                    f"无法证明「内容能解析 != 没被拦」"
                )

        browser.close()

    print("\n=== 断言 ===")
    print("  设计前提：三档 fixture 都带合法 post_article，所以 content_ok 必须全为 True；")
    print("  而 F2/F3 必须 blocked=True。两者同时成立即证明内容检查不能替代封锁检查。")
    if failures:
        for f in failures:
            print(f"  FAIL  {f}")
        print(f"\n{len(failures)} 条断言失败 —— v4 检测器不成立。")
    else:
        print("  全部通过：F1 不响，F2/F3 都响；且 F2/F3 上内容检查仍然成功")
        print("  -> 证明「内容能解析」不能当作「没被拦」，而可见文字能发现浮层。")

    out = REPO / "runtime/referer-probe-selftest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    json.dump(rows, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"\n明细 -> {out.relative_to(REPO)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
