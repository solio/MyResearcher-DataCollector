#!/usr/bin/env bash
# Run from any directory; use --node for the standalone collector preset.
set -euo pipefail
compose_cmd=(docker compose)
retry_command='bash migrate-storage.sh'
if (($# == 0)); then
  : # Existing hub defaults.
elif (($# == 1)) && [[ $1 == --node ]]; then
  compose_cmd+=(-f compose.node.yml)
  retry_command+=' --node'
else
  printf '%s\n' 'Usage: bash migrate-storage.sh [--node]' '--node: 使用独立采集节点预设；不带参数使用中央控制台预设。' >&2
  exit 2
fi
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

printf '%s\n' '构建新版本（构建期间旧服务继续运行）…'
"${compose_cmd[@]}" build collector-console
printf '%s\n' '停止采集服务，准备离线迁移…'
"${compose_cmd[@]}" stop collector-console
trap 'printf "%s\n" "迁移或启动未完成，请检查上方错误。旧库仅在完整校验成功后删除；修复后重新执行 ${retry_command}。" >&2' ERR
"${compose_cmd[@]}" run --rm --no-deps collector-console \
  python apps/http_backfill/migrate_storage.py --data-dir /data
printf '%s\n' '迁移完成，启动服务…'
"${compose_cmd[@]}" up -d --no-build collector-console
"${compose_cmd[@]}" exec -T collector-console python - <<'PY'
import time
import urllib.request
for attempt in range(30):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8790/healthz', timeout=2) as response:
            if response.status == 200:
                break
    except (OSError, urllib.error.URLError):
        if attempt == 29:
            raise SystemExit('新服务未通过健康检查，请查看采集服务日志')
        time.sleep(1)
print('健康检查通过。数据位于 /data/collector.db；任务按重启规则暂停，请在控制台检查后继续。')
PY
trap - ERR
