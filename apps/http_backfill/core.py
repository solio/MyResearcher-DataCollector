"""Isolated, fail-closed HTTP experiment. No production database writes.

The production parser defines source fields; the raw static challenge detector
also recognises the observed overlay shape from the 2026-09-30 HTTP research.
Neither parser renders JavaScript or establishes a server-side blacklist.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import errno
import fcntl
import hashlib
import http.client
from html.parser import HTMLParser
import inspect
import json
import math
import os
from pathlib import Path
import random
import re
import sqlite3
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urljoin, urlparse

from myresearcher_collector.sources.eastmoney_guba import parser as guba
from myresearcher_collector.sources.eastmoney_guba.content_rules import (
    DETAIL_BODY_CONTENT_SOURCE, LIST_TITLE_CONTENT_SOURCE,
    detail_body_metadata, detail_enrichment_trigger, list_title_metadata,
)
from lifecycle import LifecycleMixin
from window_seek import WindowSeekMixin
from stock_runtime import StockRuntimeMixin
from rate_audit import tail_audit
from compatible_store import CompatibleDataStore, _utc
from unified_store import LAYOUT, guard_runtime_path, initialize_schema, simple_store_adapter

VERSION = "http-backfill.v5"
MAX_BODY = 16 * 1024 * 1024
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")
REQUEST_PROFILE = "chrome-referer.v1"
GOOGLE_REFERER = "https://www.google.com/"
BAIDU_REFERER = "https://www.baidu.com/"
REDIRECTS = {301, 302, 303, 307, 308}
SAFE_HEADERS = {"content-type", "content-length", "content-encoding", "location", "retry-after", "date", "server"}
NETWORK_RETRY_KINDS = {"network_timeout", "tls_error", "network_connect", "network_io"}
ORDINARY_RECHECKS = {"list_delay_recheck", "manual_resume", "process_restart", "config_updated", "periodic_recheck"}
MAX_RECOVERY_REQUESTS, MAX_RECOVERY_PASSES = 64, 6


class RecoveryLimitError(ValueError):
    pass


@dataclass
class Response:
    status: int | None
    body: bytes
    headers: dict
    url: str | None = None
    error: str | None = None
    network_attempted: bool = True
    proxy: dict | None = None
    proxy_error: str | None = None
    transient_error: str | None = None


def _transient_error_kind(exc):
    """Classify transport exceptions without treating local/config failures as a block."""
    seen = set()
    while isinstance(exc, BaseException) and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)):
            return "network_timeout"
        if isinstance(exc, ssl.SSLError):
            return "tls_error"
        if isinstance(exc, socket.gaierror):
            return "network_connect"
        if isinstance(exc, (http.client.RemoteDisconnected, http.client.IncompleteRead, EOFError)):
            return "network_io"
        if isinstance(exc, ConnectionError):
            return "network_connect"
        if isinstance(exc, OSError) and exc.errno in {
                errno.ECONNABORTED, errno.ECONNRESET, errno.ECONNREFUSED,
                errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ENETDOWN, errno.EPIPE}:
            return "network_connect"
        exc = exc.reason if isinstance(exc, urllib.error.URLError) else exc.__cause__
    return None


def _curl_transient_error(code):
    if code in {5, 6, 7}:
        return "network_connect"
    if code == 28:
        return "network_timeout"
    if code in {35, 51, 60}:
        return "tls_error"
    if code in {16, 18, 52, 55, 56, 92}:
        return "network_io"
    return None


def _legacy_transient_error(error):
    """Narrow replay classification of old transport diagnostics, never source content."""
    text = error or ""
    match = re.search(r"curl(?: exit |:\s*\()(\d+)", text)
    if match:
        return _curl_transient_error(int(match[1]))
    if re.search(r"\b(?:TimeoutError|TimeoutExpired|timed out)\b", text, re.I):
        return "network_timeout"
    if re.search(r"\b(?:SSLError|SSLCertVerificationError|CERTIFICATE_VERIFY_FAILED|SSL_ERROR_SYSCALL)\b|\[SSL:", text):
        return "tls_error"
    return None


def _read_http_body(response):
    # Shared read1-based bound keeps bytes already received before a timeout,
    # including a short challenge shell that must not disappear into a buffer.
    from proxy import _bounded_read
    return _bounded_read(response, MAX_BODY + 1, time.monotonic() + 25)


def _dump(value):
    return json.dumps(value, ensure_ascii=False, default=lambda x: x.isoformat(), separators=(",", ":"))


def _iso(epoch):
    try:
        return datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _allowed(url):
    try:
        p = urlparse(url)
        return (p.scheme == "https" and p.hostname is not None
                and (p.hostname == "eastmoney.com" or p.hostname.endswith(".eastmoney.com"))
                and not p.username and not p.password and p.port in (None, 443))
    except ValueError:
        return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def list_url(stock, page=1):
    return f"https://guba.eastmoney.com/list,{stock},f" + (f"_{page}" if page > 1 else "") + ".html"


def request_headers(referer=None):
    headers = {"User-Agent": UA, "Accept": "text/html"}
    if referer is not None:
        if (not isinstance(referer, str) or any(ord(c) < 32 or ord(c) > 126 for c in referer)
                or (referer not in {GOOGLE_REFERER, BAIDU_REFERER} and not _allowed(referer))):
            raise ValueError("Referer 必须为公开来源 URL 或允许的搜索来源站点")
        headers["Referer"] = referer
    return headers


def fetch(url, client, referer=None):
    """Exactly one anonymous GET, no redirects or retries in either client."""
    if not _allowed(url):
        raise ValueError("请求地址必须属于公开 HTTPS Eastmoney 来源")
    headers = request_headers(referer)
    if client == "urllib":
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        request = urllib.request.Request(url, headers=headers)
        observed_status, observed_headers = None, {}
        try:
            try:
                response = opener.open(request, timeout=25)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                observed_status = response.status
                observed_headers = {k.lower(): v for k, v in response.headers.items() if k.lower() in SAFE_HEADERS}
                body = _read_http_body(response)
                return Response(observed_status, body, observed_headers, url,
                                "响应超出 16 MiB，内容可能不完整" if len(body) > MAX_BODY else None)
        except Exception as exc:
            partial = getattr(exc, "partial", b"")
            partial = partial if isinstance(partial, bytes) else b""
            return Response(observed_status, partial[:MAX_BODY + 1], observed_headers, url, f"{type(exc).__name__}: {exc}",
                            transient_error=_transient_error_kind(exc))
    if client != "curl":
        raise ValueError("client 只能是 curl 或 urllib")
    with tempfile.TemporaryDirectory(prefix="http-backfill-") as tmp:
        hp, bp = Path(tmp) / "headers", Path(tmp) / "body"
        command = ["curl", "-q", "--silent", "--show-error", "--connect-timeout", "10", "--max-time", "25",
                   "--max-filesize", str(MAX_BODY), "--noproxy", "*", "--proto", "=https",
                   "--dump-header", str(hp), "--output", str(bp), "--write-out", "%{http_code}",
                   "--user-agent", headers["User-Agent"], "--header", "Accept: text/html"]
        if referer is not None:
            command.extend(["--referer", referer])
        command.append(url)
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=30)
            headers = {}
            if hp.exists():
                for line in hp.read_text(errors="replace").splitlines():
                    if line.startswith("HTTP/"):
                        headers = {}
                    elif ":" in line:
                        k, v = line.split(":", 1)
                        if k.lower() in SAFE_HEADERS:
                            headers[k.lower()] = v.strip()
            body = bp.read_bytes() if bp.exists() else b""
            status = int(proc.stdout.strip()) if proc.stdout.strip().isdigit() else None
            return Response(status or None, body, headers, url,
                            (proc.stderr.strip() or f"curl exit {proc.returncode}") if proc.returncode else None,
                            transient_error=_curl_transient_error(proc.returncode))
        except Exception as exc:
            # A killed curl may have received an auth/block status before its
            # final write-out. Retain that fact rather than retrying as a timeout.
            observed_status, observed_headers = None, {}
            if hp.exists():
                for line in hp.read_text(errors="replace").splitlines():
                    if line.startswith("HTTP/"):
                        parts = line.split()
                        observed_status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
                        observed_headers = {}
                    elif ":" in line:
                        key, value = line.split(":", 1)
                        if key.lower() in SAFE_HEADERS:
                            observed_headers[key.lower()] = value.strip()
            return Response(observed_status, bp.read_bytes() if bp.exists() else b"", observed_headers, url, f"{type(exc).__name__}: {exc}",
                            network_attempted=not isinstance(exc, (FileNotFoundError, PermissionError)),
                            transient_error=_transient_error_kind(exc))


class _StaticHTML(HTMLParser):
    """Only source markup, inline visibility and text; no CSS/JS execution."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.title, self.text, self.overlay_text = [], [], [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        hidden = (any(x[1] for x in self.stack) or tag in {"script", "style", "noscript"}
                  or "hidden" in a or bool(re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", a.get("style") or "", re.I)))
        marker = (a.get("id") or "") + " " + (a.get("class") or "")
        overlay = not hidden and bool(re.search(r"captcha|emcaptcha|geetest|nc-container|nc_wrapper|verify(?:[_-]?box|[_-]?container)?", marker, re.I)
                                      or re.search(r"position\s*:\s*fixed", a.get("style") or "", re.I))
        if tag not in {"meta", "link", "img", "input", "br", "hr", "source", "area", "wbr"}:
            self.stack.append((tag, hidden, overlay))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if any(x[0] == "title" for x in self.stack):
            self.title.append(data)
        if not any(x[1] for x in self.stack):
            self.text.append(data)
            if any(x[2] for x in self.stack):
                self.overlay_text.append(data)


def challenge_evidence(html):
    view = _StaticHTML()
    view.feed(html)
    title = " ".join("".join(view.title).split())
    assets = [x for x in ("em_capt.js", "fd_guba_validate", "validate.js", "emcaptcha") if x in html.lower()]
    phrases = ("拖动下方滑块完成拼图", "拖动滑块", "请完成验证", "请先完成验证", "请完成安全验证", "请进行人机验证", "访问过于频繁")
    instructions = [x for x in phrases if x in " ".join(view.overlay_text)]
    shell = title in {"身份核实", "访问验证", "安全验证", "人机验证"}
    return {"challenge": shell or bool(instructions), "title": title, "assets": assets,
            "overlay_instructions": instructions, "dynamic_js": "unobserved",
            "reason": "身份核实页面" if shell else "静态验证遮罩" if instructions else None}


def _item_load(value):
    d = json.loads(value)
    for key in ("published_at", "last_updated_at", "display_time"):
        d[key] = datetime.fromisoformat(d[key]) if d[key] is not None else None
    return guba.GubaListItem(**d)


def _validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("任务配置必须是 JSON 对象")
    unknown = set(config) - {"stocks", "from_date", "to_date", "interval_seconds", "client"}
    if unknown:
        raise ValueError("未知配置字段: " + ", ".join(sorted(unknown)))
    stocks = config.get("stocks")
    if (not isinstance(stocks, list) or not stocks
            or any(not isinstance(s, str) or not re.fullmatch(r"[0-9]{6}", s) for s in stocks)):
        raise ValueError("stocks 必须是非空六位股票代码数组")
    if len(stocks) != len(set(stocks)):
        raise ValueError("股票代码不能重复")
    for key in ("from_date", "to_date"):
        value = config.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError(f"{key} 必须是 YYYY-MM-DD 日期")
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{key} 日期无效") from exc
    if config["from_date"] > config["to_date"]:
        raise ValueError("开始日期不能晚于结束日期")
    interval = config.get("interval_seconds", 60)
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval < 0:
        raise ValueError("请求间隔必须是非负的有限秒数")
    client = config.get("client", "curl")
    if client not in {"curl", "urllib"}:
        raise ValueError("client 只能是 curl 或 urllib")
    return {"stocks": stocks, "from_date": config["from_date"], "to_date": config["to_date"],
            "interval_seconds": interval, "client": client}


class Engine(StockRuntimeMixin, LifecycleMixin, WindowSeekMixin):
    def __init__(self, data_dir, transport=None, clock=None):
        self._stock_runtime_ready = False
        self._stock_local = threading.local()
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        db_path = guard_runtime_path(self.data_dir)
        self.raw_dir = self.data_dir / "raw"
        self.raw_dir.mkdir(exist_ok=True, mode=0o700)
        self._lock_file = open(self.data_dir / "worker.lock", "a+b")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._lock_file.close()
            raise RuntimeError("该实验目录已有运行中的 worker，不能启动第二个进程") from exc
        self._mutex = threading.RLock()
        self._inflight = False
        self._inflight_stock = None
        self._inflight_probe = False
        self._inflight_task = None
        self._closed = False
        self.clock = clock or time.time
        from proxy import ProxyManager
        self.proxy = ProxyManager(self.data_dir, clock=self.clock)
        self._managed_transport = transport is None
        self.transport = transport or self.proxy.fetch
        try:
            inspect.signature(self.transport).bind("url", "curl", referer=None)
        except (TypeError, ValueError):
            self._transport_with_referer = False
        else:
            self._transport_with_referer = True
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        with self.db:
            initialize_schema(self.db)
        has_stock_runtime = self._get_global("stock_runtime_schema") is not None
        self._init_lifecycle_schema()
        with self.db:
            if self._get("instance_id") is None:
                self._set("instance_id", str(uuid.uuid4()))
            if self._get("state") is None:
                self._set("state", "paused")
                self._set("reason", "尚未创建任务")
                self._set("next_due", 0)
            interrupted = self.db.execute("SELECT * FROM requests WHERE outcome='reserved'").fetchall()
            self._stock_interrupted = [dict(row) for row in interrupted]
            for row in interrupted:
                raw = self.raw_dir / f"{row['id']:09d}.body"
                partial = raw.with_suffix(".tmp")
                evidence_file = raw if raw.exists() else partial if partial.exists() else None
                evidence_bytes = evidence_file.read_bytes() if evidence_file else None
                self.db.execute("UPDATE requests SET outcome='interrupted_unknown',finished=?,error=?,raw_ref=?,response_bytes=?,sha256=? WHERE id=?",
                                (self.clock(), "进程中断，网络请求是否到达来源未知；留存字节不代表完整响应",
                                 str(evidence_file.relative_to(self.data_dir)) if evidence_file else None,
                                 len(evidence_bytes) if evidence_bytes is not None else None,
                                 hashlib.sha256(evidence_bytes).hexdigest() if evidence_bytes is not None else None,
                                 row["id"]))
                self.db.execute("UPDATE tasks SET status='pending' WHERE id=?", (row["task"],))
            if interrupted:
                self._set("state", "error")
                self._set("active_halt", "interrupted_unknown")
                self._set("reason", "前次请求执行中进程中断，需要人工单次探测")
                self._event("interrupted_unknown", self._get("reason"), {"requests": [r["id"] for r in interrupted]})
                self._end_segment("interrupted_process", min(r["started"] for r in interrupted))
            elif self._get("state") == "running":
                self._set("state", "paused")
                self._set("reason", "进程重启后安全暂停，等待继续")
                self._event("restart_paused", self._get("reason"))
                last_observed = self.db.execute("SELECT MAX(COALESCE(finished,started)) FROM requests WHERE job=?", (self._get("job_id"),)).fetchone()[0]
                self._end_segment("restart_paused", last_observed)
            self._set("probe", False)
            self._suspend_proxy_recovery()
            if not interrupted and not has_stock_runtime:
                self._migrate_network_halt()
            self._migrate_content_policy()
            if not has_stock_runtime:
                self._migrate_frontiers()
                self._migrate_window_seek()
                if self._get("job_id") and self._get("state") != "completed":
                    self._mark_recovery_needed("process_restart")
        self.compatible_store = CompatibleDataStore(self.data_dir, self._get("instance_id"))
        self._sync_storage()
        from task_proxy import TaskProxyRoutes
        self.task_proxies = TaskProxyRoutes(self.data_dir, self.proxy, clock=self.clock)
        self._init_stock_runtime()

    def _get_global(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _set_global(self, key, value):
        self.db.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, _dump(value)))

    def _get(self, key, default=None):
        if self._stock_runtime_ready and self._stock_scope() and self._stock_key(key):
            return self._stock_get(key, default)
        return self._get_global(key, default)

    def _set(self, key, value):
        if self._stock_runtime_ready and self._stock_scope() and self._stock_key(key):
            return self._stock_set(key, value)
        return self._set_global(key, value)

    def _scope_stocks(self, config=None):
        config = config or self._config()
        scope = self._stock_scope() if self._stock_runtime_ready else None
        return [scope[1]] if scope else config["stocks"] if config else []

    def _proxy_for_stock(self):
        scope = self._stock_scope() if self._stock_runtime_ready else None
        return self.task_proxies.manager(str(scope[0]), scope[1]) if scope else self.proxy

    def _sanitize(self, value):
        routes = getattr(self, "task_proxies", None)
        return routes.sanitize(value) if routes else self.proxy.sanitize(value)

    def configure_stock_proxy(self, stock, settings):
        with self._mutex, self.db:
            settings = dict(settings)
            requested_job = settings.pop("job_id", self._get("job_id"))
            job, _ = self._resolve_stock_scope(stock, requested_job)
            with self._stock_context(stock, job=job):
                if self._inflight_stock == (job, stock) or self._get("state") == "running" or self._get("probe"):
                    raise RuntimeError("请先暂停这只股票并等待它的请求结束，再修改出口")
                result = self.task_proxies.configure(str(job), stock, settings)
                self._suspend_proxy_recovery()
                self._event("stock_proxy_configured", "更新本股票出口；阻断、冷却和断点保留", {"mode": result["settings"]["mode"]})
        return self.status()

    def mihomo_config(self):
        with self._mutex:
            job = self._get("job_id")
            scopes = [(str(runtime["job"]), runtime["stock"]) for runtime in self._stock_runtimes(include_detached=True)]
            if not scopes:
                raise ValueError("请先创建任务并配置股票出口")
            return {"yaml": self.task_proxies.mihomo_fragment(str(job), scopes=scopes),
                    "script": self.task_proxies.mihomo_script(str(job), scopes=scopes),
                    "filename": "collector-mihomo-listeners.yaml", "script_filename": "collector-mihomo-extension.js",
                    "instance_id": self._get("instance_id"), "version": VERSION, "contains_private_credentials": True}

    def _event(self, kind, message, evidence=None):
        scope = self._stock_scope() if self._stock_runtime_ready else None
        if scope:
            evidence = {**(evidence or {}), "stock": scope[1]}
        self.db.execute("INSERT INTO events(job,created,kind,message,evidence) VALUES(?,?,?,?,?)",
                        (scope[0] if scope else self._get("job_id"), self.clock(), kind, message, _dump(evidence) if evidence is not None else None))

    def _migrate_network_halt(self):
        halt = self._get("active_halt")
        if halt not in {"transport_error", "proxy_error"}:
            return
        target = self._halt_target()
        if (not target or target["status"] not in {"pending", "inflight"}
                or not self._task_in_active_scope(target) or self._job_archived(target["job"])):
            return
        row = self.db.execute("SELECT * FROM requests WHERE task=? ORDER BY id DESC LIMIT 1", (target["id"],)).fetchone()
        if (not row or row["probe"] or row["finished"] is None
                or row["outcome"] != halt or row["http_status"] in {401, 403, 407, 429}):
            return
        analysis = json.loads(row["analysis"]) if row["analysis"] else {}
        if halt == "proxy_error" and analysis.get("proxy_error") != "proxy_connect":
            return
        kind = _legacy_transient_error(row["error"])
        if not kind:
            return
        if row["raw_ref"]:
            try:
                raw = (self.data_dir / row["raw_ref"]).resolve()
                raw.relative_to(self.raw_dir)
                body = raw.read_bytes()
                if hashlib.sha256(body).hexdigest() != row["sha256"] or challenge_evidence(body.decode("utf-8-sig", errors="replace"))["challenge"]:
                    return
            except (OSError, ValueError):
                return
        self._clear_active_halt()
        self._set("network_retry", None)
        self._set("state", "paused")
        self._set("reason", "旧版网络故障已改为可重试；历史证据和冷却保留，等待继续")
        self._event("network_retry_cleared_legacy", self._get("reason"), {"request_id": row["id"], "kind": kind})

    def _begin_segment(self, probe=False):
        scope = self._stock_scope() if self._stock_runtime_ready else None
        job = scope[0] if scope else self._get("job_id")
        count = self.db.execute("SELECT COUNT(*) FROM requests WHERE job=?", (job,)).fetchone()[0]
        sid = self.db.execute("INSERT INTO run_segments(job,started,probe,attempts_at_start) VALUES(?,?,?,?)",
                              (job, self.clock(), int(probe), count)).lastrowid
        self._set("segment_id", sid)

    def _end_segment(self, reason, at=None):
        self.db.execute("UPDATE run_segments SET ended=?,stop_reason=? WHERE id=? AND ended IS NULL",
                        (self.clock() if at is None else at, reason, self._get("segment_id")))

    def _config(self):
        row = self.db.execute("SELECT config FROM jobs WHERE id=?", (self._get("job_id"),)).fetchone()
        return json.loads(row[0]) if row else None

    def _store_list_post(self, item, source_row, request_id):
        """Keep every eligible list item; the shared rule controls enrichment."""
        data = asdict(item)
        data["source_metadata"] = list_title_metadata(item.source_metadata, item.title)
        status = "pending" if detail_enrichment_trigger(item.title) else "list_only"
        request = self.db.execute("SELECT finished,raw_ref,final_url FROM requests WHERE id=?", (request_id,)).fetchone()
        if request is None or request["finished"] is None:
            raise ValueError("列表缺少已保存的采集时间，不能写入原 posts")
        acquired = self.compatible_store._source_item(item, datetime.fromtimestamp(request["finished"], timezone.utc),
                                                      item.title or "", data["source_metadata"],
                                                      {"list": request["raw_ref"]}, request["final_url"])
        store = simple_store_adapter(self.db, self.data_dir / "collector.db")
        store.upsert_source_item(acquired, stock_code=item.requested_bar_code, content=None,
                                 updated_at=_utc(request["finished"]))
        self.db.execute("INSERT INTO http_post_state(post_id,item,source_row,status,list_request,content_source) VALUES(?,?,?,?,?,?)",
                        (item.source_item_id, _dump(data), source_row, status, request_id, LIST_TITLE_CONTENT_SOURCE))
        return status

    def _migrate_content_policy(self):
        """Repair old queues without rewriting source requests or acquired bodies."""
        if self._get("content_policy_version") == 3:
            return
        halt = self._halt_target() if self._get("active_halt") else None
        retained = halt["id"] if halt else None
        changed, skipped = 0, 0
        for post in self.db.execute("SELECT post_id,item,status FROM http_post_state"):
            data = json.loads(post["item"])
            title = data.get("title")
            trigger = detail_enrichment_trigger(title)
            status = post["status"]
            if status == "complete":
                metadata = detail_body_metadata(data.get("source_metadata") or {}, title=title, trigger=trigger)
                source = DETAIL_BODY_CONTENT_SOURCE
            else:
                metadata = list_title_metadata(data.get("source_metadata") or {}, title)
                source = LIST_TITLE_CONTENT_SOURCE
                if status == "pending" and trigger is None:
                    status = "list_only"
                    skipped += self.db.execute("UPDATE tasks SET status='skipped' WHERE kind='detail' AND post_id=? AND status IN ('pending','inflight') AND id!=COALESCE(?,-1)",
                                               (post["post_id"], retained)).rowcount
                    if halt and halt["kind"] == "detail" and halt["post_id"] == post["post_id"]:
                        self._set("halted_task_id", retained)
                        self._set("halt_task_id", retained)
                        self._set("halted_probe_only", True)
            data["source_metadata"] = metadata
            self.db.execute("UPDATE http_post_state SET item=?,status=?,content_source=? WHERE post_id=?",
                            (_dump(data), status, source, post["post_id"]))
            changed += 1
        self._set("content_policy_version", 3)
        self._set("content_policy", {"version": 3, "trigger": "shared detail_enrichment_trigger", "default": "strip(title) length >=40", "short_title": "retain list_title; never claim detail_body"})
        self._event("content_policy_migrated", "统一原采集详情触发规则：标题去首尾空白后 >=40 才补详情；保留短标题列表与已有正文", {"version": 3, "posts_annotated": changed, "short_detail_tasks_skipped": skipped, "retained_probe_task": retained})

    def _sync_storage(self, request_id=None):
        """Verify/journal the shared original rows after acquisition commits."""
        with self._mutex:
            previous = self._get("storage_halt")
            if previous:
                request_id = None  # Repair every committed row before clearing a local halt.
            try:
                summary = self.compatible_store.sync(self, request_id)
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                with self.db:
                    self._suspend_proxy_recovery()
                    halt = previous or {"previous_state": self._get("state"), "previous_reason": self._get("reason")}
                    halt.update({"kind": "local_storage_error", "error": message, "request_id": request_id, "at": _iso(self.clock())})
                    self._set("storage_halt", halt)
                    self._set("data_storage", {"schema_version": "legacy_posts", "storage_layout": LAYOUT, "single_runtime_database": True, "status": "error", "db_path": str(self.compatible_store.db_path),
                                               "last_error": message, "research_only": True, "model_database_eligible": False})
                    self._set("state", "blocked" if self._get("active_halt") and self._get("state") == "blocked" else "error")
                    self._set("reason", "本地兼容数据投影失败；原始响应/主台账已保留，请修复存储后本地重投影: " + message)
                    self._set("probe", False)
                    self._end_segment("local_storage_error")
                    self._event("local_storage_error", self._get("reason"), halt)
                    if self._stock_runtime_ready:
                        for runtime in self._stock_runtimes(include_detached=True):
                            with self._stock_context(runtime["stock"], runtime["job"]):
                                self._suspend_proxy_recovery()
                                self._set("probe", False)
                                if not runtime["active_halt"] and runtime["state"] != "completed":
                                    self._set("state", "paused")
                                    self._set("reason", "本地存储异常，节点已暂停；修复后需手动继续")
                                    self._set("resume_recovery", True)
                                self._end_segment("local_storage_error")
                        self._publish_stock_summary()
                return False
            with self.db:
                self._set("data_storage", {**summary, "schema_version": "legacy_posts", "status": "ready",
                                           "last_error": None, "last_synced_at": _iso(self.clock())})
                if previous:
                    self._set("storage_halt", None)
                    self._set("probe", False)
                    if self._get("active_halt"):
                        self._set("state", previous["previous_state"] if previous["previous_state"] in {"blocked", "error"} else "error")
                        self._set("reason", previous["previous_reason"])
                    else:
                        self._set("state", "paused")
                        self._set("reason", "本地兼容数据投影已修复；未请求来源，等待继续")
                        self._set("resume_recovery", True)
                    self._event("local_storage_repaired", "本地数据已重投影，来源阻断/间隔保留；未请求来源", summary)
            return True

    def _halt_target(self):
        hid = self._get("halted_task_id", self._get("halt_task_id"))
        if hid is None and self._get("active_halt"):
            row = self.db.execute("SELECT task FROM requests WHERE outcome NOT IN ('real_data','redirect','detail_unavailable','reserved') ORDER BY id DESC LIMIT 1").fetchone()
            hid = row[0] if row else None
        return self.db.execute("SELECT * FROM tasks WHERE id=?", (hid,)).fetchone() if hid else None

    def _target(self, probe_target=False):
        if self._get("active_halt") and (probe_target or self._get("probe") or self._get("job_id") is None):
            halted = self._halt_target()
            if halted:
                return halted
        network_target = self._network_retry_target()
        if network_target:
            return network_target
        scope = self._stock_scope() if self._stock_runtime_ready else None
        where = " AND stock=?" if scope else ""
        args = (scope[0], scope[1]) if scope else (self._get("job_id"),)
        inflight = self.db.execute("SELECT * FROM tasks WHERE job=?" + where + " AND status='inflight' ORDER BY id LIMIT 1", args).fetchone()
        if inflight:
            return inflight
        seeking = self.db.execute("SELECT * FROM tasks WHERE job=?" + where + " AND kind='list' AND purpose='seek' AND status='pending' ORDER BY id LIMIT 1", args).fetchone()
        if seeking:
            return seeking
        forced = self.db.execute("SELECT * FROM tasks WHERE job=?" + where + " AND kind='list' AND purpose='recovery' AND status='pending' ORDER BY id LIMIT 1", args).fetchone()
        if forced:
            rec = self._recovery(forced["job"], forced["stock"])
            if rec and rec.get("force_first"):
                return forced
        return self.db.execute("SELECT * FROM tasks WHERE job=?" + where + " AND status IN ('pending','inflight') "
                               "ORDER BY CASE WHEN status='inflight' THEN 0 WHEN kind='detail' THEN 1 WHEN purpose='recovery' THEN 2 ELSE 3 END,id LIMIT 1",
                               args).fetchone()

    def _network_retry_target(self):
        retry = self._get("network_retry")
        if not retry:
            return None
        target = self.db.execute("SELECT * FROM tasks WHERE id=?", (retry["task_id"],)).fetchone()
        if (target and target["job"] == self._get("job_id") and target["status"] in {"pending", "inflight"}
                and self._task_in_active_scope(target) and not self._job_archived(target["job"])):
            return target
        return None

    @staticmethod
    def _target_json(row):
        return {k: row[k] for k in ("kind", "stock", "page", "post_id", "url", "purpose")} if row else None

    def create_job(self, config):
        config = _validate_config(config)
        with self._mutex, self.db:
            if self._inflight or self._get("active_halt") or self._get("storage_halt"):
                raise RuntimeError("已有未解决的暂停原因；不能新建任务绕过，请先单次探测")
            if self._stock_runtime_ready:
                blocked = [r["stock"] for r in self._stock_runtimes(include_detached=True)
                           if r["active_halt"] and r["stock"] in config["stocks"]]
                if blocked:
                    raise RuntimeError("这些股票仍有原阻断，须先在旧任务入口单次探测：" + "、".join(sorted(set(blocked))))
            if self._get("job_id") and self._get("state") != "completed":
                raise RuntimeError("已有未完成任务；请继续现有任务，避免丢失覆盖进度")
            end = datetime.fromisoformat(config["to_date"] + "T23:59:59.999999+08:00").timestamp()
            start = datetime.fromisoformat(config["from_date"] + "T00:00:00+08:00").timestamp()
            effective_to = min(end, self.clock())
            if start > effective_to:
                raise ValueError("开始日期晚于当前可观察时间，不能创建未来数据任务")
            job = self.db.execute("INSERT INTO jobs(config,created) VALUES(?,?)", (_dump(config), self.clock())).lastrowid
            self._set("job_id", job)
            self._set("state", "paused")
            self._set("reason", "任务已创建，等待开始")
            self._set("probe", False)
            self._suspend_proxy_recovery()
            self._set("effective_to_epoch", effective_to)
            self._set("segment_id", None)
            self._set("last_success", None)
            for stock in config["stocks"]:
                self.db.execute("INSERT INTO coverage(job,stock) VALUES(?,?)", (job, stock))
                self._begin_window_seek(job, stock)
            self._register_job_lifecycle(job)
            self._event("job_created", "创建隔离回补任务", config)
            if self._stock_runtime_ready:
                self._ensure_current_stock_runtimes()
        return self.status()

    def _enqueue_list(self, job, stock, page):
        url = list_url(stock, page)
        self.db.execute("INSERT INTO tasks(job,kind,stock,page,url,original_url) VALUES(?,'list',?,?,?,?)", (job, stock, page, url, url))

    def _detail_list_referer(self, task):
        # Recovery can move an existing post to a different page. Prefer the
        # most recent accepted observation in this job/bar over its first page.
        observed = self.db.execute(
            "SELECT r.id,r.url,r.final_url FROM observations o JOIN requests r ON r.id=o.request_id "
            "WHERE o.job=? AND o.stock=? AND o.post_id=? AND r.kind='list' AND r.outcome='real_data' "
            "ORDER BY o.id DESC LIMIT 1", (task["job"], task["stock"], task["post_id"])).fetchone()
        if observed is None:
            observed = self.db.execute(
                "SELECT r.id,r.url,r.final_url FROM http_post_state p JOIN requests r ON r.id=p.list_request "
                "WHERE p.post_id=? AND r.kind='list' AND r.outcome='real_data'",
                (task["post_id"],)).fetchone()
        if observed:
            return observed["final_url"] or observed["url"], "detail_observed_list", observed["id"]
        return list_url(task["stock"], task["page"] or 1), "detail_task_list_page", None

    def _request_profile(self, task):
        # One selection per logical task. A redirect or manual retry must not
        # silently change the chosen originating page. Pre-upgrade attempts
        # have no matching profile and receive the newly authorized headers.
        previous = self.db.execute("SELECT analysis FROM requests WHERE task=? ORDER BY id DESC LIMIT 1",
                                   (task["id"],)).fetchone()
        saved = json.loads(previous["analysis"]) if previous and previous["analysis"] else {}
        if (saved.get("request_profile") == REQUEST_PROFILE
                and saved.get("request_headers", {}).get("User-Agent") == UA):
            return {"request_profile": REQUEST_PROFILE,
                    "request_headers": request_headers(saved["request_headers"]["Referer"]),
                    "referer_source": saved["referer_source"],
                    "referer_list_request_id": saved.get("referer_list_request_id")}
        list_request_id = None
        if task["kind"] == "list":
            if task["page"] > 1:
                referer, source = list_url(task["stock"], task["page"] - 1), "list_previous_page"
            else:
                referer, source = GOOGLE_REFERER, "google_search"
        else:
            choice = random.randrange(100)
            if choice < 30:
                referer, source = GOOGLE_REFERER, "google_search"
            elif choice < 40:
                referer, source = BAIDU_REFERER, "baidu_search"
            else:
                referer, source, list_request_id = self._detail_list_referer(task)
        return {"request_profile": REQUEST_PROFILE, "request_headers": request_headers(referer),
                "referer_source": source, "referer_list_request_id": list_request_id}

    def _request_analysis(self, rid, analysis=None):
        saved = self.db.execute("SELECT analysis FROM requests WHERE id=?", (rid,)).fetchone()
        return {**(json.loads(saved[0]) if saved and saved[0] else {}), **(analysis or {})}

    def _task_in_active_scope(self, task):
        cfg = self._config()
        if (task["job"] != self._get("job_id") or not cfg or self._job_archived(task["job"])
                or task["stock"] not in cfg["stocks"]):
            return False
        prior = self._get("halt_config") if self._get("active_halt") else None
        if prior and any(prior.get(k) != cfg.get(k) for k in ("from_date", "to_date")):
            return False
        if task["kind"] == "detail":
            row = self.db.execute("SELECT item FROM http_posts WHERE post_id=?", (task["post_id"],)).fetchone()
            if not row:
                return False
            try:
                published = _item_load(row[0]).published_at
                if published is None or published.tzinfo is None:
                    return False
                epoch = math.floor(published.timestamp())
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                return False
            start_epoch, end_epoch = self._window_epochs(cfg)
            return start_epoch <= epoch <= end_epoch
        return True

    @staticmethod
    def _frontier_rows(rows):
        return [r for r in rows if not (type(r.get("post_top_status")) is int and r["post_top_status"] != 0)]

    def _ordinary_task_needed(self, task):
        if not self._task_in_active_scope(task):
            return False
        if task["kind"] == "detail":
            post = self.db.execute("SELECT status FROM http_posts WHERE post_id=?", (task["post_id"],)).fetchone()
            return bool(post and post["status"] == "pending")
        return True

    @classmethod
    def _navigation_rows(cls, rows):
        unpinned = cls._frontier_rows(rows)
        standard = [r for r in unpinned if r.get("post_type") == 0]
        # Captured type20 placements are not publish-ordered even on f.html.
        # Preserve them as auxiliary IDs, never use their oldest date to seek
        # weeks of history from an otherwise recent ordinary-post page.
        return standard or unpinned

    def _frontier(self, job, stock):
        row = self.db.execute("SELECT payload FROM frontiers WHERE job=? AND stock=?", (job, stock)).fetchone()
        return json.loads(row[0]) if row else None

    def _save_frontier(self, job, stock, value):
        self.db.execute("INSERT INTO frontiers VALUES(?,?,?) ON CONFLICT(job,stock) DO UPDATE SET payload=excluded.payload", (job, stock, _dump(value)))

    def _recovery(self, job, stock):
        row = self.db.execute("SELECT payload FROM recoveries WHERE job=? AND stock=?", (job, stock)).fetchone()
        return json.loads(row[0]) if row else None

    def _save_recovery(self, rec):
        self.db.execute("INSERT INTO recoveries VALUES(?,?,?) ON CONFLICT(job,stock) DO UPDATE SET payload=excluded.payload", (rec["job"], rec["stock"], _dump(rec)))

    def _migrate_frontiers(self):
        """v1 list observations establish an unverified anchor, never proof."""
        for cov in self.db.execute("SELECT * FROM coverage WHERE job=? AND pages>0", (self._get("job_id"),)).fetchall():
            if cov["stock"] not in self._scope_stocks():
                continue
            seek = self._seek_state(cov["job"], cov["stock"])
            if seek and not seek.get("entry_verified"):
                continue
            if self._frontier(cov["job"], cov["stock"]):
                continue
            requests = self.db.execute("SELECT id,page FROM requests WHERE job=? AND stock=? AND kind='list' AND outcome='real_data' AND purpose='forward' ORDER BY id DESC",
                                       (cov["job"], cov["stock"])).fetchall()
            snapshots = []
            for request in requests:
                rows = [json.loads(r[0]) for r in self.db.execute("SELECT source_row FROM observations WHERE request_id=? ORDER BY id", (request["id"],))]
                if rows:
                    snapshots.append({"page": request["page"], "rows": rows, "request_id": request["id"]})
                if len(snapshots) >= 2:
                    break
            if snapshots:
                value = snapshots[0] | {"anchor_page": snapshots[-1]["page"], "anchor_rows": snapshots[-1]["rows"],
                                        "terminal": cov["stop_reason"], "reconciled": False, "migrated_v1": True}
                self._save_frontier(cov["job"], cov["stock"], value)
                self._event("anchor_migrated", "旧版观察已转换为待校准锚点，未声明连续完整", {"stock": cov["stock"], "page": value["page"]})

    def _migrate_window_seek(self):
        """Convert an unentered historic prefix without discarding acquired facts."""
        if self._get("active_halt") or self._get("storage_halt") or self._get("state") == "completed":
            return
        job, cfg = self._get("job_id"), self._config()
        if not job or not cfg or self._job_archived(job):
            return
        cutoff = datetime.fromtimestamp(self._get("effective_to_epoch"), guba.SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")
        for stock in self._scope_stocks(cfg):
            if self._seek_state(job, stock):
                continue
            frontier = self._frontier(job, stock)
            if not frontier or frontier.get("terminal"):
                continue
            standard = [r for r in self._navigation_rows(frontier["rows"]) if r.get("post_type") == 0]
            if not standard or min(r["post_publish_time"] for r in standard) <= cutoff:
                continue
            self.db.execute("UPDATE tasks SET status='superseded' WHERE job=? AND stock=? AND kind='list' AND status='pending'", (job, stock))
            self.db.execute("DELETE FROM frontiers WHERE job=? AND stock=?", (job, stock))
            self.db.execute("DELETE FROM recoveries WHERE job=? AND stock=?", (job, stock))
            self._begin_window_seek(job, stock, reason="historic_prefix_upgrade")
            self._event("window_seek_upgraded", "旧任务尚未进入日期上界，改为快速定位；原请求和观察保留", {"stock": stock})

    def _mark_recovery_needed(self, reason):
        if self._stock_runtime_ready and not self._stock_scope():
            for job, stock in self._current_stock_scopes():
                with self._stock_context(stock, job):
                    if not self._get("active_halt"):
                        self._mark_recovery_needed(reason)
            return
        job = self._get("job_id")
        cfg = self._config()
        if not job or not cfg:
            return
        for stock in self._scope_stocks(cfg):
            seek = self._seek_state(job, stock)
            if seek and not seek.get("entry_verified"):
                continue
            frontier = self._frontier(job, stock)
            if frontier:
                old = self._recovery(job, stock)
                try:
                    self._begin_recovery(job, stock, frontier, reason, force_first=True,
                                         original=old if old and old.get("phase") != "complete" else None)
                except ValueError as exc:
                    if isinstance(exc, RecoveryLimitError):
                        if not self._get("active_halt"):
                            request = self.db.execute("SELECT id FROM requests WHERE job=? AND stock=? AND kind='list' ORDER BY id DESC LIMIT 1", (job, stock)).fetchone()
                            if request:
                                self._halt("error", "calibration_limit", str(exc), request[0])
                                self._set("halted_probe_only", True)
                        self._event("calibration_limit", "校准预算仍已耗尽；原来源阻断保留，需人工探测后继续", {"stock": stock})
                        continue
                    # A valid but unusable frontier (for example only pinned
                    # rows) must not prevent the control server from starting.
                    # Retain any existing halt/target and never guess a page.
                    cov = self.db.execute("SELECT gaps FROM coverage WHERE job=? AND stock=?", (job, stock)).fetchone()
                    gaps = json.loads(cov[0])
                    gap = {"kind": "recovery_anchor_unavailable", "reason": str(exc)}
                    if gap not in gaps:
                        gaps.append(gap)
                        self.db.execute("UPDATE coverage SET gaps=? WHERE job=? AND stock=?", (_dump(gaps), job, stock))
                    if not self._get("active_halt"):
                        request = self.db.execute("SELECT id FROM requests WHERE job=? AND stock=? AND kind='list' ORDER BY id DESC LIMIT 1", (job, stock)).fetchone()
                        if request:
                            self._halt("error", "recovery_anchor_unavailable", "恢复锚点不可用，已停止推进: " + str(exc), request[0])
                        else:
                            self._set("state", "error")
                            self._set("active_halt", "recovery_anchor_unavailable")
                            self._set("reason", "恢复锚点不可用，需要人工检查: " + str(exc))
                    self._event("recovery_anchor_unavailable", "保留原阻断并安全启动；恢复锚点不可用，不能猜测下一页", {"stock": stock, "reason": str(exc)})

    def _begin_recovery(self, job, stock, frontier, reason, force_first=False, original=None):
        previous = self._recovery(job, stock)
        generation = (previous or {}).get("generation", 0) + 1
        if original:
            rec = dict(original)
        else:
            anchor_source = frontier.get("anchor_rows", frontier["rows"])
            anchors = self._navigation_rows(anchor_source)
            end_rows = self._navigation_rows(frontier["rows"])
            if not anchors or not end_rows:
                raise ValueError("无法建立非置顶来源行锚点，不能猜测下一历史页")
            rec = {"job": job, "stock": stock, "anchor_page": frontier.get("anchor_page", frontier["page"]),
                   "anchor_ids": [str(r["post_id"]) for r in anchors],
                   "auxiliary_anchor_ids": [str(r["post_id"]) for r in self._frontier_rows(anchor_source)],
                   "target_ids": [str(r["post_id"]) for r in end_rows],
                   "time_order_verified": any(r.get("post_type") == 0 for r in anchors) and any(r.get("post_type") == 0 for r in end_rows),
                   "anchor_min": min(r["post_publish_time"] for r in anchors),
                   "anchor_max": max(r["post_publish_time"] for r in anchors),
                   "target_time": min(r["post_publish_time"] for r in end_rows),
                   "forward_page": frontier["page"], "terminal": frontier.get("terminal"),
                   "drift_count": 0, "new_posts": 0, "rechecks": 0, "time_fallback": False}
        self.db.execute("UPDATE tasks SET status='superseded' WHERE job=? AND stock=? AND kind='list' AND status='pending' AND id!=COALESCE(?, -1)",
                        (job, stock, self._get("halted_task_id")))
        trigger = (original.get("trigger_reason", original.get("reason")) if original else reason)
        signature = self._stable_page_signature(frontier["rows"])
        source_count = frontier.get("source_count")
        if source_count is None:
            observed = self.db.execute("SELECT source_count FROM page_observations WHERE request_id=?", (frontier.get("request_id"),)).fetchone()
            source_count = observed[0] if observed else None
        cheap = (reason in ORDINARY_RECHECKS and trigger in ORDINARY_RECHECKS and not rec["terminal"]
                 and signature is not None and type(source_count) is int
                 and rec.get("time_order_verified", True)
                 and not rec["time_fallback"] and not rec["drift_count"]
                 and not (original and original.get("strategy") == "two_pass"))
        rec.update({"generation": generation, "phase": "verify_frontier" if cheap else "seek", "reason": reason,
                    "trigger_reason": trigger, "strategy": "stable_frontier" if cheap else "two_pass",
                    "validated_requests": rec.get("validated_requests", 0), "max_requests": MAX_RECOVERY_REQUESTS,
                    "completed_passes": rec.get("completed_passes", rec.get("passes", 0)), "max_passes": MAX_RECOVERY_PASSES,
                    "check_page": frontier["page"], "check_signature": signature, "check_source_count": source_count,
                    "current_page": frontier["page"] if cheap else rec["anchor_page"],
                    "force_first": force_first, "passes": 0, "last_signature": None, "pass_members": [],
                    "pass_pages": [], "visited": {}, "anchor_seen": False, "started_at": _iso(self.clock())})
        self._save_recovery(rec)
        if not rec.get("time_order_verified", True):
            self._recovery_gap(rec, "time_order_unverified", "当前锚点区间含全非标准帖页面，只核对来源 ID，不能用其发布时间证明历史次序")
        self._enqueue_recovery(rec, rec["current_page"])
        self._event("recovery_started", "先核对末次前进页，变化时才回扫区间" if cheap else "重新定位 ID/发布时间锚点；核对前不推进历史页",
                    {"stock": stock, "reason": reason, "trigger_reason": trigger, "strategy": rec["strategy"], "anchor_page": rec["anchor_page"]})

    @staticmethod
    def _stable_page_signature(rows):
        standard = [r for r in rows if r.get("post_type") == 0 and not (type(r.get("post_top_status")) is int and r["post_top_status"] != 0)]
        if not standard or any(type(r.get("post_top_status")) is not int or r["post_top_status"] != 0 for r in standard):
            return None
        members = [(str(r["post_id"]), r["post_publish_time"]) for r in standard]
        times = [time for _, time in members]
        if times != sorted(times, reverse=True):
            return None
        return hashlib.sha256(_dump(members).encode()).hexdigest()

    def _enqueue_recovery(self, rec, page):
        if rec.get("validated_requests", 0) >= MAX_RECOVERY_REQUESTS or rec.get("completed_passes", 0) >= MAX_RECOVERY_PASSES:
            rec["phase"] = "error"
            self._save_recovery(rec)
            self._recovery_gap(rec, "calibration_limit", "校准预算已用完，保留缺口并停止自动回扫")
            raise RecoveryLimitError("校准预算已用完，需人工核对；不会继续反复翻页")
        rec["current_page"] = page
        self._save_recovery(rec)
        url = f"https://guba.eastmoney.com/list,{rec['stock']},f" + (f"_{page}" if page > 1 else "") + ".html"
        self.db.execute("INSERT INTO tasks(job,kind,stock,page,url,original_url,purpose,recovery_id) VALUES(?,'list',?,?,?,?,'recovery',?)",
                        (rec["job"], rec["stock"], page, url, url, rec["generation"]))

    def _recovery_gap(self, rec, kind, message):
        cov = self.db.execute("SELECT gaps FROM coverage WHERE job=? AND stock=?", (rec["job"], rec["stock"])).fetchone()
        gaps = json.loads(cov[0])
        gap = {"kind": kind, "anchor_min": rec["anchor_min"], "anchor_max": rec["anchor_max"], "reason": message}
        if gap not in gaps:
            gaps.append(gap)
            self.db.execute("UPDATE coverage SET gaps=? WHERE job=? AND stock=?", (_dump(gaps), rec["job"], rec["stock"]))
        self._event(kind, message, {"stock": rec["stock"], "page": rec["current_page"]})

    def _advance_recovery(self, task, rows, stats):
        rec = self._recovery(task["job"], task["stock"])
        if not rec or task["recovery_id"] != rec["generation"]:
            return {"superseded": True}
        rec["force_first"] = False
        rec["new_posts"] += stats["new_eligible_posts"]
        rec["validated_requests"] = rec.get("validated_requests", 0) + 1
        rec.setdefault("max_requests", MAX_RECOVERY_REQUESTS)
        rec.setdefault("max_passes", MAX_RECOVERY_PASSES)
        self._save_recovery(rec)
        if rec["phase"] == "verify_frontier":
            matched = (task["page"] == rec["check_page"] and self._stable_page_signature(rows) == rec["check_signature"]
                       and type(stats["source_count"]) is int and stats["source_count"] >= rec["check_source_count"])
            if matched:
                rec.update({"phase": "complete", "end_page": task["page"], "completed_at": _iso(self.clock()),
                            "proof_level": "last_forward_page_stable", "verified_requests": {"stable_page": stats["request_id"]}})
                self._save_recovery(rec)
                frontier = self._frontier(task["job"], task["stock"])
                frontier.update({"page": task["page"], "rows": rows, "request_id": stats["request_id"], "source_count": stats["source_count"],
                                 "anchor_page": task["page"], "anchor_rows": rows, "checked_at": self.clock(),
                                 "reconciled": False, "navigation_stable": True,
                                 "reconciliation": {"proof_level": rec["proof_level"], "request_id": stats["request_id"]}})
                self._save_frontier(task["job"], task["stock"], frontier)
                self._enqueue_list(task["job"], task["stock"], task["page"] + 1)
                self._event("frontier_stable", "末次前进页的有序 ID／发布时间未变，从下一页继续；仅核实导航锚点",
                            {"stock": task["stock"], "page": task["page"], "next_page": task["page"] + 1, "request_id": stats["request_id"]})
                return {"phase": "complete", "proof_level": rec["proof_level"]}
            rec.update({"phase": "seek", "strategy": "two_pass", "fallback_reason": "末次前进页变化或核对条件不足，改为区间两轮校准"})
            self._enqueue_recovery(rec, rec["anchor_page"])
            self._event("frontier_check_changed", rec["fallback_reason"], {"stock": task["stock"], "page": task["page"]})
            return {"phase": "seek", "stable_check": False}
        items = self._navigation_rows(rows)
        if not items:
            rec["phase"] = "error"
            self._save_recovery(rec)
            self._recovery_gap(rec, "anchor_not_located", "来源空页或没有可用非置顶时间，无法恢复锚点")
            raise ValueError("校准无法定位锚点：来源空页/无可用发布时间；已暂停")
        times = [r["post_publish_time"] for r in items]
        temporal = rec.get("time_order_verified", True)
        if temporal and not any(r.get("post_type") == 0 for r in items):
            self._recovery_gap(rec, "time_order_unverified", "普通帖时间锚点定位过程中仅遇非标准行，无法按时间方向判断")
            raise ValueError("校准无法定位普通帖时间锚点；当前页只有非标准行，已暂停")
        if temporal and times != sorted(times, reverse=True):
            raise ValueError("校准列表不符合非置顶来源行发布时间降序，无法安全定位")
        seen_anchor = bool({str(r["post_id"]) for r in items} & set(rec["anchor_ids"]))
        if rec["phase"] == "seek":
            if not temporal and not seen_anchor:
                self._recovery_gap(rec, "anchor_not_located", "非标准页面旧 ID 锚点不可见，且没有可靠时间顺序可降级定位")
                raise ValueError("校准无法定位 ID 锚点；非标准页时间次序未知，已暂停")
            direction = 1 if min(times) > rec["anchor_max"] else -1 if max(times) < rec["anchor_min"] else 0
            next_page = task["page"] + direction
            rec["visited"][str(task["page"])] = {"earliest": min(times), "latest": max(times)}
            if not seen_anchor and direction and next_page >= 1 and str(next_page) not in rec["visited"]:
                rec["drift_count"] += 1
                self._enqueue_recovery(rec, next_page)
                self._event("anchor_shift", "锚点已移位，按源发布时间向相邻页定位", {"stock": task["stock"], "from_page": task["page"], "next_page": next_page})
                return {"phase": "seek", "next_page": next_page}
            if not seen_anchor:
                rec["time_fallback"] = True
                self._recovery_gap(rec, "anchor_ids_missing_time_fallback", "旧锚点 ID 不再可见；仅按源发布时间回扫，区间存在不可证明缺口")
            rec.update({"phase": "scan", "scan_start_page": task["page"], "start_rows": rows, "anchor_seen": seen_anchor})
        rec["anchor_seen"] = rec["anchor_seen"] or seen_anchor
        # Compare a fixed source-time interval rather than rows after the frozen
        # upper cutoff. Newly published posts may shift physical page numbers.
        members = [(str(r["post_id"]), r["post_publish_time"]) for r in items
                   if not temporal or rec["target_time"] <= r["post_publish_time"] <= rec["anchor_max"]]
        rec["pass_members"].extend(members)
        rec["pass_pages"].append({"page": task["page"], "request_id": stats["request_id"], "sha256": stats["ordered_id_sha256"], "source_count": stats["source_count"]})
        reached_target = min(times) <= rec["target_time"] if temporal else bool({str(r["post_id"]) for r in items} & set(rec.get("target_ids", rec["anchor_ids"])))
        if not reached_target:
            self._enqueue_recovery(rec, task["page"] + 1)
            return {"phase": "scan", "next_page": task["page"] + 1}
        unique = sorted(set(map(tuple, rec["pass_members"])), key=lambda x: (x[1], x[0]), reverse=True)
        signature = hashlib.sha256(_dump(unique).encode()).hexdigest()
        rec["passes"] += 1
        rec["completed_passes"] = rec.get("completed_passes", rec["passes"] - 1) + 1
        if signature == rec["last_signature"] and (rec["anchor_seen"] or rec["time_fallback"]):
            if rec.get("trigger_reason", rec["reason"]) == "forward_no_progress" and task["page"] <= rec["forward_page"] and not rec["new_posts"]:
                rec["phase"] = "error"
                self._save_recovery(rec)
                raise ValueError("分页无进展：重新定位后仍未发现更深历史，不能反复猜测下一页")
            rec.update({"phase": "complete", "completed_at": _iso(self.clock()), "end_page": task["page"], "signature": signature,
                        "verified_requests": {"first_pass": rec.get("previous_pass_pages", []), "second_pass": rec["pass_pages"]},
                        "proof_level": "id_interval_time_order_unverified" if not temporal else "time_boundary_with_gap" if rec["time_fallback"] else "two_matching_anchor_interval_observations"})
            self._save_recovery(rec)
            frontier = self._frontier(task["job"], task["stock"])
            frontier.update({"page": task["page"], "rows": rows, "request_id": stats["request_id"], "source_count": stats["source_count"],
                             "anchor_page": rec["scan_start_page"], "navigation_stable": False,
                             "anchor_rows": rec["start_rows"], "reconciled": True, "reconciliation": {"signature": signature, "passes": rec["passes"], "proof_level": rec["proof_level"]}})
            frontier["checked_at"] = self.clock()
            self._save_frontier(task["job"], task["stock"], frontier)
            if not rec["terminal"]:
                self._enqueue_list(task["job"], task["stock"], task["page"] + 1)
            else:
                self.db.execute("UPDATE coverage SET list_complete=1 WHERE job=? AND stock=?", (task["job"], task["stock"]))
            self._event("reconciliation_complete", "两轮已观察锚点时间区间一致；下一页使用重新定位后的物理页码", {"stock": task["stock"], "end_page": task["page"], "new_posts": rec["new_posts"], "proof_level": rec["proof_level"]})
            return {"phase": "complete", "proof_level": rec["proof_level"]}
        if rec["last_signature"] is not None:
            rec["drift_count"] += 1
            self._event("pagination_drift", "两轮时间区间 ID 不一致，补入新详情并重新回扫", {"stock": task["stock"], "passes": rec["passes"]})
        rec.update({"phase": "seek", "last_signature": signature, "pass_members": [], "pass_pages": [],
                    "previous_pass_pages": list(rec["pass_pages"]), "visited": {}, "anchor_seen": False, "rechecks": rec["rechecks"] + 1})
        self._enqueue_recovery(rec, rec["scan_start_page"])
        return {"phase": "seek", "verification_pending": True}

    def _start_one(self):
        with self._mutex:
            if self._get("storage_halt"):
                self._sync_storage()
                return self.status()
        with self._mutex, self.db:
            if self._inflight and (not self._stock_scope() or self._inflight_stock == self._stock_scope()):
                raise RuntimeError("当前请求尚在执行，请等待其结果保存后继续")
            if not self._config():
                raise RuntimeError("请先创建任务")
            if self._get("active_halt"):
                raise RuntimeError("当前有未解决的阻断或错误；只能人工单次探测，不能直接继续")
            if self._get("state") == "completed":
                raise RuntimeError("任务已结束，请查看覆盖缺口或创建新任务")
            if self._get("state") == "running":
                return self.status()
            self._migrate_window_seek()
            if self._get("resume_recovery"):
                self._mark_recovery_needed("manual_resume")
                self._set("resume_recovery", False)
                if self._get("active_halt"):
                    return self.status()
            self._set("state", "running")
            self._set("proxy_auto_suspended", False)
            self._set("reason", "按全局间隔运行")
            self.db.execute("UPDATE jobs SET started=COALESCE(started,?) WHERE id=?", (self.clock(), self._get("job_id")))
            self._begin_segment()
            self._event("started", "开始/继续任务")
        return self.status()

    def _pause_one(self):
        with self._mutex, self.db:
            self._suspend_proxy_recovery()
            self._set("probe", False)
            self._set("resume_recovery", True)
            self._end_segment("manual_pause")
            if self._get("state") not in {"blocked", "error", "completed"}:
                self._set("state", "paused")
                self._set("reason", "人工暂停" + ("；当前请求会保留结果" if self._inflight else ""))
            self._event("paused", "人工暂停；不会发起后续请求")
        return self.status()

    def _retry_one(self, *, automatic=False):
        with self._mutex:
            if self._get("storage_halt"):
                self._sync_storage()
                return self.status()
        with self._mutex, self.db:
            if (self._inflight and (not self._stock_scope() or self._inflight_stock == self._stock_scope())) or self._get("probe"):
                raise RuntimeError("请求或单次探测已在执行/排队")
            if self._get("state") == "running":
                raise RuntimeError("请先暂停任务再单次探测")
            if not self._target(probe_target=True):
                raise RuntimeError("没有待请求目标")
            if not automatic:
                self._proxy_for_stock().prepare_manual_probe()
            self._set("probe", True)
            self._suspend_proxy_recovery()
            self._set("state", "running")
            self._set("reason", "单次探测已排队；遵守原有请求间隔和冷却，结束后暂停")
            self.db.execute("UPDATE jobs SET started=COALESCE(started,?) WHERE id=?", (self.clock(), self._get("job_id")))
            self._begin_segment(probe=True)
            self._event("probe_scheduled", self._get("reason"))
        return self.status()

    def _suspend_proxy_recovery(self):
        self._set("proxy_auto_suspended", True)
        self._set("proxy_auto_probe", False)
        self._set("proxy_control_generation", self._get("proxy_control_generation", 0) + 1)

    def configure_proxy(self, settings):
        with self._mutex, self.db:
            if self._inflight or self._get("state") == "running" or self._get("probe"):
                raise RuntimeError("请先暂停并等待当前请求结束，再保存出站配置")
            try:
                result = self.proxy.configure(settings)
            except OSError:
                raise RuntimeError("代理配置无法持久保存，请检查节点数据目录") from None
            self._suspend_proxy_recovery()
            self._event("proxy_configured", "更新来源出站设置；原任务、阻断和冷却保留", {"mode": result["mode"]})
        return self.status()

    def rotate_proxy(self):
        with self._mutex, self.db:
            try:
                self.proxy.rotate()
            except OSError:
                raise RuntimeError("代理切换无法持久保存，请检查节点数据目录") from None
            self._set("proxy_control_generation", self._get("proxy_control_generation", 0) + 1)
            self._set("proxy_auto_probe", False)
            self._event("proxy_rotation_requested", "更换下一次来源请求的出口；不改变原冷却和断点")
            if not self._inflight and self._get("state") in {"blocked", "error"} and not self._get("storage_halt"):
                self.retry()
        return self.status()

    def _maybe_proxy_recovery(self):
        """Only schedule one existing target; never perform network I/O here."""
        with self._mutex, self.db:
            if (self._closed or self._inflight or self._get("storage_halt")
                    or self._get("proxy_auto_suspended", True)
                    or self._get("state") not in {"blocked", "error"}):
                return
            halt = self._get("active_halt")
            if halt not in {"challenge", "http_403", "http_429", "proxy_error"}:
                return
            if halt == "proxy_error" and self._get("last_proxy_error") not in {
                    "proxy_connect", "provider_unavailable", "provider_duplicate"}:
                return
            manager = self._proxy_for_stock()
            proxy_status = manager.status()
            settings = proxy_status["settings"]
            if settings["mode"] not in {"mayi", "qingguo"} or not settings["auto_recover"]:
                return
            if (proxy_status.get("fallback") or {}).get("blocked"):
                self._suspend_proxy_recovery()
                self._event("proxy_direct_fallback_blocked", "回退直连遭来源验证/限流，自动恢复已暂停；需要人工处理")
                return
            target = self._target(probe_target=True)
            if (not target or self._get("halted_probe_only", False)
                    or not self._task_in_active_scope(target) or self._job_archived(target["job"])):
                return
            evidence = self._get("block_evidence") or {}
            due = max(self._get("next_due", 0), (evidence.get("finished") or evidence.get("started") or self.clock())
                      + settings["recovery_cooldown_seconds"])
            if self.clock() < due:
                return
            if not manager.claim_recovery():
                proxy_status = manager.status()
                if proxy_status["recovery_attempts"] >= settings["recovery_max_attempts"]:
                    self._suspend_proxy_recovery()
                    self._event("proxy_recovery_exhausted", "连续恢复尝试预算耗尽，自动恢复已暂停；需要人工处理")
                return
            self._retry_one(automatic=True)
            self._set("proxy_auto_suspended", False)
            self._set("proxy_auto_probe", True)
            self._event("proxy_recovery_scheduled", "动态代理单次恢复已排队；仍受原限速和来源冷却约束")

    def _record_proxy_dispatch(self, rid, route):
        with self._mutex, self.db:
            analysis = self._request_analysis(rid, {"proxy": route})
            self.db.execute("UPDATE requests SET analysis=?,started=? WHERE id=?",
                            (_dump(analysis), self.clock(), rid))

    def proxy_status(self):
        with self._mutex:
            result = self._proxy_for_stock().status()
            result["auto_suspended"] = self._get("proxy_auto_suspended", True)
            if (not result["auto_suspended"] and result["settings"]["auto_recover"]
                    and self._get("state") in {"blocked", "error"}):
                evidence = self._get("block_evidence") or {}
                finished = evidence.get("finished") or evidence.get("started")
                if finished is not None:
                    result["next_recovery_at"] = _iso(max(
                        self._get("next_due", 0), finished + result["settings"]["recovery_cooldown_seconds"]))
            return result

    def _tick_one(self):
        """Reserve durably, release control lock during I/O, then commit one outcome."""
        self._maybe_proxy_recovery()
        with self._mutex, self.db:
            if self._closed or self._inflight or self._get("storage_halt") or self._get("state") != "running" or self.clock() < self._get("next_due", 0):
                return {"attempted": False}
            if self._get("network_retry") and not self._network_retry_target():
                self._set("network_retry", None)
            task = self._target(probe_target=self._get("probe", False))
            if not self._get("probe", False) and not self._get("active_halt"):
                skipped = []
                while task and not self._ordinary_task_needed(task) and len(skipped) < 100:
                    self.db.execute("UPDATE tasks SET status='superseded' WHERE id=?", (task["id"],))
                    skipped.append(task["id"])
                    task = self._target()
                if skipped:
                    self._event("out_of_scope_tasks_skipped", "跳过窗口外或已取得正文的待执行任务，历史记录仍保留",
                                {"count": len(skipped), "task_ids": skipped})
                if task and not self._ordinary_task_needed(task):
                    return {"attempted": False}
            if not task:
                self._finish_job()
                return {"attempted": False}
            if not self._get("probe", False) and not self._get("active_halt"):
                task = self._prepare_forward_target(task)
            task = dict(task)
            config, now = self._config(), self.clock()
            probe = self._get("probe", False)
            auto_probe = probe and self._get("proxy_auto_probe", False)
            control_generation = self._get("proxy_control_generation", 0)
            self._set("proxy_auto_probe", False)
            halted = self._halt_target() if self._get("active_halt") else None
            if halted and probe and halted["id"] == task["id"]:
                config = self._get("halted_config") or self._get("halt_config") or json.loads(self.db.execute("SELECT config FROM jobs WHERE id=?", (task["job"],)).fetchone()[0])
            if config is None:
                raise RuntimeError("请求缺少原任务配置，无法安全探测")
            probe_only = probe and (self._get("halted_probe_only", False) or not self._task_in_active_scope(task))
            profile = self._request_profile(task)
            self._set("probe", False)
            self._set("next_due", max(now, self._get("next_due", 0)) + config["interval_seconds"])
            interval = max(config["interval_seconds"], (self._config() or config)["interval_seconds"])
            self._set_global("node_next_due", max(now, self._get_global("node_next_due", 0)) + interval)
            rid = self.db.execute("INSERT INTO requests(job,task,kind,stock,page,post_id,url,started,probe,purpose,probe_only,analysis) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                                  (task["job"], task["id"], task["kind"], task["stock"], task["page"], task["post_id"], task["url"], now, int(probe), task["purpose"], int(probe_only), _dump(profile))).lastrowid
            self.db.execute("UPDATE tasks SET status='inflight' WHERE id=?", (task["id"],))
            self._inflight = True
            self._inflight_stock = (task["job"], task["stock"])
            self._inflight_probe = probe
            self._inflight_task = task
        # A concurrent pause is now effective for the next request; this response
        # remains fully recorded. Transport never recursively requests a redirect.
        dispatched = False
        def record_route(route):
            nonlocal dispatched
            self._record_proxy_dispatch(rid, route)
            dispatched = True
        try:
            manager = self._proxy_for_stock()
            if self._managed_transport:
                response = manager.fetch(task["url"], config["client"], referer=profile["request_headers"]["Referer"],
                                            on_route=record_route)
            elif self._transport_with_referer:
                response = self.transport(task["url"], config["client"], referer=profile["request_headers"]["Referer"])
            else:
                response = self.transport(task["url"], config["client"])
            if not isinstance(response, Response):
                raise ValueError("transport 必须返回 Response")
        except Exception as exc:
            response = Response(None, b"", {}, task["url"], self._sanitize(f"{type(exc).__name__}: {exc}"),
                                network_attempted=dispatched if self._managed_transport else True,
                                transient_error=_transient_error_kind(exc))
        with self._mutex:
            try:
                self._record_response(rid, task, response, config, probe, probe_only)
                try:
                    outcome = self.db.execute("SELECT outcome FROM requests WHERE id=?", (rid,)).fetchone()[0]
                    manager.observe(response, outcome, self._get("next_due", 0))
                except Exception:
                    with self.db:
                        self._halt("error", "proxy_state_error", "代理运行状态保存失败；保留已取得数据，暂停后续请求", rid)
            except Exception as exc:
                # Acquisition's body/state transaction may have rolled back,
                # while the fsynced raw file is already durable. Link only an
                # exact retained response, without accepting it as parsed data.
                retained = None
                raw = self.raw_dir / f"{rid:09d}.body"
                try:
                    if isinstance(response.body, bytes) and raw.is_file() and not raw.is_symlink():
                        body = raw.read_bytes()
                        if body == response.body:
                            retained = (len(body), hashlib.sha256(body).hexdigest(), str(raw.relative_to(self.data_dir)))
                except OSError:
                    pass  # The original write error remains the stop reason.
                with self.db:
                    self.db.execute("UPDATE requests SET outcome='internal_error',finished=?,error=?,network_attempted=? WHERE id=?",
                                    (self.clock(), self._sanitize(f"{type(exc).__name__}: {exc}"), int(response.network_attempted), rid))
                    if retained is not None:
                        headers = {str(k).lower(): str(v) for k, v in response.headers.items() if str(k).lower() in SAFE_HEADERS} if isinstance(response.headers, dict) else {}
                        self.db.execute("UPDATE requests SET http_status=?,response_bytes=?,sha256=?,raw_ref=?,headers=?,final_url=? WHERE id=?",
                                        (response.status, *retained, _dump(headers), response.url or task["url"], rid))
                    self._set("next_due", max(self._get("next_due", 0), self.clock() + config["interval_seconds"]))
                    self.db.execute("UPDATE tasks SET status='pending' WHERE id=?", (task["id"],))
                    self._halt("error", "internal_error", self._sanitize(f"结果保存/解析错误，需要人工处理: {exc}"), rid)
            finally:
                with self.db:
                    self._set_global("node_next_due", max(self._get_global("node_next_due", 0), self.clock() + interval))
                self._inflight = False
                self._inflight_stock = None
                self._inflight_probe = False
                self._inflight_task = None
            self._sync_storage(rid)
            if (auto_probe and not probe_only and not self._get("proxy_auto_suspended", True)
                    and self._get("proxy_control_generation", 0) == control_generation
                    and not self._get("active_halt") and not self._get("storage_halt")
                    and self._get("state") == "paused" and self._task_in_active_scope(task)
                    and not self._job_archived(task["job"])):
                self._start_one()
            return {"attempted": True, "request_id": rid, "state": self._get("state")}

    def _prepare_forward_target(self, task):
        if task["kind"] != "list" or task["purpose"] != "forward":
            return task
        frontier = self._frontier(task["job"], task["stock"])
        if not frontier:
            return task
        previous_request = self.db.execute("SELECT finished FROM requests WHERE id=?", (frontier.get("request_id"),)).fetchone()
        finished = (previous_request[0] or 0) if previous_request else 0
        checked = max(frontier.get("checked_at", 0), finished)
        rec = self._recovery(task["job"], task["stock"])
        interval = self._config()["interval_seconds"]
        if self.clock() - checked < interval + 300 or (rec and rec.get("phase") != "complete"):
            return task
        self._begin_recovery(task["job"], task["stock"], frontier, "list_delay_recheck", force_first=True)
        return self._target()

    def _record_response(self, rid, task, response, config, probe, probe_only=False):
        if not isinstance(response.body, bytes):
            raise ValueError("响应 body 必须为 bytes")
        raw = self.raw_dir / f"{rid:09d}.body"
        tmp = raw.with_suffix(".tmp")
        with open(tmp, "wb") as out:
            out.write(response.body)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, raw)
        directory_fd = os.open(self.raw_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        headers = {str(k).lower(): str(v) for k, v in response.headers.items() if str(k).lower() in SAFE_HEADERS}
        final_url = response.url or task["url"]
        with self.db:
            if response.proxy is not None:
                self.db.execute("UPDATE requests SET analysis=? WHERE id=?",
                                (_dump(self._request_analysis(rid, {"proxy": response.proxy})), rid))
            self.db.execute("UPDATE requests SET finished=?,http_status=?,response_bytes=?,sha256=?,raw_ref=?,headers=?,final_url=?,network_attempted=? WHERE id=?",
                            (self.clock(), response.status, len(response.body), hashlib.sha256(response.body).hexdigest(),
                             str(raw.relative_to(self.data_dir)), _dump(headers), final_url, int(response.network_attempted), rid))
            # A conservative finish-to-next-start gap also prevents a slow fetch
            # from bunching up the next request immediately after it finishes.
            self._set("next_due", max(self._get("next_due", 0), self.clock() + config["interval_seconds"]))
            self.db.execute("UPDATE tasks SET status='pending' WHERE id=?", (task["id"],))
            if not _allowed(final_url) or final_url != task["url"]:
                self._outcome(rid, "unexpected_final_url", "transport 不得自动重定向或请求外部地址")
                self._halt("error", "unexpected_final_url", "transport 返回意外最终地址，拒绝接受数据", rid)
                return
            if response.status in {401, 403, 407, 429} and not response.proxy_error:
                due = self._retry_after(headers.get("retry-after"))
                self._set("next_due", max(self._get("next_due"), due))
                self._outcome(rid, "access_block", response.error, {"http_status": response.status, "retry_after": headers.get("retry-after")})
                self._halt("blocked", "http_" + str(response.status), f"来源返回 HTTP {response.status}；已停止自动请求", rid)
                return
            # Even a partial timed-out body can contain positive challenge
            # evidence. It may stop access, but is never accepted as source data.
            if response.error and not response.proxy_error:
                partial_evidence = challenge_evidence(response.body.decode("utf-8-sig", errors="replace"))
                if partial_evidence["challenge"]:
                    self._outcome(rid, "access_block", partial_evidence["reason"], partial_evidence)
                    self._halt("blocked", "challenge", f"检测到{partial_evidence['reason']}；已停止自动请求", rid)
                    return
            if response.error and response.transient_error in NETWORK_RETRY_KINDS and response.proxy_error != "proxy_auth":
                self._record_network_retry(rid, task, response, config, probe)
                return
            if response.proxy_error:
                self._set("last_proxy_error", response.proxy_error)
                if response.proxy_error not in {"proxy_connect", "provider_unavailable", "provider_duplicate"}:
                    self._suspend_proxy_recovery()
                self._outcome(rid, "proxy_error", self._sanitize(response.error or "代理出站失败"),
                              {"proxy_error": response.proxy_error, "source_attempted": response.network_attempted})
                self._halt("blocked" if response.proxy_error == "proxy_auth" else "error", "proxy_error",
                           self._sanitize("代理出站失败: " + (response.error or response.proxy_error)), rid)
                return
            if response.error:
                error = self._sanitize(response.error)
                self._outcome(rid, "transport_error", error)
                self._halt("error", "transport_error", "网络/传输失败: " + error, rid)
                return
            try:
                html = response.body.decode("utf-8-sig", errors="strict")
            except UnicodeDecodeError:
                self._outcome(rid, "schema_error", "响应不是完整有效 UTF-8")
                self._halt("error", "schema_error", "响应不是有效 UTF-8，已暂停", rid)
                return
            evidence = challenge_evidence(html)
            if evidence["challenge"]:
                self._outcome(rid, "access_block", evidence["reason"], evidence)
                self._halt("blocked", "challenge", f"检测到{evidence['reason']}；已停止自动请求", rid)
                return
            # A truncated not-found shell cannot become a permanent missing-post
            # outcome. Check framing for every non-challenge response first.
            try:
                self._validate_framing(response.body, headers)
            except ValueError as exc:
                self._outcome(rid, "schema_error", str(exc), evidence)
                self._halt("error", "schema_error", "响应不完整/编码不支持: " + str(exc), rid)
                return
            if response.status in REDIRECTS:
                self._set("network_retry", None)
                target = urljoin(task["url"], headers.get("location", ""))
                if not headers.get("location") or not _allowed(target) or task["hops"] >= 5 or target == task["url"]:
                    self._outcome(rid, "redirect_error", "重定向地址不允许、形成循环或超出 5 跳")
                    self._halt("error", "redirect_error", "重定向不能安全继续，已暂停", rid)
                    return
                self.db.execute("UPDATE tasks SET url=?,hops=hops+1 WHERE id=?", (target, task["id"]))
                self._outcome(rid, "redirect", None, {"location": target})
                self._event("redirect", "下一跳已排队，每跳仍遵守全局间隔", {"request_id": rid, "url": target})
                if probe:
                    self._set("state", "blocked" if self._get("active_halt") else "paused")
                    self._set("reason", "单次探测收到重定向；尚未验证有效数据，下一跳需继续或再探测")
                    self._end_segment("probe_redirect")
                return
            if task["kind"] == "detail" and response.status in {200, 404} and guba.is_not_found_page(html):
                if not probe_only:
                    self.db.execute("UPDATE http_post_state SET status='removed',detail_request=? WHERE post_id=?", (rid, task["post_id"]))
                self.db.execute("UPDATE tasks SET status='done' WHERE id=?", (task["id"],))
                self._outcome(rid, "detail_unavailable", None, {"source_missing_shell": True, "deleted": "not_proven"})
                self._event("detail_unavailable", "来源明确返回帖子不存在/不可访问页；保留列表及缺正文状态", {"request_id": rid, "post_id": task["post_id"]})
                self._after_success(rid, probe)
                return
            if response.status != 200:
                self._outcome(rid, "http_error", f"HTTP {response.status}")
                self._halt("error", "http_error", f"来源返回 HTTP {response.status}，已暂停", rid)
                return
            try:
                if task["kind"] == "list":
                    page_result = self._accept_list(rid, task, html, config, probe_only=probe_only)
                    evidence["list_observation"] = page_result
                else:
                    self._accept_detail(rid, task, html, probe_only=probe_only)
            except (ValueError, TypeError, KeyError) as exc:
                saved = self.db.execute("SELECT analysis FROM requests WHERE id=?", (rid,)).fetchone()
                validated = json.loads(saved[0]) if saved and saved[0] else {}
                if validated.get("list_structure_validated"):
                    evidence.update(validated)
                if isinstance(exc, RecoveryLimitError):
                    # The list was fully validated and its posts retained. The
                    # limit stops navigation, not source-data acquisition.
                    self.db.execute("UPDATE tasks SET status='done' WHERE id=?", (task["id"],))
                    self._outcome(rid, "real_data", None, evidence | {"calibration_limit": str(exc)})
                    self._set("last_success", self.clock())
                    self._halt("error", "calibration_limit", str(exc), rid)
                    self._set("halted_probe_only", True)
                    return
                self._outcome(rid, "schema_error", str(exc), evidence)
                self._halt("error", "schema_error", "响应结构/身份不匹配: " + str(exc), rid)
                return
            self.db.execute("UPDATE tasks SET status='done' WHERE id=?", (task["id"],))
            self._outcome(rid, "real_data", None, evidence)
            self._after_success(rid, probe)

    @staticmethod
    def _validate_framing(body, headers):
        length = headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) != len(body)):
            raise ValueError("Content-Length 与保留的响应字节数不一致")
        if len(body) > MAX_BODY:
            raise ValueError("响应超出最大留存长度")
        if headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
            raise ValueError("不支持的 Content-Encoding")
        charset = re.search(r"charset\s*=\s*[\"']?([^;\s\"']+)", headers.get("content-type", ""), re.I)
        if charset and charset[1].lower() not in {"utf-8", "utf8"}:
            raise ValueError("不支持的源响应字符集")

    def _retry_after(self, value):
        if value is None:
            return self.clock()
        if value.strip().isdigit():
            return self.clock() + int(value.strip())
        try:
            d = parsedate_to_datetime(value)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return max(self.clock(), d.timestamp())
        except (TypeError, ValueError, OverflowError):
            return self.clock()

    def _outcome(self, rid, outcome, error=None, analysis=None):
        combined = self._request_analysis(rid, analysis)
        self.db.execute("UPDATE requests SET outcome=?,error=?,analysis=? WHERE id=?", (outcome, error, _dump(combined) if combined else None, rid))

    def _record_network_retry(self, rid, task, response, config, probe):
        error = self._sanitize(response.error)
        facts = {"transient_error": response.transient_error, "proxy_error": response.proxy_error,
                 "source_attempted": response.network_attempted}
        if probe or self._get("active_halt"):
            # A failed one-shot probe is not permission to keep sending. Preserve
            # the protective halt and original evidence if one already exists.
            self._set("network_retry", None)
            self._outcome(rid, "transport_error", error, facts | {"automatic_retry": False})
            if self._get("active_halt"):
                halt = self._get("active_halt")
                blocked = halt in {"challenge", "http_401", "http_403", "http_407", "http_429"} or (
                    halt == "proxy_error" and self._get("last_proxy_error") == "proxy_auth")
                self._set("state", "blocked" if blocked else "error")
                self._set("reason", (self._get("block_evidence") or {}).get("reason") or "原阻断尚未解除")
            elif self._get("state") == "running":
                self._set("state", "paused")
                self._set("reason", "单次探测遇到网络故障；已暂停，等待继续或再次单次探测")
            self._end_segment("probe_network_error")
            self._event("network_probe_failed", "单次探测遇到网络故障，未安排自动重试", {"request_id": rid, **facts})
            return
        previous = self._get("network_retry") or {}
        attempt = previous.get("attempt", 0) + 1 if previous.get("task_id") == task["id"] else 1
        delay = max(config["interval_seconds"], min(900, config["interval_seconds"] * 2 ** min(attempt - 1, 10)))
        due = max(self._get("next_due", 0), self.clock() + delay, self._retry_after(response.headers.get("retry-after")))
        retry = {"kind": response.transient_error, "attempt": attempt, "task_id": task["id"],
                 "request_id": rid, "retry_at": _iso(due), "error": error}
        self._set("network_retry", retry)
        self._set("next_due", due)
        self._outcome(rid, "transport_error", error, facts | {"network_retry": retry, "automatic_retry": True})
        if self._get("state") == "running":
            self._set("reason", f"网络故障，保留原请求等待第 {attempt} 次退避重试；未观察到来源阻断")
        self._event("network_retry_scheduled", "网络故障已记录，保留原目标并按全局间隔退避", retry)

    def _clear_active_halt(self):
        for key in ("active_halt", "halted_task_id", "halt_task_id", "halted_config", "halt_config"):
            self._set(key, None)
        self._set("halted_probe_only", False)

    def _halt(self, state, kind, reason, rid):
        self._set("network_retry", None)
        if kind not in {"challenge", "http_403", "http_429", "proxy_error"}:
            self._suspend_proxy_recovery()
        self._set("state", state)
        self._set("active_halt", kind)
        self._set("reason", reason)
        self._end_segment(kind)
        row = self.db.execute("SELECT id,url,http_status,raw_ref,sha256,started,finished,analysis FROM requests WHERE id=?", (rid,)).fetchone()
        evidence = dict(row)
        evidence["analysis"] = json.loads(evidence["analysis"]) if evidence["analysis"] else None
        evidence.update({"kind": kind, "reason": reason, "server_blacklist": "unproven"})
        failed = self.db.execute("SELECT task,job FROM requests WHERE id=?", (rid,)).fetchone()
        self._set("halted_task_id", failed["task"])
        self._set("halt_task_id", failed["task"])
        config_row = self.db.execute("SELECT config FROM jobs WHERE id=?", (failed["job"],)).fetchone()
        self._set("halt_config", json.loads(config_row[0]) if config_row else self._config())
        self._set("block_evidence", evidence)
        self._event(kind, reason, evidence)

    def _after_success(self, rid, probe):
        self._set("network_retry", None)
        self._set("last_success", self.clock())
        if probe:
            if self._get("active_halt") == "calibration_limit":
                request = self.db.execute("SELECT job,stock FROM requests WHERE id=?", (rid,)).fetchone()
                rec = self._recovery(request["job"], request["stock"])
                if rec:
                    usage = {"validated_requests": rec.get("validated_requests", 0), "completed_passes": rec.get("completed_passes", 0)}
                    rec.update({"previous_budget": usage, "validated_requests": 0, "completed_passes": 0, "passes": 0, "strategy": "two_pass"})
                    self._save_recovery(rec)
                    self._event("calibration_budget_reviewed", "人工单次探测有效；保留原缺口，继续时允许新一轮有界校准", usage | {"request_id": rid})
            self._set("resume_recovery", True)
            self._clear_active_halt()
            self._set("state", "paused")
            self._set("reason", "单次探测取得有效来源响应；已暂停，等待继续")
            self._end_segment("probe_success")
            self._event("probe_success", self._get("reason"), {"request_id": rid})
        elif self._get("state") == "running":
            self._set("reason", "按全局间隔运行")
            if not self._target():
                self._finish_job()

    def _accept_list(self, rid, task, html, config, probe_only=False):
        expected_path = urlparse(task["original_url"]).path
        if urlparse(task["url"]).hostname != "guba.eastmoney.com" or urlparse(task["url"]).path != expected_path:
            raise ValueError("列表重定向后的股票/页码不符")
        page = guba.parse_list_page(html, task["stock"])
        payload = guba._embedded_json(html, "article_list")
        if type(payload.get("rc")) is not int or payload["rc"] != 1:
            raise ValueError("article_list.rc 必须是整数 1")
        rows = payload["re"]
        ids, times = [], []
        for row in rows:
            if not isinstance(row, dict) or isinstance(row.get("post_id"), bool) or not re.fullmatch(r"[0-9]+", str(row.get("post_id", ""))):
                raise ValueError("列表行缺少合法源 ID")
            if type(row.get("post_type")) is not int:
                raise ValueError("列表行缺少整数 post_type")
            published = guba.parse_source_time(row.get("post_publish_time"), "post_publish_time", required=True)
            if published.strftime("%Y-%m-%d %H:%M:%S") != row["post_publish_time"]:
                raise ValueError("发布时间必须为完整 YYYY-MM-DD HH:MM:SS")
            ids.append(str(row["post_id"]))
            times.append(row["post_publish_time"])
        if len(set(ids)) != len(ids):
            raise ValueError("同页出现重复源 ID")
        known = {r[0] for r in self.db.execute("SELECT DISTINCT post_id FROM observations WHERE job=? AND stock=?", (task["job"], task["stock"]))}
        by_id = {x.source_item_id: x for x in page.rows}
        start, end = config["from_date"], config["to_date"]
        effective_to = self._get("effective_to_epoch")
        def eligible_item(item):
            return (start <= item.published_at.date().isoformat() <= end
                    and item.published_at.timestamp() <= effective_to)
        # Validate cross-observation identity before making any queue changes.
        for item in page.rows:
            p = urlparse(item.url)
            if (not _allowed(item.url) or p.hostname != "guba.eastmoney.com"
                    or not re.fullmatch(r"/news,[A-Za-z0-9]+," + re.escape(item.source_item_id) + r"\.html", p.path)):
                raise ValueError("列表中的标准详情链接不符合已确认的来源路径")
            if not probe_only and eligible_item(item):
                prior = self.db.execute("SELECT item FROM http_posts WHERE post_id=?", (item.source_item_id,)).fetchone()
                if prior:
                    old = _item_load(prior[0])
                    for field in ("published_at", "canonical_bar_code", "author_id", "author_name"):
                        if getattr(old, field) != getattr(item, field):
                            raise ValueError(f"重复源 ID 的 {field} 不一致")
                    if old.title and item.title and old.title != item.title:
                        raise ValueError("重复源 ID 的标题不一致")
        stats = {"request_id": rid, "job": task["job"], "stock": task["stock"], "page": task["page"], "purpose": task.get("purpose", "forward"),
                 "source_count": page.source_count, "rows": len(rows), "new_ids": len(set(ids) - known), "new_eligible_posts": 0,
                 "overlap": len(set(ids) & known), "ordered_id_sha256": hashlib.sha256(_dump(ids).encode()).hexdigest(),
                 "earliest": min(times, default=None), "latest": max(times, default=None)}
        if probe_only:
            stats["probe_only"] = True
            return stats
        self.db.execute("UPDATE requests SET analysis=? WHERE id=?", (_dump(self._request_analysis(rid, {"list_structure_validated": True, "list_observation": stats})), rid))
        cov = self.db.execute("SELECT * FROM coverage WHERE job=? AND stock=?", (task["job"], task["stock"])).fetchone()
        if cov is None:
            raise ValueError("任务股票已不在活动覆盖范围")
        # Do not infer page monotonicity or historic availability from count.
        # A full below-window page needs a second full below-window confirmation.
        # Pinned rows remain retained/eligible, but cannot hold the historic
        # frontier open forever. Only source-explicit nonzero top flags exclude
        # a row from boundary proof; an absent/unknown flag remains included.
        boundary_rows = [r for r in self._frontier_rows(rows) if r.get("post_type") == 0]
        below = bool(boundary_rows) and all(r["post_publish_time"][:10] < start for r in boundary_rows)
        under = cov["under_pages"] + 1 if below else 0
        boundary = under >= 2
        exhausted = not rows
        gaps = json.loads(cov["gaps"])
        if task.get("purpose") != "recovery" and exhausted and not boundary and not cov["boundary"]:
            gaps.append({"kind": "source_exhausted_before_boundary", "requested_from": start,
                         "earliest_observed": min(times + ([cov["earliest"]] if cov["earliest"] else []), default=None)})
        for row in rows:
            pid = str(row["post_id"])
            self.db.execute("INSERT INTO observations(job,request_id,stock,page,post_id,source_row) VALUES(?,?,?,?,?,?)",
                            (task["job"], rid, task["stock"], task["page"], pid, _dump(row)))
            item = by_id.get(pid)
            eligible = item is not None and eligible_item(item)
            self.db.execute("INSERT OR IGNORE INTO associations VALUES(?,?,?,?,?)", (task["job"], task["stock"], pid, int(eligible), rid))
            if eligible:
                prior = self.db.execute("SELECT status FROM http_posts WHERE post_id=?", (pid,)).fetchone()
                if prior is None:
                    stats["new_eligible_posts"] += 1
                    status = self._store_list_post(item, _dump(row), rid)
                else:
                    status = prior["status"]
                if status == "pending" and detail_enrichment_trigger(item.title):
                    exists = self.db.execute("SELECT 1 FROM tasks WHERE job=? AND stock=? AND kind='detail' AND post_id=? AND status IN ('pending','inflight')", (task["job"], task["stock"], pid)).fetchone()
                    if not exists:
                        self.db.execute("INSERT INTO tasks(job,kind,stock,page,post_id,url,original_url) VALUES(?,'detail',?,?,?,?,?)",
                                        (task["job"], task["stock"], task["page"], pid, item.url, item.url))
        earliest = min(times + ([cov["earliest"]] if cov["earliest"] else []), default=None)
        latest = max(times + ([cov["latest"]] if cov["latest"] else []), default=None)
        if task.get("purpose") != "seek":
            self.db.execute("UPDATE coverage SET rows=rows+?,earliest=?,latest=?,source_count=?,gaps=? WHERE job=? AND stock=?",
                            (len(rows), earliest, latest, page.source_count, _dump(gaps), task["job"], task["stock"]))
        self.db.execute("INSERT INTO page_observations(request_id,job,stock,page,source_count,rows,new_ids,overlap,id_sha256,earliest,latest,purpose) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (rid, task["job"], task["stock"], task["page"], page.source_count, len(rows),
                         stats["new_ids"], stats["overlap"], stats["ordered_id_sha256"], stats["earliest"], stats["latest"], stats["purpose"]))
        if task.get("purpose") == "seek":
            stats["window_seek"] = self._advance_window_seek(task, rows, stats)
            self._event("window_seek_observed", f"{task['stock']} 第 {task['page']} 页已观察，用于日期定位", {"request_id": rid, "stock": task["stock"], "page": task["page"]})
            entry = stats["window_seek"]
            if entry.get("phase") != "complete" or task["page"] != 1 or entry.get("start_page") != 1:
                return stats
            # The fresh head is already validated and is the chosen entry. Reuse
            # its facts once instead of downloading the same head a second time.
            self.db.execute("UPDATE tasks SET status='superseded' WHERE job=? AND stock=? AND kind='list' AND purpose='forward' AND page=1 AND status='pending'", (task["job"], task["stock"]))
            self.db.execute("UPDATE coverage SET rows=rows+?,earliest=?,latest=?,source_count=?,gaps=? WHERE job=? AND stock=?",
                            (len(rows), earliest, latest, page.source_count, _dump(gaps), task["job"], task["stock"]))
            stats["head_reused_as_forward"] = True
        if task.get("purpose") == "recovery":
            stats["recovery"] = self._advance_recovery(task, rows, stats)
        else:
            previous = self._frontier(task["job"], task["stock"])
            navigation = self._navigation_rows(rows)
            standard = [r for r in navigation if r.get("post_type") == 0]
            if standard and [r["post_publish_time"] for r in standard] != sorted((r["post_publish_time"] for r in standard), reverse=True):
                raise ValueError("前进列表不符合非置顶标准帖发布时间降序")
            entry = self._seek_state(task["job"], task["stock"])
            if entry and entry.get("phase") == "complete" and not entry.get("entry_verified"):
                if (task["page"] > 1 and (not rows or (standard and max(r["post_publish_time"] for r in standard) < entry["target_time"]))):
                    stats["window_seek"] = self._begin_window_seek(task["job"], task["stock"], reason="entry_shifted")
                    return stats
                if rows and not standard:
                    raise ValueError("日期定位入口缺少非置顶标准帖，不能确认窗口上边界")
                entry["entry_verified"] = True
                entry["entry_request_id"] = rid
                self._save_seek_state(entry)
            previous_navigation = self._navigation_rows(previous["rows"]) if previous else []
            previous_ids = {str(r["post_id"]) for r in previous_navigation}
            current_ids = {str(r["post_id"]) for r in navigation}
            if previous and current_ids and not current_ids - previous_ids:
                self._begin_recovery(task["job"], task["stock"], previous, "forward_no_progress")
                self._recovery_gap(self._recovery(task["job"], task["stock"]), "forward_no_new_ids", "下一页没有新增 ID，停止盲目 page+1 并重新定位")
                return stats
            previous_standard = [r for r in previous_navigation if r.get("post_type") == 0]
            previous_count = previous.get("source_count") if previous else None
            if previous and previous_count is None:
                prior = self.db.execute("SELECT source_count FROM page_observations WHERE request_id=?", (previous.get("request_id"),)).fetchone()
                previous_count = prior[0] if prior else None
            shrink = previous_count is not None and page.source_count is not None and page.source_count < previous_count
            shifted_newer = (previous_standard and standard and min(r["post_publish_time"] for r in standard)
                             > min(r["post_publish_time"] for r in previous_standard))
            if previous and (shrink or shifted_newer):
                self._begin_recovery(task["job"], task["stock"], previous, "source_count_decrease" if shrink else "forward_time_shift")
                return stats
            terminal = "date_boundary_confirmed" if boundary else "source_exhausted" if exhausted else None
            self.db.execute("UPDATE coverage SET pages=pages+1,under_pages=?,boundary=?,list_complete=0,stop_reason=? WHERE job=? AND stock=?",
                            (under, int(boundary), terminal, task["job"], task["stock"]))
            if rows:
                frontier = {"page": task["page"], "rows": rows, "request_id": rid, "source_count": page.source_count, "reconciled": False,
                            "anchor_page": previous["page"] if previous else task["page"],
                            "anchor_rows": previous["rows"] if previous else rows, "terminal": terminal}
                self._save_frontier(task["job"], task["stock"], frontier)
                if terminal or (cov["pages"] + 1) % 25 == 0 or not standard:
                    self._begin_recovery(task["job"], task["stock"], frontier,
                                         "terminal_recheck" if terminal else "periodic_recheck" if standard else "nonstandard_page_recheck")
                else:
                    self._enqueue_list(task["job"], task["stock"], task["page"] + 1)
            elif previous:
                previous["terminal"] = terminal
                self._save_frontier(task["job"], task["stock"], previous)
                self._begin_recovery(task["job"], task["stock"], previous, "source_tail_recheck")
            else:
                self.db.execute("UPDATE coverage SET list_complete=1 WHERE job=? AND stock=?", (task["job"], task["stock"]))
        self._event("list_acquired", f"{task['stock']} 第 {task['page']} 页已校验并入隔离队列",
                    {"request_id": rid, "rows": len(rows), "new_ids": len(set(ids) - known), "boundary_confirmed": boundary})
        return stats

    def _accept_detail(self, rid, task, html, probe_only=False):
        post = self.db.execute("SELECT * FROM http_posts WHERE post_id=?", (task["post_id"],)).fetchone()
        if not post:
            raise ValueError("详情缺少已获取的列表来源")
        p = urlparse(task["url"])
        match = re.fullmatch(r"/news,[^,/]+,([0-9]+)\.html", p.path)
        if p.hostname != "guba.eastmoney.com" or not match or match[1] != task["post_id"]:
            raise ValueError("详情响应 URL 的源 ID 不匹配")
        detail = guba.parse_detail_page(html)
        item = _item_load(post["item"])
        merged = guba.merge_list_and_detail(item, detail)
        if detail.source_item_id != task["post_id"]:
            raise ValueError("详情源 ID 不匹配")
        payload = guba._embedded_json(html, "post_article")
        if detail.published_at.strftime("%Y-%m-%d %H:%M:%S") != payload["post_publish_time"]:
            raise ValueError("详情发布时间格式不完整")
        if probe_only:
            return
        data = json.loads(post["item"])
        data["source_metadata"] = detail_body_metadata(merged["source_metadata"], title=item.title,
                                                      trigger=detail_enrichment_trigger(item.title))
        request = self.db.execute("SELECT finished FROM requests WHERE id=?", (rid,)).fetchone()
        store = simple_store_adapter(self.db, self.data_dir / "collector.db")
        if not store.update_content(guba.SOURCE, task["post_id"], detail.content, updated_at=_utc(request["finished"])):
            raise ValueError("详情对应的原 posts 行缺失，不能将正文状态标为完成；请先本地修复")
        metadata_payload = dict(payload)
        metadata_payload.pop("post_content", None)
        self.db.execute("UPDATE http_post_state SET status='complete',detail_request=?,detail_payload=?,item=?,content_source=? WHERE post_id=?",
                        (rid, _dump(metadata_payload), _dump(data), DETAIL_BODY_CONTENT_SOURCE, task["post_id"]))
        self._event("detail_acquired", "正文已完整获取" if detail.content else "来源返回合法空正文",
                    {"request_id": rid, "post_id": task["post_id"], "characters": len(detail.content)})

    def _finish_job(self):
        self._set("state", "completed")
        self._set("reason", "任务遍历结束；覆盖缺口和缺正文需单独查看，实验数据不会自动进入模型库")
        self._end_segment("completed")
        self._event("completed", self._get("reason"))

    def status(self):
        with self._mutex:
            job_id, config = self._get("job_id"), self._config()
            stocks = config["stocks"] if config else []
            stock_args = ",".join("?" for _ in stocks) or "NULL"
            start_epoch, end_epoch = self._window_epochs(config)
            pub_epoch = self._publication_epoch_sql()
            job = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            counts = self.db.execute("SELECT COUNT(*) AS attempts,SUM(network_attempted=1) AS source_attempts,"
                                     "SUM(outcome='real_data' AND kind='list' AND (purpose='forward' OR (purpose='seek' AND json_extract(analysis,'$.list_observation.head_reused_as_forward')=1))) AS list_pages,"
                                     "SUM(kind='list' AND purpose='seek') AS seek_requests,"
                                     "SUM(kind='list' AND purpose='recovery') AS calibration_requests,"
                                     "SUM(outcome='real_data' AND kind='list' AND purpose='recovery') AS calibration_pages,"
                                     "SUM(outcome NOT IN ('real_data','redirect','detail_unavailable','reserved')) AS failures FROM requests WHERE job=?", (job_id,)).fetchone()
            post_counts = self.db.execute("SELECT COUNT(*) AS unique_posts,SUM(status='complete' AND typeof(content)='text') AS body_complete,"
                                         "SUM(status='pending') AS pending,SUM(status='removed') AS removed,SUM(status='complete' AND typeof(content)!='text') AS missing_body,"
                                         "SUM(status='list_only') AS list_only,SUM(status IN ('pending','complete','removed')) AS detail_required,"
                                         "SUM(json_extract(item,'$.source_metadata.list_title_length')>=40) AS detail_policy_required,"
                                         "SUM(status='complete' AND content!='') AS nonempty_body,SUM(status='complete' AND content='') AS source_empty_body "
                                         f"FROM http_posts p WHERE post_id IN (SELECT post_id FROM associations WHERE job=? AND eligible=1 AND stock IN ({stock_args})) "
                                         f"AND {pub_epoch} BETWEEN ? AND ?", (job_id, *stocks, start_epoch, end_epoch)).fetchone()
            aggregate = {k: counts[k] or 0 for k in counts.keys()} | {k: post_counts[k] or 0 for k in post_counts.keys()}
            flagged_posts = self.db.execute(f"SELECT COUNT(DISTINCT post_id) FROM associations WHERE job=? AND eligible=1 AND stock IN ({stock_args})",
                                           (job_id, *stocks)).fetchone()[0]
            aggregate["excluded_posts"] = max(0, flagged_posts - aggregate["unique_posts"])
            aggregate["total_attempts"] = self.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
            aggregate["total_source_attempts"] = self.db.execute("SELECT COALESCE(SUM(network_attempted=1),0) FROM requests").fetchone()[0]
            coverage = []
            for row in self.db.execute(f"SELECT * FROM coverage WHERE job=? AND stock IN ({stock_args}) ORDER BY stock", (job_id, *stocks)).fetchall():
                c = dict(row)
                c["gaps"] = json.loads(c["gaps"])
                c["list_complete"], c["date_boundary_reached"] = bool(c["list_complete"]), bool(c.pop("boundary"))
                n = self.db.execute("SELECT SUM(p.status IN ('pending','complete','removed')) AS required,COUNT(*) AS observed,"
                                    "SUM(p.status='list_only') AS list_only,SUM(json_extract(p.item,'$.source_metadata.list_title_length')>=40) AS policy_required,"
                                    "SUM(p.status='complete' AND typeof(p.content)='text') AS complete,SUM(p.status='pending') AS pending,SUM(p.status='removed') AS removed,SUM(p.status='complete' AND typeof(p.content)!='text') AS missing_body,"
                                    f"MIN({pub_epoch}) AS earliest_epoch,MAX({pub_epoch}) AS latest_epoch "
                                    "FROM associations a JOIN http_posts p ON p.post_id=a.post_id WHERE a.job=? AND a.stock=? AND a.eligible=1 "
                                    f"AND {pub_epoch} BETWEEN ? AND ?", (job_id, c["stock"], start_epoch, end_epoch)).fetchone()
                c["details"] = {k: n[k] or 0 for k in n.keys() if k not in {"earliest_epoch", "latest_epoch"}}
                c["requested_window"] = {"from_date": config["from_date"], "to_date": config["to_date"]} if config else None
                c["post_time_range"] = {"earliest": self._source_epoch_time(n["earliest_epoch"]), "latest": self._source_epoch_time(n["latest_epoch"])}
                c["forward_time_range"] = {"earliest": c["earliest"], "latest": c["latest"]}
                nav = self.db.execute("SELECT MIN(earliest) AS earliest,MAX(latest) AS latest FROM page_observations WHERE job=? AND stock=?",
                                      (job_id, c["stock"])).fetchone()
                c["navigation_time_range"] = {"earliest": nav["earliest"], "latest": nav["latest"]}
                flagged = self.db.execute("SELECT COUNT(*) FROM associations WHERE job=? AND stock=? AND eligible=1", (job_id, c["stock"])).fetchone()[0]
                c["excluded_posts"] = max(0, flagged - c["details"]["observed"])
                c["details_complete"] = c["details"]["pending"] == 0 and c["details"]["removed"] == 0 and c["details"]["missing_body"] == 0
                c["proof_level"] = "observed_pages_only"
                rec = self._recovery(job_id, c["stock"])
                c["recovery"] = self._recovery_json(rec)
                frontier = self._frontier(job_id, c["stock"])
                c["reconciliation_complete"] = bool(frontier and frontier.get("reconciled") and rec and rec["phase"] == "complete" and not rec["time_fallback"] and rec.get("time_order_verified", True))
                seek = self._seek_state(job_id, c["stock"])
                c["window_seek"] = {key: seek.get(key) for key in ("job", "stock", "phase", "target_time", "probes", "current_page", "start_page", "reason", "error", "completion_reason")} if seek else None
                c["pagination_overlap"] = self.db.execute("SELECT COALESCE(SUM(overlap),0) FROM page_observations WHERE job=? AND stock=?", (job_id, c["stock"])).fetchone()[0]
                if c["details"]["removed"]:
                    c["gaps"].append({"kind": "details_unavailable", "count": c["details"]["removed"]})
                if c["details"]["missing_body"]:
                    c["gaps"].append({"kind": "local_body_missing", "count": c["details"]["missing_body"]})
                coverage.append(c)
            started = job["started"] if job else None
            first = self.db.execute("SELECT MIN(started) FROM requests WHERE job=?", (job_id,)).fetchone()[0]
            last = self.db.execute("SELECT MAX(finished) FROM requests WHERE job=?", (job_id,)).fetchone()[0]
            segment = self.db.execute("SELECT * FROM run_segments WHERE id=?", (self._get("segment_id"),)).fetchone()
            continuous = max(0, (segment["ended"] if segment["ended"] is not None else self.clock()) - segment["started"]) if segment else 0
            effective_to = self._get("effective_to_epoch")
            lifecycle = self.db.execute("SELECT revision,archived FROM job_lifecycle WHERE job=?", (job_id,)).fetchone()
            result = {"version": VERSION, "instance_id": self._get("instance_id"), "state": self._get("state"), "reason": self._get("reason"),
                      "job": {"id": job_id, "config": config, "created_at": _iso(job["created"]), "started_at": _iso(started),
                              "effective_to": _iso(effective_to), "effective_to_shanghai": datetime.fromtimestamp(effective_to, guba.SHANGHAI).isoformat(),
                              "revision": lifecycle["revision"] if lifecycle else 1, "archived": bool(lifecycle and lifecycle["archived"] is not None)} if job else None,
                      "config": config, "current": self._target_json(self._target()), "aggregate": aggregate,
                      "next_request_at": _iso(self._get("next_due")) if self._get("next_due") else None,
                      "next_request_epoch": self._get("next_due", 0), "server_time": _iso(self.clock()),
                      "runtime_seconds": continuous, "continuous_run_seconds": continuous,
                      "run_started_at": _iso(segment["started"]) if segment else None,
                      "run_stopped_at": _iso(segment["ended"]) if segment else None,
                      "job_age_seconds": max(0, self.clock() - job["created"]) if job else 0,
                      "observed_span_seconds": max(0, last - first) if first is not None and last is not None else 0,
                      "last_success": _iso(self._get("last_success")), "block_evidence": self._get("block_evidence"),
                      "active_halt": self._get("active_halt"), "probe_pending": self._get("probe", False),
                      "network_retry": self._get("network_retry") if self._network_retry_target() else None,
                      "request_inflight": self._inflight, "coverage": coverage, "research_only": True,
                      "window_seek": [c["window_seek"] for c in coverage if c.get("window_seek")],
                      "calibration_policy": {"every_forward_pages": 25, "list_delay_seconds": 300,
                                             "list_delay_excludes_configured_interval": True,
                                             "ordinary_check": "stable_last_forward_page", "max_validated_requests": MAX_RECOVERY_REQUESTS,
                                             "max_completed_passes": MAX_RECOVERY_PASSES, "proof_scope": "navigation_or_recent_anchor_interval_only"},
                      "content_policy": self._get("content_policy"),
                      "http_request_profile": {"version": REQUEST_PROFILE, "user_agent": UA,
                                               "list_first_page_referer": GOOGLE_REFERER,
                                               "list_next_page_referer": "previous_page",
                                               "detail_referer_probability": {"observed_list": 0.6, "google": 0.3, "baidu": 0.1},
                                               "search_referers": {"google": GOOGLE_REFERER, "baidu": BAIDU_REFERER}},
                      "data_storage": self._get("data_storage"), "storage_halt": self._get("storage_halt"),
                      "model_database_eligible": False, "dynamic_challenge_detection": "unobserved: no JavaScript execution"}
            result["rate_audit"] = tail_audit(self)
            result["proxy"] = self.proxy.status()
            result["observed_work_complete"] = bool(coverage) and all(c["date_boundary_reached"] and c["details_complete"] and not c["gaps"] for c in coverage)
            result["recovery"] = [c["recovery"] for c in coverage if c["recovery"]]
            result["reconciliation_complete"] = bool(coverage) and all(c["reconciliation_complete"] for c in coverage)
            result["coverage_complete"] = False
            result["coverage_proof"] = "observed_pages_only"
            result["needs_review"] = True
            result["coverage_limitations"] = ["日期定位跳页不计作连续覆盖；定期校准只验证实际回扫的近期锚点区间，不能证明此前整个连续前进区间无遗漏", "来源可能删除或不再提供历史数据，锚点丢失时保留显式缺口"]
            if self._stock_runtime_ready:
                result.update(self._stock_summary())
                result["probe_pending"] = result["probe_pending"] or self._inflight_probe
                if self._inflight_task:
                    result["current"] = self._target_json(self._inflight_task)
                runtime_map = {r["stock"]: r for r in result["stock_runtimes"]}
                for c in coverage:
                    runtime = runtime_map[c["stock"]]
                    runtime["request_inflight"] = self._inflight_stock == (runtime["job"], runtime["stock"])
                    runtime["probe_pending"] = runtime["probe"] or (runtime["request_inflight"] and self._inflight_probe)
                    c["runtime"], c["status"] = runtime, runtime["state"]
                    c["task_proxy"] = self.task_proxies.status(str(runtime["job"]), runtime["stock"])
                    containerized = Path("/.dockerenv").exists()
                    c["task_proxy"]["suggested_listen_address"] = "0.0.0.0" if containerized else "127.0.0.1"
                    c["task_proxy"]["suggested_endpoint"] = "http://" + ("host.docker.internal" if containerized else "127.0.0.1") + ":" + str(c["task_proxy"]["suggested_port"])
                for runtime in result["detached_stock_runtimes"]:
                    runtime["request_inflight"] = self._inflight_stock == (runtime["job"], runtime["stock"])
                    runtime["probe_pending"] = runtime["probe"] or (runtime["request_inflight"] and self._inflight_probe)
                    runtime["task_proxy"] = self.task_proxies.status(str(runtime["job"]), runtime["stock"])
                    containerized = Path("/.dockerenv").exists()
                    runtime["task_proxy"]["suggested_listen_address"] = "0.0.0.0" if containerized else "127.0.0.1"
                    runtime["task_proxy"]["suggested_endpoint"] = "http://" + ("host.docker.internal" if containerized else "127.0.0.1") + ":" + str(runtime["task_proxy"]["suggested_port"])
                result["proxy"]["auto_suspended"] = all(r["proxy_auto_suspended"] for r in result["stock_runtimes"])
            return result

    def _window_epochs(self, config=None):
        config = self._config() if config is None else config
        if not config:
            return 0, -1
        start = datetime.fromisoformat(config["from_date"] + "T00:00:00+08:00").timestamp()
        # Source timestamps have second precision. Floor the upper cutoff to
        # avoid SQLite rounding 23:59:59.999999 into the following day.
        return math.floor(start), math.floor(self._get("effective_to_epoch"))

    @staticmethod
    def _publication_epoch_sql():
        value = "json_extract(p.item,'$.published_at')"
        # Operational item times are complete ISO datetimes with an explicit
        # offset. Do not let SQLite treat a legacy naive value as UTC.
        return (f"CASE WHEN substr({value},11,1)='T' "
                f"AND strftime('%Y-%m-%dT%H:%M:%S',substr({value},1,19),'+0 seconds')=substr({value},1,19) "
                f"AND (substr({value},-1)='Z' OR substr({value},-6) GLOB '[+-][0-2][0-9]:[0-5][0-9]') "
                f"THEN CAST(strftime('%s',{value}) AS INTEGER) END")

    @staticmethod
    def _source_epoch_time(epoch):
        return datetime.fromtimestamp(epoch, guba.SHANGHAI).strftime("%Y-%m-%d %H:%M:%S") if epoch is not None else None

    @staticmethod
    def _recovery_json(rec):
        if not rec:
            return None
        keys = ("job", "stock", "phase", "reason", "anchor_page", "current_page", "passes", "drift_count", "new_posts", "proof_level",
                "anchor_min", "anchor_max", "target_time", "time_fallback", "time_order_verified", "started_at", "completed_at", "verified_requests",
                "strategy", "trigger_reason", "fallback_reason", "validated_requests", "completed_passes", "max_requests", "max_passes")
        return {k: rec.get(k) for k in keys}

    def requests(self, limit=50):
        limit = self._limit(limit)
        with self._mutex:
            result = []
            for row in self.db.execute("SELECT * FROM requests ORDER BY id DESC LIMIT ?", (limit,)).fetchall():
                d = dict(row)
                for key in ("headers", "analysis"):
                    d[key] = json.loads(d[key]) if d[key] else None
                d["started_at"], d["finished_at"] = _iso(d["started"]), _iso(d["finished"])
                d["instance_id"] = self._get("instance_id")
                result.append(d)
            return result

    def events(self, limit=50):
        limit = self._limit(limit)
        with self._mutex:
            return [{**dict(r), "created_at": _iso(r["created"]), "evidence": json.loads(r["evidence"]) if r["evidence"] else None}
                    for r in self.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    @staticmethod
    def _limit(value):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1000:
            raise ValueError("limit 必须为 1–1000 的整数")
        return value

    def raw_posts(self, limit=100, offset=0):
        limit = self._limit(limit)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset 必须为非负整数")
        with self._mutex:
            config = self._config()
            stocks = config["stocks"] if config else []
            stock_args = ",".join("?" for _ in stocks) or "NULL"
            start_epoch, end_epoch = self._window_epochs(config)
            pub_epoch = self._publication_epoch_sql()
            rows = self.db.execute(f"SELECT p.* FROM http_posts p WHERE post_id IN (SELECT post_id FROM associations WHERE job=? AND eligible=1 AND stock IN ({stock_args})) "
                                   f"AND {pub_epoch} BETWEEN ? AND ? ORDER BY p.rowid LIMIT ? OFFSET ?",
                                   (self._get("job_id"), *stocks, start_epoch, end_epoch, limit, offset)).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                for key in ("item", "source_row", "detail_payload"):
                    d[key] = json.loads(d[key]) if d[key] else None
                if row["status"] == "complete":
                    # Re-read evidence for this API response; a prior projection
                    # cache cannot conceal a later raw-file change.
                    self.compatible_store._raw_cache.clear()
                    _, html = self.compatible_store._raw_request(self, row["detail_request"])
                    payload = guba._embedded_json(html, "post_article")
                    detail = guba.parse_detail_page(html)
                    if detail.source_item_id != row["post_id"] or detail.content != row["content"]:
                        raise RuntimeError("原 posts 正文与 SHA 校验后的详情响应不一致")
                    d["detail_payload"] = payload
                    self.compatible_store._raw_cache.clear()
                d["source_metadata"] = d["item"].get("source_metadata") or {}
                d["detail_enrichment_trigger"] = detail_enrichment_trigger(d["item"].get("title"))
                d["detail_policy_required"] = d["detail_enrichment_trigger"] is not None
                d["detail_required"] = d["status"] in {"pending", "complete", "removed"}
                d["associations"] = [dict(r) for r in self.db.execute("SELECT job,stock,request_id FROM associations WHERE post_id=? AND eligible=1", (row["post_id"],)).fetchall()]
                d["raw_refs"] = [dict(r) for r in self.db.execute("SELECT id,raw_ref,sha256,outcome FROM requests WHERE id IN (?,?)", (row["list_request"], row["detail_request"])).fetchall()]
                d.update({"source": "eastmoney_guba", "source_item_id": row["post_id"], "schema_version": VERSION, "instance_id": self._get("instance_id"),
                          "body_complete": row["status"] == "complete" and row["content_source"] == DETAIL_BODY_CONTENT_SOURCE and isinstance(row["content"], str), "research_only": True,
                          "model_database_eligible": False, "dataset_complete": False,
                          "coverage_proof": "observed_pages_only", "job_id": self._get("job_id"),
                          "effective_to": _iso(self._get("effective_to_epoch"))})
                result.append(d)
            return result

    def close(self):
        with self._mutex:
            if self._closed:
                return
            if self._inflight:
                raise RuntimeError("当前请求仍在执行，请暂停并等待 worker 退出后关闭")
            with self.db:
                if self._stock_runtime_ready:
                    for runtime in self._stock_runtimes(include_detached=True):
                        with self._stock_context(runtime["stock"], runtime["job"]):
                            self._suspend_proxy_recovery()
                            self._set("probe", False)
                            if runtime["state"] == "running":
                                self._set("state", "blocked" if runtime["active_halt"] in {"challenge", "http_401", "http_403", "http_429"} else "error" if runtime["active_halt"] else "paused")
                                if not runtime["active_halt"]:
                                    self._set("reason", "进程关闭后安全暂停，等待继续")
                                    self._set("resume_recovery", True)
                            self._end_segment("process_closed")
                    self._publish_stock_summary()
                elif self._get("state") == "running":
                    self._set("state", "paused")
                    self._set("reason", "进程关闭后安全暂停，等待继续")
                self._end_segment("process_closed")
                self._set("probe", False)
            self.db.close()
            fcntl.flock(self._lock_file, fcntl.LOCK_UN)
            self._lock_file.close()
            self._closed = True
