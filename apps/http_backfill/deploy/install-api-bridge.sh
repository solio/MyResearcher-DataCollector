#!/bin/bash
# Let the collector container reach the host's mihomo controller.
#
# mihomo listens on 127.0.0.1:9090, and inside a container that address is the container
# itself. This installs a small TCP forwarder on the docker bridge address (the host as the
# container sees it), points MIHOMO_API at it and recreates the collector.
#
#     bash apps/http_backfill/deploy/install-api-bridge.sh [node-name-for-verification]
set -euo pipefail

REPO="${COLLECTOR_REPO:-/usr/local/src/MyResearcher-DataCollector}"
APP="$REPO/apps/http_backfill"
UNIT=/etc/systemd/system/mihomo-api-bridge.service
BRIDGE=/opt/mihomo/api-bridge.py

CONTAINER=$(docker ps --format '{{.Names}}' | grep -i collector | head -1)
if [ -z "$CONTAINER" ]; then
  echo "找不到 collector 容器"; exit 1
fi
NETWORK=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}' "$CONTAINER")
GATEWAY=$(docker network inspect "$NETWORK" -f '{{(index .IPAM.Config 0).Gateway}}')
if [ -z "$GATEWAY" ]; then
  echo "无法确定 $NETWORK 的网关地址"; exit 1
fi
echo "容器网络 $NETWORK，宿主机地址 $GATEWAY"

if [ ! -f "$BRIDGE" ]; then
  mkdir -p /opt/mihomo
  cat > "$BRIDGE" <<'PY'
#!/usr/bin/env python3
"""Forward the docker-bridge address to the local mihomo controller.

mihomo keeps listening on 127.0.0.1:9090 so /opt/mihomo/switch.sh keeps working unchanged.
Containers reach the same controller through the bridge address this proxy listens on.
"""
import socket
import sys
import threading

LISTEN_HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
LISTEN_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 9090
TARGET_HOST = sys.argv[3] if len(sys.argv) > 3 else "127.0.0.1"
TARGET_PORT = int(sys.argv[4]) if len(sys.argv) > 4 else 9090


def pipe(source, destination):
    try:
        while True:
            chunk = source.recv(65536)
            if not chunk:
                break
            destination.sendall(chunk)
    except OSError:
        pass
    finally:
        try:
            destination.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def handle(client):
    try:
        upstream = socket.create_connection((TARGET_HOST, TARGET_PORT), timeout=10)
    except OSError:
        client.close()
        return
    threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()
    pipe(upstream, client)


def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LISTEN_HOST, LISTEN_PORT))
    server.listen(128)
    while True:
        client, _ = server.accept()
        threading.Thread(target=handle, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
PY
  chmod 755 "$BRIDGE"
fi

cat > "$UNIT" <<UNIT
[Unit]
Description=Forward docker bridge to the local mihomo controller
After=network-online.target mihomo.service
Wants=mihomo.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 $BRIDGE $GATEWAY 9090 127.0.0.1 9090
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable mihomo-api-bridge.service >/dev/null 2>&1 || true
systemctl restart mihomo-api-bridge.service
sleep 1
systemctl is-active mihomo-api-bridge.service

if ! docker exec "$CONTAINER" python3 - "$GATEWAY" <<'PY'
import sys
import urllib.request

url = "http://" + sys.argv[1] + ":9090/version"
try:
    urllib.request.urlopen(url, timeout=5).read()
except Exception as exc:
    print("容器仍无法访问控制器:", exc)
    raise SystemExit(1)
print("容器已能访问控制器:", url)
PY
then
  exit 1
fi

if grep -q '^MIHOMO_API=' "$APP/.env" 2>/dev/null; then
  sed -i "s|^MIHOMO_API=.*|MIHOMO_API=http://$GATEWAY:9090|" "$APP/.env"
else
  printf 'MIHOMO_API=http://%s:9090\n' "$GATEWAY" >> "$APP/.env"
fi
echo "MIHOMO_API=http://$GATEWAY:9090"

cd "$APP"
# Seed the storage watermark first: a recreate without it makes the new process re-verify the
# whole ledger, which on a multi-gigabyte database looks like a hang.
if curl -fsS "http://127.0.0.1:8790/healthz" >/dev/null 2>&1 && [ -f "$APP/data/collector.db" ]; then
  python3 - "$APP/data/collector.db" <<'PY'
import sqlite3
import sys

db = sqlite3.connect(sys.argv[1])
highest = db.execute("select coalesce(max(rowid),0) from http_posts").fetchone()[0]
db.execute("insert into meta values('storage_sync_rowid',?) on conflict(key) do update set value=excluded.value",
           (str(highest),))
db.execute("insert into meta values('clean_shutdown','1') on conflict(key) do update set value=excluded.value")
db.commit()
print("同步水位:", highest)
PY
fi
if docker compose version >/dev/null 2>&1; then compose() { docker compose "$@"; }; else compose() { docker-compose "$@"; }; fi
compose up -d --force-recreate --no-deps collector-console >/dev/null
for _ in $(seq 1 300); do
  curl -fsS "http://127.0.0.1:8790/healthz" >/dev/null 2>&1 && break
  sleep 2
done

TOKEN=$(cat "$APP/data/console.token")
code=$(curl -s -o /tmp/bridge-check.json -w '%{http_code}' -X POST \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"___bridge_probe___"}' http://127.0.0.1:8790/api/mihomo/select --max-time 20)
echo "切换接口自检 HTTP $code"
head -c 200 /tmp/bridge-check.json; echo
case "$code" in
  400) echo "OK：控制器可达，返回的是节点不存在" ;;
  *) echo "异常：期望 400（节点不存在），实际 $code" ;;
esac
