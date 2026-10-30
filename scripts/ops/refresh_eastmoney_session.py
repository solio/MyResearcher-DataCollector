#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""refresh_eastmoney_session.py —— 复刻一次 backfill 的浏览行为（零记录）。

目的：http_backfill 被验证码拦停时，跑本脚本制造与完整 backfill 等效的
      "正常浏览器活动"（20 只 × 3 页列表、第 1 页 google 进入、第 N 页
      用第 N-1 页做 referer、约 6 秒/页，全程约 7 分钟），让随后的单次
      探测通过率大幅提升。

实测（2026-10-13）：
  - backfill 活动进行中发探测：**2/2 一次通过**（启动后 19 秒 / 2 分钟内）；
  - 活动结束后隔 20+ 分钟再探测：失败（活动窗口已关闭）。
  ⇒ **探测必须与"活动窗口"重叠**：本脚本跑起来后，在它运行期间发探测。

建议用法：
  1) 运行本脚本（默认约 60 个页面请求、约 7 分钟）；
  2) 运行期间（尤其前 2-3 分钟内）发 http_backfill 的单次探测；
  3) 探测成功 → 对该股"开始"（恢复采集）。

行为（复刻 backfill 的浅层访问）：
  * 20 只股票按队列顺序；
  * 每只翻 3 页：f.html -> f_2.html -> f_3.html；
  * 第 1 页 referer=https://www.google.com/ ；第 N 页 referer=同股第 N-1 页 URL；
  * 页间隔默认 6 秒（贴近 backfill 实测节奏）。
  * 任何页面出现"身份核实/访问验证"等挑战 → 打印提示并等待人工在
    弹出的浏览器窗口中完成验证（默认最多 300 秒）；挑战解除继续，
    挑战等待超时或整体超时 → 直接退出。

不落任何记录：不写数据库、不写文件、不解析不保存；除标准输出提示外零副作用。
（唯一的状态变化是浏览器 profile 自身的会话数据，与日常跑 backfill 相同。）

用法（在仓库根目录下）:
  /opt/homebrew/anaconda3/bin/python scripts/ops/refresh_eastmoney_session.py
  可选: --pages 3 --delay 6.0 --challenge-timeout 300 --total-timeout 900 --headless
"""

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
PROFILE_DIR = str(REPO / ".runtime" / "browser-profiles" / "eastmoney-managed-persistent")

# 与 backfill 驱动相同的 20 只队列
STOCKS = [
    "601012", "002463", "601888", "300666", "300054",
    "603039", "002648", "002028", "603179", "605020",
    "600312", "002891", "603806", "688676", "300487",
    "603997", "603129", "002353", "300124", "600031",
]

BLOCK_MARKERS = ("身份核实", "访问验证", "安全验证", "人机验证")


def build_plan(pages: int):
    plan = []
    for code in STOCKS:
        for p in range(1, pages + 1):
            url = (f"https://guba.eastmoney.com/list,{code},f.html" if p == 1
                   else f"https://guba.eastmoney.com/list,{code},f_{p}.html")
            plan.append((code, p, url))
    return plan


def is_block(page) -> bool:
    try:
        title = (page.title() or "").strip()
    except Exception:
        title = ""
    if any(m in title for m in BLOCK_MARKERS):
        return True
    try:
        return bool(page.query_selector("#emcaptcha, [class*='captcha'], iframe[src*='captcha']"))
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="复刻一次 backfill 的浏览行为（零记录）")
    ap.add_argument("--pages", type=int, default=3, help="每只股票翻页数（默认 3）")
    ap.add_argument("--delay", type=float, default=6.0, help="每页间隔秒数（默认 6）")
    ap.add_argument("--challenge-timeout", type=int, default=300, help="验证码等待上限秒数")
    ap.add_argument("--total-timeout", type=int, default=900, help="整体超时秒数")
    ap.add_argument("--headless", action="store_true", help="无窗口模式（默认有窗口，便于人工过码）")
    args = ap.parse_args()

    plan = build_plan(args.pages)
    total = len(plan)
    start = time.time()
    print(f"[计划] {len(STOCKS)} 只 × {args.pages} 页 = {total} 个页面请求，"
          f"间隔 {args.delay}s；预计约 {int(total * (args.delay + 1) / 60)} 分钟。")
    print(f"[提示] 运行期间即可发 http_backfill 的单次探测（活动窗口=探测窗口）。")

    with sync_playwright() as pw:
        try:
            ctx = pw.chromium.launch_persistent_context(
                PROFILE_DIR,
                channel="chrome",
                headless=args.headless,
            )
        except Exception as exc:
            print(f"[错误] 无法启动浏览器（profile 可能正被采集任务占用）：{exc}")
            return 1
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            prev_url = None
            for idx, (code, p, url) in enumerate(plan, 1):
                if time.time() - start > args.total_timeout:
                    print(f"[超时] 整体超时（{args.total_timeout}s），退出。")
                    return 3
                ref = prev_url if p > 1 else "https://www.google.com/"
                try:
                    page.goto(url, timeout=30000, wait_until="domcontentloaded", referer=ref)
                except Exception as exc:
                    print(f"[{idx}/{total}] {code} 第 {p}/{args.pages} 页 —— 访问异常（跳过）：{exc}")
                    prev_url = None
                    continue
                if is_block(page):
                    print(f"[{idx}/{total}] {code} 第 {p}/{args.pages} 页 —— ⚠️ 出现验证码/身份核实，"
                          f"请在浏览器窗口中完成验证，等待中…")
                    waited = 0
                    resolved = False
                    while waited < args.challenge_timeout:
                        time.sleep(3)
                        waited += 3
                        if not is_block(page):
                            print(f"  ✅ 验证已通过（等待 {waited}s），继续。")
                            resolved = True
                            break
                        if time.time() - start > args.total_timeout:
                            print("[超时] 整体超时，退出。")
                            return 3
                    if not resolved:
                        print(f"[超时] 验证码等待超时（{args.challenge_timeout}s），退出。")
                        return 2
                else:
                    print(f"[{idx}/{total}] {code} 第 {p}/{args.pages} 页 ✓")
                prev_url = url
                time.sleep(args.delay)
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    print(f"[完成] {total} 个页面访问完毕（{int(time.time() - start)}s），正常退出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
