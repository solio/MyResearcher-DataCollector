"""Isolated, fail-closed HTTP experiment. No production database writes.

The production parser defines source fields; the raw static challenge detector
also recognises the observed overlay shape from the 2026-09-30 HTTP research.
Neither parser renders JavaScript or establishes a server-side blacklist.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import sqlite3
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
from rate_audit import tail_audit
from compatible_store import CompatibleDataStore, _utc
from unified_store import LAYOUT, guard_runtime_path, initialize_schema, simple_store_adapter

VERSION = "http-backfill.v5"
MAX_BODY = 16 * 1024 * 1024
UA = "MyResearcher-HTTP-Backfill/1.0 (public-source research)"
REDIRECTS = {301, 302, 303, 307, 308}
SAFE_HEADERS = {"content-type", "content-length", "content-encoding", "location", "retry-after", "date", "server"}


@dataclass
class Response:
    status: int | None
    body: bytes
    headers: dict
    url: str | None = None
    error: str | None = None


def _dump(value):
    return json.dumps(value, ensure_ascii=False, default=lambda x: x.isoformat(), separators=(",", ":"))


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch is not None else None


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


def fetch(url, client):
    """Exactly one anonymous GET, no redirects or retries in either client."""
    if not _allowed(url):
        raise ValueError("请求地址必须属于公开 HTTPS Eastmoney 来源")
    if client == "urllib":
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
        try:
            try:
                response = opener.open(request, timeout=25)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                body = response.read(MAX_BODY + 1)
                headers = {k.lower(): v for k, v in response.headers.items() if k.lower() in SAFE_HEADERS}
                return Response(response.status, body, headers, url,
                                "响应超出 16 MiB，内容可能不完整" if len(body) > MAX_BODY else None)
        except Exception as exc:
            return Response(None, b"", {}, url, f"{type(exc).__name__}: {exc}")
    if client != "curl":
        raise ValueError("client 只能是 curl 或 urllib")
    with tempfile.TemporaryDirectory(prefix="http-backfill-") as tmp:
        hp, bp = Path(tmp) / "headers", Path(tmp) / "body"
        command = ["curl", "-q", "--silent", "--show-error", "--connect-timeout", "10", "--max-time", "25",
                   "--max-filesize", str(MAX_BODY), "--noproxy", "*", "--proto", "=https",
                   "--dump-header", str(hp), "--output", str(bp), "--write-out", "%{http_code}",
                   "--user-agent", UA, "--header", "Accept: text/html", url]
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
                            (proc.stderr.strip() or f"curl exit {proc.returncode}") if proc.returncode else None)
        except Exception as exc:
            return Response(None, bp.read_bytes() if bp.exists() else b"", {}, url, f"{type(exc).__name__}: {exc}")


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
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval < 60:
        raise ValueError("请求间隔必须至少 60 秒")
    client = config.get("client", "curl")
    if client not in {"curl", "urllib"}:
        raise ValueError("client 只能是 curl 或 urllib")
    return {"stocks": stocks, "from_date": config["from_date"], "to_date": config["to_date"],
            "interval_seconds": interval, "client": client}


class Engine(LifecycleMixin):
    def __init__(self, data_dir, transport=None, clock=None):
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
        self._closed = False
        self.transport, self.clock = transport or fetch, clock or time.time
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        with self.db:
            initialize_schema(self.db)
        self._init_lifecycle_schema()
        with self.db:
            if self._get("instance_id") is None:
                self._set("instance_id", str(uuid.uuid4()))
            if self._get("state") is None:
                self._set("state", "paused")
                self._set("reason", "尚未创建任务")
                self._set("next_due", 0)
            interrupted = self.db.execute("SELECT * FROM requests WHERE outcome='reserved'").fetchall()
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
            self._migrate_content_policy()
            self._migrate_frontiers()
            if self._get("job_id") and self._get("state") != "completed":
                self._mark_recovery_needed("process_restart")
        self.compatible_store = CompatibleDataStore(self.data_dir, self._get("instance_id"))
        self._sync_storage()

    def _get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _set(self, key, value):
        self.db.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, _dump(value)))

    def _event(self, kind, message, evidence=None):
        self.db.execute("INSERT INTO events(job,created,kind,message,evidence) VALUES(?,?,?,?,?)",
                        (self._get("job_id"), self.clock(), kind, message, _dump(evidence) if evidence is not None else None))

    def _begin_segment(self, probe=False):
        count = self.db.execute("SELECT COUNT(*) FROM requests WHERE job=?", (self._get("job_id"),)).fetchone()[0]
        sid = self.db.execute("INSERT INTO run_segments(job,started,probe,attempts_at_start) VALUES(?,?,?,?)",
                              (self._get("job_id"), self.clock(), int(probe), count)).lastrowid
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
        inflight = self.db.execute("SELECT * FROM tasks WHERE job=? AND status='inflight' ORDER BY id LIMIT 1", (self._get("job_id"),)).fetchone()
        if inflight:
            return inflight
        forced = self.db.execute("SELECT * FROM tasks WHERE job=? AND kind='list' AND purpose='recovery' AND status='pending' ORDER BY id LIMIT 1", (self._get("job_id"),)).fetchone()
        if forced:
            rec = self._recovery(forced["job"], forced["stock"])
            if rec and rec.get("force_first"):
                return forced
        return self.db.execute("SELECT * FROM tasks WHERE job=? AND status IN ('pending','inflight') "
                               "ORDER BY CASE WHEN status='inflight' THEN 0 WHEN kind='detail' THEN 1 WHEN purpose='recovery' THEN 2 ELSE 3 END,id LIMIT 1",
                               (self._get("job_id"),)).fetchone()

    @staticmethod
    def _target_json(row):
        return {k: row[k] for k in ("kind", "stock", "page", "post_id", "url", "purpose")} if row else None

    def create_job(self, config):
        config = _validate_config(config)
        with self._mutex, self.db:
            if self._inflight or self._get("active_halt") or self._get("storage_halt"):
                raise RuntimeError("已有未解决的暂停原因；不能新建任务绕过，请先单次探测")
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
            self._set("effective_to_epoch", effective_to)
            self._set("segment_id", None)
            self._set("last_success", None)
            for stock in config["stocks"]:
                self.db.execute("INSERT INTO coverage(job,stock) VALUES(?,?)", (job, stock))
                self._enqueue_list(job, stock, 1)
            self._register_job_lifecycle(job)
            self._event("job_created", "创建隔离回补任务", config)
        return self.status()

    def _enqueue_list(self, job, stock, page):
        url = f"https://guba.eastmoney.com/list,{stock},f" + (f"_{page}" if page > 1 else "") + ".html"
        self.db.execute("INSERT INTO tasks(job,kind,stock,page,url,original_url) VALUES(?,'list',?,?,?,?)", (job, stock, page, url, url))

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
            item = _item_load(row[0])
            return cfg["from_date"] <= item.published_at.date().isoformat() <= cfg["to_date"] and item.published_at.timestamp() <= self._get("effective_to_epoch")
        return True

    @staticmethod
    def _frontier_rows(rows):
        return [r for r in rows if not (type(r.get("post_top_status")) is int and r["post_top_status"] != 0)]

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

    def _mark_recovery_needed(self, reason):
        job = self._get("job_id")
        cfg = self._config()
        if not job or not cfg:
            return
        for stock in cfg["stocks"]:
            frontier = self._frontier(job, stock)
            if frontier:
                old = self._recovery(job, stock)
                try:
                    self._begin_recovery(job, stock, frontier, reason, force_first=True,
                                         original=old if old and old.get("phase") != "complete" else None)
                except ValueError as exc:
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
        rec.update({"generation": generation, "phase": "seek", "reason": reason, "current_page": rec["anchor_page"],
                    "force_first": force_first, "passes": 0, "last_signature": None, "pass_members": [],
                    "pass_pages": [], "visited": {}, "anchor_seen": False, "started_at": _iso(self.clock())})
        self._save_recovery(rec)
        if not rec.get("time_order_verified", True):
            self._recovery_gap(rec, "time_order_unverified", "当前锚点区间含全非标准帖页面，只核对来源 ID，不能用其发布时间证明历史次序")
        self._enqueue_recovery(rec, rec["anchor_page"])
        self._event("recovery_started", "重新定位 ID/发布时间锚点；核对前不推进历史页", {"stock": stock, "reason": reason, "anchor_page": rec["anchor_page"]})

    def _enqueue_recovery(self, rec, page):
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
        if signature == rec["last_signature"] and (rec["anchor_seen"] or rec["time_fallback"]):
            if rec["reason"] == "forward_no_progress" and task["page"] <= rec["forward_page"] and not rec["new_posts"]:
                rec["phase"] = "error"
                self._save_recovery(rec)
                raise ValueError("分页无进展：重新定位后仍未发现更深历史，不能反复猜测下一页")
            rec.update({"phase": "complete", "completed_at": _iso(self.clock()), "end_page": task["page"], "signature": signature,
                        "verified_requests": {"first_pass": rec.get("previous_pass_pages", []), "second_pass": rec["pass_pages"]},
                        "proof_level": "id_interval_time_order_unverified" if not temporal else "time_boundary_with_gap" if rec["time_fallback"] else "two_matching_anchor_interval_observations"})
            self._save_recovery(rec)
            frontier = self._frontier(task["job"], task["stock"])
            frontier.update({"page": task["page"], "rows": rows, "anchor_page": rec["scan_start_page"],
                             "anchor_rows": rec["start_rows"], "reconciled": True, "reconciliation": {"signature": signature, "passes": rec["passes"], "proof_level": rec["proof_level"]}})
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

    def start(self):
        with self._mutex:
            if self._get("storage_halt"):
                self._sync_storage()
                return self.status()
        with self._mutex, self.db:
            if self._inflight:
                raise RuntimeError("当前请求尚在执行，请等待其结果保存后继续")
            if not self._config():
                raise RuntimeError("请先创建任务")
            if self._get("active_halt"):
                raise RuntimeError("当前有未解决的阻断或错误；只能人工单次探测，不能直接继续")
            if self._get("state") == "completed":
                raise RuntimeError("任务已结束，请查看覆盖缺口或创建新任务")
            if self._get("state") == "running":
                return self.status()
            if self._get("resume_recovery"):
                self._mark_recovery_needed("manual_resume")
                self._set("resume_recovery", False)
                if self._get("active_halt"):
                    return self.status()
            self._set("state", "running")
            self._set("reason", "按全局间隔运行")
            self.db.execute("UPDATE jobs SET started=COALESCE(started,?) WHERE id=?", (self.clock(), self._get("job_id")))
            self._begin_segment()
            self._event("started", "开始/继续任务")
        return self.status()

    def pause(self):
        with self._mutex, self.db:
            self._set("probe", False)
            self._set("resume_recovery", True)
            self._end_segment("manual_pause")
            if self._get("state") not in {"blocked", "error", "completed"}:
                self._set("state", "paused")
                self._set("reason", "人工暂停" + ("；当前请求会保留结果" if self._inflight else ""))
            self._event("paused", "人工暂停；不会发起后续请求")
        return self.status()

    def retry(self):
        with self._mutex:
            if self._get("storage_halt"):
                self._sync_storage()
                return self.status()
        with self._mutex, self.db:
            if self._inflight or self._get("probe"):
                raise RuntimeError("请求或单次探测已在执行/排队")
            if self._get("state") == "running":
                raise RuntimeError("请先暂停任务再单次探测")
            if not self._target(probe_target=True):
                raise RuntimeError("没有待请求目标")
            self._set("probe", True)
            self._set("state", "running")
            self._set("reason", "单次探测已排队；遵守原有请求间隔和冷却，结束后暂停")
            self.db.execute("UPDATE jobs SET started=COALESCE(started,?) WHERE id=?", (self.clock(), self._get("job_id")))
            self._begin_segment(probe=True)
            self._event("probe_scheduled", self._get("reason"))
        return self.status()

    def tick(self):
        """Reserve durably, release control lock during I/O, then commit one outcome."""
        with self._mutex, self.db:
            if self._closed or self._inflight or self._get("storage_halt") or self._get("state") != "running" or self.clock() < self._get("next_due", 0):
                return {"attempted": False}
            task = self._target(probe_target=self._get("probe", False))
            if not task:
                self._finish_job()
                return {"attempted": False}
            task = dict(task)
            config, now = self._config(), self.clock()
            probe = self._get("probe", False)
            halted = self._halt_target() if self._get("active_halt") else None
            if halted and probe and halted["id"] == task["id"]:
                config = self._get("halted_config") or self._get("halt_config") or json.loads(self.db.execute("SELECT config FROM jobs WHERE id=?", (task["job"],)).fetchone()[0])
            if config is None:
                raise RuntimeError("请求缺少原任务配置，无法安全探测")
            probe_only = probe and (self._get("halted_probe_only", False) or not self._task_in_active_scope(task))
            self._set("probe", False)
            self._set("next_due", max(now, self._get("next_due", 0)) + config["interval_seconds"])
            rid = self.db.execute("INSERT INTO requests(job,task,kind,stock,page,post_id,url,started,probe,purpose,probe_only) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                  (task["job"], task["id"], task["kind"], task["stock"], task["page"], task["post_id"], task["url"], now, int(probe), task["purpose"], int(probe_only))).lastrowid
            self.db.execute("UPDATE tasks SET status='inflight' WHERE id=?", (task["id"],))
            self._inflight = True
        # A concurrent pause is now effective for the next request; this response
        # remains fully recorded. Transport never recursively requests a redirect.
        try:
            response = self.transport(task["url"], config["client"])
            if not isinstance(response, Response):
                raise ValueError("transport 必须返回 Response")
        except Exception as exc:
            response = Response(None, b"", {}, task["url"], f"{type(exc).__name__}: {exc}")
        with self._mutex:
            try:
                self._record_response(rid, task, response, config, probe, probe_only)
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
                    self.db.execute("UPDATE requests SET outcome='internal_error',finished=?,error=?,network_attempted=1 WHERE id=?",
                                    (self.clock(), f"{type(exc).__name__}: {exc}", rid))
                    if retained is not None:
                        headers = {str(k).lower(): str(v) for k, v in response.headers.items() if str(k).lower() in SAFE_HEADERS} if isinstance(response.headers, dict) else {}
                        self.db.execute("UPDATE requests SET http_status=?,response_bytes=?,sha256=?,raw_ref=?,headers=?,final_url=? WHERE id=?",
                                        (response.status, *retained, _dump(headers), response.url or task["url"], rid))
                    self._set("next_due", max(self._get("next_due", 0), self.clock() + config["interval_seconds"]))
                    self.db.execute("UPDATE tasks SET status='pending' WHERE id=?", (task["id"],))
                    self._halt("error", "internal_error", f"结果保存/解析错误，需要人工处理: {exc}", rid)
            finally:
                self._inflight = False
            self._sync_storage(rid)
            return {"attempted": True, "request_id": rid, "state": self._get("state")}

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
            self.db.execute("UPDATE requests SET finished=?,http_status=?,response_bytes=?,sha256=?,raw_ref=?,headers=?,final_url=?,network_attempted=1 WHERE id=?",
                            (self.clock(), response.status, len(response.body), hashlib.sha256(response.body).hexdigest(),
                             str(raw.relative_to(self.data_dir)), _dump(headers), final_url, rid))
            # A conservative finish-to-next-start gap also prevents a slow fetch
            # from bunching up the next request immediately after it finishes.
            self._set("next_due", max(self._get("next_due", 0), self.clock() + config["interval_seconds"]))
            self.db.execute("UPDATE tasks SET status='pending' WHERE id=?", (task["id"],))
            if response.status in {403, 429}:
                due = self._retry_after(headers.get("retry-after"))
                self._set("next_due", max(self._get("next_due"), due))
                self._outcome(rid, "access_block", response.error, {"http_status": response.status, "retry_after": headers.get("retry-after")})
                self._halt("blocked", "http_" + str(response.status), f"来源返回 HTTP {response.status}；已停止自动请求", rid)
                return
            if response.error:
                self._outcome(rid, "transport_error", response.error)
                self._halt("error", "transport_error", "网络/传输失败: " + response.error, rid)
                return
            if not _allowed(final_url) or final_url != task["url"]:
                self._outcome(rid, "unexpected_final_url", "transport 不得自动重定向或请求外部地址")
                self._halt("error", "unexpected_final_url", "transport 返回意外最终地址，拒绝接受数据", rid)
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
        self.db.execute("UPDATE requests SET outcome=?,error=?,analysis=? WHERE id=?", (outcome, error, _dump(analysis) if analysis else None, rid))

    def _halt(self, state, kind, reason, rid):
        self._set("state", state)
        self._set("active_halt", kind)
        self._set("reason", reason)
        self._end_segment(kind)
        row = self.db.execute("SELECT id,url,http_status,raw_ref,sha256,started,finished FROM requests WHERE id=?", (rid,)).fetchone()
        evidence = dict(row)
        evidence.update({"kind": kind, "reason": reason, "server_blacklist": "unproven"})
        failed = self.db.execute("SELECT task,job FROM requests WHERE id=?", (rid,)).fetchone()
        self._set("halted_task_id", failed["task"])
        self._set("halt_task_id", failed["task"])
        config_row = self.db.execute("SELECT config FROM jobs WHERE id=?", (failed["job"],)).fetchone()
        self._set("halt_config", json.loads(config_row[0]) if config_row else self._config())
        self._set("block_evidence", evidence)
        self._event(kind, reason, evidence)

    def _after_success(self, rid, probe):
        self._set("last_success", self.clock())
        if probe:
            self._set("active_halt", None)
            self._set("halted_task_id", None)
            self._set("halt_task_id", None)
            self._set("halted_probe_only", False)
            self._set("halted_config", None)
            self._set("halt_config", None)
            self._set("state", "paused")
            self._set("reason", "单次探测取得有效来源响应；已暂停，等待继续")
            self._end_segment("probe_success")
            self._event("probe_success", self._get("reason"), {"request_id": rid})
        elif self._get("state") == "running" and not self._target():
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
        self.db.execute("UPDATE requests SET analysis=? WHERE id=?", (_dump({"list_structure_validated": True, "list_observation": stats}), rid))
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
                    exists = self.db.execute("SELECT 1 FROM tasks WHERE job=? AND kind='detail' AND post_id=? AND status IN ('pending','inflight')", (task["job"], pid)).fetchone()
                    if not exists:
                        self.db.execute("INSERT INTO tasks(job,kind,stock,page,post_id,url,original_url) VALUES(?,'detail',?,?,?,?,?)",
                                        (task["job"], task["stock"], task["page"], pid, item.url, item.url))
        earliest = min(times + ([cov["earliest"]] if cov["earliest"] else []), default=None)
        latest = max(times + ([cov["latest"]] if cov["latest"] else []), default=None)
        self.db.execute("UPDATE coverage SET rows=rows+?,earliest=?,latest=?,source_count=?,gaps=? WHERE job=? AND stock=?",
                        (len(rows), earliest, latest, page.source_count, _dump(gaps), task["job"], task["stock"]))
        self.db.execute("INSERT INTO page_observations(request_id,job,stock,page,source_count,rows,new_ids,overlap,id_sha256,earliest,latest,purpose) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (rid, task["job"], task["stock"], task["page"], page.source_count, len(rows),
                         stats["new_ids"], stats["overlap"], stats["ordered_id_sha256"], stats["earliest"], stats["latest"], stats["purpose"]))
        if task.get("purpose") == "recovery":
            stats["recovery"] = self._advance_recovery(task, rows, stats)
        else:
            previous = self._frontier(task["job"], task["stock"])
            if ids and task["page"] > 1 and not set(ids) - known:
                if not previous:
                    raise ValueError("分页没有新增源 ID，且没有可恢复锚点")
                self._begin_recovery(task["job"], task["stock"], previous, "forward_no_progress")
                self._recovery_gap(self._recovery(task["job"], task["stock"]), "forward_no_new_ids", "下一页没有新增 ID，停止盲目 page+1 并重新定位")
                return stats
            terminal = "date_boundary_confirmed" if boundary else "source_exhausted" if exhausted else None
            self.db.execute("UPDATE coverage SET pages=pages+1,under_pages=?,boundary=?,list_complete=0,stop_reason=? WHERE job=? AND stock=?",
                            (under, int(boundary), terminal, task["job"], task["stock"]))
            if rows:
                frontier = {"page": task["page"], "rows": rows, "request_id": rid, "reconciled": False,
                            "anchor_page": previous["page"] if previous else task["page"],
                            "anchor_rows": previous["rows"] if previous else rows, "terminal": terminal}
                self._save_frontier(task["job"], task["stock"], frontier)
                self._begin_recovery(task["job"], task["stock"], frontier, "details_completed_recheck")
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
            job = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            counts = self.db.execute("SELECT COUNT(*) AS attempts,SUM(outcome='real_data' AND kind='list' AND purpose='forward') AS list_pages,"
                                     "SUM(kind='list' AND purpose='recovery') AS calibration_requests,"
                                     "SUM(outcome='real_data' AND kind='list' AND purpose='recovery') AS calibration_pages,"
                                     "SUM(outcome NOT IN ('real_data','redirect','detail_unavailable','reserved')) AS failures FROM requests WHERE job=?", (job_id,)).fetchone()
            post_counts = self.db.execute("SELECT COUNT(*) AS unique_posts,SUM(status='complete' AND typeof(content)='text') AS body_complete,"
                                         "SUM(status='pending') AS pending,SUM(status='removed') AS removed,SUM(status='complete' AND typeof(content)!='text') AS missing_body,"
                                         "SUM(status='list_only') AS list_only,SUM(status IN ('pending','complete','removed')) AS detail_required,"
                                         "SUM(json_extract(item,'$.source_metadata.list_title_length')>=40) AS detail_policy_required,"
                                         "SUM(status='complete' AND content!='') AS nonempty_body,SUM(status='complete' AND content='') AS source_empty_body "
                                         "FROM http_posts WHERE post_id IN (SELECT post_id FROM associations WHERE job=? AND eligible=1)", (job_id,)).fetchone()
            aggregate = {k: counts[k] or 0 for k in counts.keys()} | {k: post_counts[k] or 0 for k in post_counts.keys()}
            aggregate["total_attempts"] = self.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
            coverage = []
            for row in self.db.execute("SELECT * FROM coverage WHERE job=? ORDER BY stock", (job_id,)).fetchall():
                c = dict(row)
                c["gaps"] = json.loads(c["gaps"])
                c["list_complete"], c["date_boundary_reached"] = bool(c["list_complete"]), bool(c.pop("boundary"))
                n = self.db.execute("SELECT SUM(p.status IN ('pending','complete','removed')) AS required,COUNT(*) AS observed,"
                                    "SUM(p.status='list_only') AS list_only,SUM(json_extract(p.item,'$.source_metadata.list_title_length')>=40) AS policy_required,"
                                    "SUM(p.status='complete' AND typeof(p.content)='text') AS complete,SUM(p.status='pending') AS pending,SUM(p.status='removed') AS removed,SUM(p.status='complete' AND typeof(p.content)!='text') AS missing_body "
                                    "FROM associations a JOIN http_posts p ON p.post_id=a.post_id WHERE a.job=? AND a.stock=? AND a.eligible=1", (job_id, c["stock"])).fetchone()
                c["details"] = {k: n[k] or 0 for k in n.keys()}
                c["details_complete"] = c["details"]["pending"] == 0 and c["details"]["removed"] == 0 and c["details"]["missing_body"] == 0
                c["proof_level"] = "observed_pages_only"
                rec = self._recovery(job_id, c["stock"])
                c["recovery"] = self._recovery_json(rec)
                c["reconciliation_complete"] = bool(rec and rec["phase"] == "complete" and not rec["time_fallback"] and rec.get("time_order_verified", True))
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
                      "request_inflight": self._inflight, "coverage": coverage, "research_only": True,
                      "content_policy": self._get("content_policy"),
                      "data_storage": self._get("data_storage"), "storage_halt": self._get("storage_halt"),
                      "model_database_eligible": False, "dynamic_challenge_detection": "unobserved: no JavaScript execution"}
            result["rate_audit"] = tail_audit(self)
            result["observed_work_complete"] = bool(coverage) and all(c["date_boundary_reached"] and c["details_complete"] and not c["gaps"] for c in coverage)
            result["recovery"] = [c["recovery"] for c in coverage if c["recovery"]]
            result["reconciliation_complete"] = bool(coverage) and all(c["reconciliation_complete"] for c in coverage)
            result["coverage_complete"] = False
            result["coverage_proof"] = "observed_pages_only"
            result["needs_review"] = True
            result["coverage_limitations"] = ["已按源 ID/时间锚点回扫并比较两轮局部区间；不能证明来源全部历史完整或发现已永久消失的未观察帖子", "来源可能删除或不再提供历史数据，锚点丢失时保留显式缺口"]
            return result

    @staticmethod
    def _recovery_json(rec):
        if not rec:
            return None
        keys = ("job", "stock", "phase", "reason", "anchor_page", "current_page", "passes", "drift_count", "new_posts", "proof_level",
                "anchor_min", "anchor_max", "target_time", "time_fallback", "time_order_verified", "started_at", "completed_at", "verified_requests")
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
            rows = self.db.execute("SELECT * FROM http_posts WHERE post_id IN (SELECT post_id FROM associations WHERE job=? AND eligible=1) ORDER BY rowid LIMIT ? OFFSET ?",
                                   (self._get("job_id"), limit, offset)).fetchall()
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
                if self._get("state") == "running":
                    self._set("state", "paused")
                    self._set("reason", "进程关闭后安全暂停，等待继续")
                self._end_segment("process_closed")
                self._set("probe", False)
            self.db.close()
            fcntl.flock(self._lock_file, fcntl.LOCK_UN)
            self._lock_file.close()
            self._closed = True
