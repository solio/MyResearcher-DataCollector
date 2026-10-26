"""Durable inbox/outbox. Source observations and dispatch uncertainty stay distinct."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from datetime import datetime

KINDS = {
    "challenge": "需要人工验证",
    "http_401": "来源要求登录",
    "http_403": "来源拒绝访问",
    "http_407": "代理认证失败",
    "http_429": "来源限流",
}


def live_target(status):
    # Only the current job may raise a phone alert. A detached runtime is a block the node left
    # behind when it moved on to another job: that stock is no longer being collected, the
    # worker's resume guard cannot match it, and acting on it would pause whatever runs now.
    # It is history for the console, never an item the operator is asked to verify from a phone.
    candidates = [r for r in status.get("stock_runtimes", []) if r.get("active_halt") in KINDS]
    barrier = status.get("node_source_auth_barrier") or {}
    owner = next((r for r in candidates if r.get("job") == barrier.get("job")
                  and r.get("stock") == barrier.get("stock")), None)
    if owner:
        return owner
    return max(candidates, key=lambda r: (r.get("block_evidence") or {}).get("id", 0), default=None)


def fresh(node, now):
    if node.get("connection") != "online" or not node.get("status"):
        return False
    try:
        age = now - datetime.fromisoformat(node["last_seen_at"].replace("Z", "+00:00")).timestamp()
        return -10 <= age < 45
    except (ValueError, KeyError, TypeError):
        return False


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          PRAGMA journal_mode=WAL;
          PRAGMA foreign_keys=ON;
          CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, node TEXT NOT NULL,
            name TEXT NOT NULL, stock TEXT NOT NULL, kind TEXT NOT NULL,
            target TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
            created REAL NOT NULL, seen REAL NOT NULL, snoozed REAL NOT NULL DEFAULT 0);
          CREATE UNIQUE INDEX IF NOT EXISTS active_owner ON incidents(owner) WHERE state='pending';
          CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, token TEXT NOT NULL, updated REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS outbox (
            incident TEXT NOT NULL REFERENCES incidents(id), device TEXT NOT NULL REFERENCES devices(id),
            state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            due REAL NOT NULL DEFAULT 0, reminder INTEGER NOT NULL DEFAULT 0,
            error TEXT, PRIMARY KEY(incident,device));
          CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, incident TEXT NOT NULL, phase TEXT NOT NULL,
            result TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL);
        """)
        # A previous process may have died after submitting a probe. Never replay it.
        self.db.execute("UPDATE sessions SET phase='uncertain',result=? WHERE phase='dispatching'",
                        (json.dumps({"message": "主控重启，探测结果待核对；没有重发请求"}),))
        self.db.execute("UPDATE sessions SET phase='expired' WHERE phase IN ('preparing','verifying')")
        self.db.commit()

    def reconcile(self, nodes, now=None):
        now = time.time() if now is None else now
        with self.db:
            for node in nodes:
                if not fresh(node, now):
                    continue  # stale/offline is UNKNOWN, never resolution
                status, row = node["status"], live_target(node["status"])
                owner = None
                if row:
                    task = row.get("halt_task_id") or row.get("halted_task_id")
                    evidence = row.get("block_evidence") or {}
                    if not task or not evidence.get("id") or not status.get("instance_id"):
                        continue
                    owner = json.dumps([node["id"], status["instance_id"], row["job"], row["stock"], task])
                    target = {"instance_id": status["instance_id"], "job": row["job"],
                              "stock": row["stock"], "task": task, "request_id": evidence["id"],
                              "revision": (status.get("job") or {}).get("revision")}
                    active = self.db.execute("SELECT id FROM incidents WHERE owner=? AND state='pending'", (owner,)).fetchone()
                    if active:
                        self.db.execute("UPDATE incidents SET target=?,seen=?,kind=?,name=? WHERE id=?",
                                        (json.dumps(target), now, row["active_halt"], node["name"], active[0]))
                    else:
                        self.db.execute("INSERT INTO incidents(id,owner,node,name,stock,kind,target,created,seen) VALUES(?,?,?,?,?,?,?,?,?)",
                                        (uuid.uuid4().hex, owner, node["id"], node["name"], row["stock"],
                                         row["active_halt"], json.dumps(target), now, now))
                # Replaced/cleared is distinct from our own successfully verified result.
                self.db.execute("UPDATE incidents SET state='cleared' WHERE node=? AND state='pending' AND owner IS NOT ?",
                                (node["id"], owner))
            self.queue_notifications()

    def queue_notifications(self):
        self.db.execute("""INSERT OR IGNORE INTO outbox(incident,device)
                           SELECT i.id,d.id FROM incidents i CROSS JOIN devices d
                           WHERE i.state='pending'""")

    def device(self, device_id, token):
        with self.db:
            self.db.execute("INSERT INTO devices VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET token=excluded.token,updated=excluded.updated",
                            (device_id, token, time.time()))
            self.queue_notifications()

    def remove_device(self, device_id):
        with self.db:
            self.db.execute("DELETE FROM outbox WHERE device=?", (device_id,))
            self.db.execute("DELETE FROM devices WHERE id=?", (device_id,))

    def incident(self, ident):
        row = self.db.execute("SELECT * FROM incidents WHERE id=?", (ident,)).fetchone()
        if not row:
            raise ValueError("待处理事项不存在，请刷新")
        value = dict(row)
        value["target"] = json.loads(value["target"])
        return value

    def inbox(self, nodes):
        online = {n["id"]: fresh(n, time.time()) for n in nodes}
        items = []
        for row in self.db.execute("SELECT * FROM incidents WHERE state='pending' ORDER BY created DESC"):
            value = {k: row[k] for k in ("id", "node", "name", "stock", "kind", "created", "seen", "snoozed")}
            value.update(title=KINDS[row["kind"]], online=online.get(row["node"], False))
            value["can_verify"] = value["online"] and row["kind"] == "challenge"
            items.append(value)
        return items

    def snooze(self, ident):
        self.incident(ident)
        with self.db:
            self.db.execute("UPDATE incidents SET snoozed=? WHERE id=?", (time.time() + 3600, ident))
            self.db.execute("UPDATE outbox SET state='pending',attempts=0,reminder=reminder+1,due=? WHERE incident=?",
                            (time.time() + 3600, ident))

    def session(self, ident, incident=None, phase=None, result=None):
        if incident is not None:
            with self.db:
                self.db.execute("INSERT INTO sessions(id,incident,phase,created) VALUES(?,?,?,?)",
                                (ident, incident, phase, time.time()))
        elif phase is not None:
            with self.db:
                self.db.execute("UPDATE sessions SET phase=?,result=? WHERE id=?",
                                (phase, json.dumps(result or {}, ensure_ascii=False), ident))
        row = self.db.execute("SELECT * FROM sessions WHERE id=?", (ident,)).fetchone()
        if not row:
            raise ValueError("验证会话不存在或已结束")
        return {"id": row["id"], "phase": row["phase"], "result": json.loads(row["result"])}

    def notifications(self):
        return self.db.execute("""SELECT o.*,d.token,i.name,i.stock,i.kind FROM outbox o
            JOIN devices d ON d.id=o.device JOIN incidents i ON i.id=o.incident
            WHERE o.state='pending' AND o.due<=? AND i.snoozed<=? AND i.state='pending'
            ORDER BY i.created LIMIT 10""", (time.time(), time.time())).fetchall()

    def notified(self, item, error=None):
        attempts = item["attempts"] + 1
        with self.db:
            self.db.execute("UPDATE outbox SET state=?,attempts=?,due=?,error=? WHERE incident=? AND device=?",
                            ("accepted" if error is None else ("failed" if attempts >= 5 else "pending"),
                             attempts, time.time() + min(3600, 60 * 2 ** attempts), error,
                             item["incident"], item["device"]))


def notification_payload(item, config):
    # Stable across provider retries; provider acceptance != observed delivery.
    message_id = hashlib.sha256((item["incident"] + item["device"] + str(item["reminder"])).encode()).hexdigest()
    return {
        "payload": {"notification": {
            "category": config.get("category", "MARKETING"),
            "title": f"{item['name']} · {item['stock']}", "body": KINDS[item["kind"]],
            "appMessageId": message_id,
            "notifyId": int(message_id[:7], 16),
            "clickAction": {"actionType": 1, "uri": "waygate://incident/" + item["incident"],
                            "data": {"incident_id": item["incident"]}},
        }},
        "target": {"token": [item["token"]]},
        "pushOptions": {"testMessage": config.get("test_message", True), "ttl": 3600},
    }
