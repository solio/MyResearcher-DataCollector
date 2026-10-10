#!/usr/bin/env python3
"""Status, request logs and explicit task controls for a native backfill node."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
STATES = {"running": "采集中", "paused": "已暂停", "blocked": "已阻断",
          "completed": "已完成", "error": "异常停止"}
OUTCOMES = {"real_data": "正常", "access_block": "验证码/身份核验",
            "transport_error": "传输异常", "proxy_error": "代理异常",
            "schema_error": "结构异常", "source_error": "源站错误",
            "http_error": "HTTP 异常", "redirect": "等待重定向",
            "detail_unavailable": "正文不可用", "interrupted_unknown": "请求被中断"}


def text(value):
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value)) if value is not None else "—"


def timestamp(value):
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(value, SHANGHAI)
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return text(value) + "（时区未知）"
        parsed = parsed.astimezone(SHANGHAI)
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


class Client:
    def __init__(self, url, data_dir):
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("API 地址必须是无认证参数的 HTTP/HTTPS 地址")
        self.url = url.rstrip("/")
        self.token = (data_dir / "console.token").read_text().strip()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, *, query=None, body=None, method="GET"):
        url = self.url + "/api/" + path
        if query:
            url += "?" + urlencode(query)
        request = urllib.request.Request(url, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        with self.opener.open(request, timeout=10) as response:
            return json.load(response)

    def get(self, path, query=None):
        return self.request(path, query=query)

    def control(self, stock, action, job=None):
        if not re.fullmatch(r"[0-9]{6}", stock):
            raise ValueError("股票代码须为六位数字")
        if action not in {"start", "pause", "retry"}:
            raise ValueError("不支持的逐股控制动作")
        body = {"action": action}
        if job is not None:
            body["job_id"] = job
        return self.request(f"stocks/{stock}/control", body=body, method="POST")


def state_signature(status):
    barrier = status.get("node_source_auth_barrier") or {}
    return (status.get("state"), barrier.get("request_id"),
            tuple((r["job"], r["stock"], r["state"], r.get("active_halt"),
                   (r.get("block_evidence") or {}).get("id"))
                  for r in status.get("stock_runtimes", []) + status.get("detached_stock_runtimes", [])))


def print_state(status):
    state = status.get("state")
    print(f"状态：{STATES.get(state, text(state))} · {timestamp(status.get('server_time'))} 北京时间", flush=True)
    barrier = status.get("node_source_auth_barrier")
    if barrier:
        evidence = barrier.get("block_evidence") or {}
        print(f"节点已停止来源请求：{text(barrier.get('stock'))} · 请求 #{text(barrier.get('request_id'))} · "
              f"{text(barrier.get('reason') or evidence.get('reason'))}", flush=True)
    for runtime in status.get("stock_runtimes", []) + status.get("detached_stock_runtimes", []):
        evidence = runtime.get("block_evidence") or {}
        if runtime.get("active_halt"):
            print(f"{runtime['stock']} 阻断：{text(runtime['active_halt'])} · 请求 #{text(evidence.get('id'))} · "
                  f"{text(evidence.get('reason') or runtime.get('reason'))}", flush=True)
    if not barrier and not any(r.get("active_halt") for r in status.get("stock_runtimes", [])
                               + status.get("detached_stock_runtimes", [])):
        print("当前没有阻断", flush=True)


def print_status(status):
    print_state(status)
    config = status.get("config") or {}
    aggregate = status.get("aggregate") or {}
    print(f"窗口：{text(config.get('from_date'))} ～ {text(config.get('to_date'))} · "
          f"{text(config.get('client'))} · 请求间隔 {text(config.get('interval_seconds'))} 秒")
    print(f"已采帖子：{text(aggregate.get('unique_posts'))} · "
          f"已补正文：{text(aggregate.get('body_complete'))} · 请求记录：{text(aggregate.get('attempts'))}")
    for runtime in status.get("stock_runtimes", []) + status.get("detached_stock_runtimes", []):
        target = runtime.get("current") or {}
        label = "正文 " + text(target.get("post_id")) if target.get("kind") == "detail" else "列表"
        print(f"{runtime['stock']}：{STATES.get(runtime['state'], text(runtime['state']))} · "
              f"{label} · 源页码 {text(target.get('page'))} · 下一次 {timestamp(runtime.get('next_request_at'))}")


def print_request(row):
    target = "正文 " + text(row.get("post_id")) if row.get("kind") == "detail" else "列表"
    outcome = row.get("display_outcome") or row.get("outcome")
    line = (f"{timestamp(row.get('started_at') or row.get('started'))} · #{row['id']} · "
            f"{text(row.get('stock'))} · {target} · 源页码 {text(row.get('page'))} · "
            f"{OUTCOMES.get(outcome, text(outcome))} · HTTP {text(row.get('http_status'))}")
    error = row.get("display_error") or row.get("error")
    print(line + (" · " + text(error) if error else ""), flush=True)


def request_rows(client, limit, after=None):
    query = {"paged": 1, "limit": limit}
    rows = []
    while True:
        page = client.get("requests", query)
        rows.extend(r for r in page["items"] if after is None or r["id"] > after)
        if (after is None or not page["has_more"] or not page["items"]
                or page["items"][-1]["id"] <= after):
            return sorted(rows, key=lambda r: r["id"])
        query.update(snapshot_id=page["snapshot_id"], before_id=page["next_cursor"])


def logs(client, limit, follow):
    status = client.get("status")
    print_state(status)
    signature = state_signature(status)
    rows = request_rows(client, limit)
    cursor = rows[0]["id"] - 1 if rows else 0
    disconnected = False
    while True:
        for row in rows:
            if row.get("outcome") == "reserved" or row.get("finished") is None:
                break
            print_request(row)
            cursor = row["id"]
        if not follow:
            return
        time.sleep(5)
        try:
            status = client.get("status")
            if disconnected or state_signature(status) != signature:
                print_state(status)
                signature = state_signature(status)
            rows = request_rows(client, 200, after=cursor)
            disconnected = False
        except (OSError, ValueError) as exc:
            rows = []
            if not disconnected:
                print(f"无法读取节点 API（{type(exc).__name__}）；来源是否阻断需恢复连接后核实", flush=True)
            disconnected = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent / "data")
    parser.add_argument("--url", default="http://127.0.0.1:8790")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("status", help="查看当前采集状态和阻断证据")
    log_parser = sub.add_parser("logs", help="查看采集请求记录")
    log_parser.add_argument("-f", "--follow", action="store_true")
    log_parser.add_argument("-n", "--lines", type=int, default=20)
    for action, label in [("collect", "继续本股采集"), ("pause", "暂停本股"), ("probe", "单次探测原目标")]:
        control = sub.add_parser(action, help=label)
        control.add_argument("stock")
        control.add_argument("--job-id", type=int)
    configure = sub.add_parser("configure", help="创建明确的股票/日期任务，保持暂停")
    configure.add_argument("--stocks", nargs="+", required=True)
    configure.add_argument("--from-date", required=True)
    configure.add_argument("--to-date", required=True)
    configure.add_argument("--interval", type=float, default=60)
    configure.add_argument("--client", choices=["curl", "urllib"], default="curl")
    page = sub.add_parser("start-page", help="为本股设置起始页，保持暂停")
    page.add_argument("stock")
    page.add_argument("page", type=int)
    page.add_argument("--job-id", type=int)
    interval = sub.add_parser("interval", help="修改已暂停任务的请求间隔")
    interval.add_argument("seconds", type=float)
    args = parser.parse_args()
    if args.action == "logs" and not 1 <= args.lines <= 200:
        parser.error("记录数量须为 1～200")
    try:
        client = Client(args.url, args.data_dir)
        if args.action == "status":
            print_status(client.get("status"))
        elif args.action == "logs":
            logs(client, args.lines, args.follow)
        elif args.action == "configure":
            print_status(client.request("jobs", method="POST", body={"stocks": args.stocks,
                "from_date": args.from_date, "to_date": args.to_date,
                "interval_seconds": args.interval, "client": args.client}))
        elif args.action == "start-page":
            if not re.fullmatch(r"[0-9]{6}", args.stock):
                raise ValueError("股票代码须为六位数字")
            body = {"page": args.page}
            if args.job_id is not None:
                body["job_id"] = args.job_id
            print_status(client.request(f"stocks/{args.stock}/start-page", method="POST", body=body))
        elif args.action == "interval":
            config = client.get("status").get("config")
            if not config:
                raise ValueError("尚未创建任务")
            print_status(client.request("jobs/current", method="PATCH",
                                        body={**config, "interval_seconds": args.seconds}))
        else:
            result = client.control(args.stock, {"collect": "start", "pause": "pause", "probe": "retry"}[args.action], args.job_id)
            print_status(result)
    except KeyboardInterrupt:
        return 0
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read(16384)).get("error") or "节点 API 拒绝请求"
        except (ValueError, OSError):
            message = "节点 API 拒绝请求"
        print(f"HTTP {exc.code}：{text(message)}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"无法读取节点状态（{type(exc).__name__}）；请检查服务和令牌文件", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
