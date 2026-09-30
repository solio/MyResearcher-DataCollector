"""Auditable local job edits and archives; never delete source evidence.

Engine supplies its database, lock, clock and queue helpers. Recovery is owned
by core: a changed scope starts at page 1 instead of inheriting a stale cursor.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import urlparse


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=lambda x: x.isoformat(), separators=(",", ":"))


def _iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


class LifecycleMixin:
    def _init_lifecycle_schema(self):
        """Add tables to v1 databases; existing rows and raw files are untouched."""
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS job_lifecycle(
          job INTEGER PRIMARY KEY,revision INTEGER NOT NULL DEFAULT 1,
          effective_to REAL NOT NULL,archived REAL);
        CREATE TABLE IF NOT EXISTS job_revisions(
          id INTEGER PRIMARY KEY,job INTEGER NOT NULL,revision INTEGER NOT NULL,
          changed REAL NOT NULL,reason TEXT NOT NULL,config TEXT NOT NULL,
          effective_to REAL NOT NULL,snapshot TEXT NOT NULL,
          UNIQUE(job,revision));
        """)
        with self.db:
            self._sync_lifecycle_jobs()

    def _sync_lifecycle_jobs(self):
        for row in self.db.execute("SELECT id FROM jobs WHERE id NOT IN (SELECT job FROM job_lifecycle)").fetchall():
            self._register_job_lifecycle(row["id"])

    def _register_job_lifecycle(self, job):
        if self.db.execute("SELECT 1 FROM job_lifecycle WHERE job=?", (job,)).fetchone():
            return
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
        if row is None:
            raise RuntimeError("任务不存在")
        config = json.loads(row["config"])
        end = self._window_end(config)
        effective_to = self._get("effective_to_epoch") if job == self._get("job_id") else None
        if effective_to is None:
            effective_to = min(end, row["created"])
        self.db.execute("INSERT INTO job_lifecycle(job,effective_to) VALUES(?,?)", (job, effective_to))
        self.db.execute("INSERT INTO job_revisions(job,revision,changed,reason,config,effective_to,snapshot) VALUES(?,1,?,?,?,?,?)",
                        (job, row["created"], "initial", row["config"], effective_to,
                         _json({"after": self._lifecycle_snapshot(job)})))

    @staticmethod
    def _window_end(config):
        return datetime.fromisoformat(config["to_date"] + "T23:59:59.999999+08:00").timestamp()

    def _job_archived(self, job):
        row = self.db.execute("SELECT archived FROM job_lifecycle WHERE job=?", (job,)).fetchone()
        return row is not None and row[0] is not None

    def _lifecycle_snapshot(self, job):
        # Config/cutoff plus immutable observations reconstruct prior eligibility;
        # do not copy hundreds of thousands of association/task rows per edit.
        result = {"eligibility_replay": "revision config/effective_to and immutable observations",
                  "association_counts": [dict(r) for r in self.db.execute(
                      "SELECT stock,eligible,COUNT(*) AS rows FROM associations WHERE job=? GROUP BY stock,eligible", (job,)).fetchall()],
                  "task_counts": [dict(r) for r in self.db.execute(
                      "SELECT stock,kind,status,COUNT(*) AS rows FROM tasks WHERE job=? GROUP BY stock,kind,status", (job,)).fetchall()]}
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("coverage", "frontiers", "recoveries"):
            if table in tables:
                result[table] = [dict(r) for r in self.db.execute(f"SELECT * FROM {table} WHERE job=?", (job,)).fetchall()]
        return result

    def _editable_job(self):
        if self._closed:
            raise RuntimeError("采集器已经关闭")
        if self._inflight or self._get("state") == "running" or self._get("probe", False):
            raise RuntimeError("请先暂停任务并等待正在执行的请求结束，再修改或归档")
        self._sync_lifecycle_jobs()
        job = self._get("job_id")
        if not job or self._job_archived(job):
            raise RuntimeError("没有可编辑的当前任务")
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
        if row is None:
            raise RuntimeError("当前任务不存在")
        return job, json.loads(row["config"])

    def _retained_halt_target(self):
        if not self._get("active_halt"):
            return None
        task_id = self._get("halted_task_id") or self._get("halt_task_id")
        if task_id is not None:
            row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        else:
            row = self._halt_target()
        if row is None:
            raise RuntimeError("阻断目标缺失，不能通过修改配置清除阻断")
        self._set("halted_task_id", row["id"])
        return row["id"]

    def _cancel_scope_tasks(self, job, retained, status, stocks=None):
        stock_clause = "" if stocks is None else " AND stock IN (" + ",".join("?" for _ in stocks) + ")"
        self.db.execute("UPDATE tasks SET status=? WHERE job=? AND status IN ('pending','inflight') AND (? IS NULL OR id!=?)" + stock_clause,
                        (status, job, retained, retained, *(stocks or [])))
        if retained is not None:
            self.db.execute("UPDATE tasks SET status='pending' WHERE id=?", (retained,))

    def _detach_halt_probe(self, old_config, retained):
        if retained is not None:
            failed_config = self._get("halted_config") or self._get("halt_config") or old_config
            self._set("halt_config", failed_config)
            self._set("halted_config", failed_config)
            self._set("halted_probe_only", True)

    def _clear_derived_positions(self, job, stocks):
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("frontiers", "recoveries"):
            if table in tables:
                self.db.execute(f"DELETE FROM {table} WHERE job=? AND stock IN (" + ",".join("?" for _ in stocks) + ")", (job, *stocks))

    def _observed_item(self, observation, cache):
        """Recover an exact observed link; never manufacture a detail URL."""
        from core import guba
        rid = observation["request_id"]
        if rid not in cache:
            request = self.db.execute("SELECT raw_ref,sha256,outcome FROM requests WHERE id=?", (rid,)).fetchone()
            if request is None or request["outcome"] != "real_data" or not request["raw_ref"]:
                raise ValueError("历史列表缺少成功响应证据，不能重建详情队列")
            path = (self.data_dir / request["raw_ref"]).resolve()
            if not path.is_relative_to(self.raw_dir.resolve()) or not path.is_file():
                raise ValueError("历史列表原始响应缺失，不能重建详情队列")
            body = path.read_bytes()
            if hashlib.sha256(body).hexdigest() != request["sha256"]:
                raise ValueError("历史列表原始响应哈希不一致")
            page = guba.parse_list_page(body.decode("utf-8-sig", errors="strict"), observation["stock"])
            cache.clear()  # Consecutive observations share one request; keep memory bounded.
            cache[rid] = {item.source_item_id: item for item in page.rows}
        item = cache[rid].get(observation["post_id"])
        if item is None:
            raise ValueError("历史原始列表与保存的帖子 ID 不一致")
        parsed = urlparse(item.url)
        if (parsed.scheme != "https" or parsed.hostname != "guba.eastmoney.com"
                or not re.fullmatch(r"/news,[A-Za-z0-9]+," + re.escape(item.source_item_id) + r"\.html", parsed.path)):
            raise ValueError("历史标准详情链接不符合来源路径")
        return item

    def _rebuild_scope(self, job, old_config, config, effective_to, retained):
        from core import guba, _item_load, detail_enrichment_trigger
        old_stocks, new_stocks = set(old_config["stocks"]), set(config["stocks"])
        window_changed = any(config[k] != old_config[k] for k in ("from_date", "to_date"))
        reset = new_stocks if window_changed else new_stocks - old_stocks
        affected = sorted(reset | (old_stocks - new_stocks))
        self._cancel_scope_tasks(job, retained, "superseded", affected)
        self._clear_derived_positions(job, affected)
        placeholders = ",".join("?" for _ in affected)
        for table in ("associations", "coverage"):
            self.db.execute(f"DELETE FROM {table} WHERE job=? AND stock IN (" + placeholders + ")", (job, *affected))
        for stock in config["stocks"]:
            if stock not in reset:
                continue
            self.db.execute("INSERT INTO coverage(job,stock) VALUES(?,?)", (job, stock))
            self._enqueue_list(job, stock, 1)
        if not reset:
            self._mark_recovery_needed("config_updated")
            return []
        probe_target = retained if self._get("halted_probe_only", False) else None
        queued = {r[0] for r in self.db.execute(
            "SELECT post_id FROM tasks WHERE job=? AND kind='detail' AND status IN ('pending','inflight') AND (? IS NULL OR id!=?)",
            (job, probe_target, probe_target))}
        cache, seen, projection_requests = {}, set(), set()
        reset = sorted(reset)
        placeholders = ",".join("?" for _ in reset)
        observations = self.db.execute(
            "SELECT * FROM observations WHERE job=? AND stock IN (" + placeholders + ") ORDER BY id DESC",
            (job, *reset))
        for observation in observations:
            key = (observation["stock"], observation["post_id"])
            if key in seen:
                continue
            seen.add(key)
            raw_row = json.loads(observation["source_row"])
            published = guba.parse_source_time(raw_row.get("post_publish_time"), "post_publish_time", required=True)
            eligible = (type(raw_row.get("post_type")) is int and raw_row["post_type"] == 0
                        and config["from_date"] <= published.date().isoformat() <= config["to_date"]
                        and published.timestamp() <= effective_to)
            self.db.execute("INSERT INTO associations VALUES(?,?,?,?,?)",
                            (job, observation["stock"], observation["post_id"], int(eligible), observation["request_id"]))
            if not eligible:
                continue
            post = self.db.execute("SELECT * FROM posts WHERE post_id=?", (observation["post_id"],)).fetchone()
            if post is None:
                item = self._observed_item(observation, cache)
                status = self._store_list_post(item, observation["source_row"], observation["request_id"])
                projection_requests.add(observation["request_id"])
            else:
                item, status = _item_load(post["item"]), post["status"]
                if item.published_at != published:
                    raise ValueError("历史相同源 ID 的发布时间不一致，不能重建配置范围")
            if status == "pending" and detail_enrichment_trigger(item.title) and item.source_item_id not in queued:
                queued.add(item.source_item_id)
                # A retained old probe only validates; normal acquisition has
                # its own task after the halt has been resolved.
                self.db.execute("INSERT INTO tasks(job,kind,stock,page,post_id,url,original_url) VALUES(?,'detail',?,?,?,?,?)",
                                (job, observation["stock"], observation["page"], item.source_item_id, item.url, item.url))
        self._mark_recovery_needed("config_updated")
        return sorted(projection_requests)

    def _record_job_revision(self, job, config, effective_to, reason, before):
        row = self.db.execute("SELECT revision FROM job_lifecycle WHERE job=?", (job,)).fetchone()
        revision = row[0] + 1
        self.db.execute("UPDATE job_lifecycle SET revision=?,effective_to=? WHERE job=?", (revision, effective_to, job))
        self.db.execute("INSERT INTO job_revisions(job,revision,changed,reason,config,effective_to,snapshot) VALUES(?,?,?,?,?,?,?)",
                        (job, revision, self.clock(), reason, _json(config), effective_to,
                         _json({"before": before, "after": self._lifecycle_snapshot(job)})))

    def update_job(self, config):
        from core import _validate_config
        config = _validate_config(config)
        projection_requests = []
        with self._mutex, self.db:
            job, old = self._editable_job()
            if config == old:
                return self.status()
            current_cutoff = self.db.execute("SELECT effective_to FROM job_lifecycle WHERE job=?", (job,)).fetchone()[0]
            cutoff = min(self._window_end(config), self.clock()) if config["to_date"] != old["to_date"] else current_cutoff
            start = datetime.fromisoformat(config["from_date"] + "T00:00:00+08:00").timestamp()
            if start > cutoff:
                raise ValueError("开始日期晚于当前可观察时间，不能创建未来数据任务")
            before = self._lifecycle_snapshot(job)
            retained = self._retained_halt_target()
            scope_changed = (set(config["stocks"]) != set(old["stocks"])
                             or config["from_date"] != old["from_date"] or config["to_date"] != old["to_date"])
            self.db.execute("UPDATE jobs SET config=? WHERE id=?", (_json(config), job))
            self._set("effective_to_epoch", cutoff)
            if scope_changed:
                affected = set(old["stocks"]) ^ set(config["stocks"])
                if config["from_date"] != old["from_date"] or config["to_date"] != old["to_date"]:
                    affected |= set(old["stocks"]) | set(config["stocks"])
                target = self.db.execute("SELECT stock FROM tasks WHERE id=?", (retained,)).fetchone() if retained else None
                if target and target["stock"] in affected:
                    self._detach_halt_probe(old, retained)
                projection_requests = self._rebuild_scope(job, old, config, cutoff, retained)
            self._end_segment("config_updated")
            if not self._get("active_halt"):
                self._set("state", "paused")
                self._set("reason", "配置已更新；等待开始" + ("并重新核对来源列表位置" if scope_changed else ""))
            self._record_job_revision(job, config, cutoff, "config_updated", before)
            self._event("config_updated", "修改任务配置；已采原始响应与历史配置保留", {"previous": old, "config": config, "scope_rebuilt": scope_changed})
        for request_id in projection_requests:
            if not self._sync_storage(request_id):
                break
        return self.status()

    def remove_stock(self, stock):
        with self._mutex, self.db:
            _, config = self._editable_job()
            if not isinstance(stock, str) or stock not in config["stocks"]:
                raise ValueError("股票不在当前任务中")
            if len(config["stocks"]) == 1:
                return self.delete_job()
            return self.update_job({**config, "stocks": [s for s in config["stocks"] if s != stock]})

    def delete_job(self):
        with self._mutex, self.db:
            job, config = self._editable_job()
            before = self._lifecycle_snapshot(job)
            retained = self._retained_halt_target()
            self._detach_halt_probe(config, retained)
            self._cancel_scope_tasks(job, retained, "cancelled")
            self._end_segment("job_archived")
            self.db.execute("UPDATE job_lifecycle SET archived=? WHERE job=?", (self.clock(), job))
            cutoff = self.db.execute("SELECT effective_to FROM job_lifecycle WHERE job=?", (job,)).fetchone()[0]
            self._record_job_revision(job, config, cutoff, "job_archived", before)
            self._event("job_archived", "归档任务并取消待采队列；已有数据和原始证据保留", {"job": job, "retained_probe_task": retained})
            self._set("job_id", None)
            self._set("effective_to_epoch", None)
            self._set("segment_id", None)
            self._set("probe", False)
            if not self._get("active_halt"):
                self._set("state", "paused")
                self._set("reason", "任务已归档，可以创建新任务")
            else:
                self._set("reason", "任务已归档；实例仍有未解除阻断，须对原目标单次探测后再创建任务")
        return self.status()

    def jobs(self):
        with self._mutex, self.db:
            self._sync_lifecycle_jobs()
            result = []
            rows = self.db.execute("SELECT j.*,l.revision,l.effective_to,l.archived FROM jobs j JOIN job_lifecycle l ON l.job=j.id ORDER BY j.id DESC").fetchall()
            for row in rows:
                revisions = [{"revision": r["revision"], "changed_at": _iso(r["changed"]), "reason": r["reason"],
                              "config": json.loads(r["config"]), "effective_to": _iso(r["effective_to"]), "snapshot_available": True}
                             for r in self.db.execute("SELECT revision,changed,reason,config,effective_to FROM job_revisions WHERE job=? ORDER BY revision", (row["id"],)).fetchall()]
                result.append({"id": row["id"], "config": json.loads(row["config"]), "created_at": _iso(row["created"]),
                               "started_at": _iso(row["started"]), "effective_to": _iso(row["effective_to"]),
                               "revision": row["revision"], "archived": row["archived"] is not None,
                               "archived_at": _iso(row["archived"]), "current": row["id"] == self._get("job_id"),
                               "revisions": revisions})
            return result
