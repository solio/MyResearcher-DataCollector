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
    docker compose build collector-console
    docker compose up -d collector-console
  else
    docker-compose build collector-console
    docker-compose up -d collector-console
  fi
fi

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
