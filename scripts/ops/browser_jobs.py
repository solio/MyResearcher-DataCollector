#!/usr/bin/env python3
"""Native Python batch entry for managed backfill and CDP enrichment."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src"), str(Path(__file__).resolve().parent)]

from backfill_session import BrowserSession
import enrich_plan
from myresearcher_collector.backfill import resolve_backfill_range
from myresearcher_collector.cli.main import backfill_plan, build_parser, _json_default
from myresearcher_collector.sources.eastmoney_guba.parser import SHANGHAI


def stock_codes(values):
    codes = re.split(r"[\s,，]+", " ".join(values).strip())
    if not codes or any(not re.fullmatch(r"[0-9]{6}", code) for code in codes):
        raise ValueError("股票代码必须是6位数字，可用空格或逗号分隔")
    return list(dict.fromkeys(codes))


def roster():
    text = (REPO / "scripts/ops/backfill_all_stocks.sh").read_text(encoding="utf-8")
    match = re.search(r"^all_stocks=\(([^)]*)\)", text, re.M)
    if not match:
        raise ValueError("现有 backfill 股票清单无法读取，请用 --stocks 指定")
    return stock_codes([match[1]])


def backfill_arguments(args, stock):
    argv = ["backfill", "--source", "eastmoney_guba", "--stock", stock,
            "--data-dir", str(args.data_dir), "--list-only", "--acquisition-mode", "managed-chromium",
            "--min-interval", str(args.min_interval), "--max-interval", str(args.max_interval),
            "--challenge-wait", str(args.challenge_wait)]
    start_page = args.start_page
    if args.from_date:
        top = args.to_date or datetime.now(SHANGHAI).date().isoformat()
        if args.to_date is None and start_page is None and (args.data_dir / "collector.db").is_file():
            uri = (args.data_dir / "collector.db").as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as db:
                row = db.execute("SELECT covered_from FROM backfill_coverage WHERE source='eastmoney_guba' AND stock_code=? ORDER BY covered_from LIMIT 1", (stock,)).fetchone()
            if row:
                floor = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
                requested = resolve_backfill_range(source="eastmoney_guba", stock_code=stock,
                                                  from_value=args.from_date, to_value=top, days=None,
                                                  now=datetime.now(timezone.utc))
                if floor <= requested.from_time:
                    return None
                top = row[0]
            else:
                start_page = 1
        elif args.to_date is None and start_page is None:
            start_page = 1
        argv += ["--from", args.from_date, "--to", top]
    else:
        if args.to_date:
            raise ValueError("--to 需要与 --from 一起填写")
        argv += ["--days", str(args.days)]
        start_page = start_page or 1
    if start_page is not None:
        argv += ["--start-page", str(start_page)]
    if args.list_click_paging:
        argv.append("--list-click-paging")
    argv.append("--plan-only" if args.plan_only else "--confirm-live")
    return argv


def run_backfill(args):
    stocks = stock_codes(args.stocks) if args.stocks else roster()
    tasks = []
    for stock in stocks:
        argv = backfill_arguments(args, stock)
        if argv is None:
            print(f"{stock}：已有覆盖起点早于目标开始日期，本轮跳过")
        else:
            # Validate every target before creating a browser or writing data.
            parsed = build_parser().parse_args(argv)
            plan = backfill_plan(parsed)
            tasks.append((stock, argv, plan))
    if args.plan_only:
        print(json.dumps([plan for _, _, plan in tasks], ensure_ascii=False, indent=2, default=_json_default))
        return 0
    logs = REPO / "runtime/logs"
    logs.mkdir(parents=True, exist_ok=True)
    tag = datetime.now(timezone.utc).strftime("%Y%m%d")
    session = BrowserSession()
    try:
        for stock, argv, _ in tasks:
            report, error = logs / f"backfill-{stock}-{tag}.json", logs / f"backfill-{stock}-{tag}.err"
            print(f"{stock}：开始 backfill · 日志 {error}", flush=True)
            code = session.run({"argv": argv, "report": str(report), "error": str(error)})["exit_code"]
            if code:
                print(f"{stock}：采集已停止，保留断点；查看 {report} 和 {error}", flush=True)
                return code
            print(f"{stock}：本轮 backfill 完成", flush=True)
    finally:
        session.close()
    print("本轮 backfill 已结束")
    return 0


def enrich_arguments(args, stock):
    argv = ["enrich-details", "--source", "eastmoney_guba", "--stock", stock,
            "--data-dir", str(args.data_dir), "--acquisition-mode", "chrome-cdp",
            "--min-delay", str(args.min_delay), "--max-delay", str(args.max_delay),
            "--challenge-wait", str(args.challenge_wait), "--challenge-retries", str(args.challenge_retries),
            "--enrich-order", args.enrich_order, "--pace-model", args.pace_model,
            "--read-every", str(args.read_every), "--read-min", str(args.read_min), "--read-max", str(args.read_max)]
    if args.detail_referer_from_list:
        argv.append("--detail-referer-from-list")
    argv.append("--plan-only" if args.plan_only else "--confirm-live")
    return argv


def cdp_version(endpoint):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(endpoint.rstrip("/") + "/json/version", timeout=2) as response:
        value = json.load(response)
    if not isinstance(value.get("webSocketDebuggerUrl"), str):
        raise ValueError("该端点不是 Chrome CDP 服务")
    return value


def run_enrich(args):
    db_path = args.data_dir / "collector.db"
    if not db_path.is_file():
        raise ValueError(f"找不到采集数据库：{db_path}；先 backfill 或指定 --data-dir")
    work = (enrich_plan.pending_for(stock_codes(args.stocks), db_path) if args.stocks
            else enrich_plan.backlog(db_path))
    if args.plan_only:
        print(json.dumps({"mode": "PLAN_ONLY", "acquisition_mode": "chrome-cdp", "data_dir": str(args.data_dir),
                          "stocks": [{"stock": stock, "pending": pending} for stock, pending in work]}, ensure_ascii=False, indent=2))
        return 0
    if not work:
        print("没有待补详情：仅处理标题至少40字符、正文尚未获取且未记为跳过的帖子")
        return 0
    try:
        cdp_version(args.cdp_endpoint)
    except (OSError, ValueError) as exc:
        raise RuntimeError("无法连接 CDP 浏览器；先运行 browser_jobs.py open-cdp，或用 --cdp-endpoint 指定你已打开的浏览器") from exc
    logs = REPO / "runtime/enrich-runs"
    logs.mkdir(parents=True, exist_ok=True)
    previous = os.environ.get("MYRESEARCHER_CDP_ENDPOINT")
    os.environ["MYRESEARCHER_CDP_ENDPOINT"] = args.cdp_endpoint
    try:
        for index, (stock, pending) in enumerate(work):
            if index:
                time.sleep(args.min_delay)
            report, error = logs / f"{stock}.json", logs / f"{stock}.err"
            print(f"{stock}：待补详情 {pending} · 日志 {error}", flush=True)
            with report.open("w", encoding="utf-8") as output, error.open("w", encoding="utf-8", buffering=1) as errors:
                environment = dict(os.environ)
                environment["PYTHONPATH"] = str(REPO / "src") + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
                code = subprocess.run([sys.executable, "-X", "utf8", "-m", "myresearcher_collector.cli.main",
                                       *enrich_arguments(args, stock)], stdout=output, stderr=errors,
                                      env=environment, check=False).returncode
            try:
                result = json.loads(report.read_text(encoding="utf-8"))
                if not isinstance(result, dict) or not isinstance(result.get("stopped"), bool):
                    raise ValueError("报告缺少停止状态")
            except (OSError, ValueError) as exc:
                print(f"{stock}：无法读取采集结果（{exc}）；后续股票未执行。查看 {error}", flush=True)
                return code or 1
            if code not in (0, 1) or result["stopped"]:
                print(f"{stock}：采集已停止；查看 {report} 和 {error}。后续股票未执行", flush=True)
                return code or 1
            print(f"{stock}：本轮详情完成 · 成功 {result.get('success', 0)} · 已记为不存在 {result.get('skipped_not_found_added', 0)}", flush=True)
    finally:
        if previous is None:
            os.environ.pop("MYRESEARCHER_CDP_ENDPOINT", None)
        else:
            os.environ["MYRESEARCHER_CDP_ENDPOINT"] = previous
    print("本轮 enrich 已结束，CDP 浏览器保持打开")
    return 0


def find_chrome(explicit=None):
    if explicit:
        candidates = [Path(explicit)]
    else:
        candidates = [Path(os.environ[key]) / "Google/Chrome/Application/chrome.exe"
                      for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA") if os.environ.get(key)]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ValueError("未找到 Google Chrome；请安装 Chrome，或用 --chrome 指定 chrome.exe")


def open_cdp(args):
    chrome = find_chrome(args.chrome)
    if not 1024 <= args.port <= 65535:
        raise ValueError("CDP 端口必须在1024至65535之间")
    try:
        with socket.create_connection(("127.0.0.1", args.port), timeout=0.3):
            raise ValueError(f"端口 {args.port} 已被占用；若是你已打开的采集 CDP 浏览器，直接运行 enrich")
    except OSError:
        pass
    profile = args.profile_dir.expanduser().resolve()
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([str(chrome), f"--user-data-dir={profile}", "--remote-debugging-address=127.0.0.1",
                      f"--remote-debugging-port={args.port}", "--no-first-run", "--no-default-browser-check", "about:blank"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    endpoint = f"http://127.0.0.1:{args.port}"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            cdp_version(endpoint)
        except (OSError, ValueError):
            time.sleep(0.2)
        else:
            print(f"采集 Chrome 已打开 · CDP {endpoint} · 配置目录 {profile}")
            return 0
    raise RuntimeError("Chrome 未在15秒内提供 CDP；检查专用配置目录是否已被其他 Chrome 占用")


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("backfill", "enrich"):
        command = commands.add_parser(name)
        command.add_argument("--stocks", nargs="+")
        command.add_argument("--data-dir", type=Path, default=REPO / "data")
        command.add_argument("--plan-only", action="store_true")
        command.add_argument("--challenge-wait", type=float, default=180)
    backfill = commands.choices["backfill"]
    window = backfill.add_mutually_exclusive_group()
    window.add_argument("--from", dest="from_date")
    window.add_argument("--days", type=int, default=14)
    backfill.add_argument("--to", dest="to_date")
    backfill.add_argument("--start-page", type=int)
    backfill.add_argument("--min-interval", type=float, default=3)
    backfill.add_argument("--max-interval", type=float, default=10)
    backfill.add_argument("--list-click-paging", action="store_true")
    enrich = commands.choices["enrich"]
    enrich.add_argument("--cdp-endpoint", default=os.environ.get("MYRESEARCHER_CDP_ENDPOINT", "http://127.0.0.1:9222"))
    enrich.add_argument("--min-delay", type=float, default=3)
    enrich.add_argument("--max-delay", type=float, default=10)
    enrich.add_argument("--enrich-order", choices=("asc", "desc"), default="asc")
    enrich.add_argument("--pace-model", choices=("longtail", "uniform"), default="longtail")
    enrich.add_argument("--read-every", type=int, default=20)
    enrich.add_argument("--read-min", type=float, default=30)
    enrich.add_argument("--read-max", type=float, default=90)
    enrich.add_argument("--challenge-retries", type=int, default=1)
    enrich.add_argument("--detail-referer-from-list", action="store_true")
    chrome = commands.add_parser("open-cdp")
    chrome.add_argument("--chrome", type=Path)
    chrome.add_argument("--port", type=int, default=9222)
    chrome.add_argument("--profile-dir", type=Path, default=REPO / ".runtime/browser-profiles/eastmoney-enrich-cdp")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    os.chdir(REPO)
    if hasattr(args, "data_dir"):
        args.data_dir = args.data_dir.expanduser().resolve()
    return {"backfill": run_backfill, "enrich": run_enrich, "open-cdp": open_cdp}[args.command](args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("已暂停，现有数据和断点保留", file=sys.stderr)
        raise SystemExit(130)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"浏览器采集失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
