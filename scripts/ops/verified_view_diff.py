#!/usr/bin/env python3
"""Diff the list view served over a VERIFIED browser session against the
anonymous baseline already stored in data/collector.db.

WHY THIS EXISTS
---------------
Proving "we are not being served a degraded page" needs an anchor OUTSIDE the
anonymous view. Two anonymous fetches that agree prove only that they are the
*same* view, not that the view is complete -- a site can hand a crawler a
self-consistent subset (real titles, contiguous ids, rc=1, 80 rows/page, no
time gaps) and every internal consistency check will still pass.

So: run `eastmoney-browser-host --headful --operator-wait-seconds N`, complete
the site's login/ad/captcha in that visible window by hand, then run this. It
fetches the SAME urls through that session and reports which posts the verified
view has that the database does not.

NOTE ON THE HOST CACHE: the host caches the preflight response (page 1 of the
preflight stock) and serves it exactly once. That cached body was captured
BEFORE the operator verified, so this script requests that url TWICE and keeps
the second (live, post-verification) response.

USAGE
-----
    PYTHONPATH=src python scripts/ops/verified_view_diff.py 601012 [pages]

Requires a running host on --browser-socket (default:
/tmp/myresearcher-eastmoney-browser.sock). Read-only: never writes to the
database.
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from myresearcher_collector.sources.eastmoney_guba.browser_transport import (  # noqa: E402
    EastmoneyBrowserSocketTransport,
)
from myresearcher_collector.sources.eastmoney_guba.collector import (  # noqa: E402
    EastmoneyGubaCollector,
)
from myresearcher_collector.sources.eastmoney_guba.parser import (  # noqa: E402
    is_access_block_page,
    parse_list_page,
)

SOCKET = "/tmp/myresearcher-eastmoney-browser.sock"


def fetch(transport, url: str) -> str:
    response = transport.get(url, timeout=40.0)
    if response.status_code != 200:
        raise SystemExit(f"{url} -> HTTP {response.status_code}")
    if is_access_block_page(response.text):
        raise SystemExit(f"{url} -> access-block shell (verification not cleared)")
    return response.text


def main() -> int:
    stock = sys.argv[1] if len(sys.argv) > 1 else "601012"
    pages = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    urls = [EastmoneyGubaCollector.list_url(stock, n) for n in range(1, pages + 1)]
    transport = EastmoneyBrowserSocketTransport(SOCKET)

    print(f"stock={stock} pages=1..{pages} socket={SOCKET}")
    print("host 缓存的是验证前那一份，page1 故意取两次，用第二次（实时/验证后）\n")

    verified: dict[str, int] = {}
    for index, url in enumerate(urls, start=1):
        if index == 1:
            fetch(transport, url)  # 丢弃：宿主缓存的验证前正文
        html = fetch(transport, url)
        page = parse_list_page(html, stock)
        rows = list(page.rows) + list(page.out_of_scope_rows)
        types = Counter(r.get("post_type") for r in page.out_of_scope_rows)
        span = sorted(
            r.get("post_publish_time") for r in page.out_of_scope_rows
            if r.get("post_publish_time")
        )
        print(
            f"page{index} rows={len(rows)} accepted={len(page.rows)} "
            f"out_of_scope={len(page.out_of_scope_rows)} src_count={page.source_count}"
        )
        for item in page.rows:
            verified[item.source_item_id] = 0
        for raw in page.out_of_scope_rows:
            if isinstance(raw, dict) and raw.get("post_id") is not None:
                verified[str(raw["post_id"])] = raw.get("post_type") or 0
        _ = types, span

    con = sqlite3.connect(f"file:{REPO / 'data/collector.db'}?mode=ro", uri=True)
    stored = {
        sid for (sid,) in con.execute(
            "SELECT source_item_id FROM posts WHERE stock_code=?", (stock,)
        )
    }
    con.close()

    verified_only = sorted(set(verified) - stored)
    # ONLY post_type == 0 ids can signal degradation: the database deliberately
    # stores nothing else (`_parse_item` routes post_type != 0 to out_of_scope),
    # so every non-zero id is *expected* to be absent. Counting them as evidence
    # would manufacture a false "we are being degraded" verdict out of the known
    # scope filter -- which is exactly the mistake this whole exercise is about.
    degraded = [sid for sid in verified_only if verified[sid] == 0]
    expected_absent = [sid for sid in verified_only if verified[sid] != 0]

    print(
        f"\n验证后会话: {len(verified)} 条 (type0={sum(1 for v in verified.values() if v == 0)}, "
        f"非0={sum(1 for v in verified.values() if v != 0)})"
    )
    print(f"库内该股:  {len(stored)} 条")
    print(f"\n验证后视图独有: {len(verified_only)} 条")
    print(f"  其中 post_type != 0（预期缺席，不算证据）: {len(expected_absent)}")
    print(f"  其中 post_type == 0（降级信号）:          {len(degraded)}")
    for sid in degraded[:40]:
        print(f"      ! {sid}")
    if len(degraded) > 40:
        print(f"      … 另有 {len(degraded) - 40} 条")

    if degraded:
        print(
            "\n判定: 验证后视图多出「正常帖子」(post_type==0) 而库中没有"
            " -> 匿名视图被降级，此前基于匿名视图的条数结论作废。"
        )
    else:
        print(
            "\n判定: 本次实测的这几页中，验证后视图未多出任何 post_type==0 的帖子"
            " -> 未观测到降级。注意这仍只覆盖实测页，不等于全站无损，"
            "也不能解释用户看到的登录/广告/验证码。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
