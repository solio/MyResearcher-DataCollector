from __future__ import annotations

import argparse
import asyncio
import contextlib
import errno
import hmac
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit

from aiohttp import ClientConnectorError, ClientPayloadError, ClientSession, ClientTimeout, WSMsgType, web

from .collector import Collector, allowed_connect, check_target, connection, direct_descriptor
from .store import Store, fresh, notification_payload
from .push import TokenProvider

MAX_FLEET_BYTES = 16 * 1024 * 1024


class Waygate:
    def __init__(self, config, store, collector=None):
        self.config, self.store = config, store
        self.collector = collector or Collector()
        self.token = Path(config["collector_token_file"]).read_text().strip()
        if len(self.token) < 16:
            raise ValueError("控制台令牌无效")
        self.nodes, self.sessions, self.console_tickets, self.console_cookies = [], {}, {}, {}
        self.jobs = set()
        self.last_poll, self.poll_error = 0, "尚未连接主控"
        self.http = None
        self.push_token = TokenProvider()

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.jobs.add(task)
        task.add_done_callback(self.jobs.discard)
        return task

    async def fleet(self):
        body = bytearray()
        try:
            async with self.http.get(self.config["collector_url"].rstrip("/") + "/api/fleet",
                                     headers={"Authorization": "Bearer " + self.token}, allow_redirects=False) as response:
                if response.status != 200:
                    raise ValueError("collector /api/fleet 返回 HTTP " + str(response.status))
                # StreamReader.read(n) may return just the first network fragment.
                # Wait for EOF, retaining the response-size and total-time limits.
                async for chunk in response.content.iter_chunked(64 * 1024):
                    if len(body) + len(chunk) > MAX_FLEET_BYTES:
                        raise ValueError("collector /api/fleet 响应超过 16 MiB 上限")
                    body.extend(chunk)
        except ClientConnectorError as exc:
            reason = "连接被拒绝" if exc.os_error.errno == errno.ECONNREFUSED else "无法建立连接"
            raise ValueError("collector /api/fleet " + reason + "（errno=" + str(exc.os_error.errno) + "）") from None
        except ClientPayloadError:
            raise ValueError("collector /api/fleet 响应传输中断（已收到 %d 字节）" % len(body)) from None
        except asyncio.TimeoutError:
            raise ValueError("collector /api/fleet 请求超时（25 秒，已收到 %d 字节）" % len(body)) from None
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError("collector /api/fleet 返回无效 JSON（已读取 %d 字节，错误位置 %d）" % (len(body), exc.pos)) from None
        except UnicodeError:
            raise ValueError("collector /api/fleet 响应字符编码无效（已读取 %d 字节）" % len(body)) from None
        if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), list):
            raise ValueError("collector /api/fleet 响应缺少 nodes 数组")
        self.store.reconcile(payload["nodes"])
        self.nodes = payload["nodes"]
        self.last_poll, self.poll_error = time.time(), ""

    async def verification_target(self, ident):
        await self.fleet()
        item = self.store.incident(ident)
        if item["state"] != "pending" or item["kind"] != "challenge":
            raise ValueError("此事项不需要滑动验证，请查看控制台")
        node = next((n for n in self.nodes if n["id"] == item["node"]), None)
        if not node or not fresh(node, time.time()):
            raise ValueError("节点离线或状态过期，请稍后刷新")
        return item, node

    async def direct_verification(self, ident):
        item, node = await self.verification_target(ident)
        # No SSH, lease, task mutation or source request. The phone executes the
        # existing helper over its own SSH connection and confirms the target.
        return direct_descriptor(self.config, node, item["target"])

    async def create_session(self, ident):
        item, node = await self.verification_target(ident)
        if any(s["node_id"] == item["node"] for s in self.sessions.values()):
            raise ValueError("此节点已有验证会话，请先结束或等待会话过期")
        ssh = connection(self.config, node)
        sid = secrets.token_urlsafe(24)
        session = {"id": sid, "incident": ident, "node_id": item["node"], "node": ssh,
                   "target": item["target"], "created": time.time(), "touch": time.time(),
                   "tunnel": None, "sockets": set(), "ready": False, "phase": "preparing"}
        self.sessions[sid] = session
        self.store.session(sid, ident, "preparing")
        self.spawn(self.prepare(session))
        return {"id": sid, "phase": "preparing"}

    def set_phase(self, session, phase, result=None):
        session["phase"] = phase
        self.store.session(session["id"], phase=phase, result=result)

    async def prepare(self, session):
        try:
            node = session["node"]
            view = await self.collector.call(node, "audit")
            if session["phase"] != "preparing":
                return
            target = check_target(view, session["target"])
            session["target"] = target
            await self.collector.call(node, "prepare", target=target)
            if session["phase"] != "preparing":
                return
            exit_check = await self.collector.call(node, "exit", stock=target["stock"], job=target["job"])
            if session["phase"] != "preparing":
                return
            if exit_check["route"]["signature"] != target["route"]["signature"]:
                raise ValueError("服务器出口已改变，请重新处理")
            target["exit_ip"] = str(ipaddress.ip_address(exit_check["exit_ip"]))
            tunnel = await self.collector.tunnel(node, target["route"])
            session["tunnel"] = tunnel
            if session["phase"] != "preparing":
                await asyncio.to_thread(tunnel.close)
                return
            self.set_phase(session, "verifying", {"exit_ip": target["exit_ip"], "expires_at": session["created"] + 600})
        except Exception as exc:
            if session["phase"] != "cancelled":
                self.set_phase(session, "failed", {"message": safe_error(exc)})
            await self.release(session)
        finally:
            if session["phase"] == "cancelled":
                await self.release(session)

    def active(self, sid):
        s = self.sessions.get(sid)
        if not s or s["phase"] != "verifying" or time.time() - s["created"] >= 600:
            raise ValueError("验证会话已结束或过期，请返回待办")
        return s

    async def ready(self, sid, browser_ip):
        s = self.active(sid)
        if str(ipaddress.ip_address(browser_ip)) != s["target"]["exit_ip"]:
            await self.cancel(sid)
            raise ValueError("手机验证窗口与节点出口不一致，已关闭通道")
        view = await self.collector.call(s["node"], "audit")
        check_target(view, s["target"])
        # Server source URL is released only after the browser's neutral check.
        self.active(sid)
        s["ready"], s["touch"] = True, time.time()
        return {"url": s["target"]["url"]}

    async def finish(self, sid):
        s = self.active(sid)
        if not s["ready"]:
            raise ValueError("尚未完成共同出口检查")
        # Durable claim precedes any remote mutation. A repeated POST cannot dispatch twice.
        self.set_phase(s, "dispatching", {"message": "正在单次探测；请勿重复操作"})
        self.spawn(self.probe(s))
        return self.store.session(sid)

    async def probe(self, s):
        try:
            await self.close_relay(s)
            queued = await self.collector.call(s["node"], "probe", target=s["target"])
            deadline = time.monotonic() + 125
            result = None
            while time.monotonic() < deadline:
                result = await self.collector.call(s["node"], "result", target=s["target"], **queued)
                if result["done"]:
                    break
                await asyncio.sleep(2)
            if not result or not result["done"]:
                raise ValueError("探测结果尚未确认；没有再次请求，请在控制台核对")
            request = result["request"]
            if request["outcome"] not in {"real_data", "detail_unavailable"}:
                self.set_phase(s, "failed", {"message": "探测未通过，任务保持暂停", "request_id": request["id"], "outcome": request["outcome"]})
                return
            try:
                await self.collector.call(s["node"], "resume", target=s["target"])
            except Exception as exc:
                self.set_phase(s, "verified", {"message": "验证通过，尚未继续：" + safe_error(exc), "request_id": request["id"]})
            else:
                self.set_phase(s, "resumed", {"message": "验证通过，已继续 " + s["target"]["stock"], "request_id": request["id"]})
        except Exception as exc:
            self.set_phase(s, "uncertain", {"message": safe_error(exc)})
        finally:
            await self.release(s)

    async def close_relay(self, s):
        for ws in list(s["sockets"]):
            await ws.close(code=1001)
        if s["tunnel"]:
            tunnel, s["tunnel"] = s["tunnel"], None
            await asyncio.to_thread(tunnel.close)

    async def release(self, s):
        await self.close_relay(s)
        self.sessions.pop(s["id"], None)

    async def cancel(self, sid):
        s = self.sessions.get(sid)
        if s and s["phase"] in {"preparing", "verifying"}:
            preparing = s["phase"] == "preparing"
            self.set_phase(s, "cancelled", {"message": "已关闭临时通道，任务保持原状态"})
            if not preparing:
                await self.release(s)
        return self.store.session(sid)

    async def relay(self, request):
        s = self.active(request.match_info["sid"])
        if len(s["sockets"]) >= 24:
            raise ValueError("验证连接数已达上限")
        ws = web.WebSocketResponse(heartbeat=15, max_msg_size=256 * 1024, compress=False)
        await ws.prepare(request)
        s["sockets"].add(ws)
        writer = None
        upstream = None
        try:
            data = b""
            async with asyncio.timeout(12):
                while b"\r\n\r\n" not in data:
                    msg = await ws.receive()
                    if msg.type != WSMsgType.BINARY:
                        raise ValueError("验证通道只接收二进制代理数据")
                    data += msg.data
                    if len(data) > 16384:
                        raise ValueError("代理请求头过大")
            head, rest = data.split(b"\r\n\r\n", 1)
            host = allowed_connect(head, self.config["source_domains"], s["ready"])
            self.active(s["id"])
            reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", s["tunnel"].port), 10)
            # Regenerate CONNECT, discarding all client-supplied proxy headers.
            writer.write(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode() + rest)
            await writer.drain()

            async def download():
                while chunk := await reader.read(65536):
                    await ws.send_bytes(chunk)
                await ws.close()

            upstream = asyncio.create_task(download())
            async for message in ws:
                if message.type != WSMsgType.BINARY:
                    break
                writer.write(message.data)
                await writer.drain()
        except (ValueError, OSError, asyncio.TimeoutError, KeyError, AttributeError):
            await ws.close(code=1008, message=b"Verification relay closed")
        finally:
            if upstream:
                upstream.cancel()
                await asyncio.gather(upstream, return_exceptions=True)
            if writer:
                writer.close()
                with contextlib.suppress(OSError):
                    await writer.wait_closed()
            s["sockets"].discard(ws)
        return ws

    async def push_once(self):
        cfg = self.config.get("push", {})
        if not cfg.get("enabled"):
            return
        for row in self.store.notifications():
            try:
                project = cfg.get("project_id", "")
                if not re.fullmatch(r"[A-Za-z0-9_-]+", project):
                    raise ValueError("Push Kit project_id 未配置")
                jwt = self.push_token.get(cfg)
                url = "https://push-api.cloud.huawei.com/v3/" + project + "/messages:send"
                async with self.http.post(url, headers={"Authorization": "Bearer " + jwt, "push-type": "0"},
                                          json=notification_payload(row, cfg), allow_redirects=False) as response:
                    body = await response.json()
                    if response.status != 200 or str(body.get("code")) != "80000000":
                        # Do not reflect token-bearing vendor response into logs/UI.
                        raise ValueError("Push Kit 未接受消息，HTTP " + str(response.status))
                self.store.notified(row)
            except Exception as exc:
                self.store.notified(row, safe_error(exc))

    async def loop(self):
        while True:
            try:
                await self.fleet()
            except Exception as exc:
                error = safe_error(exc)
                if error != self.poll_error:
                    logging.warning("Waygate 同步 collector 失败：%s", error)
                self.poll_error = error
            await asyncio.sleep(10)

    async def reap(self):
        while True:
            for s in list(self.sessions.values()):
                if s["phase"] != "dispatching" and (time.time() - s["touch"] > 45 or time.time() - s["created"] > 600):
                    await self.cancel(s["id"])
            self.console_tickets = {k: v for k, v in self.console_tickets.items() if v > time.time()}
            self.console_cookies = {k: v for k, v in self.console_cookies.items() if v > time.time()}
            await asyncio.sleep(2)

    async def push_loop(self):
        while True:
            await self.push_once()
            await asyncio.sleep(20)


def safe_error(exc):
    # remote helper errors are curated; raw network/process exceptions may carry credentials.
    if isinstance(exc, (ValueError, RuntimeError)):
        return str(exc)[:400]
    return "连接未完成（" + type(exc).__name__ + "），请查看节点和通道状态"


def stock_summary(status, runtime):
    coverage = next((r for r in status.get("coverage", []) if r.get("stock") == runtime["stock"]), {})
    span = coverage.get("post_time_range") or {}
    details = coverage.get("details") or {}
    earliest, latest = span.get("earliest"), span.get("latest")
    return {"stock": runtime["stock"], "state": runtime["state"],
            "window": (str(earliest)[:10] + " 至 " + str(latest)[:10]) if earliest and latest else "尚未取得",
            "posts": str(details.get("observed", "—")), "pending": str(details.get("pending", "—"))}


HUB_KEY = web.AppKey("waygate", Waygate)


@web.middleware
async def authenticate(request, handler):
    hub = request.app[HUB_KEY]
    try:
        if request.path == "/health":
            return web.json_response({"ok": True})
        if request.path.startswith("/console/"):
            return await handler(request)
        bearer = request.headers.get("Authorization", "")
        if not hmac.compare_digest(bearer, "Bearer " + hub.token):
            return web.json_response({"error": "主控令牌不正确或已失效"}, status=401)
        response = await handler(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
    except (ValueError, RuntimeError) as exc:
        return web.json_response({"error": safe_error(exc)}, status=409)
    except web.HTTPException:
        raise
    except Exception as exc:
        logging.warning("waygate request failed: %s", type(exc).__name__)
        return web.json_response({"error": safe_error(exc)}, status=502)


async def route(request):
    hub = request.app[HUB_KEY]
    path = request.path
    data = await request.json() if request.method == "POST" else {}
    if not isinstance(data, dict):
        raise ValueError("请求必须是 JSON 对象")
    if path == "/v1/inbox":
        nodes = [{"id": n["id"], "name": n["name"], "online": fresh(n, time.time()),
                  "state": (n.get("status") or {}).get("state", "unknown"),
                  "stocks": [stock_summary(n.get("status") or {}, r)
                             for r in (n.get("status") or {}).get("stock_runtimes", [])]} for n in hub.nodes]
        push_errors = hub.store.db.execute("SELECT COUNT(*) FROM outbox o JOIN incidents i ON i.id=o.incident WHERE i.state='pending' AND o.state IN ('pending','failed') AND o.error IS NOT NULL").fetchone()[0]
        return web.json_response({"items": hub.store.inbox(hub.nodes), "nodes": nodes,
                                  "last_poll": hub.last_poll, "error": hub.poll_error,
                                  "push_enabled": bool(hub.config.get("push", {}).get("enabled")),
                                  "push_errors": push_errors})
    if path == "/v1/devices":
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,100}", data.get("id", "")):
            raise ValueError("设备编号无效")
        token = data.get("token", "")
        if not isinstance(token, str) or not 10 <= len(token) <= 4096:
            raise ValueError("推送令牌无效")
        hub.store.device(data["id"], token)
        return web.json_response({"ok": True})
    if path == "/v1/console-ticket":
        ticket = secrets.token_urlsafe(32)
        hub.console_tickets[ticket] = time.time() + 30
        return web.json_response({"url": hub.config["public_url"].rstrip("/") + "/console/?ticket=" + ticket})
    if "incident" in request.match_info:
        ident = request.match_info["incident"]
        if path.endswith("/verification"):
            return web.json_response(await hub.direct_verification(ident))
        if path.endswith("/snooze"):
            hub.store.snooze(ident)
            return web.json_response({"ok": True})
        return web.json_response(await hub.create_session(ident), status=202)
    if "device" in request.match_info:
        hub.store.remove_device(request.match_info["device"])
        return web.json_response({"ok": True})
    sid = request.match_info["sid"]
    if path.endswith("/ready"):
        return web.json_response(await hub.ready(sid, data.get("browser_ip", "")))
    if path.endswith("/finish"):
        if data.get("human_confirmed") is not True:
            raise ValueError("请在人工验证完成后确认")
        return web.json_response(await hub.finish(sid), status=202)
    if path.endswith("/cancel"):
        return web.json_response(await hub.cancel(sid))
    if sid in hub.sessions:
        hub.sessions[sid]["touch"] = time.time()
    return web.json_response(hub.store.session(sid))


async def console(request):
    hub = request.app[HUB_KEY]
    if request.query.get("ticket"):
        expiry = hub.console_tickets.pop(request.query["ticket"], 0)
        if expiry < time.time():
            raise web.HTTPUnauthorized(text="控制台入口已过期，请返回应用重开")
        cookie = secrets.token_urlsafe(32)
        hub.console_cookies[cookie] = time.time() + 43200
        response = web.HTTPFound(hub.config["public_url"].rstrip("/") + "/console/")
        response.headers["Referrer-Policy"] = "no-referrer"
        response.set_cookie("waygate_console", cookie, httponly=True, secure=True, samesite="Strict",
                            path=urlsplit(hub.config["public_url"]).path.rstrip("/") + "/console/", max_age=43200)
        raise response
    if hub.console_cookies.get(request.cookies.get("waygate_console"), 0) < time.time():
        raise web.HTTPUnauthorized(text="请从 Waygate 重新进入控制台")
    origin = request.headers.get("Origin")
    expected = urlsplit(hub.config["public_url"])
    if origin and origin != f"{expected.scheme}://{expected.netloc}":
        raise web.HTTPForbidden(text="跨站操作已拒绝")
    tail = request.match_info["tail"]
    if any(part in {".", ".."} for part in tail.split("/")) or "\\" in tail or tail.startswith("/"):
        raise web.HTTPBadRequest()
    url = hub.config["collector_url"].rstrip("/") + "/" + tail
    headers = {"Authorization": "Bearer " + hub.token, "Content-Type": request.headers.get("Content-Type", "application/json")}
    async with hub.http.request(request.method, url, params=request.query, data=await request.read(),
                                headers=headers, allow_redirects=False) as upstream:
        if 300 <= upstream.status < 400:
            raise ValueError("主控返回了意外跳转")
        response = web.StreamResponse(status=upstream.status,
                                      headers={k: v for k, v in upstream.headers.items()
                                               if k.lower() in {"content-type", "content-disposition"}})
        response.headers["Cache-Control"] = "no-store"
        await response.prepare(request)
        async for chunk in upstream.content.iter_chunked(65536):
            await response.write(chunk)
        return response


def create_app(config, store=None, collector=None, background=True):
    hub = Waygate(config, store or Store(Path(config["data_dir"]) / "waygate.sqlite3"), collector)
    app = web.Application(middlewares=[authenticate], client_max_size=64 * 1024)
    app[HUB_KEY] = hub
    app.router.add_get("/health", route)
    app.router.add_get("/v1/inbox", route)
    app.router.add_post("/v1/devices", route)
    app.router.add_delete("/v1/devices/{device}", route)
    app.router.add_post("/v1/console-ticket", route)
    app.router.add_route("*", "/console/{tail:.*}", console)
    app.router.add_post("/v1/incidents/{incident}/sessions", route)
    app.router.add_get("/v1/incidents/{incident}/verification", route)
    app.router.add_post("/v1/incidents/{incident}/snooze", route)
    app.router.add_get("/v1/sessions/{sid}", route)
    for action in ("ready", "finish", "cancel"):
        app.router.add_post("/v1/sessions/{sid}/" + action, route)
    app.router.add_get("/v1/sessions/{sid}/relay", hub.relay)

    async def lifecycle(_app):
        hub.http = ClientSession(timeout=ClientTimeout(total=25), trust_env=False)
        tasks = [asyncio.create_task(hub.loop()), asyncio.create_task(hub.push_loop()), asyncio.create_task(hub.reap())] if background else []
        yield
        for task in tasks + list(hub.jobs):
            task.cancel()
        await asyncio.gather(*tasks, *list(hub.jobs), return_exceptions=True)
        for s in list(hub.sessions.values()):
            await hub.release(s)
        await hub.http.close()
        hub.store.db.close()

    app.cleanup_ctx.append(lifecycle)
    return app


def main():
    parser = argparse.ArgumentParser(description="Waygate mobile verification hub")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    config = json.loads(Path(args.config).read_text())
    public = urlsplit(config["public_url"])
    collector = urlsplit(config["collector_url"])
    if public.scheme != "https" or not public.hostname or public.query or public.fragment or public.username:
        raise ValueError("public_url 必须是可信 HTTPS 主控地址")
    if collector.scheme not in {"http", "https"} or not collector.hostname or collector.username or collector.query or collector.fragment:
        raise ValueError("collector_url 必须是部署者指定的控制台地址")
    Path(config["data_dir"]).mkdir(parents=True, exist_ok=True, mode=0o700)
    web.run_app(create_app(config), host=config.get("listen", "127.0.0.1"), port=config.get("port", 8791), access_log=None)


if __name__ == "__main__":
    main()
