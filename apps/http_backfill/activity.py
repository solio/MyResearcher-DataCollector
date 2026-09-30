"""Read-only, snapshot-bound keyset pages for the local activity ledger."""
from __future__ import annotations

from datetime import datetime, timezone
from collections import OrderedDict
import json
from weakref import WeakKeyDictionary

MAX_ID = (1 << 63) - 1
_TOTALS = WeakKeyDictionary()
_CACHE_LIMIT = 32


def _positive(value, label, maximum=MAX_ID):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{label} 必须为 1–{maximum} 的整数")
    return value


def _iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def _snapshot_total(engine, kind, snapshot):
    """Append-only ledgers allow cheap counts between bounded cached snapshots.

    Called under the Engine lock. The cache is process-local and weakly owns no
    Engine; it never changes SQLite, source state or long-lived schema.
    """
    per_engine = _TOTALS.setdefault(engine, {})
    totals = per_engine.setdefault(kind, OrderedDict())
    if snapshot in totals:
        totals.move_to_end(snapshot)
        return totals[snapshot]
    lower = max((key for key in totals if key < snapshot), default=None)
    upper = min((key for key in totals if key > snapshot), default=None)
    if lower is not None:
        delta = engine.db.execute(f"SELECT COUNT(*) FROM {kind} WHERE id>? AND id<=?", (lower, snapshot)).fetchone()[0]
        total = totals[lower] + delta
    elif upper is not None:
        delta = engine.db.execute(f"SELECT COUNT(*) FROM {kind} WHERE id>? AND id<=?", (snapshot, upper)).fetchone()[0]
        total = totals[upper] - delta
    else:
        total = engine.db.execute(f"SELECT COUNT(*) FROM {kind} WHERE id<=?", (snapshot,)).fetchone()[0]
    totals[snapshot] = total
    if len(totals) > _CACHE_LIMIT:
        totals.popitem(last=False)
    return total


def activity_page(engine, kind, *, limit=30, before_id=None, snapshot_id=None):
    """Return one descending ID page without changing any queue/source state.

    The first page freezes the highest recorded ID. Later pages retain that
    snapshot and seek strictly below before_id, so new arrivals cannot shift
    older pages. Rows may finish updating while a page is read: the snapshot
    fixes ledger membership, not immutable response fields of in-flight rows.
    """
    if kind not in {"requests", "events"}:
        raise ValueError("运行记录类型无效")
    _positive(limit, "limit", 200)
    if before_id is not None:
        _positive(before_id, "before_id")
    if snapshot_id is not None:
        _positive(snapshot_id, "snapshot_id")
    with engine._mutex:
        maximum = engine.db.execute(f"SELECT COALESCE(MAX(id),0) FROM {kind}").fetchone()[0]
        if snapshot_id is not None and snapshot_id > maximum:
            raise ValueError("snapshot_id 超出当前记录范围，请回到最新页")
        snapshot = snapshot_id if snapshot_id is not None else maximum
        total = _snapshot_total(engine, kind, snapshot)
        where, parameters = "id<=?", [snapshot]
        if before_id is not None:
            where += " AND id<?"
            parameters.append(before_id)
        rows = engine.db.execute(
            f"SELECT * FROM {kind} WHERE {where} ORDER BY id DESC LIMIT ?",
            (*parameters, limit + 1)).fetchall()
        has_more = len(rows) > limit
        instance = engine._get("instance_id")
        items = []
        for row in rows[:limit]:
            item = dict(row)
            if kind == "requests":
                for key in ("headers", "analysis"):
                    item[key] = json.loads(item[key]) if item.get(key) else None
                item["started_at"], item["finished_at"] = _iso(item["started"]), _iso(item["finished"])
            else:
                item["created_at"] = _iso(item["created"])
                item["evidence"] = json.loads(item["evidence"]) if item.get("evidence") else None
            item["instance_id"] = instance
            items.append(item)
        return {"items": items, "has_more": has_more,
                "next_cursor": items[-1]["id"] if has_more else None,
                "snapshot_id": snapshot, "total": total}


def activity_query(query):
    """Validate HTTP query strings; integers are strict ASCII decimal IDs."""
    def integer(name, default=None, maximum=MAX_ID):
        values = query.get(name)
        if values is None:
            return default
        if len(values) != 1 or not values[0] or not values[0].isascii() or not values[0].isdigit():
            raise ValueError(f"{name} 必须为正整数，且不能重复")
        return _positive(int(values[0]), name, maximum)
    if query.get("paged") != ["1"]:
        raise ValueError("paged 必须为 1")
    return {"limit": integer("limit", 30, 200),
            "before_id": integer("before_id"), "snapshot_id": integer("snapshot_id")}
