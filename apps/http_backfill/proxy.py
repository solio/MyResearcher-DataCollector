"""Source-only proxy leases. No provider/source retries, browser state or fleet routing.

Secrets and durable budgets live in an owner-only JSON file. Network I/O and
the origin-dispatch callback always run outside the manager's state lock.
"""
from __future__ import annotations

import base64
import copy
from datetime import datetime, timedelta, timezone
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, quote, urlsplit
import uuid
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
SCHEMA = "http-proxy.v1"
REQUEST_TIMEOUT = 25
LEASE_MARGIN = REQUEST_TIMEOUT + 5
PROVIDER_TIMEOUT = 10
PROVIDER_MAX_BODY = 1024 * 1024
PROVIDER_RETRY_SECONDS = 300
DYNAMIC_MODES = {"mayi", "qingguo"}
FALLBACK_REASONS = {
    "provider_budget", "provider_unavailable", "provider_no_ip", "provider_duplicate",
    "provider_expiry", "provider_auth", "provider_schema", "provider_balance",
}
DEFAULTS = {
    "mode": "direct", "endpoint": "", "username": "", "password": "", "api_url": "",
    "rotate_seconds": 0, "rotate_requests": 0, "daily_limit": 0,
    "auto_recover": False, "recovery_cooldown_seconds": 0, "recovery_max_attempts": 0,
}
SECRETS = {"username", "password", "api_url"}


class _ProxyError(RuntimeError):
    def __init__(self, kind, message):
        self.kind = kind
        super().__init__(message)


def _iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def _number(value, key, *, seconds=False):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} 必须为非负整数")
    if seconds and 0 < value < 60:
        raise ValueError(f"{key} 必须为 0 或至少 60 秒")
    return value


def _clean_string(value, key, limit=8192):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"{key} 必须为不含控制字符的字符串")
    return value


def _endpoint(value):
    value = _clean_string(value, "endpoint", 2048).strip()
    if not value:
        return ""
    try:
        p = urlsplit(value)
        if (p.scheme != "http" or not p.hostname or p.username is not None or p.password is not None
                or p.path not in {"", "/"} or p.query or p.fragment or "%" in p.hostname
                or any(c.isspace() for c in p.hostname)):
            raise ValueError
        host = p.hostname.encode("idna").decode("ascii").lower()
        port = 80 if p.port is None else p.port
        if not 1 <= port <= 65535:
            raise ValueError
        if ":" in host:
            ipaddress.IPv6Address(host)
            host = f"[{host}]"
        return f"http://{host}:{port}"
    except (ValueError, UnicodeError) as exc:
        raise ValueError("endpoint 必须为无凭据的 HTTP/mixed 代理地址，例如 http://host:port；不支持 SOCKS-only") from exc


def _api_provider(value):
    host = (urlsplit(value).hostname or "").lower()
    if host == "mayihttp.com" or host.endswith(".mayihttp.com"):
        return "mayi"
    if host == "share.proxy.qg.net":
        return "qingguo"
    return None


def _api_url(value):
    value = _clean_string(value, "api_url", 8192).strip()
    if not value:
        return ""
    try:
        p = urlsplit(value)
        host = (p.hostname or "").lower()
        provider = _api_provider(value)
        if (p.scheme not in {"http", "https"} or provider is None
                or p.username is not None or p.password is not None or p.fragment or p.port not in {None, 80, 443}
                or any(c.isspace() for c in value)):
            raise ValueError
        query = parse_qs(p.query, keep_blank_values=True)
        if provider == "mayi":
            if any(query.get(k) != [v] for k, v in {"num": "1", "type": "2", "mode": "1"}.items()):
                raise ValueError("mayi 生成地址必须包含 num=1、type=2、mode=1，避免批量提取和错误协议")
        else:
            if p.path not in {"/get", "/aggregate/get"}:
                raise ValueError("青果仅支持国内短效 HTTP 新接口 /get 或 /aggregate/get")
            if len(query.get("key", [])) != 1 or not query["key"][0].strip():
                raise ValueError("青果生成地址必须包含单个非空 key")
            if "num" in query and query["num"] != ["1"]:
                raise ValueError("青果生成地址的 num 必须省略或为 1，避免批量提取")
        return value
    except ValueError as exc:
        if str(exc).startswith(("mayi 生成", "青果")):
            raise
        raise ValueError("api_url 必须为 mayihttp.com 或青果指定短效接口的生成 HTTP(S) URL，无重定向；优先 HTTPS") from exc


def _expires_at(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", value):
        raise ValueError("代理有效期格式无效")
    # Qingguo's naive deadline uses the explicit integration convention in SPEC.
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SHANGHAI).timestamp()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _bounded_read(response, limit, deadline):
    """Bound transfer time, not merely each socket's idle time."""
    pieces, size = [], 0
    read = getattr(response, "read1", response.read)
    sock = None
    for candidate in (response, getattr(response, "fp", None)):
        try:
            sock = candidate.fp.raw._sock
            break
        except AttributeError:
            continue
    while size < limit:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("HTTP 响应超过总传输时限")
        if sock is not None:
            sock.settimeout(remaining)
        block = read(min(65536, limit - size))
        if not block:
            break
        pieces.append(block)
        size += len(block)
    return b"".join(pieces)


class ProxyManager:
    def __init__(self, data_dir, clock=time.time):
        original = Path(data_dir).absolute()
        if original.is_symlink():
            raise RuntimeError("代理配置目录不能是符号链接")
        self.data_dir = original.resolve()
        production = Path(__file__).resolve().parents[2] / "data"
        if self.data_dir == production or production in self.data_dir.parents:
            raise RuntimeError("代理设置不能写入生产 data 目录")
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.data_dir / "proxy.json"
        self.clock, self._lock = clock, threading.RLock()
        self._extract_lock = threading.Lock()
        self._known_secrets = set()
        self._storage_error = None
        self._state = {
            "schema": SCHEMA, "config": dict(DEFAULTS), "generation": 0,
            "route_id": uuid.uuid4().hex, "lease": None, "quarantine": {},
            "daily": {"date": self._day(), "count": 0}, "recovery_attempts": 0,
            "next_recovery_at": None, "last_error": None, "fallback": None,
        }
        if self.path.exists() or self.path.is_symlink():
            if self.path.is_symlink() or not self.path.is_file():
                raise RuntimeError("代理私密配置文件类型不安全")
            try:
                if self.path.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError
                state = json.loads(self.path.read_text("utf-8"))
                if not isinstance(state, dict) or state.get("schema") != SCHEMA:
                    raise ValueError
                state["config"] = self._validate(state.get("config"), previous=dict(DEFAULTS))
                if not isinstance(state.get("quarantine"), dict) or not isinstance(state.get("daily"), dict):
                    raise ValueError
                for key in ("generation", "recovery_attempts"):
                    _number(state.get(key), key)
                _number(state["daily"].get("count"), "daily count")
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", state["daily"].get("date", "")) or not isinstance(state.get("route_id"), str):
                    raise ValueError
                if state.get("next_recovery_at") is not None and not isinstance(state["next_recovery_at"], (float, int)):
                    raise ValueError
                lease = state.get("lease")
                if lease is not None:
                    if not isinstance(lease, dict) or not isinstance(lease.get("route_id"), str):
                        raise ValueError
                    _number(lease.get("requests"), "lease requests")
                    _endpoint(lease.get("endpoint"))
                    if not isinstance(lease.get("acquired_at"), (float, int)):
                        raise ValueError
                    if lease.get("mode") in DYNAMIC_MODES and not isinstance(lease.get("expires_at"), (float, int)):
                        raise ValueError
                for row in state["quarantine"].values():
                    if not isinstance(row, dict) or not isinstance(row.get("until"), (float, int)):
                        raise ValueError
                fallback = state.get("fallback")
                if fallback is not None:
                    if (not isinstance(fallback, dict) or fallback.get("reason") not in FALLBACK_REASONS
                            or not isinstance(fallback.get("since"), (float, int))
                            or type(fallback.get("manual_retry")) is not bool
                            or type(fallback.get("blocked")) is not bool
                            or (fallback.get("retry_at") is not None
                                and not isinstance(fallback["retry_at"], (float, int)))):
                        raise ValueError
                    _clean_string(fallback.get("message"), "fallback message", 2000)
                self._state.update(state)
                self._remember(self._state["config"])
                self._remember(self._state.get("lease") or {})
                os.chmod(self.path, 0o600)
            except Exception as exc:
                raise RuntimeError("代理私密配置损坏或版本不支持；原文件已保留") from exc

    def _day(self):
        return datetime.fromtimestamp(self.clock(), SHANGHAI).date().isoformat()

    def _remember(self, values):
        for key in ("username", "password", "api_url", "user", "pass"):
            val = values.get(key)
            if isinstance(val, str) and val:
                self._known_secrets.update((val, quote(val, safe="")))
        user, password = values.get("username", values.get("user")), values.get("password", values.get("pass"))
        if isinstance(user, str) and user and isinstance(password, str) and password:
            combined = user + ":" + password
            self._known_secrets.update((combined, base64.b64encode(combined.encode("utf-8")).decode("ascii")))
        api = values.get("api_url")
        if isinstance(api, str) and api:
            for vals in parse_qs(urlsplit(api).query, keep_blank_values=True).values():
                self._known_secrets.update(v for v in vals if v and len(v) >= 4)

    def sanitize(self, value):
        with self._lock:
            secrets = sorted(self._known_secrets, key=len, reverse=True)
        text = str(value)
        for secret in secrets:
            text = text.replace(secret, "[redacted]")
        text = re.sub(r"(?i)(?:https?://)[^\s\"'<>]*(?:mayihttp\.com|qg\.net)[^\s\"'<>]*", "[provider URL redacted]", text)
        text = re.sub(r"(?i)(proxy-authorization|authorization)\s*[:=]\s*\S+(?:\s+\S+)?", r"\1: [redacted]", text)
        return text[:2000]

    def _save(self):
        # Caller holds only the short-lived state lock, never a network/engine lock.
        fd, name = tempfile.mkstemp(prefix=".proxy-", dir=self.data_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                os.fchmod(f.fileno(), 0o600)
                json.dump(self._state, f, ensure_ascii=False, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            if self.path.is_symlink():
                raise RuntimeError("代理私密配置文件不能是符号链接")
            os.replace(name, self.path)
            dfd = os.open(self.data_dir, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
            self._storage_error = None
        except Exception:
            self._storage_error = "代理私密状态保存失败；来源发送已锁止，需要修复本地存储"
            raise RuntimeError(self._storage_error) from None
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _validate(self, partial, previous=None):
        if not isinstance(partial, dict):
            raise ValueError("代理配置必须为 JSON 对象")
        unknown = set(partial) - set(DEFAULTS) - {"clear_auth", "clear_api_url"}
        if unknown:
            raise ValueError("未知代理配置字段")
        cfg = dict(previous or self._state["config"])
        for key in ("clear_auth", "clear_api_url", "auto_recover"):
            if key in partial and not isinstance(partial[key], bool):
                raise ValueError(f"{key} 必须为布尔值")
        for key, value in partial.items():
            if key in DEFAULTS:
                if key in SECRETS:
                    value = _clean_string(value, key)
                    if not value:
                        continue
                cfg[key] = value
        if partial.get("clear_auth"):
            cfg["username"] = cfg["password"] = ""
        if partial.get("clear_api_url"):
            cfg["api_url"] = ""
        if not isinstance(cfg["mode"], str) or cfg["mode"] not in {"direct", "http"} | DYNAMIC_MODES:
            raise ValueError("mode 必须为 direct、http、mayi 或 qingguo")
        if cfg["mode"] not in DYNAMIC_MODES and "mode" in partial and "auto_recover" not in partial:
            cfg["auto_recover"] = False
        cfg["endpoint"], cfg["api_url"] = _endpoint(cfg["endpoint"]), _api_url(cfg["api_url"])
        if cfg["mode"] in DYNAMIC_MODES and cfg["api_url"] and _api_provider(cfg["api_url"]) != cfg["mode"]:
            raise ValueError("所选动态代理与 api_url 供应商不一致；切换供应商时请填写对应的新提取地址")
        for key in ("username", "password"):
            _clean_string(cfg[key], key)
        if bool(cfg["username"]) != bool(cfg["password"]) or ":" in cfg["username"]:
            raise ValueError("代理 username/password 必须同时配置，username 不能包含冒号")
        for key in ("rotate_seconds", "rotate_requests", "daily_limit", "recovery_cooldown_seconds", "recovery_max_attempts"):
            _number(cfg[key], key, seconds=key in {"rotate_seconds", "recovery_cooldown_seconds"})
        if cfg["mode"] == "http" and not cfg["endpoint"]:
            raise ValueError("HTTP/mixed 模式需要 endpoint")
        if cfg["mode"] in DYNAMIC_MODES and (not cfg["api_url"] or cfg["daily_limit"] <= 0):
            raise ValueError("动态代理模式需要生成 api_url 和正整数 daily_limit")
        if cfg["auto_recover"] and (cfg["mode"] not in DYNAMIC_MODES or cfg["recovery_cooldown_seconds"] < 60 or cfg["recovery_max_attempts"] <= 0):
            raise ValueError("自动恢复仅支持动态代理，且冷却至少 60 秒、最大尝试必须为正整数")
        return cfg

    def configure(self, partial_dict):
        with self._lock:
            cfg = self._validate(partial_dict)
            self._remember(cfg)
            if cfg != self._state["config"] or self._state.get("fallback"):
                previous = copy.deepcopy(self._state)
                self._state["config"] = cfg
                self._invalidate("config_changed", exclude=False)
                self._state["last_error"] = None
                # Editing settings cannot reset persistent recovery/extraction budgets.
                try:
                    self._save()
                except Exception:
                    self._state = previous
                    raise
            elif self._storage_error:
                self._save()
            return self.status()

    def _invalidate(self, reason, *, exclude, clear_fallback=True):
        lease = self._state.get("lease")
        if exclude and lease and lease.get("ip"):
            expiry = max(self.clock() + 60, lease.get("expires_at") or 0)
            self._state["quarantine"][lease["ip"]] = {"until": expiry, "reason": reason}
        self._state["generation"] += 1
        self._state["route_id"] = uuid.uuid4().hex
        self._state["lease"] = None
        if clear_fallback:
            self._state["fallback"] = None

    def rotate(self):
        with self._lock:
            previous = copy.deepcopy(self._state)
            self._invalidate("manual_rotation", exclude=True)
            self._state["last_error"] = None
            try:
                self._save()
            except Exception:
                self._state = previous
                raise
            return self.status()

    def prepare_manual_probe(self):
        with self._lock:
            fallback = self._state.get("fallback")
            if not fallback or not fallback["blocked"]:
                return
            previous = copy.deepcopy(self._state)
            self._invalidate("manual_direct_probe", exclude=False, clear_fallback=False)
            fallback["blocked"] = False
            try:
                self._save()
            except Exception:
                self._state = previous
                raise

    def status(self):
        with self._lock:
            cfg = self._state["config"]
            settings = {k: v for k, v in cfg.items() if k not in SECRETS}
            settings.update(has_auth=bool(cfg["username"]), has_api_url=bool(cfg["api_url"]))
            daily = self._state["daily"] if self._state["daily"].get("date") == self._day() else {"date": self._day(), "count": 0}
            lease = self._state.get("lease")
            fallback = self._state.get("fallback")
            public_fallback = None
            if fallback and cfg["mode"] in DYNAMIC_MODES:
                public_fallback = {k: fallback[k] for k in ("reason", "manual_retry", "blocked")}
                public_fallback.update(message=self.sanitize(fallback["message"]),
                                       since=_iso(fallback["since"]), retry_at=_iso(fallback["retry_at"]))
            public_lease = None
            if lease:
                public_lease = {k: lease.get(k) for k in ("route_id", "endpoint", "ip", "port", "requests", "selection_reason", "recent_success_outcome")}
                for key in ("acquired_at", "expires_at", "recent_success_at"):
                    public_lease[key] = _iso(lease.get(key))
                public_lease["remaining_seconds"] = max(0, round(lease["expires_at"] - self.clock(), 1)) if lease.get("expires_at") else None
            return {
                "settings": settings, "mode": cfg["mode"], "has_api_url": settings["has_api_url"], "has_auth": settings["has_auth"],
                "effective_mode": "direct" if public_fallback else cfg["mode"], "fallback": public_fallback,
                "daily_extractions": {"date": daily["date"], "count": daily["count"], "limit": cfg["daily_limit"], "remaining": max(0, cfg["daily_limit"] - daily["count"])},
                "lease": public_lease, "recovery_attempts": self._state["recovery_attempts"],
                "last_error": self._storage_error or (self.sanitize(self._state["last_error"]) if self._state["last_error"] else None),
                "next_recovery_at": _iso(self._state["next_recovery_at"]),
                "cooling_candidates": sum(v["until"] > self.clock() for v in self._state["quarantine"].values()),
            }

    def _fallback_route(self):
        fallback = self._state["fallback"]
        if fallback["blocked"]:
            raise _ProxyError("provider_fallback_blocked", "回退直连已遭来源验证/限流；需人工处理，未发送来源请求")
        return {"mode": "direct", "route_id": self._state["route_id"], "configured_mode": self._state["config"]["mode"],
                "fallback_reason": fallback["reason"], "fallback_message": self.sanitize(fallback["message"]),
                "fallback_since": _iso(fallback["since"])}

    def _activate_fallback(self, reason, message):
        # Called under the state lock, before any proxy/source dispatch.
        cfg, now = self._state["config"], self.clock()
        if cfg["mode"] not in DYNAMIC_MODES:
            raise _ProxyError("proxy_config_changed", "代理设置已改变；未发送来源请求")
        previous = self._state.get("fallback") or {}
        manual = reason in {"provider_auth", "provider_schema", "provider_balance"}
        if reason == "provider_budget":
            tomorrow = datetime.fromtimestamp(now, SHANGHAI).date() + timedelta(days=1)
            retry_at = datetime.combine(tomorrow, datetime.min.time(), tzinfo=SHANGHAI).timestamp()
        else:
            retry_at = None if manual else now + max(PROVIDER_RETRY_SECONDS, cfg["recovery_cooldown_seconds"])
        self._invalidate("provider_fallback", exclude=False, clear_fallback=False)
        self._state["fallback"] = {"reason": reason, "message": self.sanitize(message),
                                   "since": previous.get("since", now), "retry_at": retry_at,
                                   "manual_retry": manual, "blocked": previous.get("blocked", False)}
        self._state["last_error"] = self._state["fallback"]["message"]
        self._save()
        return self._fallback_route()

    def _provider(self, api_url):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        req = urllib.request.Request(api_url, headers={"Accept": "application/json"})
        deadline = time.monotonic() + PROVIDER_TIMEOUT
        try:
            response = opener.open(req, timeout=PROVIDER_TIMEOUT)
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise _ProxyError("provider_auth" if status in {401, 403, 407} else "provider_unavailable", "供应商提取返回 HTTP 错误；响应未写入来源证据") from None
        except Exception:
            raise _ProxyError("provider_unavailable", "供应商连接失败；本次未发送来源请求") from None
        with response:
            if response.status != 200:
                raise _ProxyError("provider_unavailable", "供应商提取返回非 200 响应")
            try:
                body = _bounded_read(response, PROVIDER_MAX_BODY + 1, deadline)
            except (OSError, http.client.HTTPException):
                raise _ProxyError("provider_unavailable", "供应商响应传输中断；未发送来源请求") from None
            if len(body) > PROVIDER_MAX_BODY:
                raise _ProxyError("provider_schema", "供应商提取响应过大")
            try:
                obj = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeError):
                raise _ProxyError("provider_schema", "供应商未返回有效 UTF-8 JSON") from None
        if _api_provider(api_url) == "qingguo":
            return self._qingguo_candidate(obj)
        return self._mayi_candidate(obj)

    @staticmethod
    def _qingguo_candidate(obj):
        if not isinstance(obj, dict) or not isinstance(obj.get("code"), str):
            raise _ProxyError("provider_schema", "青果未返回有效的 JSON 请求状态码")
        code = obj["code"]
        if code != "SUCCESS":
            # Static descriptions only: provider messages may contain credentials.
            if code in {"INVALID_KEY", "UNAVAILABLE_KEY", "ACCESS_DENY", "API_AUTH_DENY", "KEY_BLOCK"}:
                raise _ProxyError("provider_auth", "青果提取认证或权限失败；使用直连，修正配置后再提取")
            if code == "BALANCE_INSUFFICIENT":
                raise _ProxyError("provider_balance", "青果提取余额不足；使用直连，充值后保存配置或手动更换再提取")
            if code in {"INTERNAL_ERROR", "REQUEST_LIMIT_EXCEEDED", "NO_AVAILABLE_CHANNEL",
                        "NO_RESOURCE_FOUND", "FAILED_OPERATION", "EXTRACT_LIMIT_EXCEEDED"}:
                raise _ProxyError("provider_no_ip", "青果暂无可提取 IP 或提取额度受限；使用直连，至少五分钟后再提取")
            raise _ProxyError("provider_schema", "青果提取参数错误或返回未知状态码；响应消息不回显")
        if obj.get("data") == []:
            raise _ProxyError("provider_no_ip", "青果未返回可用 IP；使用直连，稍后再提取")
        if not isinstance(obj.get("data"), list) or len(obj["data"]) != 1 or not isinstance(obj["data"][0], dict):
            raise _ProxyError("provider_schema", "青果未返回单个成功的 JSON 代理候选")
        row = obj["data"][0]
        try:
            server = _clean_string(row["server"], "provider server", 2048)
            if not server or server != server.strip() or "://" in server:
                raise ValueError
            p = urlsplit("http://" + server)
            if p.port is None or p.path or p.query or p.fragment:
                raise ValueError
            endpoint = _endpoint("http://" + server)
            if not isinstance(row["proxy_ip"], str) or "%" in row["proxy_ip"]:
                raise ValueError
            ip = str(ipaddress.ip_address(row["proxy_ip"]))
            expires = _expires_at(row["deadline"])
        except (KeyError, ValueError, TypeError, UnicodeError) as exc:
            raise _ProxyError("provider_schema", "青果候选 server、proxy_ip 或 deadline 缺失/无效") from exc
        # Proxy Authkey/Authpwd come from explicit config, independent of API pwd.
        return {"endpoint": endpoint, "ip": ip, "port": p.port, "username": "", "password": "", "expires_at": expires}

    @staticmethod
    def _mayi_candidate(obj):
        if isinstance(obj, dict) and (obj.get("success") is False
                or (obj.get("success") is True and obj.get("data") == [])):
            raise _ProxyError("provider_no_ip", "供应商提取失败或没有可用 IP；响应消息不回显")
        if (not isinstance(obj, dict) or obj.get("success") is not True
                or ("code" in obj and (type(obj["code"]) is not int or obj["code"] != 200))
                or not isinstance(obj.get("data"), list) or len(obj["data"]) != 1):
            # Never echo provider error/message, which can include credentials.
            raise _ProxyError("provider_schema", "供应商未返回单个成功的 JSON 代理候选")
        row = obj["data"][0]
        if not isinstance(row, dict):
            raise _ProxyError("provider_schema", "供应商代理候选结构不正确")
        try:
            if not isinstance(row["ip"], str) or "%" in row["ip"]:
                raise ValueError
            ip = str(ipaddress.ip_address(row["ip"]))
            port = row["port"]
            if isinstance(port, str) and re.fullmatch(r"[0-9]{1,5}", port):
                port = int(port)
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError
            expires = _expires_at(row["expire_time"])
            user, password = row.get("user") or "", row.get("pass") or ""
            _clean_string(user, "provider username")
            _clean_string(password, "provider password")
            if bool(user) != bool(password) or ":" in user:
                raise ValueError
        except (KeyError, ValueError, TypeError) as exc:
            raise _ProxyError("provider_schema", "供应商候选 IP、端口、认证或 expire_time 缺失/无效") from exc
        host = f"[{ip}]" if ":" in ip else ip
        return {"endpoint": f"http://{host}:{port}", "ip": ip, "port": port, "username": user, "password": password, "expires_at": expires}

    def _route(self):
        with self._lock:
            now, cfg = self.clock(), dict(self._state["config"])
            if cfg["mode"] == "direct":
                return {"mode": "direct", "route_id": self._state["route_id"]}
            if cfg["mode"] == "http":
                lease = self._state.get("lease")
                if not lease:
                    p = urlsplit(cfg["endpoint"])
                    lease = {"mode": "http", "route_id": self._state["route_id"], "endpoint": cfg["endpoint"], "ip": None,
                             "port": p.port, "username": cfg["username"], "password": cfg["password"], "acquired_at": now,
                             "expires_at": None, "requests": 0, "selection_reason": "explicit_endpoint", "recent_success_at": None,
                             "recent_success_outcome": None}
                    self._state["lease"] = lease
                return dict(lease)
            fallback = self._state.get("fallback")
            if fallback and (fallback["manual_retry"] or now < fallback["retry_at"]):
                return self._fallback_route()
            lease = self._state.get("lease")
            reason = "initial_extraction"
            if lease:
                if lease["expires_at"] <= now + LEASE_MARGIN:
                    reason = "lease_expiring"
                elif cfg["rotate_seconds"] and now - lease["acquired_at"] >= cfg["rotate_seconds"]:
                    reason = "elapsed_rotation"
                elif cfg["rotate_requests"] and lease["requests"] >= cfg["rotate_requests"]:
                    reason = "attempt_count_rotation"
                else:
                    return dict(lease)
                self._invalidate(reason, exclude=True, clear_fallback=False)
            if not self._extract_lock.acquire(blocking=False):
                raise _ProxyError("provider_busy", "已有供应商提取进行中；本次未发送来源请求")
            daily = self._state["daily"]
            if daily.get("date") != self._day():
                daily = self._state["daily"] = {"date": self._day(), "count": 0}
            if daily["count"] >= cfg["daily_limit"]:
                self._extract_lock.release()
                return self._activate_fallback("provider_budget", "动态代理今日提取上限已耗尽；使用节点原直连出口，次日再提取")
            daily["count"] += 1
            generation = self._state["generation"]
            self._state["last_error"] = None
            try:
                self._save()
            except Exception:
                self._extract_lock.release()
                raise
        try:
            try:
                candidate = self._provider(cfg["api_url"])
            except _ProxyError as exc:
                with self._lock:
                    if generation != self._state["generation"]:
                        raise _ProxyError("proxy_config_changed", "提取期间代理设置已改变；未发送来源请求") from None
                    if exc.kind in FALLBACK_REASONS:
                        return self._activate_fallback(exc.kind, str(exc))
                raise
            # Provider lease auth is authoritative; explicit account credentials
            # are only a fallback for a candidate without returned user/pass.
            if not candidate.get("username") and cfg["username"]:
                candidate["username"], candidate["password"] = cfg["username"], cfg["password"]
            with self._lock:
                self._remember(candidate)
                if generation != self._state["generation"]:
                    raise _ProxyError("proxy_config_changed", "提取期间代理设置已改变；候选未用于来源请求")
                now = self.clock()
                if candidate["expires_at"] <= now + LEASE_MARGIN:
                    return self._activate_fallback("provider_expiry", "代理租约剩余不足 30 秒；使用节点原直连出口")
                cooling = self._state["quarantine"].get(candidate["ip"])
                if cooling and cooling["until"] > now:
                    return self._activate_fallback("provider_duplicate", "供应商重复返回冷却/待替换 IP；使用节点原直连出口")
                self._state["quarantine"] = {k: v for k, v in self._state["quarantine"].items() if v["until"] > now}
                lease = {**candidate, "mode": cfg["mode"], "route_id": uuid.uuid4().hex, "acquired_at": now, "requests": 0,
                         "selection_reason": reason, "recent_success_at": None, "recent_success_outcome": None}
                self._state["lease"] = lease
                self._state["fallback"] = self._state["last_error"] = None
                self._save()
                return dict(lease)
        finally:
            self._extract_lock.release()

    @staticmethod
    def _public_route(route, started):
        return {k: route[k] for k in ("mode", "route_id", "endpoint", "ip", "port", "configured_mode",
                                     "fallback_reason", "fallback_message", "fallback_since") if k in route} | {
            "lease_expires_at": _iso(route.get("expires_at")), "exit_identity": "provider_candidate" if route.get("ip") else "unknown",
            "source_started_at": started,
        }

    def fetch(self, url, client, referer=None, on_route=None):
        import core
        if not core._allowed(url):
            raise ValueError("请求地址必须属于公开 HTTPS Eastmoney 来源")
        core.request_headers(referer)
        if client not in {"curl", "urllib"}:
            raise ValueError("client 只能是 curl 或 urllib")
        with self._lock:
            if self._storage_error:
                return core.Response(None, b"", {}, url, self._storage_error, network_attempted=False,
                                     proxy={"mode": self._state["config"]["mode"], "phase": "state", "source_attempted": False},
                                     proxy_error="proxy_state")
        try:
            route = self._route()
        except Exception as exc:
            error = self.sanitize(f"代理候选准备失败: {type(exc).__name__}: {exc}")
            with self._lock:
                self._state["last_error"] = error
                try:
                    self._save()
                except Exception:
                    error = "代理私密状态保存失败；未发送来源请求"
                    return core.Response(None, b"", {}, url, error, network_attempted=False,
                                         proxy={"mode": self._state["config"]["mode"], "phase": "state", "source_attempted": False},
                                         proxy_error="proxy_state")
            return core.Response(None, b"", {}, url, error, network_attempted=False,
                                 proxy={"mode": self.status()["mode"], "phase": "provider", "source_attempted": False},
                                 proxy_error=exc.kind if isinstance(exc, _ProxyError) else "proxy_state",
                                 )
        try:
            with self._lock:
                lease = self._state.get("lease")
                if lease and lease["route_id"] == route["route_id"]:
                    lease["requests"] += 1
                    self._save()
        except Exception:
            return core.Response(None, b"", {}, url, "代理尝试台账保存失败；未发送来源请求", network_attempted=False,
                                 proxy={"mode": route["mode"], "phase": "state", "source_attempted": False}, proxy_error="proxy_state")
        public = self._public_route(route, self.clock())
        if on_route is not None:
            try:
                on_route(dict(public))
            except Exception:
                raise RuntimeError("来源请求台账写入失败；未发送来源请求") from None
        if route["mode"] in DYNAMIC_MODES and route["expires_at"] <= self.clock() + REQUEST_TIMEOUT:
            try:
                with self._lock:
                    lease = self._state.get("lease")
                    if not lease or lease["route_id"] != route["route_id"]:
                        raise _ProxyError("proxy_config_changed", "落盘期间出口已改变；未发送来源请求")
                    route = self._activate_fallback("provider_expiry", "落盘期间代理租约余量不足；使用节点原直连出口")
            except Exception as exc:
                public["source_attempted"] = False
                return core.Response(None, b"", {}, url, self.sanitize(str(exc)), network_attempted=False,
                                     proxy=public, proxy_error=exc.kind if isinstance(exc, _ProxyError) else "proxy_state")
            public = self._public_route(route, self.clock())
            if on_route is not None:
                try:
                    on_route(dict(public))
                except Exception:
                    public["source_attempted"] = False
                    return core.Response(None, b"", {}, url, "回退来源请求台账写入失败；未发送来源请求",
                                         network_attempted=False, proxy=public, proxy_error="proxy_state")
        if route["mode"] == "direct":
            response = core.fetch(url, client, referer=referer)
        elif client == "curl":
            response = self._curl(core, url, route, referer)
        else:
            response = self._urllib(core, url, route, referer)
        response.proxy = public
        public["source_attempted"] = response.network_attempted
        if response.error:
            response.error = self.sanitize(response.error)
        return response

    def _urllib(self, core, url, route, referer):
        trace = {"origin_started": False}

        class ObservedConnection(http.client.HTTPSConnection):
            _proxy_tunnelling = False
            _proxy_ready = False

            def _tunnel(self):
                self._proxy_tunnelling = True
                try:
                    super()._tunnel()
                finally:
                    self._proxy_tunnelling = False

            def connect(self):
                super().connect()
                self._proxy_ready = True

            def send(self, data):
                if self.sock is None:
                    self.connect()
                if self._proxy_ready and not self._proxy_tunnelling:
                    trace["origin_started"] = True
                return super().send(data)

        class ExplicitHTTPS(urllib.request.HTTPSHandler):
            def https_open(self, req):
                return self.do_open(ObservedConnection, req, context=self._context)

        request = urllib.request.Request(url, headers=core.request_headers(referer))
        # set_proxy directly: ProxyHandler's proxy_bypass would honour NO_PROXY.
        request.set_proxy(urlsplit(route["endpoint"]).netloc, "http")
        if route.get("username"):
            credentials = (route["username"] + ":" + route["password"]).encode("utf-8")
            request.add_unredirected_header("Proxy-Authorization", "Basic " + base64.b64encode(credentials).decode("ascii"))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(), ExplicitHTTPS())
        deadline = time.monotonic() + REQUEST_TIMEOUT
        try:
            try:
                response = opener.open(request, timeout=REQUEST_TIMEOUT)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                body = _bounded_read(response, core.MAX_BODY + 1, deadline)
                headers = {k.lower(): v for k, v in response.headers.items() if k.lower() in core.SAFE_HEADERS}
                error = "响应超出 16 MiB，内容可能不完整" if len(body) > core.MAX_BODY else None
                return core.Response(response.status, body, headers, url, error)
        except Exception as exc:
            text = self.sanitize(f"{type(exc).__name__}: {exc}")
            match = re.search(r"Tunnel connection failed:\s*(\d{3})", str(exc))
            code = int(match[1]) if match else None
            pe = "proxy_auth" if code == 407 else "proxy_connect"
            return core.Response(None, b"", {}, url, text, network_attempted=trace["origin_started"], proxy_error=pe)

    def _curl(self, core, url, route, referer):
        with tempfile.TemporaryDirectory(prefix="http-backfill-proxy-") as tmp:
            hp, bp = Path(tmp) / "headers", Path(tmp) / "body"
            command = ["curl", "-q", "--silent", "--show-error", "--connect-timeout", "10", "--max-time", "25",
                       "--max-filesize", str(core.MAX_BODY), "--noproxy", "", "--proxy", route["endpoint"],
                       "--proto", "=https", "--suppress-connect-headers", "--dump-header", str(hp), "--output", str(bp),
                       "--write-out", "%{http_code} %{http_connect}", "--user-agent", core.UA, "--header", "Accept: text/html"]
            config = ""
            if route.get("username"):
                escaped = (route["username"] + ":" + route["password"]).replace("\\", "\\\\").replace('"', '\\"')
                config = 'proxy-user = "' + escaped + '"\n'
                command.extend(["--proxy-basic", "--config", "-"])
            if referer is not None:
                command.extend(["--referer", referer])
            command.append(url)
            try:
                proc = subprocess.run(command, input=config, capture_output=True, text=True, timeout=30)
                headers = {}
                if hp.exists():
                    for line in hp.read_text(errors="replace").splitlines():
                        if line.startswith("HTTP/"):
                            headers = {}
                        elif ":" in line:
                            k, v = line.split(":", 1)
                            if k.lower() in core.SAFE_HEADERS:
                                headers[k.lower()] = v.strip()
                body = bp.read_bytes() if bp.exists() else b""
                parts = proc.stdout.strip().split()
                status = int(parts[0]) if parts and parts[0].isdigit() else 0
                connect = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                error = self.sanitize(proc.stderr.strip() or f"curl exit {proc.returncode}") if proc.returncode else None
                pe = None
                if connect not in {0, 200}:
                    pe = "proxy_auth" if connect == 407 else "proxy_connect"
                    error = error or f"代理拒绝 CONNECT ({connect})"
                    status, headers, body = 0, {}, b""
                elif proc.returncode and connect != 200:
                    pe = "proxy_connect"
                return core.Response(status or None, body, headers, url, error,
                                     network_attempted=connect == 200 or status > 0, proxy_error=pe)
            except Exception as exc:
                error = self.sanitize(f"{type(exc).__name__}: {exc}")
                did_start = not isinstance(exc, (FileNotFoundError, PermissionError))
                return core.Response(None, bp.read_bytes() if bp.exists() else b"", {}, url, error,
                                     network_attempted=did_start, proxy_error="proxy_connect" if did_start else "proxy_config")

    def observe(self, response, outcome, cooldown_until=None):
        public = getattr(response, "proxy", None) or {}
        route_id = public.get("route_id")
        if not route_id:
            return
        with self._lock:
            now, cfg = self.clock(), self._state["config"]
            lease = self._state.get("lease")
            current = lease is not None and lease["route_id"] == route_id
            if outcome in {"real_data", "detail_unavailable"} and not getattr(response, "proxy_error", None):
                changed = False
                if current:
                    lease["recent_success_at"], lease["recent_success_outcome"] = now, outcome
                    changed = True
                if current or (public.get("mode") == "direct" and route_id == self._state["route_id"]):
                    changed = changed or bool(self._state["recovery_attempts"] or self._state["next_recovery_at"] or self._state["last_error"])
                    self._state["recovery_attempts"] = 0
                    self._state["next_recovery_at"] = self._state["last_error"] = None
                if changed:
                    self._save()
                return
            pe = getattr(response, "proxy_error", None)
            source_block = outcome in {"access_block", "blocked", "challenge", "http_block", "rate_limited"} or (response.status in {403, 429} and not pe)
            if not source_block and not pe:
                return
            fallback = self._state.get("fallback")
            if (source_block and fallback and public.get("mode") == "direct"
                    and public.get("configured_mode") in DYNAMIC_MODES and route_id == self._state["route_id"]):
                fallback["blocked"] = True
            floor = now + max(60, cfg["recovery_cooldown_seconds"])
            until = max(floor, cooldown_until or 0)
            if public.get("ip"):
                old = self._state["quarantine"].get(public["ip"], {})
                self._state["quarantine"][public["ip"]] = {"until": max(until, old.get("until", 0)), "reason": "source_block_observed" if source_block else "proxy_connection_error"}
            if current:
                self._invalidate("source_block_observed" if source_block else "proxy_connection_error", exclude=False)
            self._state["next_recovery_at"] = max(until, self._state["next_recovery_at"] or 0)
            self._state["last_error"] = self.sanitize(response.error or ("来源已观察到验证/限流，候选进入冷却；未证明 IP 是唯一原因" if source_block else "代理连接失败，候选进入冷却"))
            self._save()

    def claim_recovery(self):
        with self._lock:
            cfg, now = self._state["config"], self.clock()
            if cfg["mode"] not in DYNAMIC_MODES or not cfg["auto_recover"]:
                return False
            if self._state["next_recovery_at"] and now < self._state["next_recovery_at"]:
                return False
            error = None
            if self._state["recovery_attempts"] >= cfg["recovery_max_attempts"]:
                error = "自动恢复尝试预算已耗尽；需人工处理"
            elif (self._state.get("fallback") or {}).get("blocked"):
                error = "直连回退已遭来源验证/限流；自动恢复已暂停，需人工处理"
            if error:
                if self._state["last_error"] != error:
                    self._state["last_error"] = error
                    self._save()
                return False
            self._state["recovery_attempts"] += 1
            self._state["next_recovery_at"] = now + cfg["recovery_cooldown_seconds"]
            self._invalidate("recovery_claim", exclude=True, clear_fallback=False)
            self._save()
            return True
