#!/usr/bin/env python3
"""Positive/negative controls for the referer probe's two oracles.

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

Claiming that a detector fixes that is itself a claim that needs a control, and so
is claiming that a header was or was not sent. This script has two phases and
**no external network traffic** (phase 2 runs a loopback `http.server`, bound to
port 0).

PHASE 1 -- the block classifier (`file://` fixtures)

  F1  real page, no overlay                 -> NOT blocked, content parses
  F2  real page + overlay whose class contains `captcha` AND slider text
                                            -> blocked (class + text signals)
  F3  real page + overlay with an INNOCENT class name, neutral title, and the
      only distinguishing feature being the visible words
      「拖动下方滑块完成拼图」
                                            -> blocked via `visible_text` ONLY

F3 is the case v3 missed by construction; if F3 does not fire, v4 does not fix
anything and this script fails.

PHASE 2 -- the wire reader (loopback `http.server`)

Asserts what the three arms actually put on the wire, and in particular pins a
reader trap that produced a **false claim** during this investigation:
Playwright's `request.headers` does **not** expose `sec-fetch-*` (only the
`sec-ch-ua` client hints), so reading `sec-fetch-site` from it returns `None` for
every arm. That reads as "the site sent no `Sec-Fetch-Site`" when the truth is
that the reader is blind. `all_headers()` does expose it.

Because `Sec-Fetch-Site` is computed by Chromium from the *initiator*, these
values are **site-independent** -- measuring them against 127.0.0.1 settles what
guba would see, without touching guba:

  A bare            referer absent   sec-fetch-site=none
  B goto(referer=)  referer set      sec-fetch-site=none
  C list then js    referer set      sec-fetch-site=same-origin

The last two lines are the point: B and C are **not equivalent**. Both carry a
`Referer`, but a forged one arrives with no initiator, so the pair
(`Referer`, `Sec-Fetch-Site`) still separates a scripted jump from a real
in-page navigation.

USAGE
    python scripts/ops/referer_probe_selftest.py [--headful]
Exit 0 = every fixture and every header assertion matched.
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


def wire_header_phase(browser) -> tuple[list[dict], list[str]]:
    """Phase 2: can the wire reader actually see the headers it reports?

    `Sec-Fetch-Site` is computed by Chromium from the initiator, so these values
    are **site-independent** -- measuring them against a loopback `http.server`
    settles what guba will see, without touching guba.

    It also pins the reader trap that produced a false claim: Playwright's
    `request.headers` does NOT expose `sec-fetch-*` (only the `sec-ch-ua` client
    hints), so reading the header there yields `None` for every arm, which reads
    as "the site sent none" when in fact the reader is blind. `all_headers()`
    does expose them.
    """
    import http.server
    import socketserver
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<html><head><title>local</title></head><body>ok</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # keep the test output clean
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    detail = f"http://127.0.0.1:{port}/news,601012,9.html"
    lst = f"http://127.0.0.1:{port}/list,601012,f_3.html"

    context = browser.new_context()
    page = context.new_page()
    captured: dict[str, list[dict]] = {}
    current = {"arm": "A"}

    def on_request(request):
        if request.url != detail:
            return
        entry = {"arm": current["arm"]}
        try:
            allh = request.all_headers()
            entry.update({k: allh.get(k) for k in
                          ("referer", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-user")})
        except Exception as exc:  # noqa: BLE001
            entry["all_headers_error"] = str(exc)[:60]
        entry["headers_sees_sfc"] = "sec-fetch-site" in request.headers
        captured.setdefault(current["arm"], []).append(entry)

    page.on("request", on_request)

    current["arm"] = "A"
    page.goto(detail, wait_until="domcontentloaded", timeout=15000)
    current["arm"] = "B"
    page.goto(detail, wait_until="domcontentloaded", timeout=15000, referer=lst)
    current["arm"] = "C"
    page.goto(lst, wait_until="domcontentloaded", timeout=15000)
    page.evaluate("u => { location.href = u; }", detail)
    page.wait_for_url(lambda url: str(url) == detail, wait_until="domcontentloaded", timeout=15000)
    context.close()
    server.shutdown()

    failures: list[str] = []
    rows: list[dict] = []
    expect = {
        "A": {"referer": None, "sfcs": "none"},
        "B": {"referer": lst, "sfcs": "none"},
        "C": {"referer": lst, "sfcs": "same-origin"},
    }
    for arm in ("A", "B", "C"):
        entries = captured.get(arm) or []
        if not entries:
            failures.append(f"arm {arm}: 一个请求头都没抓到")
            continue
        entry = entries[-1]
        rows.append({"arm": arm, **entry})
        print(f"\n--- arm {arm} ---")
        print(f"  referer={entry.get('referer')!r}  sec-fetch-site={entry.get('sec-fetch-site')!r}"
              f"  mode={entry.get('sec-fetch-mode')!r}  user={entry.get('sec-fetch-user')!r}")
        expected = expect[arm]
        if entry.get("referer") != expected["referer"]:
            failures.append(
                f"arm {arm}: referer={entry.get('referer')!r}, 期望 {expected['referer']!r}")
        if entry.get("sec-fetch-site") != expected["sfcs"]:
            failures.append(
                f"arm {arm}: sec-fetch-site={entry.get('sec-fetch-site')!r}, 期望 {expected['sfcs']!r}")
        if entry.get("headers_sees_sfc"):
            failures.append(
                f"arm {arm}: request.headers 竟然能看见 sec-fetch-site —— "
                f"探针里那条注释需要更新"
            )

    print("\n  so: A 无 referer；B/C 都带 referer；但只有 C 的 Sec-Fetch-Site 是 "
          "same-origin，B 是 none。")
    print("  B 与 C 因此**不是等价方案**：只看 Referer 一样，看这一对头就能分开。")
    return rows, failures


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

    # ---- phase 2: is the wire reader itself trustworthy? -------------------
    print("\n=== 第二段：三臂到底发出什么头（本机 http.server，零外网）===")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headful, channel="chrome")
        header_rows, header_failures = wire_header_phase(browser)
        browser.close()
    failures.extend(header_failures)

    print("\n=== 断言 ===")
    print("  第一段前提：三档 fixture 都带合法 post_article，所以 content_ok 必须全为 True；")
    print("  而 F2/F3 必须 blocked=True。两者同时成立即证明内容检查不能替代封锁检查。")
    print("  第二段前提：三臂发出的 referer / sec-fetch-site 必须分别符合上表。")
    if failures:
        for f in failures:
            print(f"  FAIL  {f}")
        print(f"\n{len(failures)} 条断言失败 —— 检测器/读取器不成立，不要拿它的输出下结论。")
    else:
        print("  全部通过：")
        print("    第一段 F1 不响、F2/F3 都响，且 F2/F3 上内容检查仍然成功")
        print("      -> 「内容能解析」不能当作「没被拦」，可见文字能发现浮层。")
        print("    第二段 A 无 referer；B/C 都带 referer，但只有 C 是 same-origin")
        print("      -> 「用 referer 头伪造」和「真的点进去」不是一回事。")

    out = REPO / "runtime/referer-probe-selftest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"fixtures": rows, "wire_headers": header_rows},
              open(out, "w"), ensure_ascii=False, indent=1)
    print(f"\n明细 -> {out.relative_to(REPO)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
