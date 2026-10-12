#!/bin/bash
# setup-mihomo-local-proxy.sh — 给 http_backfill 采集节点配置本地 mihomo 出站代理
#
# 设计原则：只做 app 层接入 —— http_backfill 的出站代理指向本机 mihomo 端口；
# 不改系统代理、不设 TUN、不动路由、不监听 0.0.0.0。
#
# 用法:
#   bash setup-mihomo-local-proxy.sh                  # 订阅 URL 自动读 /opt/mihomo/subscription.url
#   bash setup-mihomo-local-proxy.sh <订阅URL>         # 或直接传参（首次部署用）
#
# 幂等：可重复执行；已完成的步骤自动跳过。
#
# v2 修复：
#   - 移除所有 `| head -c N`（pipefail 下 SIGPIPE 会以 exit 23 中止脚本）→ 改用 cut/sed 全读
#   - 出口验证增加"必须与本机出口 IP 不同"（防止节点健康检查未完成时 mihomo 直连造成假阳性）
set -euo pipefail

MIHOMO_DIR=/opt/mihomo
SUB_URL="${1:-${SUB_URL:-}}"
if [ -z "$SUB_URL" ] && [ -f "$MIHOMO_DIR/subscription.url" ]; then
  SUB_URL=$(cat "$MIHOMO_DIR/subscription.url")
fi
if [ -z "$SUB_URL" ]; then
  echo "缺少订阅 URL：传参（$0 <URL>）或先写入 $MIHOMO_DIR/subscription.url"
  exit 1
fi

echo "== [1/7] 定位 http_backfill =="
CONTAINER=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -iE 'collector-console|http[-_]backfill' | sed -n '1p' || true)
APP_BASE="http://127.0.0.1:8790"
TOKEN_FILE=""
if [ -n "$CONTAINER" ]; then
  MODE=docker
  GATEWAY=$(docker inspect "$CONTAINER" --format '{{range .NetworkSettings.Networks}}{{.Gateway}}{{"\n"}}{{end}}' | awk 'NF{print; exit}')
  if [ -z "$GATEWAY" ]; then echo "!! 无法解析容器网关，退出"; exit 1; fi
  LISTEN_IP="$GATEWAY"
  WORKDIR=$(docker inspect "$CONTAINER" --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}')
  TOKEN_FILE="$WORKDIR/data/console.token"
  echo "   模式: docker | 容器: $CONTAINER"
  echo "   容器网关: $GATEWAY | compose 目录: $WORKDIR"
else
  MODE=native
  LISTEN_IP=127.0.0.1
  TOKEN_FILE=/var/lib/http-backfill/console.token
  echo "   模式: native（未发现容器）| 监听: 127.0.0.1"
fi
LISTEN_PORT=7891

echo "== [2/7] 安装 mihomo 内核 =="
mkdir -p "$MIHOMO_DIR"
if [ -x "$MIHOMO_DIR/mihomo" ]; then
  echo "   已存在: $("$MIHOMO_DIR/mihomo" -v 2>/dev/null | sed -n '1p')"
else
  if ! systemctl cat mihomo >/dev/null 2>&1; then
    for p in 7890 "$LISTEN_PORT" 9090; do
      if ss -tln 2>/dev/null | grep ":$p " >/dev/null; then
        echo "!! 端口 $p 已被其他进程占用，请先处理再重跑"; exit 1
      fi
    done
  fi
  TAG=$(curl -s -o /dev/null -w '%{redirect_url}' --max-time 30 https://github.com/MetaCubeX/mihomo/releases/latest | sed 's#.*/##')
  if [ -z "$TAG" ]; then
    echo "!! 无法获取 mihomo 版本（网络问题？）。可手动下载 linux-amd64 版本解压为 $MIHOMO_DIR/mihomo 后重跑"; exit 1
  fi
  echo "   下载 $TAG ..."
  curl -L --max-time 300 -sS -o "$MIHOMO_DIR/mihomo.gz" "https://github.com/MetaCubeX/mihomo/releases/download/$TAG/mihomo-linux-amd64-$TAG.gz"
  gzip -df "$MIHOMO_DIR/mihomo.gz"
  chmod +x "$MIHOMO_DIR/mihomo"
  echo "   完成: $("$MIHOMO_DIR/mihomo" -v 2>/dev/null | sed -n '1p')"
fi

echo "== [3/7] 写入 mihomo 配置 =="
mkdir -p "$MIHOMO_DIR/providers"
cat > "$MIHOMO_DIR/config.yaml" <<CFG
mixed-port: 7890
allow-lan: false
bind-address: 127.0.0.1
mode: rule
log-level: warning
external-controller: 127.0.0.1:9090

listeners:
  - name: collector-mixed
    type: mixed
    port: $LISTEN_PORT
    listen: $LISTEN_IP

proxy-providers:
  westworld:
    type: http
    url: $SUB_URL
    interval: 3600
    path: ./providers/westworld.yaml
    health-check:
      enable: true
      url: http://www.gstatic.com/generate_204
      interval: 600

proxy-groups:
  - name: auto
    type: url-test
    use:
      - westworld
    url: http://www.gstatic.com/generate_204
    interval: 300
    tolerance: 50
  - name: WW
    type: select
    proxies:
      - auto
    use:
      - westworld

rules:
  - MATCH,WW
CFG
chmod 600 "$MIHOMO_DIR/config.yaml"
printf '%s' "$SUB_URL" > "$MIHOMO_DIR/subscription.url"
chmod 600 "$MIHOMO_DIR/subscription.url"
echo "   已写入 config.yaml + subscription.url（权限 0600）"

echo "== [4/7] systemd 服务 =="
cat > /etc/systemd/system/mihomo.service <<UNIT
[Unit]
Description=mihomo local proxy for http_backfill
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$MIHOMO_DIR/mihomo -d $MIHOMO_DIR
Restart=always
RestartSec=3
LimitNOFILE=1000000

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable mihomo >/dev/null 2>&1
systemctl restart mihomo
sleep 5
if systemctl is-active --quiet mihomo; then
  echo "   服务: active（已设开机自启）"
else
  echo "!! mihomo 服务未启动，最近日志："
  journalctl -u mihomo -n 20 --no-pager || true
  exit 1
fi

echo "== [5/7] 验证出口（出口 IP 必须与本机不同；首次需等节点健康检查）=="
HOST_IP=$(curl -s --max-time 8 https://ipinfo.io/json | grep -o '"ip": "[^"]*"' | sed -n '1p' | cut -d'"' -f4)
if [ -z "$HOST_IP" ]; then echo "!! 无法获取本机出口 IP（网络异常？），退出"; exit 1; fi
echo "   本机出口: $HOST_IP"
EXIT_OK=""
for i in 1 2 3 4 5 6 7 8; do
  if [ "$MODE" = docker ]; then
    R=$(docker exec "$CONTAINER" curl -x "http://$LISTEN_IP:$LISTEN_PORT" -s --max-time 12 https://ipinfo.io/json || true)
  else
    R=$(curl -x "http://$LISTEN_IP:$LISTEN_PORT" -s --max-time 12 https://ipinfo.io/json || true)
  fi
  RIP=$(printf '%s' "$R" | grep -o '"ip": "[^"]*"' | sed -n '1p' | cut -d'"' -f4)
  if [ -n "$RIP" ] && [ "$RIP" != "$HOST_IP" ]; then
    printf '%s' "$R" | cut -c1-240; echo
    EXIT_OK=1; break
  fi
  echo "   retry $i（出口未变化或为空，等待节点健康检查完成）..."; sleep 6
done
if [ -z "$EXIT_OK" ]; then
  echo "!! 代理出口验证失败（出口 IP 始终与本机相同或为空），检查: journalctl -u mihomo -n 50"; exit 1
fi

echo "== [6/7] 接入 http_backfill（仅 app 层）=="
if [ -f "$TOKEN_FILE" ]; then
  T=$(cat "$TOKEN_FILE")
  TARGET="http://$LISTEN_IP:$LISTEN_PORT"
  CUR=$(curl -s -H "Authorization: Bearer $T" "$APP_BASE/api/proxy" || true)
  case "$CUR" in
    *"\"endpoint\": \"$TARGET\""*)
      echo "   app 出站已是 $TARGET（跳过）" ;;
    *)
      printf '{"mode":"http","endpoint":"%s"}' "$TARGET" > /tmp/.ff-proxy.json
      RESP=$(curl -s -X POST -H "Authorization: Bearer $T" -H "Content-Type: application/json" \
        -d @/tmp/.ff-proxy.json "$APP_BASE/api/proxy/config" || true)
      rm -f /tmp/.ff-proxy.json
      printf '%s' "$RESP" | cut -c1-200; echo
      echo "   已切换 app 出站 -> $TARGET（原任务/断点/阻断证据保留）" ;;
  esac
else
  echo "!! 未找到 token 文件（$TOKEN_FILE）"
  echo "   请在 H5 控制台手动把出站代理设为 http://$LISTEN_IP:$LISTEN_PORT"
fi

echo "== [7/7] 部署节点切换工具 switch.sh =="
cat > "$MIHOMO_DIR/switch.sh" <<'SWITCH'
#!/bin/bash
set -e
API="http://127.0.0.1:9090/proxies/WW"
CMD="${1:-}"
case "$CMD" in
  ""|-h|--help|help)
    echo "用法:"
    echo "  switch.sh list          查看当前节点与全部可选节点"
    echo "  switch.sh auto          切回自动择优"
    echo "  switch.sh <关键字>      切换至第一个名称包含关键字的节点"
    ;;
  list|ls)
    curl -s "$API" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(\"当前选中:\", d.get(\"now\"))
print(\"可选节点:\")
for i,n in enumerate(d.get(\"all\",[])): print(\" %2d. %s\" % (i+1, n))
"
    ;;
  auto)
    curl -s -X PUT "$API" -H "Content-Type: application/json" -d "{\"name\":\"auto\"}"
    echo "已切回: auto (自动择优)"
    ;;
  *)
    NAME=$(curl -s "$API" | python3 -c "
import json,sys
d=json.load(sys.stdin)
kw=sys.argv[1]
m=[n for n in d.get(\"all\",[]) if kw in n]
print(m[0] if m else \"\")
" "$CMD")
    if [ -z "$NAME" ]; then echo "没找到名称包含 [$CMD] 的节点"; exit 1; fi
    curl -s -X PUT "$API" -H "Content-Type: application/json" -d "{\"name\":\"$NAME\"}"
    echo "已切换: $NAME"
    ;;
esac
SWITCH
chmod +x "$MIHOMO_DIR/switch.sh"

echo
echo "===================== 全部完成 ====================="
echo "  节点查看/切换: $MIHOMO_DIR/switch.sh list | auto | <关键字>"
echo "  mihomo 控制台（仅本机）: http://127.0.0.1:9090"
echo "  重跑本脚本即可幂等更新（订阅/配置/服务/接入全检查）"
