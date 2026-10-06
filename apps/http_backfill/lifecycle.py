"""Auditable local job edits and archives; never delete source evidence.

Engine supplies its database, lock, clock and queue helpers. Scope rebuilds
reuse valid positions or historical hints while retaining raw source evidence.
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
        if self._get_global("legacy_stock_halt_barrier"):
            raise RuntimeError("旧阻断目标缺失，须恢复原证据后再修改任务")
        self._sync_lifecycle_jobs()
        job = self._get("job_id")
        if not job or self._job_archived(job):
            raise RuntimeError("没有可编辑的当前任务")
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
        if row is None:
            raise RuntimeError("当前任务不存在")
        return job, json.loads(row["config"])

    def _retained_halt_targets(self):
        retained = []
        for runtime in self._stock_runtimes(job=self._get("job_id")):
            if runtime["active_halt"]:
                with self._stock_context(runtime["stock"], runtime["job"]):
                    row = self._halt_target()
                    if row is None:
                        raise RuntimeError("股票阻断目标缺失，不能通过修改配置清除阻断")
                    self._set("halted_task_id", row["id"])
                    retained.append(row["id"])
        return retained

    def _cancel_scope_tasks(self, job, retained, status, stocks=None, kinds=None):
        retained = retained or []
        retained_clause = " AND id NOT IN (" + ",".join("?" for _ in retained) + ")" if retained else ""
        stock_clause = "" if stocks is None else " AND stock IN (" + ",".join("?" for _ in stocks) + ")"
        kind_clause = "" if kinds is None else " AND kind IN (" + ",".join("?" for _ in kinds) + ")"
        self.db.execute("UPDATE tasks SET status=? WHERE job=? AND status IN ('pending','inflight')" + retained_clause + stock_clause + kind_clause,
                        (status, job, *retained, *(stocks or []), *(kinds or [])))
        for task_id in retained:
            self.db.execute("UPDATE tasks SET status='pending' WHERE id=?", (task_id,))

    def _detach_halt_probe(self, old_config, retained):
        for task_id in retained:
            target = self.db.execute("SELECT job,stock FROM tasks WHERE id=?", (task_id,)).fetchone()
            with self._stock_context(target["stock"], target["job"]):
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

    def _saved_navigation_valid(self, job, stock, seek, frontier, effective_to):
        from window_seek import MAX_PAGE, MAX_PROBES
        if seek and seek.get("phase") == "error":
            return False
        if seek and seek.get("target_epoch") == effective_to:
            page = seek.get("current_page") if seek.get("phase") == "searching" else seek.get("start_page")
            probes = seek.get("probes", 0)
            if (seek.get("phase") in {"searching", "complete"} and type(page) is int and 1 <= page <= MAX_PAGE
                    and (seek["phase"] == "complete" or type(probes) is int and 0 <= probes < MAX_PROBES)):
                return True
        if not frontier or type(frontier.get("page")) is not int or frontier["page"] < 1 or not frontier.get("rows"):
            return False
        observed = self.db.execute("SELECT 1 FROM requests WHERE id=? AND job=? AND stock=? AND page=? "
                                   "AND kind='list' AND outcome='real_data'",
                                   (frontier.get("request_id"), job, stock, frontier["page"])).fetchone()
        return observed is not None

    def _manual_position_page(self, job, stock, seek, frontier, retained):
        page = seek.get("manual_start_page") or seek.get("start_page")
        if seek.get("manual_start_pending"):
            return page
        retained = retained or []
        retained_order = "CASE WHEN id IN (" + ",".join("?" for _ in retained) + ") THEN 1 ELSE 0 END," if retained else ""
        pending = self.db.execute("SELECT page FROM tasks WHERE job=? AND stock=? AND kind='list' "
                                  "AND purpose='forward' AND status IN ('pending','inflight') ORDER BY " + retained_order + "id LIMIT 1",
                                  (job, stock, *retained)).fetchone()
        if pending and type(pending[0]) is int and pending[0] > 0:
            return pending[0]
        rec = self._recovery(job, stock)
        if rec and rec.get("phase") in {"seek", "scan", "verify_frontier"}:
            candidate = rec.get("current_page")
            if type(candidate) is int and candidate > 0:
                return candidate
        return frontier["page"] if frontier and type(frontier.get("page")) is int and frontier["page"] > 0 else page

    def _reset_window_stop(self, job, stock):
        self.db.execute("UPDATE coverage SET under_pages=0,boundary=0,list_complete=0,stop_reason=NULL WHERE job=? AND stock=?", (job, stock))
        frontier = self._frontier(job, stock)
        if frontier:
            frontier["terminal"] = None
            self._save_frontier(job, stock, frontier)
        rec = self._recovery(job, stock)
        if rec:
            rec["terminal"] = None
            self._save_recovery(rec)

    def _rebuild_scope(self, job, old_config, config, effective_to, retained):
        from core import guba, _item_load, detail_enrichment_trigger
        old_stocks, new_stocks = set(old_config["stocks"]), set(config["stocks"])
        window_changed = any(config[k] != old_config[k] for k in ("from_date", "to_date"))
        reset = new_stocks if window_changed else new_stocks - old_stocks
        affected = sorted(reset | (old_stocks - new_stocks))
        previous_cutoff = self.db.execute("SELECT effective_to FROM job_lifecycle WHERE job=?", (job,)).fetchone()[0]
        upper_changed = config["to_date"] != old_config["to_date"] or effective_to != previous_cutoff
        preserve, manual_pages = set(), {}
        for stock in reset:
            seek, frontier = self._seek_state(job, stock), self._frontier(job, stock)
            if seek and seek.get("manual_direct"):
                page = self._manual_position_page(job, stock, seek, frontier, retained)
                if type(page) is int and 1 <= page <= 2 ** 53 - 1:
                    manual_pages[stock] = page
            elif (stock in old_stocks and not upper_changed
                  and self._saved_navigation_valid(job, stock, seek, frontier, effective_to)):
                preserve.add(stock)
        replace = sorted((reset - preserve) | (old_stocks - new_stocks))
        if replace:
            self._cancel_scope_tasks(job, retained, "superseded", replace)
            self._clear_derived_positions(job, replace)
        if preserve:
            self._cancel_scope_tasks(job, retained, "superseded", sorted(preserve), kinds=["detail"])
        placeholders = ",".join("?" for _ in affected)
        self.db.execute("DELETE FROM associations WHERE job=? AND stock IN (" + placeholders + ")", (job, *affected))
        for stock in old_stocks - new_stocks:
            self.db.execute("DELETE FROM coverage WHERE job=? AND stock=?", (job, stock))
        for stock in config["stocks"]:
            if stock not in reset:
                continue
            self.db.execute("INSERT OR IGNORE INTO coverage(job,stock) VALUES(?,?)", (job, stock))
            self._reset_window_stop(job, stock)
            with self._stock_context(stock, job):
                if stock in manual_pages:
                    self._begin_manual_entry(job, stock, manual_pages[stock], reason="config_updated_manual_position")
                    self._event("manual_position_retained", "日期修改保留手动采集页码；仍未证明窗口上界或此前页面覆盖",
                                {"stock": stock, "page": manual_pages[stock], "upper_boundary_verified": False})
                elif stock in preserve:
                    seek = self._seek_state(job, stock)
                    if seek:
                        seek["target_from_date"] = config["from_date"]
                        self._save_seek_state(seek)
                        if seek.get("phase") == "searching":
                            self._enqueue_seek(job, stock, seek["current_page"])
                        elif not seek.get("entry_verified"):
                            existing = self.db.execute("SELECT 1 FROM tasks WHERE job=? AND stock=? AND kind='list' "
                                                       "AND purpose='forward' AND page=? AND status IN ('pending','inflight') "
                                                       "AND id!=COALESCE(?, -1)",
                                                       (job, stock, seek["start_page"], self._retained_seek_task(job, stock))).fetchone()
                            if not existing:
                                self._enqueue_list(job, stock, seek["start_page"])
                    self._event("window_position_retained", "结束日期未变，保留已有定位／前进页码并重新计算窗口内帖子",
                                {"stock": stock, "current_page": seek.get("current_page") if seek else None,
                                 "frontier_page": (self._frontier(job, stock) or {}).get("page")})
                else:
                    self._begin_window_seek(job, stock, reason="config_updated")
        if not reset:
            self._mark_recovery_needed("config_updated")
            return []
        retained_clause = " AND id NOT IN (" + ",".join("?" for _ in retained) + ")" if retained else ""
        queued = {(r[0], r[1]) for r in self.db.execute(
            "SELECT stock,post_id FROM tasks WHERE job=? AND kind='detail' AND status IN ('pending','inflight')" + retained_clause,
            (job, *retained))}
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
            post = self.db.execute("SELECT * FROM http_posts WHERE post_id=?", (observation["post_id"],)).fetchone()
            if post is None:
                item = self._observed_item(observation, cache)
                status = self._store_list_post(item, observation["source_row"], observation["request_id"])
                projection_requests.add(observation["request_id"])
            else:
                item, status = _item_load(post["item"]), post["status"]
                if item.published_at != published:
                    raise ValueError("历史相同源 ID 的发布时间不一致，不能重建配置范围")
            if status == "pending" and detail_enrichment_trigger(item.title) and (observation["stock"], item.source_item_id) not in queued:
                queued.add((observation["stock"], item.source_item_id))
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
            self._suspend_proxy_recovery()
            current_cutoff = self.db.execute("SELECT effective_to FROM job_lifecycle WHERE job=?", (job,)).fetchone()[0]
            cutoff = min(self._window_end(config), self.clock()) if config["to_date"] != old["to_date"] else current_cutoff
            start = datetime.fromisoformat(config["from_date"] + "T00:00:00+08:00").timestamp()
            if start > cutoff:
                raise ValueError("开始日期晚于当前可观察时间，不能创建未来数据任务")
            before = self._lifecycle_snapshot(job)
            retained = self._retained_halt_targets()
            scope_changed = (set(config["stocks"]) != set(old["stocks"])
                             or config["from_date"] != old["from_date"] or config["to_date"] != old["to_date"])
            self.db.execute("UPDATE jobs SET config=? WHERE id=?", (_json(config), job))
            self._set("effective_to_epoch", cutoff)
            if scope_changed:
                affected = set(old["stocks"]) ^ set(config["stocks"])
                if config["from_date"] != old["from_date"] or config["to_date"] != old["to_date"]:
                    affected |= set(old["stocks"]) | set(config["stocks"])
                affected_targets = [task_id for task_id in retained
                                    if self.db.execute("SELECT stock FROM tasks WHERE id=?", (task_id,)).fetchone()[0] in affected]
                self._detach_halt_probe(old, affected_targets)
                projection_requests = self._rebuild_scope(job, old, config, cutoff, retained)
            self._end_segment("config_updated")
            if not self._get("active_halt"):
                self._set("state", "paused")
                self._set("reason", "配置已更新；等待开始" + ("并重新核对来源列表位置" if scope_changed else ""))
            self._record_job_revision(job, config, cutoff, "config_updated", before)
            self._event("config_updated", "修改任务配置；已采原始响应与历史配置保留", {"previous": old, "config": config, "scope_rebuilt": scope_changed})
            self._ensure_current_stock_runtimes()
            for runtime in self._stock_runtimes(job=job):
                with self._stock_context(runtime["stock"], job):
                    self._suspend_proxy_recovery()
                    self._set("probe", False)
                    if not runtime["active_halt"]:
                        self._set("state", "paused")
                        self._set("reason", "配置已更新，等待开始")
                        self._set("resume_recovery", True)
                    if runtime["stock"] not in config["stocks"] and runtime["active_halt"]:
                        self._set("halted_probe_only", True)
                    self._end_segment("config_updated")
            self._publish_stock_summary()
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
            self._suspend_proxy_recovery()
            before = self._lifecycle_snapshot(job)
            retained = self._retained_halt_targets()
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
            for runtime in self._stock_runtimes(job=job):
                with self._stock_context(runtime["stock"], job):
                    self._suspend_proxy_recovery()
                    self._set("probe", False)
                    if not runtime["active_halt"]:
                        self._set("state", "paused")
                        self._set("reason", "任务已归档")
                    self._end_segment("job_archived")
            self._publish_stock_summary()
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
