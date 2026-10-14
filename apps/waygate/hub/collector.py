"""Reuse the operator helper without putting SSH credentials on the handset."""
from __future__ import annotations

import asyncio
import importlib.util
import ipaddress
from pathlib import Path
import re
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("waygate_operator", REPO / "scripts/ops/verify_http_backfill.py")
helper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helper)


def connection(config, fleet_node):
    # Registry is the only source of collector membership. Admin only supplies
    # SSH credentials keyed by the registered host (or 'local'), once on hub.
    host = urlsplit(fleet_node.get("base_url") or "").hostname
    key = "local" if fleet_node.get("local") else host
    settings = config.get("ssh", {}).get(key)
    if not settings:
        raise ValueError("主控尚未配置此节点的 SSH 验证通道，请在部署端配置")
    node = dict(settings, host=settings.get("host", host or "127.0.0.1"))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", node["host"]):
        raise ValueError("SSH 主机配置不合法")
    if not re.fullmatch(r"[A-Za-z0-9_]+", node.get("user", "root")):
        raise ValueError("SSH 用户配置不合法")
    if type(node.get("port", 22)) is not int or not 1 <= node.get("port", 22) <= 65535:
        raise ValueError("SSH 端口配置不合法")
    return node


class Collector:
    async def call(self, node, action, **payload):
        return await asyncio.to_thread(helper.remote, node, action, **payload)

    async def tunnel(self, node, route):
        tunnel = helper.Tunnel(node, route)
        # Shield allocation so cancellation cannot orphan a forwarding process.
        task = asyncio.create_task(asyncio.to_thread(tunnel.__enter__))
        try:
            return await asyncio.shield(task)
        except BaseException:
            try:
                await task
            finally:
                await asyncio.to_thread(tunnel.close)
            raise


def check_target(view, expected):
    if view.get("instance_id") != expected["instance_id"] or view.get("revision") != expected["revision"]:
        raise ValueError("节点或任务配置已变更，请刷新待办")
    target = next((r for r in view["targets"] if all(r.get(k) == expected[k]
                  for k in ("instance_id", "job", "stock", "task", "request_id"))), None)
    if not target:
        raise ValueError("原阻断已变化或解除，请刷新待办")
    if "route" in expected and target["route"]["signature"] != expected["route"]["signature"]:
        raise ValueError("代理出口已变更，验证会话已停止")
    return target


def allowed_connect(header, domains, ready):
    """Parse exactly one HTTPS CONNECT; never forward an arbitrary HTTP proxy."""
    try:
        text = header.decode("ascii")
        method, authority, protocol = text.split("\r\n", 1)[0].split(" ")
        host, port = authority.rsplit(":", 1)
        host = host.lower()
    except (ValueError, UnicodeDecodeError):
        raise ValueError("不支持的代理请求") from None
    if method != "CONNECT" or protocol not in {"HTTP/1.0", "HTTP/1.1"} or port != "443":
        raise ValueError("验证通道仅允许 HTTPS CONNECT")
    if not re.fullmatch(r"[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?", host) or ".." in host:
        raise ValueError("不支持的目标主机")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("验证通道不允许 IP 字面地址")
    if host == "api.ipify.org":
        return host
    if not ready or not any(host == d or host.endswith("." + d) for d in domains):
        raise ValueError("目标不在验证资源域名范围内")
    return host
