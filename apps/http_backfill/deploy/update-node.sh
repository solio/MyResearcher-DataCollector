#!/bin/bash
# Update one collector node: check the cached node-list code, patch switch.sh, rebuild the
# collector image, warm the cache and prove the console's own endpoint answers.
#
# Run on the node:
#     bash apps/http_backfill/deploy/update-node.sh
# or piped from a machine that can reach it:
#     ssh root@host 'bash -s' < apps/http_backfill/deploy/update-node.sh
set -euo pipefail

REPO="${COLLECTOR_REPO:-/usr/local/src/MyResearcher-DataCollector}"
PORT="${COLLECTOR_PORT:-8790}"
SKIP_BUILD=0
SKIP_SWEEP=0
for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=1 ;;
    --skip-sweep) SKIP_SWEEP=1 ;;
    -h|--help) echo "用法: $0 [--skip-build] [--skip-sweep]"; exit 0 ;;
    *) echo "未知参数: $arg"; exit 2 ;;
  esac
done

APP="$REPO/apps/http_backfill"
if [ ! -d "$APP" ]; then
  echo "找不到仓库目录: $APP（可用 COLLECTOR_REPO 指定）"
  exit 1
fi

echo "== 0/5 预检：待执行的数据库迁移 =="
DB="$APP/data/collector.db"
if [ -f "$DB" ]; then
  python3 - "$DB" <<'PY'
import sqlite3
import sys

db = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)


def get(key):
    row = db.execute("select value from meta where key=?", (key,)).fetchone()
    return row[0] if row else None


policy = get("content_policy_version")
print("  content_policy_version:", policy if policy is not None else "缺失")
if str(policy) != "3":
    print("  !! 有待执行的迁移：首次启动会扫描 http_post_state，库大时要几分钟，期间接口不可用")
else:
    print("  没有待执行的策略迁移")
PY
else
  echo "  没有 $DB（新库，无需迁移）"
fi

echo "== 1/5 检查代码是否为缓存版 =="
missing=0
grep -q MIHOMO_CACHE_FILE "$APP/mihomo_control.py" || { echo "  mihomo_control.py 还是旧版"; missing=1; }
grep -q "api/mihomo/nodes/refresh" "$APP/server.py" || { echo "  server.py 缺少刷新路由"; missing=1; }
grep -q "loadMihomoNodes(silent)" "$APP/static/app.js" || { echo "  app.js 还是旧版"; missing=1; }
if [ "$missing" != "0" ]; then
  echo "先在仓库拉取/同步最新代码，再重跑本脚本。"
  exit 1
fi
echo "  OK"

if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && [ -f "$DB" ]; then
  echo "  服务在运行：预置同步水位，升级后首次启动不再全量核对"
  python3 - "$DB" <<'PY'
import sqlite3
import sys

db = sqlite3.connect(sys.argv[1])
highest = db.execute("select coalesce(max(rowid),0) from http_posts").fetchone()[0]
db.execute("insert into meta values('storage_sync_rowid',?) on conflict(key) do update set value=excluded.value",
           (str(highest),))
db.execute("insert into meta values('clean_shutdown','1') on conflict(key) do update set value=excluded.value")
db.commit()
print("  水位:", highest)
PY
fi

echo "== 2/5 switch.sh 写入缓存 =="
if [ ! -x /opt/mihomo/switch.sh ]; then
  echo "  跳过：找不到 /opt/mihomo/switch.sh"
elif grep -q CACHE_FILE /opt/mihomo/switch.sh; then
  echo "  已打过补丁"
else
  python3 "$APP/deploy/patch-switch-cache.py" /opt/mihomo/switch.sh
fi

echo "== 3/5 重建 collector =="
if [ "$SKIP_BUILD" = "1" ]; then
  echo "  跳过（--skip-build）"
else
  cd "$APP"
  if docker compose version >/dev/null 2>&1; then
    compose() { docker compose "$@"; }
  else
    compose() { docker-compose "$@"; }
  fi
  compose build collector-console
  # Recreate unconditionally: a compose file without an image tag lets "up -d" keep the old
  # container while the freshly built image sits unused.
  compose up -d --force-recreate --no-deps collector-console
fi

echo "== 3.5/5 校验容器里跑的是新代码 =="
CID=$(cd "$APP" && { docker compose ps -q collector-console 2>/dev/null || docker-compose ps -q collector-console 2>/dev/null; })
if [ -z "$CID" ]; then
  echo "  容器没有跑起来"
  exit 1
fi
if ! docker exec "$CID" grep -q MIHOMO_CACHE_FILE /opt/collector/apps/http_backfill/mihomo_control.py 2>/dev/null; then
  echo "  容器内仍是旧代码（镜像没换），脚本终止"
  exit 1
fi
echo "  OK：容器内已是缓存版"

echo "== 4/5 等待接口就绪 =="
# A rebuilt container may run database migrations on first start; on a multi-gigabyte
# collector.db that took several minutes on the main console. Wait for it instead of
# declaring failure after two minutes.
ready=0
for i in $(seq 1 300); do
  if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then ready=1; break; fi
  if [ $((i % 15)) -eq 0 ]; then
    echo "  仍在启动（已 $((i * 2)) 秒）；首次升级可能在做数据库迁移，勿中断容器"
  fi
  sleep 2
done
if [ "$ready" != "1" ]; then
  echo "  接口 600 秒内未就绪：看 docker logs 确认是否仍在迁移"
fi

echo "== 5/5 测速并写缓存 =="
if [ "$SKIP_SWEEP" = "1" ]; then
  echo "  跳过（--skip-sweep）"
elif [ -x /opt/mihomo/switch.sh ]; then
  MIHOMO_TEST_TIMEOUT="${MIHOMO_TEST_TIMEOUT:-4000}" \
  MIHOMO_TEST_WORKERS="${MIHOMO_TEST_WORKERS:-12}" \
    timeout 240 /opt/mihomo/switch.sh latency 2>&1 | tail -4 || echo "  测速未完成（已有缓存仍然可用）"
else
  echo "  跳过：没有 switch.sh，只能等后台测速（需要容器能访问 mihomo 控制器）"
fi

echo "== 结果：控制台同一条接口 =="
TOKEN_FILE="$APP/data/console.token"
if [ -f "$TOKEN_FILE" ]; then
  TOKEN_EARLY=$(cat "$TOKEN_FILE")
  FIRST=$(curl -s -o /dev/null -w "%{time_total}" -H "Authorization: Bearer $TOKEN_EARLY" "http://127.0.0.1:$PORT/api/status" --max-time 30)
  SECOND=$(curl -s -o /dev/null -w "%{time_total}" -H "Authorization: Bearer $TOKEN_EARLY" "http://127.0.0.1:$PORT/api/status" --max-time 30)
  echo "  /api/status 耗时：首次 ${FIRST}s，再次 ${SECOND}s（缓存生效应 < 0.1s）"
fi
TOKEN_FILE="$APP/data/console.token"
if [ ! -f "$TOKEN_FILE" ]; then
  echo "  找不到 $TOKEN_FILE，无法校验"
  exit 1
fi
TOKEN=$(cat "$TOKEN_FILE")
if curl -fsS -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/api/mihomo/nodes" -o /tmp/nodes.json; then
  python3 - <<'PY'
import json
data = json.load(open("/tmp/nodes.json", encoding="utf-8"))
nodes = data.get("nodes") or []
print("  节点数:", len(nodes), "| 缓存年龄:", data.get("age"), "秒 | 当前:", data.get("now"))
print("  前三个:", ", ".join(f"{n['name']} {n['delay']}ms" for n in nodes[:3]) or "无")
PY
else
  echo "  接口没有返回 200，控制台仍然看不到节点"
  exit 1
fi
