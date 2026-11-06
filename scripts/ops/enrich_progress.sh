#!/bin/zsh
# enrich_progress.sh —— 随时查看 detail enrich 的进度
# 用法： zsh scripts/ops/enrich_progress.sh
cd "${0:A:h}/../.." || exit 1

LEDGER=runtime/logs/eastmoney-detail-enrichment.jsonl

echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo

if pgrep -f "enrich-details" >/dev/null; then
  echo "状态: 在跑（PID $(pgrep -f enrich-details | head -1)）"
else
  echo "状态: 没有 enrich 进程在跑"
fi
pgrep -f "backfill_then_enrich" >/dev/null && echo "驱动: backfill_then_enrich 在跑"

echo
echo "=== 待办（缺正文 / 长标题的帖）==="
PYTHONPATH=src python3 scripts/ops/enrich_plan.py list 2>/dev/null | awk '{s+=$2; n++} END {printf "  %d 帖 / %d 只\n", s+0, n}'

echo
echo "=== 台账：最近 30 分钟的写入 ==="
python3 - "$LEDGER" <<'PY'
import json, sys, collections
from datetime import datetime, timezone, timedelta
cut = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S")
n = collections.Counter(); last = None
try:
    for line in open(sys.argv[1], encoding="utf-8"):
        line = line.strip()
        if not line: continue
        try: r = json.loads(line)
        except ValueError: continue
        ts = str(r.get("timestamp", ""))
        if ts >= cut:
            n[r.get("result")] += 1; last = ts
except OSError:
    print("  (还没有台账文件)"); raise SystemExit
for k, v in sorted(n.items()):
    print(f"  {k:<28}{v}")
print(f"  合计 {sum(n.values())}")
if last:
    print(f"  最后一条: {last}")
PY

echo
echo "=== 库现状 ==="
python3 - <<'PY'
import sqlite3
c = sqlite3.connect("file:data/collector.db?mode=ro", uri=True)
c.execute("PRAGMA busy_timeout=15000")
t, f = c.execute("SELECT count(*), sum(content IS NOT NULL) FROM posts").fetchone()
print(f"  posts {t:,}   有正文 {f:,}")
PY
