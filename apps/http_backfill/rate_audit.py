"""Read-only source-request timing audit; stdlib-only, safe to feed to python -.

Ledger timestamps are wall-clock observations, not packet captures. Posts are
not source requests. Full CLI audits use one SQLite read snapshot; live status
uses a bounded tail and never claims that tail covers the whole history.
"""
from __future__ import annotations

import argparse
from collections import Counter, OrderedDict
from contextlib import closing
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import sys
import time
from weakref import WeakKeyDictionary

_CACHE = WeakKeyDictionary()
TAIL_LIMIT = 1000
REVISION_LIMIT = 256


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not -62135596800 <= value < 253402300800:
        return None
    return float(value)


def _iso(value):
    value = _number(value)
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def _threshold(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("审计间隔必须是大于 0 的有限秒数")
    return float(value)


def _columns(db, table):
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def _config_interval(raw):
    try:
        value = json.loads(raw).get("interval_seconds", 60)
        return _threshold(value)
    except (ValueError, TypeError, AttributeError):
        return None


class _Policies:
    def __init__(self, db, bounded):
        self.db, self.bounded, self.cache = db, bounded, {}
        self.history, self.truncated = [], False
        self.revisions = {"job", "revision", "changed", "config"}.issubset(_columns(db, "job_revisions"))
        self.jobs = {"id", "config"}.issubset(_columns(db, "jobs"))

    def _load(self, job):
        if job in self.cache:
            return self.cache[job]
        records = []
        if self.revisions:
            sql = "SELECT revision,changed,config FROM job_revisions WHERE job=? ORDER BY revision DESC"
            params = (job,)
            if self.bounded:
                sql += " LIMIT ?"
                params += (REVISION_LIMIT + 1,)
            rows = self.db.execute(sql, params).fetchall()
            if self.bounded and len(rows) > REVISION_LIMIT:
                self.truncated = True
                rows = rows[:REVISION_LIMIT]
            for revision, changed, config in reversed(rows):
                records.append({"job": job, "revision": revision, "changed": _number(changed),
                                "changed_at": _iso(changed), "interval_seconds": _config_interval(config),
                                "source": "recorded_job_revision"})
        if not records and self.jobs:
            row = self.db.execute("SELECT config FROM jobs WHERE id=?", (job,)).fetchone()
            if row:
                records.append({"job": job, "revision": None, "changed": None, "changed_at": None,
                                "interval_seconds": _config_interval(row[0]), "source": "current_job_only"})
        self.cache[job] = records
        # Summaries contain intervals only, never full configs or credentials.
        self.history.extend({k: v for k, v in record.items() if k != "changed"} for record in records)
        return records

    def interval_at(self, request):
        records = self._load(request["job"])
        if request.get("probe") and len({r["interval_seconds"] for r in records}) > 1:
            return None  # A probe can use a retained pre-edit halt config.
        started = _number(request["started"])
        eligible = [r for r in records if started is not None and r["changed"] is not None and r["changed"] <= started]
        if not eligible:
            return None
        return max(eligible, key=lambda r: (r["changed"], r["revision"]))["interval_seconds"]


def audit_connection(db, interval=60, *, limit=None, details_limit=50):
    """Audit an existing caller-owned read snapshot; performs no writes/commits."""
    interval = _threshold(interval)
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 10000):
        raise ValueError("limit 必须为 1–10000 的整数")
    if type(details_limit) is not int or not 0 <= details_limit <= 1000:
        raise ValueError("details_limit 必须为 0–1000 的整数")
    columns = _columns(db, "requests")
    if not {"id", "started", "finished", "kind", "outcome"}.issubset(columns):
        raise ValueError("此数据库没有可核验的来源 requests 台账，不能根据帖子数判断请求速率")
    names = ("id", "job", "kind", "page", "started", "finished", "outcome", "purpose", "probe", "network_attempted")
    selected = ",".join(name if name in columns else f"NULL AS {name}" for name in names)
    if limit is None:
        rows = db.execute(f"SELECT {selected} FROM requests ORDER BY id")
        truncated = False
    else:
        tail = db.execute(f"SELECT {selected} FROM requests ORDER BY id DESC LIMIT ?", (limit + 1,)).fetchall()
        truncated = len(tail) > limit
        rows = reversed(tail[:limit])
    policies = _Policies(db, limit is not None)
    kinds, outcomes, classification = Counter(), Counter(), Counter()
    unknown = dict.fromkeys(("attempt_rows", "timing_rows", "unfinished_rows", "pairs", "overlaps", "clock_anomalies"), 0)
    violations, policy_violations = [], []
    count = confirmed = non_source_attempts = checked = violation_count = policy_checked = policy_count = policy_unknown = 0
    minimum = start_minimum = first_started = last_started = None
    first_id = last_id = None
    previous, between = None, 0
    for values in rows:
        row = dict(zip(names, values))
        count += 1
        first_id = row["id"] if first_id is None else first_id
        last_id = row["id"]
        if row["network_attempted"] != 1:
            if row["network_attempted"] == 0 and row["outcome"] == "proxy_error" and row["finished"] is not None:
                non_source_attempts += 1
                continue  # Confirmed provider/CONNECT failure before an origin request.
            unknown["attempt_rows"] += 1
            if row["outcome"] in {"reserved", "interrupted_unknown"} or row["finished"] is None:
                unknown["unfinished_rows"] += 1
            between += 1
            continue
        confirmed += 1
        kinds[str(row["kind"])] += 1
        outcomes[str(row["outcome"])] += 1
        if row["kind"] == "list":
            classification["list_recovery" if row["purpose"] == "recovery" else "list_forward"] += 1
        elif row["kind"] == "detail":
            classification["detail"] += 1
        if row["outcome"] == "redirect":
            classification["redirect"] += 1
        if row["probe"]:
            classification["probe"] += 1
        start, finish = _number(row["started"]), _number(row["finished"])
        real_finish = finish if row["outcome"] not in {"reserved", "interrupted_unknown"} else None
        if start is None or real_finish is None:
            unknown["timing_rows"] += 1
        if real_finish is None:
            unknown["unfinished_rows"] += 1
        if start is not None:
            first_started = start if first_started is None else min(first_started, start)
            last_started = start if last_started is None else max(last_started, start)
        invalid_duration = start is not None and real_finish is not None and real_finish < start
        if invalid_duration:
            unknown["clock_anomalies"] += 1
        row["valid_finish"] = real_finish if not invalid_duration else None
        policies.interval_at(row)  # Include even a single request's history explanation.
        if previous is not None:
            prev_start = _number(previous["started"])
            if start is not None and prev_start is not None:
                start_gap = start - prev_start
                start_minimum = start_gap if start_minimum is None else min(start_minimum, start_gap)
            missing = start is None or previous["valid_finish"] is None
            if missing or between:
                unknown["pairs"] += 1
            if not missing:
                gap = start - previous["valid_finish"]
                minimum = gap if minimum is None else min(minimum, gap)
                detail = {"previous_request_id": previous["id"], "request_id": row["id"], "previous_job": previous["job"], "job": row["job"],
                          "kind": row["kind"], "page": row["page"], "previous_finished_at": _iso(previous["valid_finish"]),
                          "started_at": _iso(start), "gap_seconds": gap, "unknown_attempts_between": between}
                if gap < 0:
                    unknown["overlaps"] += 1
                    unknown["pairs"] += int(not between)
                else:
                    checked += 1
                    if gap < interval:
                        violation_count += 1
                        if len(violations) < details_limit:
                            violations.append({**detail, "required_seconds": interval})
                required = policies.interval_at(previous)
                if required is None or gap < 0 or between:
                    policy_unknown += 1
                else:
                    policy_checked += 1
                    if gap < required:
                        policy_count += 1
                        if len(policy_violations) < details_limit:
                            policy_violations.append({**detail, "required_seconds": required})
            else:
                policy_unknown += 1
        previous, between = row, 0
    # SQLite groups full CLI history without Python retaining a corpus-sized
    # minute dictionary. The status query only touches IDs in its bounded tail.
    minute_query = "SELECT COUNT(*) n FROM requests WHERE network_attempted=1 AND typeof(started) IN ('integer','real') AND started>=-62135596800 AND started<253402300800"
    minute_parameters = ()
    if "network_attempted" not in columns:
        maximum = 0
    else:
        if limit is not None:
            minute_query += " AND id>=? AND id<=?"
            minute_parameters = (first_id or 0, last_id or 0)
        minute_query += " GROUP BY CAST(started/60 AS INTEGER)"
        maximum = db.execute("SELECT COALESCE(MAX(n),0) FROM (" + minute_query + ")", minute_parameters).fetchone()[0]
    uncertain = any(unknown.values()) or checked == 0
    verdict = "fail" if violation_count else "unknown" if uncertain else "pass"
    return {"schema_version": "source-rate-audit.v1", "generated_at": _iso(time.time()), "interval_seconds": interval,
            "verdict": verdict, "verdict_scope": "recorded_confirmed_requests_in_scope",
            "scope": {"mode": "all" if limit is None else "tail", "limit": limit, "first_request_id": first_id,
                      "last_request_id": last_id, "truncated": truncated},
            "ledger_rows": count, "confirmed_requests": confirmed, "non_source_attempts": non_source_attempts,
            "by_kind": dict(kinds), "by_outcome": dict(outcomes),
            "classification": {name: classification[name] for name in ("list_forward", "list_recovery", "detail", "redirect", "probe")},
            "first_started_at": _iso(first_started), "last_started_at": _iso(last_started),
            "min_finish_to_start_seconds": minimum, "min_start_to_start_seconds": start_minimum,
            "checked_pairs": checked, "violations": {"count": violation_count, "items": violations, "omitted": violation_count - len(violations)},
            "unknown": unknown, "minute_buckets": {"max_requests": maximum, "note": "自然分钟桶仅供参考；同一分钟两次不是间隔违规的等价判据"},
            "config_policy": {"checked_pairs": policy_checked, "violations": {"count": policy_count, "items": policy_violations, "omitted": policy_count - len(policy_violations)},
                              "unknown_pairs": policy_unknown, "history_truncated": policies.truncated},
            "config_history": policies.history,
            "evidence_notes": ["帖子数不等于来源请求数；列表一次可返回多条帖子，列表回扫/详情/重定向/人工探测都计来源请求。",
                               "network_attempted=1 是台账确认的尝试；其他标记包括预留或中断，不能假定从未到达来源。",
                               "间隔使用前次完成到下次台账开始的墙上时钟记录；新版记录来源发送前的开始，不能证明包级时间，负间隔/时钟异常单独视为未知。",
                               "固定 interval 阈值与历史任务配置分开；配置推断采用前次请求开始时的已保存修订，旧版回填的初始配置不证明全部旧历史。",
                               "人工探测可能使用旧阻断配置；配置间隔曾变化时不猜其实际策略。范围之外的历史不由尾样本证明。"]}


def tail_audit(engine):
    """Bounded, cached status summary; caller already owns the Engine mutex."""
    latest = engine.db.execute("SELECT id,finished,outcome,network_attempted FROM requests ORDER BY id DESC LIMIT 1").fetchone()
    signature = (tuple(latest) if latest else None, engine.db.total_changes)
    now = time.monotonic()
    saved = _CACHE.get(engine)
    if saved and saved["signature"] == signature and now - saved["cached_at"] < 15:
        return saved["audit"]
    result = audit_connection(engine.db, 60, limit=TAIL_LIMIT, details_limit=20)
    _CACHE[engine] = {"signature": signature, "cached_at": now, "audit": result}
    return result


def _has_ledger(path):
    if not path.is_file():
        return False
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        return {"id", "started", "finished", "kind", "outcome"}.issubset(_columns(db, "requests"))


def audit_database(path, interval=60, *, details_limit=50):
    requested = Path(path).expanduser().resolve()
    actual, fallback = requested, False
    if not _has_ledger(actual):
        legacy = requested.parent / "experiment.sqlite3"
        if requested.name == "collector.db" and _has_ledger(legacy):
            actual, fallback = legacy, True
        else:
            raise ValueError("指定数据库没有来源 requests 台账；帖子表不能用于证明每分钟请求速率")
    with closing(sqlite3.connect(actual.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        result = audit_connection(db, interval, details_limit=details_limit)
        result.update(requested_db=str(requested), db_path=str(actual), legacy_fallback=fallback, read_only_snapshot=True)
        db.rollback()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=60)
    parser.add_argument("--details-limit", type=int, default=50)
    args = parser.parse_args(argv)
    try:
        result = audit_database(args.db, args.interval, details_limit=args.details_limit)
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"verdict": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 3
    result["exit_code"] = {"pass": 0, "fail": 1, "unknown": 2}[result["verdict"]]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
