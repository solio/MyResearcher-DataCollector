"""Durable stock controls around one serialized, node-paced HTTP worker.

The context is thread-local for the full dispatch lifetime. It never replaces
the engine's proxy object or copies node storage and provider-account budgets.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import re
import threading


STOCK_KEYS = frozenset({
    "state", "reason", "next_due", "probe", "active_halt", "block_evidence",
    "halted_task_id", "halt_task_id", "halted_config", "halt_config",
    "halted_probe_only", "network_retry", "resume_recovery", "last_success",
    "segment_id", "proxy_auto_suspended", "proxy_auto_probe",
    "proxy_control_generation", "last_proxy_error",
})
BLOCKED_KINDS = frozenset({"challenge", "http_401", "http_403", "http_407", "http_429"})


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch is not None else None


class StockRuntimeMixin:
    @staticmethod
    def _stock_key(key):
        return key in STOCK_KEYS

    def _stock_scope(self):
        return getattr(getattr(self, "_stock_local", None), "scope", None)

    @contextmanager
    def _stock_context(self, stock, job=None):
        if not isinstance(stock, str) or not re.fullmatch(r"[0-9]{6}", stock):
            raise ValueError("股票代码必须为六位数字")
        if job is None:
            job = self._get_global("job_id")
        if isinstance(job, bool) or not isinstance(job, int) or job <= 0:
            raise RuntimeError("股票缺少可追溯的任务编号")
        if not hasattr(self, "_stock_local"):
            self._stock_local = threading.local()
        previous = self._stock_scope()
        self._stock_local.scope = (job, stock)
        try:
            yield
        finally:
            self._stock_local.scope = previous

    @staticmethod
    def _stock_defaults():
        return {"state": "paused", "reason": "等待开始", "next_due": 0,
                "probe": False, "active_halt": None, "block_evidence": None,
                "halted_task_id": None, "halt_task_id": None,
                "halted_config": None, "halt_config": None,
                "halted_probe_only": False, "network_retry": None,
                "resume_recovery": False, "last_success": None,
                "segment_id": None, "proxy_auto_suspended": True,
                "proxy_auto_probe": False, "proxy_control_generation": 0,
                "last_proxy_error": None}

    def _stock_runtime(self, job, stock):
        row = self.db.execute("SELECT payload FROM stock_runtime WHERE job=? AND stock=?", (job, stock)).fetchone()
        payload = self._stock_defaults() | (json.loads(row[0]) if row else {})
        return {**payload, "job": job, "stock": stock}

    def _save_stock_runtime(self, job, stock, payload):
        stored = {key: payload[key] for key in STOCK_KEYS if key in payload}
        self.db.execute("INSERT INTO stock_runtime(job,stock,payload) VALUES(?,?,?) "
                        "ON CONFLICT(job,stock) DO UPDATE SET payload=excluded.payload",
                        (job, stock, _json(stored)))

    def _stock_get(self, key, default=None):
        scope = self._stock_scope()
        if scope is None:
            return self._get_global(key, default)
        row = self.db.execute("SELECT payload FROM stock_runtime WHERE job=? AND stock=?", scope).fetchone()
        return json.loads(row[0]).get(key, default) if row else self._stock_defaults().get(key, default)

    def _stock_set(self, key, value):
        scope = self._stock_scope()
        if scope is None:
            raise RuntimeError("逐股运行状态写入缺少股票上下文")
        payload = self._stock_runtime(*scope)
        payload[key] = value
        self._save_stock_runtime(*scope, payload)

    def _current_stock_scopes(self):
        job, config = self._get_global("job_id"), self._config()
        return [(job, stock) for stock in config["stocks"]] if job and config else []

    def _stock_runtimes(self, job=None, include_detached=False):
        current = self._current_stock_scopes()
        if job is not None:
            rows = self.db.execute("SELECT job,stock FROM stock_runtime WHERE job=? ORDER BY stock", (job,)).fetchall()
            scopes = [(row["job"], row["stock"]) for row in rows]
        elif include_detached:
            rows = self.db.execute("SELECT job,stock FROM stock_runtime ORDER BY job,stock").fetchall()
            scopes = [(row["job"], row["stock"]) for row in rows]
        else:
            scopes = current
        result = []
        for scope in scopes:
            runtime = self._stock_runtime(*scope)
            runtime["detached"] = scope not in current
            if runtime["detached"] and job is None and not runtime["active_halt"]:
                continue
            result.append(runtime)
        return result

    def _stock_event(self, job, stock, kind, message, evidence=None):
        facts = {"job": job, "stock": stock, **(evidence or {})}
        self.db.execute("INSERT INTO events(job,created,kind,message,evidence) VALUES(?,?,?,?,?)",
                        (job, self.clock(), kind, message, _json(facts)))

    def _legacy_halt_task(self, legacy):
        if legacy.get("active_halt") == "interrupted_unknown":
            # A cleared old halt may leave historical evidence behind. It must
            # never replace the request interrupted during this startup.
            last = self.db.execute("SELECT task,outcome FROM requests ORDER BY id DESC LIMIT 1").fetchone()
            if last and last["outcome"] == "interrupted_unknown":
                return self.db.execute("SELECT * FROM tasks WHERE id=?", (last["task"],)).fetchone()
        task_id = legacy.get("halted_task_id") or legacy.get("halt_task_id")
        if not task_id:
            evidence = legacy.get("block_evidence") or {}
            if evidence.get("id") is not None:
                request = self.db.execute("SELECT task FROM requests WHERE id=?", (evidence["id"],)).fetchone()
                task_id = request[0] if request else None
        return self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone() if task_id else None

    def _init_stock_runtime(self):
        self._stock_runtime_ready = False
        self._stock_local = threading.local()
        self.db.execute("CREATE TABLE IF NOT EXISTS stock_runtime("
                        "job INTEGER NOT NULL,stock TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(job,stock))")
        with self._mutex, self.db:
            first = not self._get_global("stock_runtime_version")
            legacy = {key: self._get_global(key) for key in STOCK_KEYS}
            scopes = self._current_stock_scopes()
            last = self.db.execute("SELECT COALESCE(finished,started) AS at FROM requests ORDER BY id DESC LIMIT 1").fetchone()
            config = self._config()
            interval = config["interval_seconds"] if config else 60
            node_due = max(self._get_global("node_next_due", 0) or 0, (last[0] + interval) if last and last[0] is not None else 0)
            self._set_global("node_next_due", node_due)
            for job, stock in scopes:
                exists = self.db.execute("SELECT 1 FROM stock_runtime WHERE job=? AND stock=?", (job, stock)).fetchone()
                if not exists:
                    payload = self._stock_defaults()
                    if first and legacy.get("state") == "completed":
                        payload.update(state="completed", reason=legacy.get("reason") or "已有任务遍历结束")
                    else:
                        payload.update(reason="进程重启后安全暂停，等待继续", resume_recovery=True)
                    success = self.db.execute("SELECT MAX(finished) FROM requests WHERE job=? AND stock=? "
                                              "AND outcome IN ('real_data','detail_unavailable')", (job, stock)).fetchone()[0]
                    payload["last_success"] = success
                    self._save_stock_runtime(job, stock, payload)
            interruptions = getattr(self, "_stock_interrupted", [])
            if first and legacy.get("active_halt") and not (legacy["active_halt"] == "interrupted_unknown" and interruptions):
                target = self._legacy_halt_task(legacy)
                if target:
                    payload = self._stock_runtime(target["job"], target["stock"])
                    payload.update({key: value for key, value in legacy.items() if key not in {"segment_id", "last_success"}})
                    payload.update(halted_task_id=target["id"], halt_task_id=target["id"])
                    self._save_stock_runtime(target["job"], target["stock"], payload)
                    self._stock_event(target["job"], target["stock"], "stock_halt_migrated",
                                      "旧实例阻断已归属原股票；原目标、证据与冷却保留", {"task_id": target["id"]})
                else:
                    self._set_global("legacy_stock_halt_barrier", {
                        "kind": legacy["active_halt"], "reason": legacy.get("reason"),
                        "block_evidence": legacy.get("block_evidence"), "next_due": legacy.get("next_due"),
                        "halted_task_id": legacy.get("halted_task_id"), "halt_task_id": legacy.get("halt_task_id"),
                        "error": "旧阻断目标无法定位，须恢复原任务证据后处理；未清除阻断"})
            if first and legacy.get("network_retry"):
                retry = legacy["network_retry"]
                target = self.db.execute("SELECT * FROM tasks WHERE id=?", (retry.get("task_id"),)).fetchone()
                if target:
                    payload = self._stock_runtime(target["job"], target["stock"])
                    payload.update(network_retry=retry, next_due=legacy.get("next_due") or node_due)
                    self._save_stock_runtime(target["job"], target["stock"], payload)
            self._record_stock_interruptions()
            rows = self.db.execute("SELECT job,stock,payload FROM stock_runtime").fetchall()
            for row in rows:
                payload = json.loads(row["payload"])
                if payload.get("state") == "running" or payload.get("probe"):
                    if payload.get("active_halt"):
                        payload["state"] = "blocked" if self._runtime_blocked(payload) else "error"
                        payload["reason"] = (payload.get("block_evidence") or {}).get("reason") or "原阻断尚未解除"
                    else:
                        payload.update(state="paused", reason="进程重启后安全暂停，等待继续", resume_recovery=True)
                    if payload.get("segment_id") is not None:
                        self.db.execute("UPDATE run_segments SET ended=?,stop_reason='restart_paused' WHERE id=? AND ended IS NULL",
                                        (self.clock(), payload["segment_id"]))
                payload.update(probe=False, proxy_auto_suspended=True, proxy_auto_probe=False,
                               proxy_control_generation=(payload.get("proxy_control_generation") or 0) + 1)
                self._save_stock_runtime(row["job"], row["stock"], payload)
            self._set_global("stock_runtime_version", 1)
            self._set_global("stock_runtime_schema", 1)
            self._stock_runtime_ready = True
            for job, stock in scopes:
                with self._stock_context(stock, job):
                    if not self._get("active_halt") and self._get("state") != "completed":
                        self._mark_recovery_needed("process_restart")
            self._publish_stock_summary()

    def _ensure_current_stock_runtimes(self):
        """Add controls for a new job or newly added stock without resetting old controls."""
        with self._mutex:
            for job, stock in self._current_stock_scopes():
                if not self.db.execute("SELECT 1 FROM stock_runtime WHERE job=? AND stock=?", (job, stock)).fetchone():
                    self._save_stock_runtime(job, stock, self._stock_defaults())
            self._publish_stock_summary()

    @staticmethod
    def _runtime_blocked(payload):
        return payload.get("active_halt") in BLOCKED_KINDS or (
            payload.get("active_halt") == "proxy_error" and payload.get("last_proxy_error") == "proxy_auth")

    def _record_stock_interruptions(self):
        ids = {row["id"] for row in self.db.execute("SELECT id FROM requests WHERE outcome='reserved'").fetchall()}
        ids.update(row["id"] for row in getattr(self, "_stock_interrupted", []))
        for request_id in sorted(ids):
            row = self.db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if not row or row["outcome"] not in {"reserved", "interrupted_unknown"}:
                continue
            raw = self.raw_dir / f"{row['id']:09d}.body"
            partial = raw.with_suffix(".tmp")
            path = raw if raw.is_file() and not raw.is_symlink() else partial if partial.is_file() and not partial.is_symlink() else None
            body = path.read_bytes() if path else None
            finished = row["finished"] if row["finished"] is not None else self.clock()
            reason = "前次请求执行中进程中断，是否到达来源未知；需要对原股票单次探测"
            self.db.execute("UPDATE requests SET outcome='interrupted_unknown',finished=?,error=?,raw_ref=?,response_bytes=?,sha256=? WHERE id=?",
                            (finished, reason, str(path.relative_to(self.data_dir)) if path else None,
                             len(body) if body is not None else None, hashlib.sha256(body).hexdigest() if body is not None else None, row["id"]))
            self.db.execute("UPDATE tasks SET status='pending' WHERE id=?", (row["task"],))
            config_row = self.db.execute("SELECT config FROM jobs WHERE id=?", (row["job"],)).fetchone()
            config = json.loads(config_row[0]) if config_row else None
            payload = self._stock_runtime(row["job"], row["stock"])
            if payload.get("segment_id") is not None:
                self.db.execute("UPDATE run_segments SET ended=?,stop_reason='interrupted_process' WHERE id=? AND ended IS NULL",
                                (finished, payload["segment_id"]))
            evidence = {"id": row["id"], "url": row["url"], "started": row["started"], "finished": finished,
                        "kind": "interrupted_unknown", "reason": reason, "server_blacklist": "unproven"}
            payload.update(state="error", reason=reason, active_halt="interrupted_unknown", block_evidence=evidence,
                           halted_task_id=row["task"], halt_task_id=row["task"], halt_config=config,
                           halted_config=config, network_retry=None, probe=False)
            self._save_stock_runtime(row["job"], row["stock"], payload)
            interval = config["interval_seconds"] if config else 60
            self._set_global("node_next_due", max(self._get_global("node_next_due", 0) or 0, finished + interval))
            self._stock_event(row["job"], row["stock"], "interrupted_unknown", reason, {"request_id": row["id"], "task_id": row["task"]})
        self._stock_interrupted = []

    def _resolve_stock_scope(self, stock, job=None):
        if stock is None and self._stock_scope() is not None:
            return self._stock_scope()
        if not isinstance(stock, str) or not re.fullmatch(r"[0-9]{6}", stock):
            raise ValueError("请指定六位股票代码")
        current = self._current_stock_scopes()
        if job is None:
            found = [scope for scope in current if scope[1] == stock]
            if found:
                return found[0]
            found = [(r["job"], r["stock"]) for r in self._stock_runtimes(include_detached=True)
                     if r["stock"] == stock and r["active_halt"]]
            if len(found) != 1:
                raise RuntimeError("股票不在当前任务中，或有多个旧阻断；请指定原任务编号")
            return found[0]
        if isinstance(job, bool) or not isinstance(job, int) or job <= 0:
            raise ValueError("任务编号无效")
        runtime = self._stock_runtime(job, stock)
        if (job, stock) not in current and not runtime["active_halt"]:
            raise RuntimeError("股票不在当前任务中，也没有可探测的原阻断")
        return job, stock

    def start(self, stock=None, *, job=None):
        with self._mutex:
            if self._get_global("legacy_stock_halt_barrier"):
                raise RuntimeError("旧阻断目标缺失，须恢复原目标证据后继续")
            scopes = [self._resolve_stock_scope(stock, job)] if stock is not None or self._stock_scope() else self._current_stock_scopes()
            if not scopes:
                raise RuntimeError("请先创建任务")
            for scope in scopes:
                if scope not in self._current_stock_scopes():
                    raise RuntimeError("已移除或归档的股票只能单次探测原阻断")
                runtime = self._stock_runtime(*scope)
                if stock is None and not self._stock_scope() and (runtime["active_halt"] or runtime["state"] == "completed"):
                    continue
                with self._stock_context(scope[1], scope[0]):
                    self._start_one()
            self._publish_stock_summary()
            return self.status()

    def pause(self, stock=None, *, job=None):
        with self._mutex:
            scopes = [self._resolve_stock_scope(stock, job)] if stock is not None or self._stock_scope() else [(r["job"], r["stock"]) for r in self._stock_runtimes(include_detached=True)]
            for scope in scopes:
                with self._stock_context(scope[1], scope[0]):
                    self._pause_one()
            self._publish_stock_summary()
            return self.status()

    def retry(self, stock=None, *, job=None, automatic=False):
        with self._mutex:
            if self._get_global("legacy_stock_halt_barrier"):
                raise RuntimeError("旧阻断目标缺失，不能猜测单次探测目标")
            if stock is not None or self._stock_scope():
                scope = self._resolve_stock_scope(stock, job)
            else:
                runtimes = self._stock_runtimes(include_detached=True)
                blocked = [r for r in runtimes if r["active_halt"]]
                choices = blocked or [r for r in runtimes if not r["detached"] and r["state"] != "completed"]
                if len(choices) != 1:
                    raise RuntimeError("请在股票卡片上选择需要单次探测的股票")
                scope = choices[0]["job"], choices[0]["stock"]
            with self._stock_context(scope[1], scope[0]):
                self._retry_one(automatic=automatic)
            self._publish_stock_summary()
            return self.status()

    def tick(self):
        with self._mutex:
            if (self._closed or self._inflight or self._get_global("storage_halt")
                    or self._get_global("legacy_stock_halt_barrier")):
                return {"attempted": False}
            scopes = self._current_stock_scopes()
            for scope in scopes:
                with self._stock_context(scope[1], scope[0]):
                    self._maybe_proxy_recovery()
            # Detached targets participate only after an explicit one-shot probe.
            scopes += [(r["job"], r["stock"]) for r in self._stock_runtimes(include_detached=True)
                       if r["detached"] and r["probe"] and r["state"] == "running"]
            if self.clock() < (self._get_global("node_next_due", 0) or 0) or not scopes:
                self._publish_stock_summary()
                return {"attempted": False}
            previous = self._get_global("stock_rr_scope")
            start = scopes.index(tuple(previous)) + 1 if isinstance(previous, list) and tuple(previous) in scopes else 0
            ordered = scopes[start:] + scopes[:start]
        for scope in ordered:
            with self._mutex:
                runtime = self._stock_runtime(*scope)
                if (runtime["state"] != "running" or runtime["active_halt"] and not runtime["probe"]
                        or self.clock() < (runtime["next_due"] or 0)):
                    continue
                with self.db:
                    self._set_global("stock_rr_scope", list(scope))
            with self._stock_context(scope[1], scope[0]):
                outcome = self._tick_one()
            with self._mutex:
                self._publish_stock_summary()
            if outcome.get("attempted"):
                return {**outcome, "stock": scope[1], "job": scope[0]}
        with self._mutex:
            self._publish_stock_summary()
        return {"attempted": False}

    def _stock_summary(self):
        active = self._stock_runtimes()
        detached = [r for r in self._stock_runtimes(include_detached=True) if r["detached"]]
        barrier, storage = self._get_global("legacy_stock_halt_barrier"), self._get_global("storage_halt")
        running = [r for r in active + detached if r["state"] == "running"]
        blocked = [r for r in active if r["active_halt"]]
        if storage or barrier:
            state, reason = "error", (barrier or {}).get("reason") or "本地存储尚未修复，节点暂停来源请求"
        elif running:
            state, reason = "running", f"{len(running)} 只股票运行；{len(blocked)} 只股票阻断，其余任务独立调度"
        elif active and all(r["state"] == "completed" for r in active):
            state, reason = "completed", "各股票已遍历结束；覆盖缺口需单独查看"
        else:
            state, reason = "paused", f"{len(blocked)} 只股票阻断；可在各股票卡片独立开始、暂停或探测" if active else "尚未创建任务"
        node_due = self._get_global("node_next_due", 0) or 0
        current, due = None, None
        public = []
        for runtime in active + detached:
            with self._stock_context(runtime["stock"], runtime["job"]):
                target = self._target(probe_target=runtime["probe"] or bool(runtime["active_halt"]))
                item = {**runtime, "current": self._target_json(target),
                        "next_request_epoch": max(node_due, runtime["next_due"] or 0),
                        "next_request_at": _iso(max(node_due, runtime["next_due"] or 0))}
            public.append(item)
            if runtime["state"] == "running" and (not runtime["active_halt"] or runtime["probe"]):
                candidate_due = item["next_request_epoch"]
                if due is None or candidate_due < due:
                    due, current = candidate_due, item["current"]
        successes = [r["last_success"] for r in active if isinstance(r["last_success"], (int, float))]
        return {"state": state, "reason": reason, "current": current,
                "next_request_epoch": due if due is not None else node_due,
                "next_request_at": _iso(due) if due is not None else None,
                "last_success": _iso(max(successes)) if successes else None,
                "active_halt": barrier.get("kind") if barrier else None,
                "block_evidence": barrier.get("block_evidence") if barrier else None,
                "probe_pending": any(r["probe"] for r in active + detached), "network_retry": None,
                "stock_runtimes": [r for r in public if not r["detached"]],
                "detached_stock_runtimes": [r for r in public if r["detached"]],
                "blocked_stocks": [r["stock"] for r in blocked], "runtime_scope": "stock",
                "node_next_request_epoch": node_due, "legacy_stock_halt_barrier": barrier}

    def _publish_stock_summary(self):
        summary = self._stock_summary()
        def write():
            for key in ("state", "reason", "active_halt", "block_evidence"):
                self._set_global(key, summary[key])
            self._set_global("probe", summary["probe_pending"])
            self._set_global("next_due", summary["next_request_epoch"])
        if self.db.in_transaction:
            write()
        else:
            with self.db:
                write()
        return summary
