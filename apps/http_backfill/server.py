#!/usr/bin/env python3
"""Private mobile console and persistent, rate-limited experimental worker."""
from __future__ import annotations

import argparse
import hashlib
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import signal
import sqlite3
import sys
import threading
import time
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "src"))
from activity import activity_page, activity_query


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, engine, token, cookie_path="/", fleet=None, api_only=False):
        super().__init__(address, Handler)
        self.engine = engine
        self.api_only = bool(api_only)
        self.token_digest = hashlib.sha256(token.encode()).digest()
        self.sessions = {}
        self.session_lock = threading.Lock()
        self.cookie_path = cookie_path
        self.fleet = fleet

    def token_valid(self, value):
        return isinstance(value, str) and secrets.compare_digest(
            self.token_digest, hashlib.sha256(value.encode()).digest())


class Handler(BaseHTTPRequestHandler):
    server_version = "HTTPBackfillConsole/0.1"

    def log_message(self, fmt, *args):
        # No source content, token, cookie or request body in access logs.
        pass

    def send(self, status, payload, content_type="application/json; charset=utf-8", headers=None):
        body = payload if isinstance(payload, bytes) else json.dumps(
            payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def authenticated(self):
        bearer = self.headers.get("Authorization", "")
        if bearer.startswith("Bearer ") and self.server.token_valid(bearer[7:]):
            return True
        if self.server.api_only:
            return False
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            return False
        item = jar.get("backfill_session")
        if item is None:
            return False
        with self.server.session_lock:
            expiry = self.server.sessions.get(item.value, 0)
            if expiry <= time.time():
                self.server.sessions.pop(item.value, None)
                return False
        return True

    def origin_valid(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlparse(origin)
        return parsed.scheme in ("http", "https") and parsed.netloc == self.headers.get("Host")

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("请求长度无效")
        if not 0 < length <= 16384:
            raise ValueError("请求体不能为空且不能超过 16 KiB")
        try:
            obj = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("请求体必须是 UTF-8 JSON")
        if not isinstance(obj, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return obj

    def cookie(self, session, max_age):
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        return f"backfill_session={session}; Path={self.server.cookie_path}; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}"

    def fleet_route(self, method, path, query=None, obj=None):
        """Only authenticated hub routes; tokens never cross the browser API."""
        if not (path == "api/fleet" or path.startswith("api/fleet/") or path.startswith("api/nodes/")):
            return False
        fleet = self.server.fleet
        if fleet is None:
            self.send(503, {"error": "当前服务未启用实例管理"})
            return True
        from fleet import FleetError
        try:
            if method == "GET" and path == "api/fleet":
                result = fleet.overview()
            elif method == "POST" and path == "api/fleet/nodes":
                result = fleet.register(obj)
            elif path.startswith("api/fleet/nodes/"):
                alias = path[len("api/fleet/nodes/"):]
                if method == "PATCH":
                    if obj.get("id", alias) != alias or obj.get("alias", alias) != alias:
                        raise ValueError("修改实例不能更换实例标识")
                    result = fleet.register({**obj, "id": alias})
                elif method == "DELETE":
                    result = fleet.remove(alias)
                else:
                    self.send(404, {"error": "接口不存在"})
                    return True
            elif method == "POST" and path == "api/fleet/sync":
                result = fleet.request_sync(obj.get("node_id", "all"))
            elif path.startswith("api/nodes/"):
                parts = path.split("/", 3)
                if len(parts) != 4 or not parts[2] or not parts[3]:
                    raise ValueError("实例接口路径无效")
                result = fleet.proxy(parts[2], method, "api/" + parts[3], body=obj, query=query or {})
            else:
                self.send(404, {"error": "接口不存在"})
                return True
            self.send(200, result)
        except FleetError as exc:
            self.send(exc.status_code, {"error": exc.error, "ambiguous": exc.ambiguous})
        except ValueError as exc:
            self.send(400, {"error": str(exc)})
        except RuntimeError as exc:
            self.send(409, {"error": str(exc)})
        return True

    def export_route(self, path, query):
        if not path.startswith("api/federation/"):
            return False
        from federation import export_page, export_raw
        def integer(name, default, minimum, maximum=2**63 - 1):
            values = query.get(name)
            if values is None:
                return default
            if len(values) != 1 or not values[0].isascii() or not values[0].isdigit():
                raise ValueError(f"{name} 必须是整数且不能重复")
            value = int(values[0])
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} 超出范围")
            return value
        try:
            if path == "api/federation/export":
                result = export_page(self.server.engine,
                                     after=integer("after", 0, 0),
                                     limit=integer("limit", 50, 1, 100),
                                     snapshot=integer("snapshot", None, 0))
            elif path == "api/federation/raw":
                rid = integer("request_id", None, 1)
                if rid is None:
                    raise ValueError("request_id 必须提供")
                result = export_raw(self.server.engine, rid)
            else:
                self.send(404, {"error": "接口不存在"})
                return True
            self.send(200, result)
        except ValueError as exc:
            self.send(400, {"error": str(exc)})
        except (RuntimeError, OSError, sqlite3.Error) as exc:
            self.send(409, {"error": str(exc)})
        return True

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path).lstrip("/")
        if path == "healthz":
            return self.send(200, {"ok": True})
        if self.server.api_only and (path == "api/session" or not path.startswith("api/")):
            return self.send(404, {"error": "此节点仅开放 Bearer 鉴权 API"})
        if path == "api/session":
            return self.send(200, {"authenticated": self.authenticated()})
        if path.startswith("api/"):
            if not self.authenticated():
                return self.send(401, {"error": "节点 API 需要有效 Bearer 令牌" if self.server.api_only else "请先登录控制台"})
            try:
                query = parse_qs(parsed.query, keep_blank_values=True)
                if self.fleet_route("GET", path, query=query) or self.export_route(path, query):
                    return
                if path in {"api/requests", "api/events"} and "paged" in query:
                    return self.send(200, activity_page(self.server.engine, path.split("/")[-1], **activity_query(query)))
                limit = int(query.get("limit", ["50"])[0])
                offset = int(query.get("offset", ["0"])[0])
                if not 1 <= limit <= 1000 or offset < 0:
                    raise ValueError("limit 应在 1–1000 之间，offset 不能小于 0")
                if path == "api/status":
                    return self.send(200, self.server.engine.status())
                if path == "api/requests":
                    return self.send(200, self.server.engine.requests(limit))
                if path == "api/events":
                    return self.send(200, self.server.engine.events(limit))
                if path == "api/posts":
                    return self.send(200, self.server.engine.raw_posts(limit, offset))
                if path == "api/jobs":
                    return self.send(200, self.server.engine.jobs())
            except ValueError as exc:
                return self.send(400, {"error": str(exc)})
            return self.send(404, {"error": "接口不存在"})
        asset = (HERE / "static" / (path or "index.html")).resolve()
        static = (HERE / "static").resolve()
        if not asset.is_relative_to(static) or not asset.is_file():
            return self.send(404, {"error": "页面不存在"})
        content_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
            content_type += "; charset=utf-8"
        self.send(200, asset.read_bytes(), content_type)

    def do_POST(self):
        path = unquote(urlparse(self.path).path).lstrip("/")
        if self.server.api_only and (path == "api/session" or not path.startswith("api/")):
            return self.send(404, {"error": "此节点仅开放 Bearer 鉴权 API"})
        if not self.origin_valid():
            return self.send(403, {"error": "跨站控制请求已拒绝"})
        try:
            obj = self.read_json()
            if path == "api/session":
                if not self.server.token_valid(obj.get("token")):
                    return self.send(401, {"error": "访问令牌不正确"})
                session = secrets.token_urlsafe(32)
                with self.server.session_lock:
                    self.server.sessions = {s: t for s, t in self.server.sessions.items() if t > time.time()}
                    self.server.sessions[session] = time.time() + 43200
                return self.send(200, {"authenticated": True}, headers={"Set-Cookie": self.cookie(session, 43200)})
            if not self.authenticated():
                return self.send(401, {"error": "节点 API 需要有效 Bearer 令牌" if self.server.api_only else "请先登录控制台"})
            if self.fleet_route("POST", path, obj=obj):
                return
            if path == "api/jobs":
                self.server.engine.create_job(obj)
            elif path == "api/control":
                action = obj.get("action")
                if action not in ("start", "pause", "retry"):
                    raise ValueError("action 必须是 start、pause 或 retry")
                getattr(self.server.engine, action)()
            else:
                return self.send(404, {"error": "接口不存在"})
            return self.send(200, self.server.engine.status())
        except ValueError as exc:
            self.send(400, {"error": str(exc)})
        except RuntimeError as exc:
            self.send(409, {"error": str(exc)})

    def do_PATCH(self):
        path = unquote(urlparse(self.path).path).lstrip("/")
        if self.server.api_only and path == "api/session":
            return self.send(404, {"error": "此节点仅开放 Bearer 鉴权 API"})
        if not self.origin_valid():
            return self.send(403, {"error": "跨站控制请求已拒绝"})
        if not self.authenticated():
            return self.send(401, {"error": "节点 API 需要有效 Bearer 令牌" if self.server.api_only else "请先登录控制台"})
        path = unquote(urlparse(self.path).path).lstrip("/")
        if path.startswith("api/fleet/") or path.startswith("api/nodes/"):
            try:
                self.fleet_route("PATCH", path, obj=self.read_json())
            except ValueError as exc:
                self.send(400, {"error": str(exc)})
            return
        if path != "api/jobs/current":
            return self.send(404, {"error": "接口不存在"})
        try:
            self.server.engine.update_job(self.read_json())
            self.send(200, self.server.engine.status())
        except ValueError as exc:
            self.send(400, {"error": str(exc)})
        except RuntimeError as exc:
            self.send(409, {"error": str(exc)})

    def do_DELETE(self):
        path = unquote(urlparse(self.path).path).lstrip("/")
        if self.server.api_only and path == "api/session":
            return self.send(404, {"error": "此节点仅开放 Bearer 鉴权 API"})
        if not self.origin_valid():
            return self.send(403, {"error": "跨站请求已拒绝"})
        path = unquote(urlparse(self.path).path).lstrip("/")
        if path.startswith("api/fleet/") or path.startswith("api/nodes/"):
            if not self.authenticated():
                return self.send(401, {"error": "节点 API 需要有效 Bearer 令牌" if self.server.api_only else "请先登录控制台"})
            self.fleet_route("DELETE", path)
            return
        if path == "api/jobs/current" or path.startswith("api/jobs/current/stocks/"):
            if not self.authenticated():
                return self.send(401, {"error": "节点 API 需要有效 Bearer 令牌" if self.server.api_only else "请先登录控制台"})
            try:
                if path == "api/jobs/current":
                    self.server.engine.delete_job()
                else:
                    self.server.engine.remove_stock(path[len("api/jobs/current/stocks/"):])
                return self.send(200, self.server.engine.status())
            except ValueError as exc:
                return self.send(400, {"error": str(exc)})
            except RuntimeError as exc:
                return self.send(409, {"error": str(exc)})
        if path != "api/session":
            return self.send(404, {"error": "接口不存在"})
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            pass
        if "backfill_session" in jar:
            with self.server.session_lock:
                self.server.sessions.pop(jar["backfill_session"].value, None)
        self.send(200, {"authenticated": False}, headers={"Set-Cookie": self.cookie("", 0)})


def main():
    from core import Engine
    from fleet import FleetManager
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8790)
    p.add_argument("--data-dir", type=Path, default=HERE / "data")
    p.add_argument("--token-file", type=Path)
    args = p.parse_args()
    api_only = os.environ.get("BACKFILL_API_ONLY", "0")
    if api_only not in {"0", "1"}:
        p.error("BACKFILL_API_ONLY 必须为 0 或 1")
    sync_enabled = os.environ.get("BACKFILL_FLEET_SYNC_ENABLED", "1")
    if sync_enabled not in {"0", "1"}:
        p.error("BACKFILL_FLEET_SYNC_ENABLED 必须为 0 或 1")
    if not 1 <= args.port <= 65535:
        p.error("--port 必须为 1–65535 的整数")
    args.data_dir.mkdir(parents=True, exist_ok=True)
    token_file = args.token_file or args.data_dir / "console.token"
    token = os.environ.get("BACKFILL_TOKEN")
    if not token:
        if not token_file.exists():
            token_file.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(secrets.token_urlsafe(32) + "\n")
        token = token_file.read_text().strip()
    if len(token) < 24:
        p.error("访问令牌至少 24 个字符；使用自动生成的 token 文件或 BACKFILL_TOKEN")
    engine = Engine(args.data_dir)
    fleet = FleetManager(args.data_dir, engine, auto_sync=sync_enabled == "1")
    srv = ConsoleServer((args.host, args.port), engine, token, os.environ.get("BACKFILL_COOKIE_PATH", "/"), fleet=fleet, api_only=api_only == "1")
    stopped = threading.Event()

    def worker():
        while not stopped.is_set():
            try:
                engine.tick()
            except Exception as exc:
                engine.pause()
                print(f"Worker paused after unexpected {type(exc).__name__}", flush=True)
            stopped.wait(0.5)

    thread = threading.Thread(target=worker, name="http-backfill-worker", daemon=True)
    thread.start()

    def fleet_worker():
        while not stopped.is_set():
            try:
                fleet.tick()
            except Exception as exc:
                print(f"Fleet local scheduling error: {type(exc).__name__}", flush=True)
            stopped.wait(0.5)

    fleet_thread = threading.Thread(target=fleet_worker, name="collector-fleet-worker", daemon=True)
    fleet_thread.start()

    def stop(*_):
        stopped.set()
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    surface = "仅 API 节点" if api_only == "1" else "控制台"
    print(f"{surface} http://{args.host}:{args.port}/，初始任务状态 {engine.status()['state']}；访问令牌文件 {token_file}", flush=True)
    try:
        srv.serve_forever(poll_interval=0.5)
    finally:
        stopped.set()
        fleet_thread.join(timeout=5)
        fleet.close()
        thread.join(timeout=35)
        srv.server_close()
        if not thread.is_alive():
            engine.close()


if __name__ == "__main__":
    main()
