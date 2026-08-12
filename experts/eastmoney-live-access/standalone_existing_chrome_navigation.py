#!/usr/bin/env python3
"""Drive the user's already-running macOS Chrome in a standalone process.

This does not open, copy, or lock the Chrome profile directory. It creates one
dedicated tab in the existing Chrome session through Chrome's Apple Events
interface, then performs bounded list/detail navigation with random delays.
It never reads or exports cookies, storage, history, passwords, or credentials.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CREATE_TAB = r'''
on run argv
  tell application "Google Chrome"
    if (count of windows) is 0 then make new window
    set targetURL to item 1 of argv
    set w to front window
    set t to make new tab at end of tabs of w with properties {URL:targetURL}
    set active tab index of w to (count of tabs of w)
    activate
    return ((id of w) as text) & "|" & ((id of t) as text)
  end tell
end run
'''

NAVIGATE_TAB = r'''
on run argv
  tell application "Google Chrome"
    set windowID to (item 1 of argv) as integer
    set tabID to (item 2 of argv) as integer
    set targetURL to item 3 of argv
    set w to first window whose id is windowID
    set t to first tab of w whose id is tabID
    set URL of t to targetURL
    activate
    return targetURL
  end tell
end run
'''

TAB_LOADING = r'''
on run argv
  tell application "Google Chrome"
    set windowID to (item 1 of argv) as integer
    set tabID to (item 2 of argv) as integer
    set w to first window whose id is windowID
    set t to first tab of w whose id is tabID
    return (loading of t) as text
  end tell
end run
'''

EXECUTE_JS = r'''
on run argv
  tell application "Google Chrome"
    set windowID to (item 1 of argv) as integer
    set tabID to (item 2 of argv) as integer
    set scriptText to item 3 of argv
    set w to first window whose id is windowID
    set t to first tab of w whose id is tabID
    return execute t javascript scriptText
  end tell
end run
'''


PAGE_STATE_JS = r'''
JSON.stringify((() => {
  const html = document.documentElement.innerHTML;
  const accessTitles = new Set(["身份核实", "访问验证", "安全验证", "人机验证"]);
  function objectAfter(marker) {
    const start = html.indexOf(marker);
    if (start < 0) return null;
    let i = html.indexOf("{", start + marker.length);
    if (i < 0) return null;
    let depth = 0, quoted = false, escaped = false;
    for (let j = i; j < html.length; j++) {
      const c = html[j];
      if (quoted) {
        if (escaped) escaped = false;
        else if (c === "\\") escaped = true;
        else if (c === '"') quoted = false;
        continue;
      }
      if (c === '"') quoted = true;
      else if (c === "{") depth++;
      else if (c === "}" && --depth === 0) {
        try { return JSON.parse(html.slice(i, j + 1)); } catch { return null; }
      }
    }
    return null;
  }
  const list = objectAfter("var article_list=");
  const detail = objectAfter("var post_article=");
  const rows = Array.isArray(list?.re) ? list.re : [];
  const standard = rows.filter(x => x?.post_type === 0);
  return {
    title: document.title,
    url: location.href,
    accessBlock: accessTitles.has(document.title.trim()),
    list: list ? {
      rc: list.rc ?? null,
      rows: rows.length,
      standardRows: standard.length,
      outOfScopeRows: rows.length - standard.length,
      firstStandard: standard[0] ? {
        id: String(standard[0].post_id),
        code: standard[0].stockbar_code,
        title: standard[0].post_title
      } : null
    } : null,
    detail: detail ? {
      id: detail.post_id == null ? null : String(detail.post_id),
      code: detail.post_guba?.stockbar_code ?? null,
      postType: detail.post_type ?? null,
      contentLength: typeof detail.post_content === "string" ? detail.post_content.length : null
    } : null
  };
})())
'''


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock", default="601012")
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--min-delay", type=float, default=3.0)
    parser.add_argument("--max-delay", type=float, default=10.0)
    parser.add_argument("--operator-wait", type=float, default=180.0)
    parser.add_argument("--load-timeout", type=float, default=30.0)
    args = parser.parse_args()
    if len(args.stock) != 6 or not args.stock.isdigit():
        parser.error("--stock must be six digits")
    if args.pages < 2 or args.pages > 10:
        parser.error("--pages must be between 2 and 10")
    if args.min_delay < 3 or args.max_delay > 10 or args.min_delay > args.max_delay:
        parser.error("delay range must stay within 3..10 seconds")
    return args


def _osascript(source: str, *values: object) -> str:
    command = ["osascript", "-e", source, "--", *(str(v) for v in values)]
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=45, check=False
    )
    if completed.returncode != 0:
        message = " ".join(completed.stderr.strip().split())
        if "Apple Events" in message or "JavaScript" in message:
            raise RuntimeError(
                "CHROME_JAVASCRIPT_FROM_APPLE_EVENTS_DISABLED: enable Chrome "
                "View > Developer > Allow JavaScript from Apple Events"
            )
        raise RuntimeError(f"CHROME_APPLE_EVENT_FAILED: {message}")
    return completed.stdout.strip()


def _wait_loaded(window_id: str, tab_id: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _osascript(TAB_LOADING, window_id, tab_id).lower() == "false":
            time.sleep(1)
            return
        time.sleep(0.5)
    raise RuntimeError("CHROME_TAB_LOAD_TIMEOUT")


def _state(window_id: str, tab_id: str) -> dict[str, Any]:
    value = _osascript(EXECUTE_JS, window_id, tab_id, PAGE_STATE_JS)
    try:
        state = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError("CHROME_PAGE_STATE_IS_NOT_JSON") from exc
    if not isinstance(state, dict):
        raise RuntimeError("CHROME_PAGE_STATE_IS_NOT_OBJECT")
    return state


def _navigate(
    window_id: str, tab_id: str, url: str, load_timeout: float
) -> dict[str, Any]:
    _osascript(NAVIGATE_TAB, window_id, tab_id, url)
    _wait_loaded(window_id, tab_id, load_timeout)
    return _state(window_id, tab_id)


def _wait_for_initial_list(
    window_id: str, tab_id: str, stock: str, seconds: float
) -> dict[str, Any]:
    deadline = time.monotonic() + seconds
    while True:
        state = _state(window_id, tab_id)
        if (
            state.get("list", {}).get("rc") == 1
            and state.get("list", {}).get("rows", 0) > 0
            and f"list,{stock},f.html" in state.get("url", "")
        ):
            return state
        if time.monotonic() >= deadline:
            raise RuntimeError("OPERATOR_WAIT_EXPIRED_WITHOUT_REAL_LIST")
        time.sleep(1)


def _write(path: Path, report: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    args = _args()
    report: dict[str, Any] = {
        "runtime": "STANDALONE_EXISTING_USER_GOOGLE_CHROME",
        "codex_browser_used": False,
        "chrome_profile_files_read": False,
        "cookies_or_storage_exported": False,
        "stock": args.stock,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "delay_range_seconds": [args.min_delay, args.max_delay],
        "steps": [],
        "status": "RUNNING",
    }
    window_id = tab_id = None
    try:
        page1 = f"https://guba.eastmoney.com/list,{args.stock},f.html"
        window_id, tab_id = _osascript(CREATE_TAB, page1).split("|", 1)
        report["chrome_window_id"] = window_id
        report["dedicated_tab_id"] = tab_id
        _wait_loaded(window_id, tab_id, args.load_timeout)
        state = _state(window_id, tab_id)
        operator_assisted = state.get("list", {}).get("rc") != 1
        if operator_assisted:
            print(
                "WAITING_FOR_MANUAL_VERIFICATION in the existing Chrome tab; "
                "the process only waits and never handles the challenge.",
                flush=True,
            )
            state = _wait_for_initial_list(
                window_id, tab_id, args.stock, args.operator_wait
            )
        report["operator_assisted"] = operator_assisted
        step = {
            "kind": "list",
            "page": 1,
            "url": state["url"],
            **state["list"],
            "result": "PASS",
        }
        report["steps"].append(step)
        print(json.dumps(step, ensure_ascii=False), flush=True)

        generator = random.SystemRandom()
        for page_number in range(2, args.pages + 1):
            delay = generator.uniform(args.min_delay, args.max_delay)
            time.sleep(delay)
            list_url = (
                f"https://guba.eastmoney.com/list,{args.stock},f_{page_number}.html"
            )
            state = _navigate(
                window_id, tab_id, list_url, args.load_timeout
            )
            if state.get("accessBlock") or state.get("list", {}).get("rc") != 1:
                raise RuntimeError(f"ACCESS_BLOCK_OR_INVALID_LIST_PAGE_{page_number}")
            step = {
                "kind": "list",
                "page": page_number,
                "delay_seconds": round(delay, 3),
                "url": state["url"],
                **state["list"],
                "result": "PASS",
            }
            report["steps"].append(step)
            print(json.dumps(step, ensure_ascii=False), flush=True)

            first = state["list"]["firstStandard"]
            if first is None:
                raise RuntimeError(f"NO_STANDARD_POST_ON_PAGE_{page_number}")
            delay = generator.uniform(args.min_delay, args.max_delay)
            time.sleep(delay)
            detail_url = (
                f"https://guba.eastmoney.com/news,{first['code']},{first['id']}.html"
            )
            state = _navigate(
                window_id, tab_id, detail_url, args.load_timeout
            )
            detail = state.get("detail")
            if state.get("accessBlock") or detail is None:
                raise RuntimeError(f"ACCESS_BLOCK_OR_INVALID_DETAIL_{first['id']}")
            if detail.get("id") != first["id"]:
                raise RuntimeError(
                    f"DETAIL_ID_MISMATCH_{first['id']}_{detail.get('id')}"
                )
            step = {
                "kind": "detail",
                "page": page_number,
                "delay_seconds": round(delay, 3),
                "url": state["url"],
                **detail,
                "result": "PASS",
            }
            report["steps"].append(step)
            print(json.dumps(step, ensure_ascii=False), flush=True)
        report["status"] = "PASS"
    except Exception as exc:
        report["status"] = "FAILED"
        report["failure"] = f"{type(exc).__name__}: {exc}"
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        _write(args.result, report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
