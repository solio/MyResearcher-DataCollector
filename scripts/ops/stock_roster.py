#!/usr/bin/env python
"""stock_roster.py — 从数据库派生采集名单（唯一事实来源）

背景：backfill_all_stocks.sh 过去维护一个硬编码数组，理由是“没有权威名单”。
但那导致：新采的股票只要忘了加数组，就永远不被 backfill 覆盖（2026-10-12 已
发生过一次：4 只票数据停在 09-30）。

这里改为从库里派生：
    posts（有帖子的股票） ∪ source_item_observations（若存在）
    ∪ backfill_coverage（若存在）

用法：
    stock_roster.py            # 每行一个代码
    stock_roster.py --check    # 只打印数量与来源，供驱动脚本做 SANITY
"""
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "collector.db"


def main() -> int:
    if not DB.exists():
        print(f"roster: db not found: {DB}", file=sys.stderr)
        return 2
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    codes, sources = set(), {}

    def add(sql, label):
        try:
            got = {r[0] for r in con.execute(sql) if r[0]}
        except sqlite3.Error:
            return
        sources[label] = len(got)
        codes.update(got)

    add("SELECT DISTINCT stock_code FROM posts", "posts")
    if "source_item_observations" in tables:
        add("SELECT DISTINCT stock_code FROM source_item_observations", "observations")
    if "backfill_coverage" in tables:
        add("SELECT DISTINCT stock_code FROM backfill_coverage", "coverage")

    if "--check" in sys.argv:
        print(f"ROSTER {len(codes)} stocks | " + ", ".join(f"{k}={v}" for k, v in sources.items()))
        return 0 if codes else 1
    for c in sorted(codes):
        print(c)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
