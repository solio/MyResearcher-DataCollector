#!/bin/bash
# node_cleanup.sh —— 采集节点磁盘清理（可复用）
#
# 设计原则（踩过的坑）：
#   1) 数据库只 VACUUM，绝不给、不删文件 —— collector.db 里同时有"内容"和"状态"（游标/任务），
#      删文件 = 丢状态，之前已经把主控的 fleet 同步搞坏过一次。
#   2) 默认 dry-run，必须 --apply 才动手。
#   3) 动手前必须有"导出已完成"的证据（--export-file），否则拒绝（除非 --force）。
#   4) 清理前后都打印大小 + DB 行数 + integrity_check，用来证明"没删数据"。
#
# 用法：
#   ./node_cleanup.sh                       # 只报告（默认）
#   ./node_cleanup.sh --export-file /path/x.jsonl          # 报告 + 校验导出完整性
#   ./node_cleanup.sh --apply --export-file /path/x.jsonl  # 真正清理
#   选项：--raw-keep-days N  保留最近 N 天的 raw（默认 0 = 全清 raw）
#         --imports-keep N  .imports/inputs 保留最近 N 个（默认 0 = 全清）
#         --data-dir PATH   指定 data 目录（默认自动探测）
#         --vacuum / --no-vacuum（默认 vacuum）
#         --force           跳过导出校验（危险）
set -uo pipefail

APPLY=0; EXPORT_FILE=""; RAW_KEEP_DAYS=0; IMPORTS_KEEP=0; VACUUM=1; FORCE=0; DATA_DIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --export-file) EXPORT_FILE="${2:-}"; shift ;;
    --raw-keep-days) RAW_KEEP_DAYS="${2:-0}"; shift ;;
    --imports-keep) IMPORTS_KEEP="${2:-0}"; shift ;;
    --data-dir) DATA_DIR="${2:-}"; shift ;;
    --vacuum) VACUUM=1 ;;
    --no-vacuum) VACUUM=0 ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
  shift
done

# ---------- 定位 data 目录 ----------
if [ -z "$DATA_DIR" ]; then
  for c in /usr/local/src/MyResearcher-DataCollector/apps/http_backfill/data \
           /root/MyResearcher-DataCollector/apps/http_backfill/data \
           /opt/collector/data; do
    [ -f "$c/collector.db" ] && { DATA_DIR="$c"; break; }
  done
fi
[ -n "$DATA_DIR" ] && [ -f "$DATA_DIR/collector.db" ] || { echo "找不到 data 目录（用 --data-dir 指定）" >&2; exit 2; }

DB="$DATA_DIR/collector.db"
echo "== 目标 =="
echo "  data    : $DATA_DIR"
echo "  db      : $DB  ($(du -h "$DB" | cut -f1))"
echo "  模式    : $( [ $APPLY -eq 1 ] && echo 'APPLY（会真的删）' || echo 'DRY-RUN（只报告）' )"
echo

# ---------- 现状 ----------
echo "== 现状 =="
du -sh "$DATA_DIR"/* 2>/dev/null | sort -h | tail -12
echo
ROWS_BEFORE=$(python3 - "$DB" <<'PY'
import sqlite3, sys
c=sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True); c.execute("PRAGMA busy_timeout=15000")
print(c.execute("SELECT count(*) FROM posts").fetchone()[0])
PY
)
echo "  posts 行数 : $ROWS_BEFORE"

# ---------- 导出校验（动手前的闸门）----------
if [ -n "$EXPORT_FILE" ]; then
  [ -f "$EXPORT_FILE" ] || { echo "导出文件不存在: $EXPORT_FILE" >&2; exit 3; }
  EX_ROWS=$(wc -l < "$EXPORT_FILE" | tr -d ' ')
  echo "  导出文件   : $EXPORT_FILE  ($EX_ROWS 行)"
  if [ "$EX_ROWS" -lt "$ROWS_BEFORE" ]; then
    echo "  ⚠ 导出少于库内行数（$EX_ROWS < $ROWS_BEFORE）：导出可能不完整" >&2
    [ $FORCE -eq 1 ] || { echo "  拒绝清理（确认无误请加 --force）" >&2; exit 4; }
  else
    echo "  ✓ 导出行数 ≥ 库内行数"
  fi
elif [ $APPLY -eq 1 ] && [ $FORCE -ne 1 ]; then
  echo "  ✗ 没给 --export-file：不知道数据是否已经导出，拒绝清理（确定要清请加 --force）" >&2
  exit 4
fi
echo

# ---------- 计划 ----------
echo "== 计划 =="
RAW_DIR="$DATA_DIR/raw"; IMP_DIR="$DATA_DIR/imports/inputs"
RAW_N=0; RAW_SZ=0; IMP_N=0; IMP_SZ=0
if [ -d "$RAW_DIR" ]; then
  if [ "$RAW_KEEP_DAYS" -le 0 ]; then
    RAW_N=$(find "$RAW_DIR" -type f | wc -l | tr -d ' '); RAW_SZ=$(du -sb "$RAW_DIR" | cut -f1)
  else
    RAW_N=$(find "$RAW_DIR" -type f -mtime +"$RAW_KEEP_DAYS" | wc -l | tr -d ' ')
    RAW_SZ=$(find "$RAW_DIR" -type f -mtime +"$RAW_KEEP_DAYS" -printf '%s\n' 2>/dev/null | awk '{s+=$1} END{print s+0}')
  fi
  echo "  raw/       : 删 $RAW_N 个文件（$(numfmt --to=iec ${RAW_SZ:-0})）  keep-days=$RAW_KEEP_DAYS"
fi
if [ -d "$IMP_DIR" ]; then
  IMP_N=$(find "$IMP_DIR" -type f | wc -l | tr -d ' ')
  if [ "$IMPORTS_KEEP" -ge 0 ]; then
    DROP=$(( IMP_N > IMPORTS_KEEP ? IMP_N - IMPORTS_KEEP : 0 ))
    echo "  imports/   : 共 $IMP_N 个，删最旧 $DROP 个（保留 $IMPORTS_KEEP）"
  fi
fi
[ "$VACUUM" -eq 1 ] && echo "  db         : VACUUM（回收空洞，保留全部行）"

if [ $APPLY -ne 1 ]; then
  echo; echo "== DRY-RUN 结束（什么都没改）=="; exit 0
fi

# ---------- 执行 ----------
echo; echo "== 执行 =="
if [ -d "$RAW_DIR" ] && [ "$RAW_N" -gt 0 ]; then
  if [ "$RAW_KEEP_DAYS" -le 0 ]; then
    find "$RAW_DIR" -type f -delete
  else
    find "$RAW_DIR" -type f -mtime +"$RAW_KEEP_DAYS" -delete
  fi
  echo "  raw/ 已删 $RAW_N 个文件"
fi
if [ -d "$IMP_DIR" ] && [ "$IMPORTS_KEEP" -ge 0 ]; then
  find "$IMP_DIR" -type f -printf '%T@ %p\n' | sort -n | head -n $(( IMP_N > IMPORTS_KEEP ? IMP_N - IMPORTS_KEEP : 0 )) | cut -d' ' -f2- | while read -r f; do rm -f -- "$f"; done
  echo "  imports/ 已删最旧的 $(( IMP_N > IMPORTS_KEEP ? IMP_N - IMPORTS_KEEP : 0 )) 个"
fi
if [ "$VACUUM" -eq 1 ]; then
  python3 - "$DB" <<'PY'
import sqlite3, sys
p=sys.argv[1]
c=sqlite3.connect(p, timeout=30)
c.execute("PRAGMA busy_timeout=30000")
before=c.execute("SELECT count(*) FROM posts").fetchone()[0]
c.execute("VACUUM"); c.commit()
after=c.execute("SELECT count(*) FROM posts").fetchone()[0]
ok=c.execute("PRAGMA integrity_check").fetchone()[0]
print("  VACUUM 完成：posts %d → %d，integrity_check=%s" % (before, after, ok))
if before != after or ok != "ok":
    raise SystemExit("  ✗ VACUUM 后行数或完整性异常")
PY
fi

# ---------- 结果 ----------
echo; echo "== 结果 =="
du -sh "$DATA_DIR"/* 2>/dev/null | sort -h | tail -12
echo
echo "  posts 行数 : $ROWS_BEFORE（清理前）"
df -h "$DATA_DIR" | tail -1
