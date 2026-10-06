"""Paced, durable time-window navigation for the HTTP worker.

Each step schedules one ordinary list task. Sampled pages are observations,
not contiguous coverage, and historical anchors are navigation hints only.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from zoneinfo import ZoneInfo

from myresearcher_collector.page_anchor import PageAnchor, choose_anchor, predict_page
from myresearcher_collector.sources.eastmoney_guba.list_paging import list_page_url

SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_PROBES = 40
MAX_PAGE = 1_000_000


def _source_time(value):
    if not isinstance(value, str):
        raise ValueError("日期定位缺少完整来源发布时间")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise ValueError("日期定位发布时间必须为完整 YYYY-MM-DD HH:MM:SS") from exc
    if parsed.strftime("%Y-%m-%d %H:%M:%S") != value:
        raise ValueError("日期定位发布时间格式不明确")
    return parsed.replace(tzinfo=SHANGHAI)


class WindowSeekMixin:
    def _seek_state(self, job, stock):
        return self._get(f"window_seek:{job}:{stock}")

    def _save_seek_state(self, state):
        self._set(f"window_seek:{state['job']}:{state['stock']}", state)

    def _fail_window_seek(self, state, message):
        state.update(phase="error", error=message)
        self._save_seek_state(state)
        raise ValueError(message)

    def _retained_seek_task(self, job, stock):
        if not self._get("active_halt"):
            return None
        task_id = self._get("halted_task_id") or self._get("halt_task_id")
        row = self.db.execute("SELECT id FROM tasks WHERE id=? AND job=? AND stock=?",
                              (task_id, job, stock)).fetchone() if task_id else None
        return row[0] if row else None

    def _enqueue_seek(self, job, stock, page):
        if type(page) is not int or not 1 <= page <= MAX_PAGE:
            raise ValueError("日期定位页码超出允许范围")
        if self.db.execute(
            "SELECT 1 FROM tasks WHERE job=? AND stock=? AND kind='list' "
            "AND purpose='seek' AND page=? AND status IN ('pending','inflight') "
            "AND id!=COALESCE(?, -1)",
            (job, stock, page, self._retained_seek_task(job, stock)),
        ).fetchone():
            return
        url = list_page_url(stock, page)
        self.db.execute(
            "INSERT INTO tasks(job,kind,stock,page,url,original_url,purpose) "
            "VALUES(?,'list',?,?,?,?,'seek')", (job, stock, page, url, url),
        )

    def _begin_window_seek(self, job, stock, reason="initial"):
        config = self._config()
        cutoff = self._get("effective_to_epoch")
        if (job != self._get("job_id") or not config or stock not in config["stocks"]
                or not isinstance(cutoff, (int, float)) or isinstance(cutoff, bool)):
            raise ValueError("日期定位任务不在当前有效范围")
        if self.db.execute(
            "SELECT 1 FROM tasks WHERE job=? AND stock=? AND kind='list' "
            "AND purpose='seek' AND status='inflight'", (job, stock),
        ).fetchone():
            raise RuntimeError("日期定位请求仍在运行，不能重置")
        self.db.execute(
            "UPDATE tasks SET status='cancelled' WHERE job=? AND stock=? "
            "AND kind='list' AND purpose='seek' AND status='pending' AND id!=COALESCE(?, -1)",
            (job, stock, self._retained_seek_task(job, stock)),
        )
        state = {
            "job": job, "stock": stock, "phase": "searching", "reason": reason,
            "target_time": datetime.fromtimestamp(cutoff, SHANGHAI).strftime("%Y-%m-%d %H:%M:%S"),
            "target_epoch": cutoff, "target_from_date": config["from_date"],
            "probes": 0, "visited": [], "observations": [],
            "too_new": None, "too_old": None, "current_page": 1,
            "start_page": None, "anchor_hint": None,
        }
        hint = self._historical_seek_hint(state, {"request_id": None, "source_count": None})
        if hint:
            state.update(current_page=hint["predicted_page"], anchor_hint=hint)
        self._save_seek_state(state)
        self._enqueue_seek(job, stock, state["current_page"])
        self._event("window_seek_started", f"{stock} 开始定位回补结束日期", state)
        return state

    def _seek_navigation(self, rows):
        # _navigation_rows has a nonstandard-row fallback for recovery IDs;
        # that fallback must never supply timestamp bounds for time seeking.
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("日期定位来源行结构不明确")
        navigation = [row for row in rows
                      if type(row.get("post_type")) is int and row["post_type"] == 0
                      and type(row.get("post_top_status")) is int and row["post_top_status"] == 0]
        if rows and not navigation:
            raise ValueError("日期定位页没有非置顶标准帖，无法安全确定时间范围")
        times = [_source_time(row.get("post_publish_time")) for row in navigation]
        if any(a < b for a, b in zip(times, times[1:])):
            raise ValueError("日期定位列表不符合非置顶标准帖发布时间降序")
        return navigation, times

    def _historical_seek_hint(self, state, stats):
        target = datetime.fromtimestamp(state["target_epoch"], SHANGHAI)
        candidates = self.db.execute(
            "SELECT p.request_id,p.page,p.source_count,p.rows,r.finished "
            "FROM page_observations p JOIN requests r ON r.id=p.request_id "
            "WHERE p.stock=? AND r.stock=p.stock AND r.job=p.job AND r.page=p.page "
            "AND r.kind='list' AND r.outcome='real_data' AND r.finished IS NOT NULL "
            "AND (? IS NULL OR p.request_id<>?) AND p.rows>0 AND p.earliest IS NOT NULL AND p.latest IS NOT NULL "
            "ORDER BY CASE WHEN p.earliest<=? AND p.latest>=? THEN 0 ELSE "
            "MIN(ABS(julianday(p.earliest)-julianday(?)),ABS(julianday(p.latest)-julianday(?))) END, "
            "r.finished DESC LIMIT 64",
            (state["stock"], stats.get("request_id"), stats.get("request_id"), state["target_time"], state["target_time"],
             state["target_time"], state["target_time"]),
        ).fetchall()
        anchors = []
        for candidate in candidates:
            try:
                rows = [json.loads(row[0]) for row in self.db.execute(
                    "SELECT source_row FROM observations WHERE request_id=? ORDER BY id",
                    (candidate["request_id"],),
                )]
                _, times = self._seek_navigation(rows)
                if (not times or type(candidate["page"]) is not int
                        or not 1 <= candidate["page"] <= MAX_PAGE
                        or candidate["rows"] != len(rows)):
                    continue
                count = candidate["source_count"]
                if type(count) is not int or count < 0:
                    count = None
                anchors.append(PageAnchor(
                    source="eastmoney_guba", stock_code=state["stock"],
                    observed_at=datetime.fromtimestamp(candidate["finished"], timezone.utc),
                    page_no=candidate["page"], page_min_time=min(times), page_max_time=max(times),
                    source_count=count, page_size=len(rows),
                ))
            except (ValueError, TypeError, KeyError, OverflowError, OSError):
                # A historical sample is optional advice, never present-day
                # validation or a reason to trust ambiguous old navigation.
                continue
        anchor = choose_anchor(anchors, target)
        if anchor is None:
            return None
        current_count = stats.get("source_count")
        if type(current_count) is not int or current_count < 0:
            current_count = None
        predicted = predict_page(anchor, current_count)
        if not 1 <= predicted <= MAX_PAGE:
            return None
        return {
            "observed_page": anchor.page_no, "predicted_page": predicted,
            "observed_at": anchor.observed_at.isoformat(),
            "page_min_time": anchor.page_min_time.strftime("%Y-%m-%d %H:%M:%S"),
            "page_max_time": anchor.page_max_time.strftime("%Y-%m-%d %H:%M:%S"),
            "source_count": anchor.source_count, "page_size": anchor.page_size,
            "navigation_only": True,
        }

    def _finish_window_seek(self, state, start_page, reason):
        start_page = max(1, start_page)
        state.update(phase="complete", start_page=start_page, current_page=None,
                     completion_reason=reason)
        self._save_seek_state(state)
        self._enqueue_list(state["job"], state["stock"], start_page)
        self._event("window_seek_completed", f"{state['stock']} 日期定位完成，从第 {start_page} 页顺序采集", state)
        return state

    def _advance_window_seek(self, task, rows, stats):
        state = self._seek_state(task["job"], task["stock"])
        if not state or state.get("phase") not in {"searching", "error"}:
            raise ValueError("日期定位状态缺失或已结束，不能猜测采集页码")
        config = self._config()
        if (task["job"] != self._get("job_id") or not config or task["stock"] not in config["stocks"]
                or state["target_epoch"] != self._get("effective_to_epoch")
                or state["target_from_date"] != config["from_date"]):
            raise ValueError("日期定位范围已改变，旧请求不能推进新任务")
        if state["phase"] == "error":
            request = self.db.execute(
                "SELECT task,probe FROM requests WHERE id=?", (stats["request_id"],),
            ).fetchone()
            if (not self._get("active_halt") or not request or not request["probe"]
                    or request["task"] != task["id"]):
                raise ValueError("日期定位错误只能通过人工单次探测重新验证")
            self._seek_navigation(rows)
            # A valid one-shot probe does not rehabilitate stale or conflicting
            # bounds. Reposition from a historical hint after explicit resume.
            return self._begin_window_seek(task["job"], task["stock"], "seek_error_recovery")
        page = task["page"]
        if page != state["current_page"] or page in state["visited"] or state["probes"] >= MAX_PROBES:
            self._fail_window_seek(state, "日期定位页码重复或探测预算已耗尽")
        state["probes"] += 1
        state["visited"].append(page)
        try:
            _, times = self._seek_navigation(rows)
        except ValueError as exc:
            self._fail_window_seek(state, str(exc))
        earliest = min(times).timestamp() if times else None
        latest = max(times).timestamp() if times else None
        observation = {
            "page": page, "request_id": stats["request_id"], "earliest": earliest, "latest": latest,
            "rows": len(rows), "source_count": stats.get("source_count"),
            "id_sha256": stats.get("ordered_id_sha256"),
        }
        prior_observations = list(state["observations"])
        state["observations"].append(observation)
        for previous in prior_observations:
            if previous["earliest"] is None:
                if times and page > previous["page"]:
                    self._fail_window_seek(state, "日期定位空页之后出现来源帖子，分页范围不明确")
                continue
            if not times and page < previous["page"]:
                self._fail_window_seek(state, "日期定位较浅页面为空，但更深页面已有帖子，分页范围不明确")
            if times:
                older, newer = ((earliest, latest), (previous["earliest"], previous["latest"])) if page > previous["page"] else ((previous["earliest"], previous["latest"]), (earliest, latest))
                if older[0] > newer[0] or older[1] > newer[1]:
                    self._fail_window_seek(state, "日期定位不同页的时间范围不符合降序，已停止猜测")
                if (stats.get("ordered_id_sha256")
                        and stats["ordered_id_sha256"] == previous.get("id_sha256")):
                    self._fail_window_seek(state, "日期定位不同页返回相同来源 ID，无法确认真实页码")
        target = state["target_epoch"]
        if page == 1 and (not rows or latest <= target):
            return self._finish_window_seek(state, 1, "target_at_or_after_head")
        if times and earliest <= target <= latest:
            return self._finish_window_seek(state, page - 1, "target_in_observed_page")
        if times and earliest > target:
            state["too_new"] = max(page, state["too_new"] or 0)
        else:
            state["too_old"] = min(page, state["too_old"] or page)
        too_new, too_old = state["too_new"], state["too_old"]
        if too_new is not None and too_old is not None:
            if too_new >= too_old:
                self._fail_window_seek(state, "日期定位上下界冲突，不能猜测时间窗口")
            if too_old - too_new == 1:
                return self._finish_window_seek(state, too_new - 1, "adjacent_observed_bounds")
            next_page = (too_new + too_old) // 2
        elif too_new is not None:
            hint = self._historical_seek_hint(state, stats) if state["probes"] == 1 else None
            if hint and (hint["predicted_page"] <= too_new or hint["predicted_page"] in state["visited"]):
                hint = None
            state["anchor_hint"] = hint or state["anchor_hint"]
            next_page = hint["predicted_page"] if hint else min(MAX_PAGE, max(2, too_new * 2))
        else:
            hint = self._historical_seek_hint(state, stats) if state["probes"] == 1 else None
            if hint and (hint["predicted_page"] >= too_old or hint["predicted_page"] in state["visited"]):
                hint = None
            state["anchor_hint"] = hint or state["anchor_hint"]
            next_page = hint["predicted_page"] if hint else max(1, too_old // 2)
        if state["probes"] >= MAX_PROBES:
            self._fail_window_seek(state, f"日期定位超过 {MAX_PROBES} 次探测预算，尚未找到结束日期")
        if next_page in state["visited"] or not 1 <= next_page <= MAX_PAGE:
            self._fail_window_seek(state, "日期定位已到页码上限或候选重复，尚未确认时间窗口")
        state["current_page"] = next_page
        self._save_seek_state(state)
        self._enqueue_seek(task["job"], task["stock"], next_page)
        self._event("window_seek_progress", f"{task['stock']} 日期定位：下一探测第 {next_page} 页", state)
        return state
