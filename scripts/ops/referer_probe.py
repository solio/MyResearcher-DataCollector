#!/usr/bin/env python3
"""Does the detail surface care about `Referer`? A controlled 3-arm probe.

WHY THIS EXISTS
---------------
Enrich attacks `news,<bar>,<id>.html` (detail) directly, one post at a time.
Backfill attacks `list,<bar>,f*.html` (list) and walks pages. Enrich hits
`access_block` far more often -- measured over 7883 recorded detail attempts:
success 7315 (92.8%), `access_block` 185 (2.3%), `manual_verification_resumed`
135 (1.7%), `fetch_failure` 112, `parse_failure` 136 -- while recent backfill
runs recorded zero blocks.

Neither path sends a `Referer` today: `EastmoneyBrowserTransport.get` calls
`page.goto(url, wait_until=..., timeout=...)` with no `referer`, and the
AppleScript path sets the tab URL, which is equivalent to typing it.

THREE ARMS
----------
  A  bare        page.goto(detail)                       <- what the code does now
  B  header      page.goto(detail, referer=list_url)     <- one-line fix, IF it works
  C  js-nav      page.goto(list_url) then
                 page.evaluate("location.href = detail")  <- a real same-origin
                 navigation, so the browser sets Referer itself (a real click)

Arm order is rotated per URL so neither the arm nor the clock is confounded.

BLOCK DETECTION COMES FIRST, AND IT IS THE WHOLE POINT OF v4
------------------------------------------------------------
The v1-v3 runs were run under a detector that could not see the block the
operator was looking at, and two "successful" rows were reported while a slider
captcha was almost certainly on screen. Root cause, in three parts:

 1. `parser.is_access_block_page` is an *asset* signature: it requires
    `<title>` in {身份核实, 访问验证, 安全验证, 人机验证} **and** one of
    `fd_guba_validate` / `em_capt.js` / `validate.js` / `emcaptcha`. It matches
    the bare initial shell, not a challenge injected later.
 2. The observed block is an **overlay**: `runtime/diagnostics/`
    `eastmoney-20260813T132249.png` shows 拖动下方滑块完成拼图 rendered as a
    modal **on top of a fully loaded, normal-looking page**. The underlying
    document, its `<title>` and its `post_article` payload are all intact, so
    `parse_detail_page` also succeeds. Both old instruments therefore reported
    "fine" while the operator saw a slider.
 3. Nothing took a screenshot, so the disagreement could not be resolved.

v4 fixes all three:
  * `page.screenshot()` for EVERY arm, before any other read;
  * a DOM/`innerText` challenge probe modelled on
    `sources/xueqiu/dom_scripts.py` (which scans rendered visible text), with
    `滑块`/`拼图` added -- the tokens the 2026-08-13 overlay actually contains
    and the old marker set does not;
  * `blocked` printed first, and `--stop-on-block` ON by default: the first
    block halts the run, mirroring the fail-closed discipline of
    `enrich_all_stocks.sh` instead of grinding on and accumulating blocks.

WHAT IS MEASURED OFF THE WIRE
-----------------------------
  * `referer`        -- did the header go out at all
  * `sec-fetch-site` -- `none` (typed/scripted) vs `same-origin` (a real in-page
                        navigation). A forged `referer=` cannot fake this, which
                        is why arm B and arm C are not equivalent even though
                        both show a Referer.
  * HTTP status      -- 200 vs a redirect into the challenge
  * `content_ok`     -- decided by the REAL parser (`parse_detail_page`)

v1-v2 RESULTS, WITH THEIR OWN CAVEAT NOW ATTACHED
-------------------------------------------------
  v1: A referer 0/6, B referer 6/6, C errored (probe bug: wait_for_load_state
      resolves against the outgoing document, then content() races the nav).
  v2: A/B ok for URL 1 only; arm C navigated (Chrome History confirms the
      detail was visited at 09:11:25) and then the browser closed. History also
      shows every title was the REAL post title with no 验证 record -- which is
      consistent with an overlay challenge and is NOT evidence of a clean run.
Both runs reported `blocked=0` under the blind detector, so their `content_ok`
values must be treated as unverified, not as "no captcha".

READ BEFORE INTERPRETING
------------------------
Zero blocks does NOT mean the Referer is useless; it means this run cannot
answer the effectiveness question. What it always establishes is the mechanism:
which arm puts a `Referer` on the wire, and with which `Sec-Fetch-Site`.

USAGE
    python scripts/ops/referer_probe.py [--urls N] [--arms A,B,C] [--headful]
                                        [--force] [--no-stop-on-block]
Read-only: fetches, never writes to the database.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from myresearcher_collector.sources.eastmoney_guba.parser import (  # noqa: E402
    GubaParseError,
    is_access_block_page,
    parse_detail_page,
)

DB = REPO / "data" / "collector.db"
ALLOWED = "guba.eastmoney.com"
ARMS = ("A", "B", "C")
LABEL = {
    "A": "bare (现状)",
    "B": "goto(referer=)",
    "C": "先开列表页再 JS 跳转",
}
SHOTS = REPO / "runtime/referer-probe-shots"
LAST_RUN = REPO / "runtime/referer-probe.json"

# Visible-text tokens. `滑块`/`拼图` are the 2026-08-13 overlay's own words and
# are absent from parser._ACCESS_MARKERS, which is why that set missed it.
TEXT_TOKENS = (
    "验证码", "身份核实", "人机验证", "安全验证", "访问验证",
    "滑块", "拼图", "请拖动", "请完成", "验证错误",
)
ATTR_TOKENS = (
    "emcaptcha", "captcha", "fd_guba_validate", "validate.js", "em_capt.js",
    "geetest", "nc_1_n1z", "slider",
)

DOM_PROBE_JS = """
() => {
  const text = (document.body ? document.body.innerText : '') || '';
  const html = document.documentElement ? document.documentElement.outerHTML : '';
  const hay = (location.href + '\\n' + document.title + '\\n' + text).toLowerCase();
  const attrHay = (location.href + '\\n' + html).toLowerCase();
  const textTokens = %s.filter(t => hay.includes(t.toLowerCase()));
  const attrTokens = %s.filter(t => attrHay.includes(t.toLowerCase()));
  const selectors = ['#emcaptcha', '[id*=captcha]', '[class*=captcha]',
                     '[class*=verify]', 'iframe[src*=captcha]', '.geetest_panel',
                     '.nc-container', '[class*=slider]'];
  const visible = [];
  for (const sel of selectors) {
    for (const el of document.querySelectorAll(sel)) {
      const r = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      if (r.width > 0 && r.height > 0 && style.visibility !== 'hidden' &&
          style.display !== 'none' && style.opacity !== '0') {
        visible.push(sel);
        break;
      }
    }
  }
  return {url: location.href, title: document.title, ready: document.readyState,
          text_tokens: textTokens, attr_tokens: attrTokens,
          visible_overlays: visible, visible_text_len: text.length,
          visible_text_head: text.replace(/\\s+/g, ' ').trim().slice(0, 160)};
}
""" % (json.dumps(list(TEXT_TOKENS), ensure_ascii=False),
       json.dumps(list(ATTR_TOKENS), ensure_ascii=False))


def bar_of(detail_url: str) -> str | None:
    match = re.search(r"/news,([^,]+),", detail_url)
    return match.group(1) if match else None


def list_url(bar: str, page: int = 3) -> str:
    """A page that plausibly exists, for the Referer. Precision is not the point."""
    return (
        f"https://guba.eastmoney.com/list,{bar},f.html"
        if page == 1
        else f"https://guba.eastmoney.com/list,{bar},f_{page}.html"
    )


def stored_urls(limit: int) -> list[str]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute(
        """
        SELECT url FROM posts
        WHERE length(trim(coalesce(title,''))) >= 40 AND content IS NULL
        ORDER BY published_at DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    con.close()
    return [r[0] for r in rows]


def cooldown_remaining(minutes: float) -> float:
    """Seconds left before another run is allowed (0 = ok to run)."""
    if not LAST_RUN.exists():
        return 0.0
    age = time.time() - LAST_RUN.stat().st_mtime
    return max(0.0, minutes * 60 - age)


def judge_content(html: str) -> tuple[bool, str | None]:
    if is_access_block_page(html or ""):
        return False, "access_block(asset signature)"
    try:
        detail = parse_detail_page(html)
    except GubaParseError as exc:
        return False, f"{type(exc).__name__}: {exc}"[:70]
    return bool((detail.content or "").strip()), None


def classify(html: str, dom: dict) -> tuple[bool, list[str]]:
    """Blocked if ANY independent signal fires. Reports which ones did."""
    reasons: list[str] = []
    if is_access_block_page(html or ""):
        reasons.append("is_access_block_page")
    if dom.get("text_tokens"):
        reasons.append("visible_text:" + ",".join(dom["text_tokens"]))
    if dom.get("visible_overlays"):
        reasons.append("visible_dom:" + ",".join(dom["visible_overlays"]))
    title = str(dom.get("title") or "")
    if any(token in title for token in ("身份核实", "验证", "人机验证")):
        reasons.append("title:" + title[:24])
    return bool(reasons), reasons


class Harness:
    """One persistent Chromium, relaunchable, with per-navigation capture."""

    def __init__(self, playwright, profile: str, headful: bool) -> None:
        self.pw = playwright
        self.profile = profile
        self.headful = headful
        self.context = None
        self.page = None
        self.wire: dict[str, dict] = {}
        self.events: list[dict] = []
        self.start()

    def start(self) -> None:
        Path(self.profile).mkdir(parents=True, exist_ok=True)
        self.context = self.pw.chromium.launch_persistent_context(
            user_data_dir=self.profile,
            headless=not self.headful,
            channel="chrome",
        )
        for page in self.context.pages:
            self._bind(page)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self._bind(self.page)

    def alive(self) -> bool:
        try:
            browser = self.context.browser if self.context else None
            return bool(browser and browser.is_connected())
        except Exception:  # noqa: BLE001
            return False

    def restart(self) -> None:
        self._note("relaunch", "browser was dead")
        try:
            if self.context:
                self.context.close()
        except Exception:  # noqa: BLE001
            pass
        self.start()

    def _note(self, kind: str, detail: str) -> None:
        self.events.append({"t": round(time.time(), 3), "event": kind, "detail": detail[:160]})

    def _bind(self, page) -> None:
        page.on("request", self._on_request)
        page.on("response", self._on_response)
        page.on("dialog", self._on_dialog)
        page.on("pageerror", lambda err: self._note("pageerror", str(err)))
        page.on("close", lambda: self._note("page_close", "a page closed"))
        page.on("crash", lambda: self._note("page_crash", "page crashed"))

    def _on_request(self, request) -> None:
        try:
            if ALLOWED in request.url:
                # `request.headers` does NOT expose sec-fetch-* (only the
                # sec-ch-ua client hints), so reading it there returns None for
                # every arm -- which reads as "the site sent no Sec-Fetch-Site"
                # when the truth is that this reader cannot see it. Verified
                # against a local http.server in referer_probe_selftest.py.
                # `all_headers()` does expose them. Measured there (browser-
                # determined, so site-independent):
                #   A bare            -> referer absent, sec-fetch-site=none
                #   B goto(referer=)  -> referer set,    sec-fetch-site=none
                #   C list then js    -> referer set,    sec-fetch-site=same-origin
                try:
                    headers = request.all_headers()
                except Exception:  # noqa: BLE001
                    headers = request.headers
                self.wire.setdefault(request.url, {})["request"] = {
                    "referer": headers.get("referer"),
                    "sec-fetch-site": headers.get("sec-fetch-site"),
                    "sec-fetch-mode": headers.get("sec-fetch-mode"),
                    "sec-fetch-user": headers.get("sec-fetch-user"),
                }
        except Exception:  # noqa: BLE001
            pass

    def _on_response(self, response) -> None:
        try:
            if ALLOWED in response.url:
                self.wire.setdefault(response.url, {})["status"] = response.status
        except Exception:  # noqa: BLE001
            pass

    def _on_dialog(self, dialog) -> None:
        try:
            self._note("dialog", f"{dialog.type}: {dialog.message}")
            dialog.dismiss()
        except Exception:  # noqa: BLE001
            pass

    def navigate(self, arm: str, detail: str, lst: str) -> None:
        page = self.page
        if arm == "A":
            page.goto(detail, wait_until="domcontentloaded", timeout=30000)
        elif arm == "B":
            page.goto(detail, wait_until="domcontentloaded", timeout=30000, referer=lst)
        else:
            page.goto(lst, wait_until="domcontentloaded", timeout=30000)
            page.evaluate("u => { location.href = u; }", detail)
            page.wait_for_url(
                lambda url: str(url).split("#")[0] == detail,
                wait_until="domcontentloaded",
                timeout=30000,
            )

    def observe(self, shot: Path) -> dict:
        """Screenshot FIRST, then DOM probe, then HTML -- never the other order."""
        dom: dict = {}
        html = ""
        try:
            shot.parent.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(shot), full_page=False)
        except Exception as exc:  # noqa: BLE001
            dom["screenshot_error"] = f"{type(exc).__name__}: {exc}"[:80]
        try:
            dom = dict(self.page.evaluate(DOM_PROBE_JS))
        except Exception as exc:  # noqa: BLE001
            dom["dom_probe_error"] = f"{type(exc).__name__}: {exc}"[:80]
        try:
            html = self.page.content()
        except Exception as exc:  # noqa: BLE001
            dom["content_error"] = f"{type(exc).__name__}: {exc}"[:80]
        return {"dom": dom, "html": html}

    def fetch(self, arm: str, detail: str, lst: str, shot: Path, *, retry: bool) -> tuple[dict, dict]:
        self.wire.clear()
        diag: dict = {}
        try:
            self.navigate(arm, detail, lst)
            return self.observe(shot), diag
        except Exception as exc:  # noqa: BLE001
            diag["error"] = f"{type(exc).__name__}: {exc}"[:110]
        if retry and not self.alive():
            diag["retried_after_relaunch"] = True
            self.restart()
            try:
                self.navigate(arm, detail, lst)
                out = self.observe(shot)
                diag.pop("error", None)
                return out, diag
            except Exception as exc:  # noqa: BLE001
                diag["error"] = f"{type(exc).__name__}: {exc}"[:110]
        return {"dom": {}, "html": ""}, diag


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", type=int, default=3)
    ap.add_argument("--arms", default="A,B,C")
    ap.add_argument("--min-delay", type=float, default=3.0,
                    help="match enrich_all_stocks.sh default")
    ap.add_argument("--max-delay", type=float, default=10.0,
                    help="match enrich_all_stocks.sh default")
    ap.add_argument("--profile", default="", help="default: a fresh temp dir (matches production)")
    ap.add_argument("--headful", action="store_true", help="match production (headless=False)")
    ap.add_argument("--cooldown-min", type=float, default=15.0)
    ap.add_argument("--force", action="store_true", help="ignore the cooldown")
    ap.add_argument("--no-stop-on-block", action="store_true")
    ap.add_argument(
        "--retry-on-dead", action="store_true",
        help="relaunch the browser if it dies mid-arm. OFF by default ON PURPOSE: "
             "if the operator closed the window, relaunching would fight them and "
             "add traffic. Default behaviour is to stop the run.",
    )
    args = ap.parse_args()

    arms = tuple(a.strip().upper() for a in args.arms.split(",") if a.strip())
    for arm in arms:
        if arm not in ARMS:
            print(f"unknown arm {arm!r}")
            return 2

    wait = cooldown_remaining(args.cooldown_min)
    if wait > 0 and not args.force:
        print(
            f"冷静期未到：距上次探针 {wait/60:.1f} 分钟 < {args.cooldown_min:.0f} 分钟。"
            f"\n这是为了防止连续微新指纹反复触发风控。要强制跑加 --force。"
        )
        return 3

    targets = stored_urls(args.urls)
    if not targets:
        print("no detail URLs available")
        return 1

    profile = args.profile or tempfile.mkdtemp(prefix="referer-probe-")
    stop_on_block = not args.no_stop_on_block
    print(
        f"profile={profile}\nurls={len(targets)} arms={','.join(arms)} "
        f"headful={args.headful} delay={args.min_delay}-{args.max_delay}s "
        f"stop_on_block={stop_on_block}"
    )

    from playwright.sync_api import sync_playwright

    results: list[dict] = []
    blocked_any = False
    died_any = False
    with sync_playwright() as pw:
        harness = Harness(pw, profile, args.headful)
        for index, detail in enumerate(targets):
            bar = bar_of(detail)
            if bar is None:
                continue
            lst = list_url(bar)
            order = arms[index % len(arms):] + arms[: index % len(arms)]
            for arm in order:
                started = time.monotonic()
                shot = SHOTS / f"{index:02d}-{arm}-{bar}.png"
                observed, diag = harness.fetch(
                    arm, detail, lst, shot, retry=args.retry_on_dead)
                html, dom = observed["html"], observed["dom"]
                elapsed = round(time.monotonic() - started, 2)

                blocked, reasons = classify(html, dom)
                ok, why = (judge_content(html) if html else (False, "no-html"))
                wire = harness.wire.get(detail, {})
                headers = wire.get("request", {})

                results.append({
                    "url": detail, "arm": arm, "list": lst,
                    "blocked": blocked, "block_reasons": reasons,
                    "content_ok": ok, "why_not": why,
                    "status": wire.get("status"),
                    "referer_on_wire": headers.get("referer"),
                    "sec_fetch_site": headers.get("sec-fetch-site"),
                    "bytes": len(html or ""),
                    "page_title": dom.get("title"),
                    "visible_overlays": dom.get("visible_overlays"),
                    "visible_text_head": dom.get("visible_text_head"),
                    "screenshot": str(shot.relative_to(REPO)),
                    "screenshot_ok": shot.exists(),
                    "elapsed_s": elapsed,
                    **diag,
                })

                flag = "BLOCK" if blocked else ("err" if diag.get("error") else "ok")
                print(f"  [{flag:>5}] {arm} {detail.split('/news,')[-1][:26]:<26} "
                      f"{('ref=' + str(headers.get('referer'))[:34]) if headers.get('referer') else 'ref=-':<40} "
                      f"sfc={str(headers.get('sec-fetch-site')):<12} {reasons if reasons else ''}")

                if blocked and stop_on_block:
                    blocked_any = True
                    print(f"\n  ** 撞上验证码（{'; '.join(reasons)}）-> 按 fail-closed 立即停止，不再继续 **")
                    print(f"  ** 截图：{shot.relative_to(REPO)} **")
                    break
                if diag.get("error") and not harness.alive():
                    died_any = True
                    print(f"\n  ** 浏览器在本次导航中死亡/被关闭（{diag['error'][:60]}）-> 停止 **")
                    print(f"  ** 不重启、不重试：默认 --retry-on-dead 关闭，免得跟你关窗的动作对着干 **")
                    break
                time.sleep(random.uniform(args.min_delay, args.max_delay))
            if (blocked_any and stop_on_block) or died_any:
                break
        events = harness.events
        try:
            harness.context.close()
        except Exception:  # noqa: BLE001
            pass

    print(f"\n=== 每臂汇总（blocked 优先）===")
    for arm in arms:
        rows = [r for r in results if r["arm"] == arm]
        if not rows:
            continue
        blk = sum(1 for r in rows if r["blocked"])
        sent = sum(1 for r in rows if r["referer_on_wire"])
        okc = sum(1 for r in rows if r["content_ok"] and not r["blocked"])
        sites = sorted({str(r["sec_fetch_site"]) for r in rows})
        print(f"  {arm} {LABEL[arm]:<26} n={len(rows)}  blocked={blk}  "
              f"content_ok(未被拦)={okc}  带Referer={sent}  Sec-Fetch-Site={sites}")

    print("\n判定：")
    if died_any:
        print("  浏览器中途死亡/被关闭 -> 本次运行**不完整**，未跑到的臂不要下任何结论。")
    if blocked_any:
        print("  本次至少撞到一次验证码 -> **Referer 有效性问题无法回答**；")
        print("  但被拦的那一臂，其 blocked 原因与截图就是下一次修复的输入。")
    elif not died_any:
        print("  本次未触发封锁 -> **对“Referer 能否降低封锁”无结论**（只是此刻没在拦）。")
    for arm in arms:
        rows = [r for r in results if r["arm"] == arm]
        if not rows:
            continue
        sent = any(r["referer_on_wire"] for r in rows)
        sites = sorted({str(r["sec_fetch_site"]) for r in rows})
        print(f"  {arm} 是否带上 Referer: {'是' if sent else '否'}   Sec-Fetch-Site={sites}")

    if events:
        print(f"\n生命周期事件 {len(events)} 条: " + ", ".join(
            f"{e['event']}({e['detail'][:22]})" for e in events[:8]))
        if any(e["event"] == "relaunch" for e in events):
            print("  ** 浏览器被重启过 —— 相关臂结果不可信 **")

    gaps = [r for r in results if not r.get("screenshot_ok") and not r.get("error")]
    if gaps:
        print(f"\n** {len(gaps)} 行没拿到截图 —— 该行结论视为未验证 **")

    shots = sorted(SHOTS.glob("*.png"))
    print(f"\n截图 {len(shots)} 张 -> {SHOTS.relative_to(REPO)}/")
    out = LAST_RUN
    out.parent.mkdir(parents=True, exist_ok=True)
    json.dump(results, open(out, "w"), ensure_ascii=False, indent=1)
    ev = REPO / "runtime/referer-probe-events.json"
    json.dump(events, open(ev, "w"), ensure_ascii=False, indent=1)
    print(f"明细 -> {out.relative_to(REPO)}   事件 -> {ev.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
