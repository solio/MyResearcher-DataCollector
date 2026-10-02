"""Private collector-node control and incremental evidence transfer.

This client only speaks the configured collectors' APIs. It never requests an
investment source, retries a mutation, or rotates the acquisition endpoint.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import csv
import hashlib
import io
import json
import ipaddress
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlencode, urlparse, urlunparse

from activity import activity_page, activity_query

VERSION = "http-backfill.v4"
SUPPORTED_NODE_VERSIONS = {VERSION, "http-backfill.v5"}
MAX_NODES = 16
MAX_JSON = 32 * 1024 * 1024
MAX_DOWNLOAD = 2 * 1024 * 1024 * 1024
DOWNLOAD_TIMEOUT = 15
DOWNLOAD_DEADLINE = 600
MAX_DOWNLOAD_FIELD = 64 * 1024 * 1024


class FleetError(RuntimeError):
    def __init__(self, status_code, error, ambiguous=False):
        super().__init__(error)
        self.status_code, self.error, self.ambiguous = status_code, error, bool(ambiguous)


@dataclass
class PostsDownload:
    """A fully validated private temporary file; caller must close it."""
    stream: object
    bytes: int
    posts: int
    snapshot_at: str
    content_type: str
    sha256: str
    remote_instance_id: str | None = None
    node_id: str | None = None
    instance_id: str | None = None

    def close(self):
        self.stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch is not None else None


def _alias(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("实例 ID 必须是 1–64 位 ASCII 字母、数字、下划线或连字符")
    return value


def normalize_base_url(value):
    if not isinstance(value, str) or not value or value != value.strip() or re.search(r"[\s\\\x00-\x1f]", value):
        raise ValueError("实例地址必须是明确的 HTTP(S) URL")
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("实例 URL 主机或端口无效") from exc
    if (parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or "?" in value or "#" in value or parsed.params
            or any(x in parsed.path for x in ("%", "//"))
            or any(p in {".", ".."} for p in parsed.path.split("/"))):
        raise ValueError("实例 URL 只能含 HTTP(S) 主机、端口和路径，不能含凭据、查询、片段或路径跳转")
    hostname = parsed.hostname.lower()
    if ":" in hostname:
        hostname = "[" + hostname + "]"
    authority = hostname + (f":{port}" if port is not None else "")
    return urlunparse((parsed.scheme.lower(), authority, parsed.path.rstrip("/"), "", "", ""))


def direct_base_url(host, port, scheme="http"):
    """Build a root URL only from an IP literal and an explicit integer port."""
    if not isinstance(host, str) or not host or host != host.strip() or "%" in host:
        raise ValueError("host 必须是 IPv4 或 IPv6 地址，不能含域名、空白或区域标识")
    if host.startswith("[") or host.endswith("]"):
        if not (host.startswith("[") and host.endswith("]") and ":" in host[1:-1]):
            raise ValueError("host 的 IPv6 括号不完整")
        host = host[1:-1]
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("host 必须是完整 IPv4 或 IPv6 地址，不能含协议、端口、路径或凭据") from exc
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("port 必须是 1–65535 的整数")
    if not isinstance(scheme, str) or scheme not in {"http", "https"}:
        raise ValueError("scheme 必须是 http 或 https")
    authority = f"[{address}]" if address.version == 6 else str(address)
    return f"{scheme}://{authority}:{port}"


def connection_fields(base_url, local=False):
    """Derive public edit fields; old registry URLs need no data migration."""
    result = {"connection_mode": "local" if local else "url", "host": None, "port": None, "scheme": None}
    if local or base_url is None:
        return result
    parsed = urlparse(base_url)
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return result
    if not parsed.path and parsed.port is not None and "%" not in str(address):
        result.update(connection_mode="direct", host=str(address), port=parsed.port, scheme=parsed.scheme)
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class NodeClient:
    """No proxies/redirects; bounded JSON reads; one attempt per command."""
    def __init__(self, timeout=3, max_bytes=MAX_JSON):
        self.timeout, self.max_bytes = timeout, max_bytes

    def request(self, method, url, token, body=None):
        encoded = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=encoded, headers=headers, method=method)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        deadline = time.monotonic() + 8
        try:
            try:
                response = opener.open(request, timeout=self.timeout)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                if response.status in {301, 302, 303, 307, 308}:
                    raise FleetError(502, "节点返回重定向；已拒绝转发鉴权信息", method != "GET")
                chunks, length = [], 0
                while True:
                    if time.monotonic() > deadline:
                        raise FleetError(504, "节点响应超时", method != "GET")
                    piece = response.read1(min(65536, self.max_bytes + 1 - length))
                    if not piece:
                        break
                    chunks.append(piece)
                    length += len(piece)
                    if length > self.max_bytes:
                        raise FleetError(502, "节点响应超过传输上限", method != "GET")
                try:
                    payload = json.loads(b"".join(chunks).decode("utf-8", errors="strict"))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise FleetError(502, "节点未返回完整 UTF-8 JSON", method != "GET") from exc
                if not 200 <= response.status < 300:
                    message = payload.get("error") if isinstance(payload, dict) else None
                    status = response.status if response.status in {400, 409, 404} else 502
                    # Redact at the manager boundary before truncating; slicing
                    # here could publish a credential prefix at the cut point.
                    raise FleetError(status, str(message or f"节点返回 HTTP {response.status}"),
                                     method != "GET" and response.status >= 500)
                return payload
        except FleetError:
            raise
        except (TimeoutError, socket.timeout) as exc:
            raise FleetError(504, "节点连接或响应超时", method != "GET") from exc
        except (OSError, urllib.error.URLError, ValueError) as exc:
            timeout = isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout))
            raise FleetError(504 if timeout else 502, "节点连接超时" if timeout else "无法连接节点；请检查地址、网络及服务", method != "GET") from exc

    @staticmethod
    def _validate_posts(stream, format, expected_posts, deadline, token):
        from data_export import COLUMNS, EXTRA
        counters = {"read_count", "reply_count", "like_count", "forward_count"}
        count = 0
        try:
            if format == "jsonl":
                while True:
                    line = stream.readline(MAX_DOWNLOAD_FIELD + 1)
                    if not line:
                        break
                    if time.monotonic() > deadline:
                        raise FleetError(504, "节点导出校验超过总时限")
                    if len(line) > MAX_DOWNLOAD_FIELD:
                        raise ValueError("导出单行过大")
                    item = json.loads(line.decode("utf-8", errors="strict"))
                    if (not isinstance(item, dict) or set(item) != set(COLUMNS + EXTRA)
                            or type(item["content_missing"]) is not bool
                            or item["content_missing"] != (item["content"] is None)
                            or item["research_only"] is not True or item["model_database_eligible"] is not False):
                        raise ValueError("导出帖子字段无效")
                    if any(item[field] is not None and type(item[field]) is not (int if field in counters else str)
                           for field in COLUMNS):
                        raise ValueError("导出帖子字段类型无效")
                    if any(token in value for value in item.values() if isinstance(value, str)):
                        raise ValueError("导出包含私密鉴权信息")
                    count += 1
            else:
                # The original export permits multiline bodies; csv.reader must
                # count records, rather than treating every newline as a post.
                csv.field_size_limit(MAX_DOWNLOAD_FIELD)
                text = io.TextIOWrapper(stream, encoding="utf-8-sig", errors="strict", newline="")
                try:
                    def lines():
                        while True:
                            line = text.readline(MAX_DOWNLOAD_FIELD + 1)
                            if not line:
                                return
                            if len(line) > MAX_DOWNLOAD_FIELD:
                                raise ValueError("导出单行过大")
                            yield line
                    reader = csv.reader(lines(), strict=True)
                    if next(reader, None) != list(COLUMNS + EXTRA):
                        raise ValueError("导出 CSV 表头无效")
                    for row in reader:
                        if time.monotonic() > deadline:
                            raise FleetError(504, "节点导出校验超过总时限")
                        if (len(row) != len(COLUMNS + EXTRA) or row[-3] not in {"True", "False"}
                                or row[-2:] != ["True", "False"]):
                            raise ValueError("导出 CSV 帖子字段无效")
                        if any(token in value for value in row):
                            raise ValueError("导出包含私密鉴权信息")
                        count += 1
                finally:
                    text.detach()  # The artifact, rather than this wrapper, owns the file.
            if count != expected_posts:
                raise ValueError("导出条数与响应声明不一致")
        except (ValueError, UnicodeError, csv.Error) as exc:
            raise FleetError(502, "节点未返回完整有效的帖子导出文件") from exc
        stream.seek(0)

    def download(self, url, token, format):
        """One GET into a temporary file, with a separate large-file budget."""
        mime = "text/csv" if format == "csv" else "application/x-ndjson"
        request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token,
                                                      "Accept": mime, "Accept-Encoding": "identity"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        deadline = time.monotonic() + DOWNLOAD_DEADLINE
        stream = None
        try:
            try:
                response = opener.open(request, timeout=DOWNLOAD_TIMEOUT)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                if response.status in {301, 302, 303, 307, 308}:
                    raise FleetError(502, "节点下载返回重定向；已拒绝转发鉴权信息")
                if response.status != 200:
                    if response.status in {401, 403}:
                        raise FleetError(502, f"节点下载鉴权失败（HTTP {response.status}）；请检查该节点登记的令牌")
                    if response.status == 404:
                        raise FleetError(404, "选中节点没有帖子下载接口（HTTP 404）；未回退主控数据")
                    raise FleetError(409 if response.status == 409 else 502, f"节点下载返回 HTTP {response.status}；未回退主控数据")
                def header(name, required=True):
                    values = response.headers.get_all(name, [])
                    if len(values) > 1 or (required and not values):
                        raise FleetError(502, "节点导出响应头缺失或重复")
                    value = values[0] if values else None
                    if value is not None and any(ord(c) < 32 or ord(c) > 126 for c in value):
                        raise FleetError(502, "节点导出响应头无效")
                    return value
                content_type = header("Content-Type")
                if not re.fullmatch(re.escape(mime) + r"(?:\s*;\s*charset\s*=\s*(?:utf-8|\"utf-8\"))?", content_type, re.I):
                    raise FleetError(502, "节点下载未返回所选 CSV/JSONL 类型")
                disposition = header("Content-Disposition")
                matched = re.fullmatch(r'attachment;\s*filename="([A-Za-z0-9][A-Za-z0-9._-]{0,199})"', disposition, re.I)
                if not matched or not matched[1].lower().endswith("." + format):
                    raise FleetError(502, "节点导出文件名或下载类型无效")
                length = header("Content-Length")
                if not length.isdigit() or len(length) > 12 or int(length) > MAX_DOWNLOAD:
                    raise FleetError(502, "节点导出长度无效或超过 2 GiB 上限")
                if header("Content-Encoding", False) not in {None, "", "identity"} or header("Transfer-Encoding", False):
                    raise FleetError(502, "节点导出使用了不支持的编码或传输长度")
                count = header("X-Collector-Post-Count")
                if not count.isdigit() or len(count) > 20:
                    raise FleetError(502, "节点导出帖子条数无效")
                snapshot = header("X-Export-Snapshot-At")
                try:
                    captured = datetime.fromisoformat(snapshot.replace("Z", "+00:00"))
                    if captured.tzinfo is None:
                        raise ValueError
                except ValueError:
                    raise FleetError(502, "节点导出快照时间无效") from None
                remote_id = header("X-Collector-Instance-ID", False)
                stream = tempfile.TemporaryFile(mode="w+b", prefix="collector-node-download-")
                digest, received, tail = hashlib.sha256(), 0, b""
                secret = token.encode("ascii")
                while True:
                    if time.monotonic() > deadline:
                        raise FleetError(504, "节点导出下载超过 10 分钟总时限")
                    chunk = response.read1(128 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > int(length) or received > MAX_DOWNLOAD:
                        raise FleetError(502, "节点导出字节数超过声明或下载上限")
                    combined = tail + chunk
                    if secret in combined:
                        raise FleetError(502, "节点导出包含私密鉴权信息，已拒绝下载")
                    tail = combined[-max(1, len(secret) - 1):]
                    digest.update(chunk)
                    stream.write(chunk)
                if received != int(length):
                    raise FleetError(502, "节点导出文件不完整，实际长度与声明不一致")
                stream.seek(0)
                self._validate_posts(stream, format, int(count), deadline, token)
                result = PostsDownload(stream, received, int(count), captured.isoformat(), mime + "; charset=utf-8",
                                       digest.hexdigest(), remote_instance_id=remote_id)
                stream = None  # Ownership transfers only after all validation succeeds.
                return result
        except FleetError:
            raise
        except (TimeoutError, socket.timeout) as exc:
            raise FleetError(504, "节点导出连接或响应超时") from exc
        except (OSError, urllib.error.URLError, ValueError) as exc:
            timeout = isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout))
            raise FleetError(504 if timeout else 502, "节点导出连接超时" if timeout else "节点导出连接或临时文件保存失败") from exc
        finally:
            if stream is not None:
                stream.close()


class FleetManager:
    def __init__(self, data_dir, engine, client=None, clock=None, auto_sync=True):
        from federation import MergeStore
        self.data_dir, self.engine = Path(data_dir).resolve(), engine
        self.directory = self.data_dir / "fleet"
        if self.directory.is_symlink():
            raise ValueError("fleet 数据目录不能使用符号链接")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.registry_path = self.directory / "registry.json"
        self._lock, self._merge_lock = threading.RLock(), threading.Lock()
        self.client, self.clock = client or NodeClient(), clock or time.time
        self.auto_sync = bool(auto_sync)
        self._closed, self._cache, self._futures, self._poll_due, self._sync_due = False, {}, {}, {}, {}
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="collector-fleet")
        self._nodes, self._requested, self._generation = self._load()
        status = self.engine.status()
        self.local_id = self._identity(status)
        if any(n["instance_id"] == self.local_id for n in self._nodes.values()):
            raise ValueError("远程注册的实例身份与本地 UUID 重复；请修复私有注册表")
        self.merge_store = MergeStore(self.data_dir)
        self._merge_summary = self.merge_store.status()
        self._local = {"id": "local", "name": "本机", "base_url": None, "instance_id": self.local_id,
                       "version": status["version"], "local": True}
        self._cache["local"] = {"connection": "online", "status": status, "last_error": None, "last_seen_at": _iso(self.clock()),
                                "sync": {"state": "idle", "cursor": self.merge_store.cursor(self.local_id), "error": None}}
        for key, node in self._nodes.items():
            self._cache[key] = {"connection": "unknown", "status": None, "last_error": None, "last_seen_at": None,
                                "sync": {"state": "idle", "cursor": self.merge_store.cursor(node["instance_id"]), "error": None}}

    def _load(self):
        if self.registry_path.is_symlink():
            raise ValueError("实例注册表不能使用符号链接")
        if not self.registry_path.exists():
            return {}, set(), {}
        os.chmod(self.registry_path, 0o600)
        try:
            saved = json.loads(self.registry_path.read_text())
            if saved.get("version") != 1 or not isinstance(saved.get("nodes"), list):
                raise ValueError
            nodes, ids = {}, set()
            for node in saved["nodes"]:
                key = _alias(node["id"])
                if key == "local" or key in nodes or node["instance_id"] in ids:
                    raise ValueError
                self._identity({"instance_id": node["instance_id"], "version": node["version"]})
                node["base_url"] = normalize_base_url(node["base_url"])
                self._token(node["token"])
                node["local"] = False
                nodes[key] = node
                ids.add(node["instance_id"])
            if len(nodes) > MAX_NODES:
                raise ValueError
            requested = set(saved.get("sync_requested") or []) & ({"local"} | set(nodes))
            generations = saved.get("sync_generation") or {}
            if not isinstance(generations, dict) or any(type(v) is not int or v < 0 for v in generations.values()):
                raise ValueError
            generations = {k: v for k, v in generations.items() if k in {"local"} | set(nodes)}
            return nodes, requested, generations
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("私有实例注册表结构无效；保留原文件并检查配置") from exc

    def _save(self):
        payload = {"version": 1, "nodes": list(self._nodes.values()), "sync_requested": sorted(self._requested), "sync_generation": self._generation}
        fd, name = tempfile.mkstemp(prefix=".registry-", dir=self.directory)
        try:
            with os.fdopen(fd, "w") as handle:
                os.fchmod(handle.fileno(), 0o600)
                json.dump(payload, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.registry_path)
            dir_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @staticmethod
    def _token(value):
        if not isinstance(value, str) or not 24 <= len(value) <= 4096 or any(ord(c) < 32 or ord(c) > 126 for c in value):
            raise ValueError("节点令牌必须为 24–4096 位可打印 ASCII 字符")
        return value

    @staticmethod
    def _identity(status):
        if not isinstance(status, dict) or status.get("version") not in SUPPORTED_NODE_VERSIONS:
            raise FleetError(409, "节点须升级到 http-backfill.v4/v5，旧版没有可验证增量导出接口")
        value = status.get("instance_id")
        try:
            if not isinstance(value, str) or str(uuid.UUID(value)) != value:
                raise ValueError
        except (ValueError, AttributeError) as exc:
            raise FleetError(502, "节点缺少合法稳定实例 UUID") from exc
        return value

    def _redact(self, value, extra=()):
        with self._lock:
            secrets = [n["token"] for n in self._nodes.values()] + list(extra)
        if isinstance(value, str):
            for secret in secrets:
                if secret:
                    value = value.replace(secret, "[redacted]")
            return value
        if isinstance(value, list):
            return [self._redact(v, extra) for v in value]
        if isinstance(value, dict):
            return {self._redact(str(k), extra): self._redact(v, extra) for k, v in value.items()}
        return value

    @staticmethod
    def _connection_hint(node):
        parsed = urlparse(node["base_url"])
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return f"请从中央服务器检查到 {host}:{port} 的连接、节点监听及安全组/防火墙准入"

    def _remote(self, node, method, path, body=None, query=None):
        url = node["base_url"] + "/" + path.lstrip("/")
        if query:
            url += "?" + urlencode(query, doseq=True)
        try:
            result = self.client.request(method, url, node["token"], body)
            return self._redact(result, (node["token"],))
        except FleetError as exc:
            error = exc.error
            if exc.status_code in {502, 504}:
                error += "；" + self._connection_hint(node)
            raise FleetError(exc.status_code, self._redact(error, (node["token"],))[:2000], exc.ambiguous) from None
        except Exception:
            raise FleetError(502, self._redact("节点请求失败；" + self._connection_hint(node), (node["token"],)), method != "GET") from None

    def _node(self, alias):
        key = _alias(alias)
        with self._lock:
            if key == "local":
                return dict(self._local)
            if key not in self._nodes:
                raise FleetError(404, "实例不存在")
            return dict(self._nodes[key])

    def _public(self, node):
        return self._redact({k: node.get(k) for k in ("id", "name", "base_url", "instance_id", "version", "local")}
                            | {"alias": node["id"], "token_configured": not node.get("local", False)}
                            | connection_fields(node.get("base_url"), node.get("local", False)))

    def list_nodes(self):
        with self._lock:
            return [self._public(self._local)] + [self._public(n) for n in self._nodes.values()]

    def register(self, config):
        if not isinstance(config, dict) or set(config) - {"id", "alias", "name", "base_url", "token", "host", "port", "scheme"}:
            raise ValueError("实例配置只允许 id、alias、name、base_url、host、port、scheme、token")
        direct = bool(set(config) & {"host", "port", "scheme"})
        if direct and "base_url" in config:
            raise ValueError("base_url 不能与 host、port、scheme 混用")
        if direct and not {"host", "port"}.issubset(config):
            raise ValueError("直连节点必须同时提供 host 和 port；scheme 不能单独修改")
        key = _alias(config.get("id", config.get("alias")))
        if key == "local" or (config.get("id") and config.get("alias") and config["id"] != config["alias"]):
            raise ValueError("local 为本机保留 ID，或 id/alias 不一致")
        with self._lock:
            prior = dict(self._nodes[key]) if key in self._nodes else None
            if self._closed:
                raise RuntimeError("实例管理器已关闭")
            if prior is None and len(self._nodes) >= MAX_NODES:
                raise ValueError(f"最多注册 {MAX_NODES} 个远程实例")
        url = (direct_base_url(config["host"], config["port"], config.get("scheme", "http")) if direct
               else normalize_base_url(config.get("base_url", (prior or {}).get("base_url"))))
        token = self._token(config.get("token", (prior or {}).get("token")))
        name = config.get("name", (prior or {}).get("name", key))
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(c) < 32 for c in name):
            raise ValueError("实例名称应为 1–80 个字符")
        node = {"id": key, "name": name.strip(), "base_url": url, "token": token, "local": False}
        status = self._remote(node, "GET", "api/status")
        identity = self._identity(status)
        cursor = self._cursor(identity)
        with self._lock:
            if self._closed or self._nodes.get(key) != prior:
                raise FleetError(409, "注册信息在验证期间改变，请重新提交")
            if prior and prior["instance_id"] != identity:
                raise FleetError(409, "节点 UUID 与已固定身份不一致；拒绝替换原实例")
            if identity == self.local_id or any(n["instance_id"] == identity and k != key for k, n in self._nodes.items()):
                raise FleetError(409, "实例 UUID 已在本机或其他节点注册；克隆数据目录不是独立采集实例")
            node.update(instance_id=identity, version=status["version"])
            self._nodes[key] = node
            self._cache[key] = {"connection": "online", "status": status, "last_error": None, "last_seen_at": _iso(self.clock()),
                                "sync": {"state": "idle", "cursor": cursor, "error": None}}
            self._requested.add(key)
            self._generation[key] = self._generation.get(key, 0) + 1
            self._save()
            return self._public(node)

    def remove(self, alias):
        key = _alias(alias)
        if key == "local":
            raise ValueError("不能移除本机实例")
        with self._lock:
            node = self._nodes.pop(key, None)
            if node is None:
                raise FleetError(404, "实例不存在")
            self._requested.discard(key)
            self._generation.pop(key, None)
            self._cache.pop(key, None)
            self._save()
            return self._public(node)

    @staticmethod
    def _route(method, path, query):
        if not isinstance(path, str):
            raise ValueError("代理路径无效")
        path = path.lstrip("/")
        allowed = ((method == "GET" and path in {"api/status", "api/jobs", "api/requests", "api/events", "api/posts"})
                   or (method == "POST" and path in {"api/jobs", "api/control"})
                   or (method == "PATCH" and path == "api/jobs/current")
                   or (method == "DELETE" and (path == "api/jobs/current" or re.fullmatch(r"api/jobs/current/stocks/[0-9]{6}", path))))
        if not allowed:
            raise ValueError("不允许代理此节点接口")
        permitted = ({"limit", "paged", "before_id", "snapshot_id"} if path in {"api/requests", "api/events"}
                     else {"limit", "offset"} if path == "api/posts" else set())
        if query and (method != "GET" or not isinstance(query, dict) or set(query) - permitted):
            raise ValueError("代理查询参数无效")
        return path

    def _check_status(self, node):
        status = self.engine.status() if node["id"] == "local" else self._remote(node, "GET", "api/status")
        if self._identity(status) != node["instance_id"]:
            raise FleetError(409, "节点 UUID 已改变；拒绝控制或同步，请检查重建/克隆目录")
        with self._lock:
            if node["id"] == "local" or self._nodes.get(node["id"]) == node:
                cache = self._cache.setdefault(node["id"], {})
                cache.update(connection="online", status=status, last_error=None, last_seen_at=_iso(self.clock()))
        return status

    def proxy(self, alias, method, path, body=None, query=None):
        method = str(method).upper()
        path = self._route(method, path, query)
        node = self._node(alias)
        try:
            if method != "GET":
                self._check_status(node)  # Pin verification immediately before the only mutation attempt.
            if node["id"] != "local":
                payload = self._remote(node, method, path, body, query)
            elif method == "GET":
                payload = self._local_get(path, query or {})
            else:
                if method == "POST" and path == "api/jobs":
                    self.engine.create_job(body)
                elif method == "POST":
                    action = body.get("action") if isinstance(body, dict) else None
                    if action not in {"start", "pause", "retry"}:
                        raise ValueError("action 必须是 start、pause 或 retry")
                    getattr(self.engine, action)()
                elif method == "PATCH":
                    self.engine.update_job(body)
                elif path == "api/jobs/current":
                    self.engine.delete_job()
                else:
                    self.engine.remove_stock(path.rsplit("/", 1)[1])
                payload = self.engine.status()
            if path == "api/status" or method != "GET":
                if self._identity(payload) != node["instance_id"]:
                    raise FleetError(409, "节点响应 UUID 与注册身份不一致", method != "GET")
                with self._lock:
                    cache = self._cache.setdefault(node["id"], {})
                    cache.update(connection="online", status=payload, last_error=None, last_seen_at=_iso(self.clock()))
            return self._redact(payload)
        except FleetError as exc:
            self._offline(node, exc)
            raise

    def download_posts(self, alias, format="jsonl"):
        """Download this registered node's own posts; never use a merged fallback."""
        if format not in {"csv", "jsonl"}:
            raise ValueError("format 必须是 csv/jsonl")
        node = self._node(alias)
        if node["id"] == "local":
            raise ValueError("本机导出应使用已有本机下载接口")
        download = None
        try:
            self._check_status(node)
            url = node["base_url"] + "/api/download/posts?" + urlencode({"scope": "local", "format": format})
            download = self.client.download(url, node["token"], format)
            if download.remote_instance_id is not None and download.remote_instance_id != node["instance_id"]:
                raise FleetError(409, "节点导出 UUID 与登记身份不一致；已拒绝下载")
            self._check_status(node)  # Older nodes lack identity headers; check the pinned endpoint again.
            with self._lock:
                if self._closed or self._nodes.get(node["id"]) != node:
                    raise FleetError(409, "节点登记在下载期间改变；请重新选择后下载")
            if self._redact(node["instance_id"], (node["token"],)) != node["instance_id"]:
                raise FleetError(409, "节点身份标识与私密鉴权信息冲突；已拒绝下载")
            safe_alias = self._redact(node["id"], (node["token"],))
            download.node_id = safe_alias if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", safe_alias) else "node"
            download.instance_id = node["instance_id"]
            result, download = download, None
            return result
        except FleetError as exc:
            error = self._redact(exc.error, (node["token"],))[:2000]
            if exc.status_code in {502, 504}:
                error += "；" + self._redact(self._connection_hint(node), (node["token"],))
            safe = FleetError(exc.status_code, error)
            self._offline(node, safe)
            raise safe from None
        except Exception:
            safe = FleetError(502, self._redact("选中节点下载失败；" + self._connection_hint(node), (node["token"],)))
            self._offline(node, safe)
            raise safe from None
        finally:
            if download is not None:
                download.close()

    def _local_get(self, path, query):
        values = {k: v if isinstance(v, list) else [str(v)] for k, v in query.items()}
        if path == "api/status":
            return self.engine.status()
        if path == "api/jobs":
            return self.engine.jobs()
        if path in {"api/requests", "api/events"} and "paged" in values:
            return activity_page(self.engine, path.rsplit("/", 1)[1], **activity_query(values))
        try:
            limit = int(values.get("limit", ["50"])[0])
            offset = int(values.get("offset", ["0"])[0])
        except (TypeError, ValueError) as exc:
            raise ValueError("limit/offset 必须为整数") from exc
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("limit 应在 1–1000 之间，offset 不能小于 0")
        if path == "api/posts":
            return self.engine.raw_posts(limit, offset)
        return getattr(self.engine, path.rsplit("/", 1)[1])(limit)

    def _offline(self, node, error):
        with self._lock:
            if node["id"] == "local" or self._nodes.get(node["id"]) == node:
                cache = self._cache.setdefault(node["id"], {})
                cache.update(connection="offline", last_error=self._redact(error.error))

    def _cursor(self, instance_id):
        # Cursor reads are short; never hold the registry lock while waiting on a merge write.
        return self.merge_store.cursor(instance_id)

    def overview(self, refresh=False):
        if refresh:
            with self._lock:
                for key in ["local", *self._nodes]:
                    self._poll_due[key] = 0
            self.tick()
        local_status = self.engine.status()
        with self._lock:
            self._cache["local"].update(status=local_status, connection="online", last_seen_at=_iso(self.clock()))
            result = []
            for node in [self._local, *self._nodes.values()]:
                cache = self._cache.get(node["id"], {})
                result.append(self._public(node) | {"connection": cache.get("connection", "unknown"), "status": cache.get("status"),
                                                   "last_error": cache.get("last_error"), "last_seen_at": cache.get("last_seen_at"),
                                                   "sync": cache.get("sync", {"state": "idle", "error": None})})
            return self._redact({"nodes": result, "merge": self._merge_summary, "auto_sync": self.auto_sync})

    def request_sync(self, alias="all"):
        with self._lock:
            if self._closed:
                raise RuntimeError("实例管理器已关闭")
            aliases = ["local", *self._nodes] if alias == "all" else [self._node(alias)["id"]]
            self._requested.update(aliases)
            for key in aliases:
                self._generation[key] = self._generation.get(key, 0) + 1
            self._save()
        self.tick()
        return {"scheduled": aliases}

    def tick(self):
        with self._lock:
            if self._closed:
                return {"scheduled": []}
            scheduled, now = [], self.clock()
            for node in [self._local, *self._nodes.values()]:
                key = node["id"]
                future = self._futures.get(key)
                if future and not future.done():
                    continue
                sync = key in self._requested or (self.auto_sync and now >= self._sync_due.get(key, 0))
                poll = now >= self._poll_due.get(key, 0)
                if not (sync or poll):
                    continue
                self._poll_due[key] = now + 10
                if sync:
                    self._sync_due[key] = now + 60
                    cache = self._cache.setdefault(key, {})
                    cache["sync"] = {**cache.get("sync", {}), "state": "syncing", "error": None}
                self._futures[key] = self._pool.submit(self._work, dict(node), sync, self._generation.get(key, 0))
                scheduled.append(key)
            return {"scheduled": scheduled}

    def _work(self, node, sync, generation=0):
        from federation import export_page, export_raw
        deadline = time.monotonic() + 30
        checked = False
        try:
            with self._lock:
                if self._closed or node["id"] != "local" and self._nodes.get(node["id"]) != node:
                    return
            self._check_status(node)
            checked = True
            if not sync:
                return
            cursor = self._cursor(node["instance_id"])
            if node["id"] == "local":
                page = export_page(self.engine, after=cursor, limit=20)
            else:
                page = self._remote(node, "GET", "api/federation/export", query={"after": cursor, "limit": 20})
            if page.get("instance_id") != node["instance_id"]:
                raise FleetError(409, "导出数据实例 UUID 与固定身份不一致")
            def raw_loader(request_id):
                if time.monotonic() > deadline:
                    raise FleetError(504, "本轮同步超过时间窗口；保留游标，下一轮继续")
                with self._lock:
                    if self._closed or (node["id"] != "local" and self._nodes.get(node["id"]) != node):
                        raise RuntimeError("节点连接已移除或更新；同步暂停，游标保留")
                return (export_raw(self.engine, request_id) if node["id"] == "local"
                        else self._remote(node, "GET", "api/federation/raw", query={"request_id": request_id}))
            with self._merge_lock:
                summary = self.merge_store.merge_page(node["instance_id"], page, raw_loader)
                merged = self.merge_store.status()
                advanced = self._cursor(node["instance_id"])
            with self._lock:
                self._merge_summary = merged
                if node["id"] == "local" or self._nodes.get(node["id"]) == node:
                    self._cache.setdefault(node["id"], {})["sync"] = {"state": "ready", "error": None, "cursor": advanced,
                                                                    "last_synced_at": _iso(self.clock()), "has_more": bool(page.get("has_more")), "summary": summary}
                    if page.get("has_more"):
                        self._requested.add(node["id"])
                    elif self._generation.get(node["id"], 0) <= generation:
                        self._requested.discard(node["id"])
                    self._save()
        except Exception as exc:
            error = exc if isinstance(exc, FleetError) else FleetError(502, self._redact(f"同步失败: {type(exc).__name__}: {exc}")[:2000])
            if not checked or isinstance(exc, FleetError) and exc.status_code in {502, 504}:
                self._offline(node, error)
            with self._lock:
                if node["id"] == "local" or self._nodes.get(node["id"]) == node:
                    if sync:
                        cache = self._cache.setdefault(node["id"], {})
                        cache["sync"] = {**cache.get("sync", {}), "state": "error", "error": error.error}
                        # Failed transfers retry only at the ordinary background
                        # interval; never spin an explicitly queued request.
                        if self._generation.get(node["id"], 0) <= generation:
                            self._requested.discard(node["id"])
                        self._save()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._save()
        self._pool.shutdown(wait=True, cancel_futures=True)
        close = getattr(self.merge_store, "close", None)
        if close:
            close()
