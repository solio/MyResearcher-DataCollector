#!/usr/bin/env python
"""fill_stock_names.py — 按股票代码自动补全名称

来源优先级：
  1. config/stock_names.json（本脚本维护的缓存）
  2. config/targets.short-term.json 的 stock_names
  3. 腾讯行情 qt.gtimg.cn（每批 50 个代码，字段 index=1 是名称）

输出 config/stock_names.json：{"stock_names": {code: name}, "_meta": {...}}

用法：
    fill_stock_names.py            # 补全缺失项并写回
    fill_stock_names.py --dry-run  # 只报告缺哪些，不写文件
"""
import json
import subprocess
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "collector.db"
CACHE = ROOT / "config" / "stock_names.json"
TARGETS = ROOT / "config" / "targets.short-term.json"


def db_codes() -> set:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    codes = {r[0] for r in con.execute("SELECT DISTINCT stock_code FROM posts") if r[0]}
    try:
        codes |= {r[0] for r in con.execute("SELECT DISTINCT stock_code FROM source_item_observations") if r[0]}
    except sqlite3.Error:
        pass
    return codes


def load_known() -> dict:
    names = {}
    if CACHE.exists():
        names.update(json.loads(CACHE.read_text(encoding="utf-8")).get("stock_names") or {})
    if TARGETS.exists():
        names.update(json.loads(TARGETS.read_text(encoding="utf-8")).get("stock_names") or {})
    return names


def fetch(codes: list) -> dict:
    """腾讯行情批量取名称；失败返回空字典，不抛异常。"""
    out = {}
    for i in range(0, len(codes), 50):
        chunk = codes[i:i + 50]
        q = ",".join(("sh" if c.startswith(("60", "68")) else "sz") + c for c in chunk)
        try:
            raw = subprocess.run(["curl", "-s", "--max-time", "15", f"https://qt.gtimg.cn/q={q}"],
                                 capture_output=True, timeout=25).stdout.decode("gb18030", "replace")
        except (OSError, subprocess.TimeoutExpired):
            continue
        for line in raw.strip().split(";"):
            p = line.split("~")
            if len(p) > 2 and p[2].isdigit() and p[1].strip():
                out[p[2]] = p[1].strip()
    return out


def main() -> int:
    if not DB.exists():
        print(f"db not found: {DB}", file=sys.stderr)
        return 2
    codes = sorted(db_codes())
    known = load_known()
    missing = [c for c in codes if c not in known]
    print(f"库内代码 {len(codes)} 个，已有名称 {len(codes) - len(missing)} 个，缺失 {len(missing)} 个")
    if missing:
        print("缺失:", " ".join(missing))
    if "--dry-run" in sys.argv:
        return 0
    fetched = fetch(missing) if missing else {}
    known.update({k: v for k, v in fetched.items() if k in set(missing)})
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({
        "_meta": {"desc": "code → 名称，由 fill_stock_names.py 维护；缺失时回落到代码",
                  "source": "targets.short-term.json + qt.gtimg.cn"},
        "stock_names": dict(sorted(known.items())),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    still = [c for c in codes if c not in known]
    print(f"补全 {len(fetched)} 个；仍未命名 {len(still)} 个 {still[:10]}")
    print("写出", CACHE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
