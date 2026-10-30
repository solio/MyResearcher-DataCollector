#!/bin/bash
# Run update-node.sh on every listed host from one machine that can log in to all of them.
#
#     bash apps/http_backfill/deploy/update-fleet.sh                 # the five known nodes
#     bash apps/http_backfill/deploy/update-fleet.sh 8.148.252.117   # just one
#     bash apps/http_backfill/deploy/update-fleet.sh --skip-sweep    # pass flags through
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOSTS=()
FLAGS=()
for arg in "$@"; do
  case "$arg" in
    --skip-build|--skip-sweep) FLAGS+=("$arg") ;;
    -h|--help) echo "用法: $0 [host ...] [--skip-build] [--skip-sweep]"; exit 0 ;;
    *) HOSTS+=("$arg") ;;
  esac
done
if [ ${#HOSTS[@]} -eq 0 ]; then
  HOSTS=(8.148.251.114 8.148.252.117 8.148.253.113 43.163.7.166 43.160.196.129)
fi

failed=0
for host in "${HOSTS[@]}"; do
  echo "########## $host"
  if ssh -o BatchMode=yes -o ConnectTimeout=10 "root@$host" "bash -s -- ${FLAGS[*]:-}" < "$HERE/update-node.sh"; then
    echo "########## $host 完成"
  else
    echo "########## $host 失败（跳过，继续下一台）"
    failed=$((failed + 1))
  fi
done

echo
echo "完成 ${#HOSTS[@]} 台中失败 $failed 台"
exit $([ "$failed" -eq 0 ] && echo 0 || echo 1)
