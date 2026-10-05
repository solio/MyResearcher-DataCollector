"""Per-stock static source routes; inherited providers retain one node budget.

Mihomo uses private authenticated listeners pinned with ``listener.proxy``.
Exported snippets never change the everyday mode, listener or selector.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
from urllib.parse import quote, urlsplit

from proxy import DEFAULTS, ProxyManager, _clean_string, _endpoint

SCHEMA = "http-stock-proxy.v1"
MODES = {"inherit", "direct", "http", "mihomo"}
FIELDS = {"mode", "endpoint", "username", "password", "outbound", "listen_address", "clear_auth"}


class _StockProxyManager(ProxyManager):
    def __init__(self, data_dir, *, clock, job, stock, route):
        self.job, self.stock, self.task_route = job, stock, dict(route)
        super().__init__(data_dir, clock=clock)

    def _public_route(self, route, started):
        public = super()._public_route(route, started)
        public.update(mode=self.task_route["mode"], transport_mode=route["mode"],
                      route_scope="stock", stock=self.stock, outbound=self.task_route["outbound"] or None)
        return public

    def status(self):
        public = super().status()
        public.update(mode=self.task_route["mode"], effective_mode=self.task_route["mode"],
                      route_scope="stock", outbound=self.task_route["outbound"] or None,
                      exit_identity="unknown")
        public["settings"].update(mode=self.task_route["mode"], outbound=self.task_route["outbound"],
                                  listen_address=self.task_route["listen_address"])
        return public


class TaskProxyRoutes:
    def __init__(self, data_dir, base_proxy, *, clock=time.time):
        self.data_dir = Path(data_dir)
        if self.data_dir.is_symlink():
            raise RuntimeError("任务代理配置目录不能是符号链接")
        self.data_dir = self.data_dir.resolve()
        self.path = self.data_dir / "task-proxy.json"
        self.base_proxy, self.clock = base_proxy, clock
        self._lock, self._managers = threading.RLock(), {}
        self._known_secrets, self._storage_error = set(), None
        self._routes = {}
        if self.path.exists() or self.path.is_symlink():
            if self.path.is_symlink() or not self.path.is_file():
                raise RuntimeError("任务代理私密配置文件类型不安全")
            try:
                if self.path.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError
                saved = json.loads(self.path.read_text("utf-8"))
                if not isinstance(saved, dict) or saved.get("schema") != SCHEMA or not isinstance(saved.get("routes"), dict):
                    raise ValueError
                if len(saved["routes"]) > 4096:
                    raise ValueError
                for key, row in saved["routes"].items():
                    if not isinstance(row, dict) or key != self._key(row.get("job"), row.get("stock")):
                        raise ValueError
                    stored = row.get("config")
                    if isinstance(stored, dict) and stored.get("mode") == "mihomo" and not (stored.get("username") and stored.get("password")):
                        raise ValueError
                    cfg = self._validate(stored, previous=self._defaults())
                    self._routes[key] = {"job": row["job"], "stock": row["stock"], "config": cfg}
                    self._remember(cfg)
                self._validate_ports(self._routes)
                os.chmod(self.path, 0o600)
            except Exception as exc:
                raise RuntimeError("任务代理私密配置损坏或版本不支持；原文件已保留") from exc

    @staticmethod
    def _key(job, stock):
        _clean_string(job, "job", 256)
        if not job or not isinstance(stock, str) or not re.fullmatch(r"\d{6}", stock):
            raise ValueError("任务代理需要有效任务 ID 和六位股票代码")
        return hashlib.sha256((job + "\0" + stock).encode("utf-8")).hexdigest()

    @staticmethod
    def _defaults():
        return {"mode": "inherit", "endpoint": "", "username": "", "password": "",
                "outbound": "", "listen_address": "127.0.0.1"}

    def _validate(self, partial, *, previous):
        if not isinstance(partial, dict) or set(partial) - FIELDS:
            raise ValueError("未知任务代理配置字段")
        if "clear_auth" in partial and type(partial["clear_auth"]) is not bool:
            raise ValueError("clear_auth 必须为布尔值")
        cfg = dict(previous)
        for key, value in partial.items():
            if key == "clear_auth":
                continue
            value = _clean_string(value, key, 2048 if key == "endpoint" else 1024)
            if key in {"username", "password"} and not value:
                continue
            cfg[key] = value
        if cfg["mode"] not in MODES:
            raise ValueError("任务代理 mode 必须为 inherit、direct、http 或 mihomo")
        if partial.get("clear_auth"):
            cfg["username"] = cfg["password"] = ""
        cfg["endpoint"] = _endpoint(cfg["endpoint"])
        cfg["outbound"] = _clean_string(cfg["outbound"], "outbound", 1024).strip()
        try:
            cfg["listen_address"] = str(ipaddress.ip_address(cfg["listen_address"]))
        except ValueError:
            raise ValueError("listen_address 必须是 Mihomo 宿主机的监听 IP，例如 127.0.0.1 或 0.0.0.0") from None
        if cfg["mode"] in {"http", "mihomo"} and not cfg["endpoint"]:
            raise ValueError("任务 HTTP/Mihomo 代理需要该采集节点可访问的 endpoint")
        if cfg["mode"] == "mihomo":
            if not cfg["outbound"] or cfg["outbound"].upper() in {"GLOBAL", "DIRECT", "REJECT", "PASS", "COMPATIBLE"}:
                raise ValueError("Mihomo 任务代理需要具体出口节点名；不要填写 GLOBAL 或 DIRECT")
            if not cfg["username"] and not cfg["password"]:
                cfg["username"] = "collector-" + secrets.token_hex(8)
                cfg["password"] = secrets.token_urlsafe(32)
        if bool(cfg["username"]) != bool(cfg["password"]) or ":" in cfg["username"]:
            raise ValueError("任务代理 username/password 必须同时填写，username 不能包含冒号")
        return cfg

    @staticmethod
    def _validate_ports(routes):
        occupied = set()
        for row in routes.values():
            cfg = row["config"]
            if cfg["mode"] != "mihomo":
                continue
            port = urlsplit(cfg["endpoint"]).port
            key = (urlsplit(cfg["endpoint"]).hostname, port)
            if key in occupied:
                raise ValueError("同一 Mihomo 宿主机的股票路由必须使用不同 listener 端口")
            occupied.add(key)

    def _remember(self, cfg):
        for key in ("username", "password"):
            if cfg[key]:
                self._known_secrets.update((cfg[key], quote(cfg[key], safe="")))
        if cfg["username"] and cfg["password"]:
            combined = cfg["username"] + ":" + cfg["password"]
            self._known_secrets.update((combined, base64.b64encode(combined.encode()).decode("ascii")))

    def sanitize(self, value):
        with self._lock:
            known = sorted(self._known_secrets, key=len, reverse=True)
            managers = list(self._managers.values())
        result = str(value)
        for secret in known:
            result = result.replace(secret, "[redacted]")
        result = self.base_proxy.sanitize(result)
        for manager in managers:
            result = manager.sanitize(result)
        return result

    def _save(self):
        fd, name = tempfile.mkstemp(prefix=".task-proxy-", dir=self.data_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump({"schema": SCHEMA, "routes": self._routes}, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            if self.path.is_symlink():
                raise RuntimeError("任务代理私密配置不能是符号链接")
            os.replace(name, self.path)
            dfd = os.open(self.data_dir, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
            self._storage_error = None
        except Exception:
            self._storage_error = "任务代理私密状态保存失败；来源请求已停止"
            raise RuntimeError(self._storage_error) from None
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _suggested_port(self, job, stock):
        row = self._routes.get(self._key(job, stock))
        if row and row["config"]["mode"] == "mihomo":
            return urlsplit(row["config"]["endpoint"]).port
        used = {urlsplit(r["config"]["endpoint"]).port for r in self._routes.values() if r["config"]["mode"] == "mihomo"}
        return next((port for port in range(17890, 18890) if port not in used), None)

    def manager(self, job, stock):
        with self._lock:
            if self._storage_error:
                raise RuntimeError(self._storage_error)
            key = self._key(job, stock)
            cfg = self._routes.get(key, {}).get("config", self._defaults())
            if cfg["mode"] == "inherit":
                return self.base_proxy
            manager = self._managers.get(key)
            if manager is None:
                manager = _StockProxyManager(self.data_dir / "task-proxies" / key, clock=self.clock,
                                            job=job, stock=stock, route=cfg)
                self._managers[key] = manager
            manager.task_route = dict(cfg)
            manager.configure(dict(DEFAULTS) | {"mode": "direct" if cfg["mode"] == "direct" else "http",
                                               "endpoint": cfg["endpoint"], "username": cfg["username"],
                                               "password": cfg["password"], "clear_auth": not bool(cfg["username"])})
            return manager

    def status(self, job, stock):
        with self._lock:
            key = self._key(job, stock)
            cfg = self._routes.get(key, {}).get("config", self._defaults())
            manager = self.manager(job, stock)
            result = manager.status()
            result.update(mode=cfg["mode"], route_scope="node" if cfg["mode"] == "inherit" else "stock",
                          stock=stock, outbound=cfg["outbound"] or None, suggested_port=self._suggested_port(job, stock),
                          exit_identity="unknown" if cfg["mode"] != "inherit" else result.get("exit_identity", "unknown"),
                          requires_mihomo_config=cfg["mode"] == "mihomo")
            result["settings"] = {k: cfg[k] for k in ("mode", "endpoint", "outbound", "listen_address")} | {"has_auth": bool(cfg["username"])}
            return result

    def configure(self, job, stock, obj):
        with self._lock:
            key = self._key(job, stock)
            previous = self._routes.get(key, {}).get("config", self._defaults())
            cfg = self._validate(obj, previous=previous)
            self._remember(cfg)
            old_routes = copy.deepcopy(self._routes)
            self._routes[key] = {"job": job, "stock": stock, "config": cfg}
            try:
                if len(self._routes) > 4096:
                    raise ValueError("任务代理保存数量达到上限")
                self._validate_ports(self._routes)
                self._save()
            except Exception:
                self._routes = old_routes
                raise
            return self.status(job, stock)

    def _mihomo_listeners(self, job, stocks=None, *, scopes=None):
        with self._lock:
            selected = None
            if scopes is not None:
                if not isinstance(scopes, (list, tuple)):
                    raise ValueError("Mihomo 导出 scopes 必须为任务与股票的配对列表")
                selected = set()
                for scope in scopes:
                    if not isinstance(scope, (list, tuple)) or len(scope) != 2:
                        raise ValueError("Mihomo 导出 scope 必须为任务与股票配对")
                    selected.add(self._key(scope[0], scope[1]))
            rows = [copy.deepcopy(row) for key, row in self._routes.items()
                    if row["config"]["mode"] == "mihomo"
                    and ((key in selected) if selected is not None
                         else row["job"] == job and (stocks is None or row["stock"] in stocks))]
        if not rows:
            raise ValueError("当前任务尚未保存 Mihomo 股票路由")
        listeners, ports = [], set()
        for row in sorted(rows, key=lambda r: r["stock"]):
            cfg = row["config"]
            port = urlsplit(cfg["endpoint"]).port
            # Different endpoint hostnames can be aliases for the same proxy
            # host. One downloaded configuration must use independent ports.
            if port in ports:
                raise ValueError("待生成的 Mihomo 监听端口重复；每只股票请配置不同端口，宿主机别名不能分隔监听")
            ports.add(port)
            listeners.append({"name": "collector-" + row["stock"] + "-" + self._key(row["job"], row["stock"])[:8],
                              "type": "http", "port": port, "listen": cfg["listen_address"],
                              "proxy": cfg["outbound"],
                              "users": [{"username": cfg["username"], "password": cfg["password"]}]})
        return listeners

    def mihomo_fragment(self, job, stocks=None, *, scopes=None):
        """Private authenticated download only; never include in status/exports."""
        listeners = self._mihomo_listeners(job, stocks, scopes=scopes)
        quoted = lambda value: json.dumps(value, ensure_ascii=False)
        lines = ["# Collector-only Mihomo listeners. Merge into the existing configuration.",
                 "# Contains private inbound credentials; do not publish or commit this file.",
                 "# Keeps everyday mixed-port, mode, rules and GLOBAL selection unchanged.",
                 "# Use concrete outbound nodes; node names do not prove different exit IPs.", "listeners:"]
        for listener in listeners:
            auth = listener["users"][0]
            lines.extend(["  - name: " + quoted(listener["name"]), "    type: http", "    port: " + str(listener["port"]),
                          "    listen: " + quoted(listener["listen"]), "    proxy: " + quoted(listener["proxy"]),
                          "    users:", "      - username: " + quoted(auth["username"]),
                          "        password: " + quoted(auth["password"])])
        return "\n".join(lines) + "\n"

    def mihomo_script(self, job, stocks=None, *, scopes=None):
        """Clash Verge private extension; retain unrelated listeners/config."""
        listeners = self._mihomo_listeners(job, stocks, scopes=scopes)
        prefix = ("// Private collector inbound credentials. Do not publish or commit.\n"
                  "// Merge into the Clash Verge extension script; preserve existing main logic.\n"
                  "const collectorListeners = " + json.dumps(listeners, ensure_ascii=False, indent=2) + ";\n\n")
        return prefix + '''function main(config, profileName) {
  if (config.listeners != null && !Array.isArray(config.listeners)) {
    throw new Error("Existing Mihomo listeners must be an array; collector configuration was not applied");
  }
  const names = new Set(collectorListeners.map(listener => listener.name));
  const kept = (config.listeners || []).filter(listener => !names.has(listener.name));
  for (const listener of collectorListeners) {
    for (const key of ["port", "socks-port", "mixed-port", "redir-port", "tproxy-port"]) {
      const port = Number(config[key]);
      if (port > 0 && port === listener.port) {
        throw new Error("Collector listener port conflicts with an everyday Mihomo port");
      }
    }
    for (const existing of kept) {
      // Conservatively avoid binding an occupied port, including wildcard
      // addresses and endpoint aliases. Do not replace another listener.
      if (Number(existing.port) === listener.port) {
        throw new Error("Collector listener port already belongs to another listener");
      }
    }
  }
  config.listeners = kept.concat(collectorListeners);
  return config;
}
'''
