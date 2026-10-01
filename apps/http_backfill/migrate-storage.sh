#!/usr/bin/env bash
# Run from any directory; all data paths come from this app's compose.yml.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

printf '%s\n' '构建新版本（构建期间旧服务继续运行）…'
docker compose build collector-console
printf '%s\n' '停止采集服务，准备离线迁移…'
docker compose stop collector-console
trap 'printf "%s\n" "迁移或启动未完成，请检查上方错误。旧库仅在完整校验成功后删除；修复后重新执行 bash migrate-storage.sh。" >&2' ERR
docker compose run --rm --no-deps collector-console \
  python apps/http_backfill/migrate_storage.py --data-dir /data
printf '%s\n' '迁移完成，启动服务…'
docker compose up -d --no-build collector-console
docker compose exec -T collector-console python - <<'PY'
import time
import urllib.request
for attempt in range(30):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8790/healthz', timeout=2) as response:
            if response.status == 200:
                break
    except (OSError, urllib.error.URLError):
        if attempt == 29:
            raise SystemExit('新服务未通过健康检查，请查看 docker compose logs --tail=100 collector-console')
        time.sleep(1)
print('健康检查通过。数据位于 /data/collector.db；任务按重启规则暂停，请在控制台检查后继续。')
PY
trap - ERR
