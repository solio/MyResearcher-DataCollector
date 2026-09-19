#!/usr/bin/env python3
"""Cross-session completeness check: does the list we are served NOW still
contain the posts an EARLIER collection session actually stored?

WHY THIS EXISTS
---------------
Two fetches in the same session agreeing proves only that they are the same
view. A genuinely useful anchor is the *other* direction in time: posts that
were collected by an EARLIER session (different day, possibly different browser
and code path) and that the site still serves. If today's served list were a
degraded subset, earlier-session posts would stop appearing in it -- and that is
detectable without needing a verified session.

It answers two questions, and a NON-ZERO answer to the first is the one that
would matter:

  * served-not-stored, post_type==0
        The list serves a type-0 post the database does not have.
        -> a real collection gap. Must be zero for "we got everything".
  * stored-not-served
        An earlier session stored it, today's list does not serve it.
        -> NOT automatically bad. A deleted post is legitimately absent from
           every list. So each one is probed directly: the site redirects
           removed posts to /error?type=2. Only a post that is STILL LIVE at
           its own URL yet absent from the list is evidence of shrinkage.
  * the same counts for post_type != 0 are printed for completeness only. The
    database stores post_type==0 by contract, so non-zero items are EXPECTED to
    be absent and must never be read as evidence.

TWO TRAPS THIS SCRIPT EXISTS TO AVOID (both were hit for real, 2026-09-19)
-------------------------------------------------------------------------
1. Window mismatch. If you compare the DB with `published_at >= <some day>`
   against a walk that goes back further (or less far), you manufacture a
   phantom gap out of pure arithmetic. The comparison window is therefore
   derived from the walk itself: only DB rows inside [oldest, newest] observed
   are compared. Nothing outside the walked window is called a gap.
2. Timestamp format. The DB writes `2026-09-17T09:45:08+08:00`; the list
   payload gives `2026-09-17 09:45:08`. `'T' > ' '`, so a naive SQL/string
   range filter silently admits or drops whole days. Everything is normalized
   through `norm()` before any comparison.

USAGE
    python scripts/ops/served_vs_stored_diff.py <stock_code> [max_pages] [--no-probe]

Read-only against the database. Walks the publish-ordered `f` surface, which is
what the collector itself requests.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DB = REPO / "data" / "collector.db"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Referer": "https://guba.eastmoney.com/"}


def norm(value: object) -> str:
    """Normalize any of the timestamp shapes in play to 'YYYY-MM-DD HH:MM:SS'.

    The DB uses ISO-8601 with a 'T' and an offset; the list payload uses a
    space and no offset. Comparing them raw is wrong because 'T' > ' '.
    """
    text = str(value).strip().replace("T", " ")
    for suffix in ("+08:00", "+0800", "Z", "+00:00"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text[:19]


def list_url(stock: str, page: int) -> str:
    if page == 1:
        return f"https://guba.eastmoney.com/list,{stock},f.html"
    return f"https://guba.eastmoney.com/list,{stock},f_{page}.html"


def post_url(stock: str, post_id: str) -> str:
    return f"https://guba.eastmoney.com/news,{stock},{post_id}.html"


def get(url: str, timeout: int = 25) -> str:
    request = urllib.request.Request(url, headers=HEADERS)
    return urllib.request.urlopen(request, timeout=timeout).read().decode("utf-8", "replace")


def fetch_list(stock: str, page: int) -> dict:
    html = get(list_url(stock, page))
    match = re.search(r"var article_list\s*=\s*(\{.*?\});", html, re.S)
    if not match:
        raise SystemExit(f"{list_url(stock, page)} -> no article_list (blocked shell?)")
    return json.loads(match.group(1))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is the signal, so refuse to follow it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


PROBE = urllib.request.build_opener(NoRedirect)


def is_removed(stock: str, post_id: str) -> bool:
    """True when the site itself says the post is gone.

    Removed posts 302 to /error?type=2. A live post answers 200 in place. If the
    probe cannot tell, it returns False so the post stays in the suspicious set
    rather than being quietly excused.
    """
    url = post_url(stock, post_id)
    try:
        with PROBE.open(urllib.request.Request(url, headers=HEADERS), timeout=25) as resp:
            return "error" in resp.geturl()
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            return "error" in (exc.headers.get("Location") or "")
        return False
    except Exception:  # noqa: BLE001 - network noise must not excuse a post
        return False


def walk(stock: str, max_pages: int) -> dict[str, dict]:
    seen: dict[str, dict] = {}
    for page in range(1, max_pages + 1):
        payload = fetch_list(stock, page)
        rows = payload.get("re") or []
        if not rows:
            print(f"page{page} rc={payload.get('rc')} rows=0 (EOF)")
            break
        times = [norm(r.get("post_publish_time")) for r in rows]
        types = Counter(r.get("post_type") or 0 for r in rows)
        print(
            f"page{page} rc={payload.get('rc')} rows={len(rows)} types={dict(types)} "
            f"span={min(times)} .. {max(times)}"
        )
        for row in rows:
            seen[str(row.get("post_id"))] = {
                "type": row.get("post_type") or 0,
                "at": norm(row.get("post_publish_time")),
            }
    return seen


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    probe = "--no-probe" not in sys.argv
    stock = args[0] if args else "601012"
    max_pages = int(args[1]) if len(args) > 1 else 8

    served = walk(stock, max_pages)
    if not served:
        print("no rows served; nothing to compare")
        return 1
    lo = min(v["at"] for v in served.values())
    hi = max(v["at"] for v in served.values())
    print(f"\n走查窗口（由走查自身推导）= {lo} .. {hi}")

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    stored = {
        str(sid): norm(pub)
        for sid, pub in con.execute(
            "SELECT source_item_id, published_at FROM posts WHERE stock_code=?", (stock,)
        )
    }
    con.close()

    in_window = {sid for sid, at in stored.items() if lo <= at <= hi}
    served_type0 = {s for s, v in served.items() if v["type"] == 0}
    served_typeN = {s for s, v in served.items() if v["type"] != 0}

    gap = sorted(served_type0 - in_window, key=int)
    suspicious = sorted(in_window - set(served), key=int)

    print(f"\nserved={len(served)} (type0={len(served_type0)}, 非0={len(served_typeN)})")
    print(f"DB({stock})={len(stored)}   其中落在走查窗内={len(in_window)}")

    print(f"\n★ 真缺口 served-not-stored, post_type==0 : {len(gap)}")
    for sid in gap:
        print(f"    {sid}  served_at={served[sid]['at']}")

    print(f"★ stored-not-served: {len(suspicious)}")
    removed: list[str] = []
    alive: list[str] = []
    for sid in suspicious:
        verdict = "?"
        if probe:
            verdict = "REMOVED" if is_removed(stock, sid) else "STILL-LIVE"
            (removed if verdict == "REMOVED" else alive).append(sid)
        print(f"    {sid}  published_at={stored[sid]}  {verdict}")

    if probe and suspicious:
        # Positive control: the probe must be able to see a live post as live,
        # otherwise "REMOVED" would be a property of the probe, not the site.
        control = sorted(served_type0, key=int)[:3]
        print("\n 控制组（列表里现存帖子，探针必须判为 STILL-LIVE）:")
        for sid in control:
            print(f"    {sid}  {'REMOVED' if is_removed(stock, sid) else 'STILL-LIVE'}")

    print()
    if gap:
        print(f"判定: 列表提供了 {len(gap)} 条库中没有的 type-0 帖子 -> 存在真实采集缺口，需补。")
    elif alive:
        print(
            f"判定: 缺口 0；但 {len(alive)} 条帖子站点仍可访问却未出现在列表中"
            f" -> 服务窗口收缩证据，需进一步确认。"
        )
    else:
        print(
            f"判定: 缺口 0；stored-not-served 中 {len(removed)} 条经探测确认已被站点删除"
            "（302 -> /error?type=2），不是降级。"
            "\n      即：早前会话存下的帖子，今日列表仍提供全部未删帖。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
