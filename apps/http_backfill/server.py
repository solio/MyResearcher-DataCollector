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
import sys
import threading
import time
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "src"))


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, engine, token, cookie_path="/"):
        super().__init__(address, Handler)
        self.engine = engine
        self.token_digest = hashlib.sha256(token.encode()).digest()
        self.sessions = {}
        self.session_lock = threading.Lock()
        self.cookie_path = cookie_path

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

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path).lstrip("/")
        if path == "healthz":
            return self.send(200, {"ok": True})
        if path == "api/session":
            return self.send(200, {"authenticated": self.authenticated()})
        if path.startswith("api/"):
            if not self.authenticated():
                return self.send(401, {"error": "请先登录控制台"})
            try:
                query = parse_qs(parsed.query)
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
        if not self.origin_valid():
            return self.send(403, {"error": "跨站控制请求已拒绝"})
        path = urlparse(self.path).path.lstrip("/")
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
                return self.send(401, {"error": "请先登录控制台"})
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

    def do_DELETE(self):
        if not self.origin_valid():
            return self.send(403, {"error": "跨站请求已拒绝"})
        if urlparse(self.path).path.lstrip("/") != "api/session":
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
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8790)
    p.add_argument("--data-dir", type=Path, default=HERE / "data")
    p.add_argument("--token-file", type=Path)
    args = p.parse_args()
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
    srv = ConsoleServer((args.host, args.port), engine, token, os.environ.get("BACKFILL_COOKIE_PATH", "/"))
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

    def stop(*_):
        stopped.set()
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"控制台 http://{args.host}:{args.port}/，初始任务状态 {engine.status()['state']}；访问令牌文件 {token_file}", flush=True)
    try:
        srv.serve_forever(poll_interval=0.5)
    finally:
        stopped.set()
        thread.join(timeout=35)
        srv.server_close()
        if not thread.is_alive():
            engine.close()


if __name__ == "__main__":
    main()
