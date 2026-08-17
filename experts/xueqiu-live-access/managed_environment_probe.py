#!/usr/bin/env python3
"""One-navigation Xueqiu environment probe using the production browser runtime.

This diagnostic intentionally does not read cookies, local/session storage, or
credentials.  It observes one public stock page for a bounded period and emits
redacted JSON suitable for comparing fresh and dedicated persistent profiles.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from myresearcher_collector.sources.eastmoney_guba.browser_runtime import (
    ManagedChromiumTransport,
)
from myresearcher_collector.sources.xueqiu.dom_transport import XueqiuDomTransport


TARGET_URL = "https://xueqiu.com/S/SH601012"
SAMPLE_DELAYS = (1, 2, 5, 7, 10)


def redact_url(value: str) -> str:
    """Retain URL shape and query names while redacting every query value."""
    parts = urlsplit(value)
    names = [name for name, _ in parse_qsl(parts.query, keep_blank_values=True)]
    query = "&".join(f"{name}=<redacted>" for name in names)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def page_facts(page) -> dict[str, object]:
    return page.evaluate(
        """
        () => {
          const body = document.body ? (document.body.innerText || '') : '';
          const challengeTokens = [
            'md5__1038', '验证码', '安全验证', '访问验证', 'captcha', '人机验证'
          ];
          return {
            ready_state: document.readyState,
            posts: document.querySelectorAll('article.timeline__item').length,
            status_list_posts: document.querySelectorAll(
              '.status-list article.timeline__item'
            ).length,
            challenge_text: challengeTokens.filter(token =>
              (location.href + '\\n' + document.title + '\\n' + body)
                .toLowerCase().includes(token.toLowerCase())
            ),
            login_text_present: body.includes('登录'),
            user_agent: navigator.userAgent,
            webdriver: navigator.webdriver,
            languages: Array.from(navigator.languages || []),
            plugin_count: navigator.plugins ? navigator.plugins.length : null,
            has_window_chrome: Boolean(window.chrome),
            platform: navigator.platform
          };
        }
        """
    )


def run(profile_dir: Path | None, observe_seconds: int) -> tuple[int, dict[str, object]]:
    runtime = ManagedChromiumTransport(
        profile_dir=profile_dir,
        record_dialogs=True,
        auto_dismiss_dialogs=False,
    )
    result: dict[str, object] = {
        "target_url": TARGET_URL,
        "launch_method": "playwright.chromium.launch_persistent_context(channel=chrome)",
        "profile_mode": runtime.profile_mode,
        "user_data_dir_type": (
            "new per-run managed directory"
            if profile_dir is None
            else "dedicated persistent directory"
        ),
        "goto_count": 0,
        "navigation_chain": [],
        "samples": [],
        "post_dom_loaded": False,
        "result": "FAIL",
    }
    exit_code = 1
    try:
        print("probe_phase=launching_browser", file=sys.stderr, flush=True)
        runtime._ensure_started()
        print("probe_phase=browser_started", file=sys.stderr, flush=True)
        runtime.page.on(
            "dialog",
            lambda dialog: print(
                json.dumps(
                    {
                        "probe_event": "dialog",
                        "type": str(dialog.type),
                        "message": str(dialog.message),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
                flush=True,
            ),
        )
        runtime.page.on(
            "framenavigated",
            lambda frame: (
                print(
                    json.dumps(
                        {
                            "probe_event": "framenavigated",
                            "url": redact_url(str(frame.url)),
                        },
                        ensure_ascii=False,
                    ),
                    file=sys.stderr,
                    flush=True,
                )
                if frame is runtime.page.main_frame
                else None
            ),
        )
        runtime.page.set_default_navigation_timeout(20_000)
        transport = XueqiuDomTransport(runtime.page, runtime=runtime, timeout_ms=20_000)
        started = time.monotonic()
        print("probe_phase=opening_stock", file=sys.stderr, flush=True)
        try:
            transport.open_stock("601012")
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        print("probe_phase=stock_open_returned", file=sys.stderr, flush=True)

        if runtime.dialogs:
            result["goto_count"] = transport.main_goto_count
            result["navigation_chain"] = [
                redact_url(value) for value in transport.frame_navigation_urls
            ]
            result["post_dom_loaded"] = transport.post_dom_loaded
            result["final_url"] = redact_url(str(runtime.page.url))
            result["dialogs"] = runtime.dialogs
            result["error"] = result.get("error", "blocking JavaScript dialog observed")
            return 1, result

        elapsed = time.monotonic() - started
        remaining = max(0.0, observe_seconds - elapsed)
        for delay in SAMPLE_DELAYS:
            if remaining <= 0:
                break
            actual_delay = min(float(delay), remaining)
            time.sleep(actual_delay)
            remaining -= actual_delay
            result["samples"].append(
                {
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "url": redact_url(str(runtime.page.url)),
                    "title": str(runtime.page.title()),
                }
            )

        result["goto_count"] = transport.main_goto_count
        result["navigation_chain"] = [
            redact_url(value) for value in transport.frame_navigation_urls
        ]
        result["post_dom_loaded"] = transport.post_dom_loaded
        result["final_url"] = redact_url(str(runtime.page.url))
        result["facts"] = page_facts(runtime.page)
        result["dialogs"] = runtime.dialogs
        facts = result["facts"]
        passed = bool(
            transport.main_goto_count == 1
            and facts["posts"] > 0
            and not facts["challenge_text"]
            and "md5__1038" not in str(runtime.page.url)
        )
        result["result"] = "PASS" if passed else "FAIL"
        exit_code = 0 if passed else 1
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["dialogs"] = runtime.dialogs
    finally:
        try:
            print("probe_phase=closing_browser", file=sys.stderr, flush=True)
            runtime.close()
            print("probe_phase=browser_closed", file=sys.stderr, flush=True)
        except Exception as exc:
            result["close_error"] = f"{type(exc).__name__}: {exc}"
    return exit_code, result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-dir", type=Path)
    parser.add_argument("--observe-seconds", type=int, default=25)
    args = parser.parse_args()
    if not 20 <= args.observe_seconds <= 30:
        parser.error("--observe-seconds must be between 20 and 30")
    exit_code, result = run(args.profile_dir, args.observe_seconds)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
