#!/usr/bin/env python3
"""Temporary shared-egress Chrome session for operator-completed verification."""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import ipaddress
import json
from pathlib import Path
import queue
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

REPO = Path(__file__).resolve().parents[2]
PRIVATE = REPO / ".runtime/http-backfill-verification"
ALIASES = {"36": "36.138.125.17", "43": "43.132.206.172"}

# Runs via SSH stdin. Credentials remain inside the node, never in its output.
REMOTE_WORKER = r'''
import hashlib, json, pathlib, re, sqlite3, subprocess, time, urllib.parse, urllib.request

AUTH_HALTS = {"challenge", "http_401", "http_403", "http_429"}

def source_url(url):
    u = urllib.parse.urlsplit(url or "")
    return (u.scheme == "https" and u.hostname == "guba.eastmoney.com"
            and u.port in (None, 443) and not u.username and not u.password
            and not u.query and not u.fragment
            and bool(re.fullmatch(r"/(?:list,[0-9]{6},f(?:_[0-9]+)?|news,[^,/]+,[0-9]+)\.html", u.path)))

def locate():
    try:
        found = subprocess.run(["docker", "ps", "--filter", "label=com.docker.compose.service=collector-console",
                                "--format", "{{.Names}}"], capture_output=True, text=True)
        names = found.stdout.splitlines()
    except FileNotFoundError:
        names = []
    if len(names) > 1:
        raise RuntimeError("服务器有多个采集器，无法自动选择")
    if names:
        c = json.loads(subprocess.run(["docker", "inspect", names[0]], capture_output=True,
                                      text=True, check=True).stdout)[0]
        data = next(pathlib.Path(m["Source"]) for m in c["Mounts"] if m["Destination"] == "/data")
        bindings = c["NetworkSettings"]["Ports"].get("8790/tcp") or []
        if not bindings:
            raise RuntimeError("采集器8790未映射到宿主机，无法调用现有控制接口")
        return data, "http://127.0.0.1:" + bindings[0]["HostPort"]
    data = pathlib.Path("/var/lib/http-backfill")
    if not (data / "console.token").is_file():
        raise RuntimeError("未找到采集器运行目录")
    return data, "http://127.0.0.1:8790"

def api(base, data, path="status", body=None):
    headers = {"Authorization": "Bearer " + (data / "console.token").read_text().strip(),
               "Content-Type": "application/json"}
    request = urllib.request.Request(base + "/api/" + path, headers=headers,
                                    data=json.dumps(body).encode() if body is not None else None)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=25) as response:
        return json.load(response)

def route(status, stock=None, job=None):
    detached = next((v for v in status.get("detached_stock_runtimes", [])
                     if v.get("stock") == stock and v.get("job") == job), None)
    if detached is not None:
        selected = detached.get("task_proxy") or {}
    elif job is not None and job != (status.get("job") or {}).get("id"):
        raise RuntimeError("原任务的代理归属已缺失，请在控制台处理")
    else:
        selected = next((v.get("task_proxy") or {} for v in status.get("coverage", [])
                         if v.get("stock") == stock), {})
    settings = selected.get("settings") or {}
    if settings.get("mode", "inherit") == "inherit":
        selected = status.get("proxy") or {}
        settings = selected.get("settings") or {}
    endpoint = settings.get("endpoint") or ""
    u = urllib.parse.urlsplit(endpoint)
    if (settings.get("mode") not in {"http", "mihomo"} or u.scheme != "http"
            or not u.hostname or not u.port or u.username or u.password
            or u.path not in ("", "/") or u.query or u.fragment or selected.get("has_auth")):
        raise RuntimeError("本命令需要当前采集任务使用无入口认证的 HTTP/mixed 代理；不会替你切换服务器出口")
    result = {"host": u.hostname, "port": u.port, "endpoint": "http://" + u.netloc,
              "mode": settings["mode"], "selectors": {}}
    config = pathlib.Path("/opt/mihomo/config.yaml")
    if config.exists():
        import yaml
        cfg = yaml.safe_load(config.read_text())
        listener = next((v for v in cfg.get("listeners", []) if v.get("port") == u.port), {})
        name = listener.get("proxy")
        if not name:
            name = next((rule.split(",")[1] for rule in cfg.get("rules", [])
                         if rule.startswith("MATCH,")), None)
        if name:
            controller = cfg.get("external-controller", "127.0.0.1:9090")
            headers = {"Authorization": "Bearer " + cfg["secret"]} if cfg.get("secret") else {}
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            request = urllib.request.Request("http://" + controller + "/proxies", headers=headers)
            with opener.open(request, timeout=5) as response:
                proxies = json.load(response)["proxies"]
            seen = set()
            while proxies.get(name, {}).get("all"):
                if name in seen:
                    raise RuntimeError("代理选择链形成循环")
                seen.add(name)
                now = proxies[name]["now"]
                result["selectors"][name] = now
                name = now
            result["outbound"] = name
    result["signature"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result

def snapshot(status):
    job = status.get("job") or {}
    runtimes = status.get("stock_runtimes", []) + status.get("detached_stock_runtimes", [])
    targets = []
    for runtime in runtimes:
        if runtime.get("active_halt") not in AUTH_HALTS:
            continue
        evidence = runtime.get("block_evidence") or {}
        task = runtime.get("halt_task_id") or runtime.get("halted_task_id")
        if not evidence.get("id") or not task or not source_url(evidence.get("url")):
            raise RuntimeError("当前身份核验缺少原请求/任务/股吧网址证据，请在控制台处理")
        targets.append({"job": runtime["job"], "stock": runtime["stock"], "task": task,
                        "request_id": evidence["id"], "url": evidence["url"],
                        "instance_id": status["instance_id"], "revision": job.get("revision"),
                        "detached": bool(runtime.get("detached")), "route": route(status, runtime["stock"], runtime["job"])})
    barrier = status.get("node_source_auth_barrier") or {}
    return {"state": status.get("state"), "instance_id": status.get("instance_id"),
            "revision": job.get("revision"), "barrier_request_id": barrier.get("request_id"),
            "barrier_target": {"job": barrier.get("job"), "stock": barrier.get("stock"),
                               "task": barrier.get("task_id")},
            "inflight": status.get("request_inflight"), "probe_pending": status.get("probe_pending"),
            "targets": targets, "stocks": [{"job": r["job"], "stock": r["stock"], "state": r["state"]}
                                          for r in status.get("stock_runtimes", [])]}

def unchanged(view, expected):
    if view["instance_id"] != expected["instance_id"] or view["revision"] != expected["revision"]:
        raise RuntimeError("节点或任务配置已变更，未提交新的来源请求")
    for target in view["targets"]:
        if all(target[k] == expected[k] for k in ("job", "stock", "task", "request_id", "url")):
            if target["route"]["signature"] != expected["route"]["signature"]:
                raise RuntimeError("代理出口选择已变更，未提交新的来源请求")
            return target
    raise RuntimeError("原阻断已经变更或解除，未提交新的来源请求")

def execute(payload):
    data, base = locate()
    status = api(base, data)
    view = snapshot(status)
    action = payload["action"]
    if action == "audit":
        return view
    if action == "exit":
        selected = route(status, payload.get("stock"), payload.get("job"))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": selected["endpoint"]}))
        with opener.open("https://api.ipify.org", timeout=15) as response:
            ip = response.read(100).decode().strip()
        return {"route": selected, "exit_ip": ip}
    expected = payload["target"]
    if action in {"prepare", "probe"}:
        unchanged(view, expected)
        if view["probe_pending"] or action == "probe" and view["inflight"]:
            raise RuntimeError("服务器还有请求或探测在执行/排队，请等待结束")
        if action == "prepare":
            paused = []
            for row in view["stocks"]:
                if row["state"] == "running":
                    api(base, data, "stocks/" + row["stock"] + "/control",
                        {"action": "pause", "job_id": row["job"]})
                    paused.append(row["stock"])
            deadline = time.monotonic() + 30
            current = api(base, data)
            while current.get("request_inflight") and time.monotonic() < deadline:
                time.sleep(0.5)
                current = api(base, data)
            if current.get("request_inflight"):
                raise RuntimeError("已暂停后续采集，正在执行的请求尚未结束；未打开验证浏览器")
            unchanged(snapshot(current), expected)
            return {"paused_stocks": paused}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": expected["route"]["endpoint"]}))
        with opener.open("https://api.ipify.org", timeout=15) as response:
            if response.read(100).decode().strip() != expected["exit_ip"]:
                raise RuntimeError("验证后服务器出口IP已经变化，未提交来源探测")
        with sqlite3.connect((data / "collector.db").as_uri() + "?mode=ro", uri=True) as db:
            before = db.execute("SELECT COALESCE(MAX(id),0) FROM requests").fetchone()[0]
        api(base, data, "stocks/" + expected["stock"] + "/control",
            {"action": "retry", "job_id": expected["job"], "retry_request_id": expected["request_id"]})
        return {"before_request_id": before}
    if action == "result":
        with sqlite3.connect((data / "collector.db").as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT id,outcome,http_status,error,finished FROM requests WHERE id>? AND job=? AND stock=? AND task=? AND probe=1 ORDER BY id LIMIT 1",
                             (payload["before_request_id"], expected["job"], expected["stock"], expected["task"])).fetchone()
            return {"done": bool(row and row["finished"] is not None), "request": dict(row) if row else None}
    if action == "resume":
        active = next((r for r in status.get("stock_runtimes", [])
                       if r["job"] == expected["job"] and r["stock"] == expected["stock"]), None)
        if (view["instance_id"] != expected["instance_id"] or view["revision"] != expected["revision"]
                or not active or active.get("active_halt") or active.get("state") != "paused"
                or status.get("node_source_auth_barrier") or status.get("legacy_stock_halt_barrier")
                or status.get("storage_halt") or view["inflight"] or view["probe_pending"]
                or route(status, expected["stock"], expected["job"])["signature"] != expected["route"]["signature"]):
            raise RuntimeError("任务或出口状态已变更，未自动继续；请在控制台查看")
        api(base, data, "stocks/" + expected["stock"] + "/control",
            {"action": "start", "job_id": expected["job"]})
        return {"started": True}
    raise RuntimeError("未知操作")

if __name__ == "__main__":
    try:
        print(json.dumps({"ok": True, "value": execute(PAYLOAD)}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
'''


def node_config(value):
    path = PRIVATE / "nodes.json"
    if not path.is_file():
        raise RuntimeError("缺少本机私密节点配置：" + str(path))
    nodes = json.loads(path.read_text())
    host = ALIASES.get(value, value)
    if host not in nodes:
        raise RuntimeError("本机没有配置该节点的 SSH 连接：" + host)
    node = dict(nodes[host], host=host)
    if (not re.fullmatch(r"[A-Za-z0-9.-]+", host) or host.startswith("-")
            or not re.fullmatch(r"[A-Za-z0-9_]+", node.get("user", "root"))
            or not isinstance(node.get("port", 22), int) or not 1 <= node.get("port", 22) <= 65535):
        raise RuntimeError("本机节点配置中的 SSH 地址/用户/端口无效")
    if node.get("identity_file"):
        node["identity_file"] = str(Path(node["identity_file"]).expanduser().resolve())
        if not Path(node["identity_file"]).is_file():
            raise RuntimeError("对应 SSH key 文件不存在")
    return node


def ssh_args(node):
    args = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=10", "-o", "ControlMaster=no", "-o", "ControlPath=none",
            "-p", str(node.get("port", 22))]
    if node.get("identity_file"):
        args += ["-o", "IdentitiesOnly=yes", "-i", node["identity_file"]]
    return args


def ssh_destination(node):
    return node.get("user", "root") + "@" + node["host"]


def remote(node, action, **payload):
    source = "PAYLOAD = " + repr(dict(action=action, **payload)) + "\n" + REMOTE_WORKER
    proc = subprocess.run(ssh_args(node) + [ssh_destination(node), "python3 -"],
                          input=source, capture_output=True, text=True, timeout=55)
    if proc.returncode:
        if "Permission denied" in proc.stderr:
            raise RuntimeError("SSH key 无法登录该节点，请检查本机私密节点配置")
        if "Host key verification failed" in proc.stderr:
            raise RuntimeError("SSH 主机指纹尚未确认或已变化，请先正常 SSH 登录核对")
        raise RuntimeError("SSH 连接失败，未修改服务器任务")
    try:
        result = json.loads(proc.stdout)
    except ValueError:
        raise RuntimeError("服务器返回了无法识别的状态，未继续操作") from None
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "服务器操作失败"))
    return result["value"]


def choose_target(view, stock=None):
    targets = [v for v in view["targets"] if stock is None or v["stock"] == stock]
    if not targets:
        return None
    stopped = next((v for v in targets if v["request_id"] == view.get("barrier_request_id")), None)
    if stopped:
        return stopped
    # Failed probes replace stock evidence while the node barrier retains the
    # first blocked request. Its task owner still identifies the current halt.
    owner = view.get("barrier_target") or {}
    if all(owner.get(k) is not None for k in ("job", "stock", "task")):
        stopped = next((v for v in targets if all(v[k] == owner[k]
                                               for k in ("job", "stock", "task"))), None)
        if stopped:
            return stopped
    return max(targets, key=lambda v: (not v.get("detached", False), v["request_id"],
                                      v["job"], v["stock"]))


class Tunnel:
    def __init__(self, node, route):
        self.node, self.route, self.process, self.errors = node, route, None, None
        self.port = None

    def __enter__(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        host = self.route["host"]
        if ":" in host:
            host = "[" + host + "]"
        forward = f"127.0.0.1:{self.port}:{host}:{self.route['port']}"
        command = ssh_args(self.node) + ["-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=3", "-L", forward, ssh_destination(self.node),
            "python3 -c 'import sys; sys.stdin.buffer.read()'"]
        self.errors = tempfile.TemporaryFile()
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                            stderr=self.errors)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError("临时 SSH 通道建立失败")
                with socket.socket() as sock:
                    sock.settimeout(0.2)
                    if sock.connect_ex(("127.0.0.1", self.port)) == 0:
                        return self
                time.sleep(0.1)
            raise RuntimeError("临时 SSH 通道连接超时")
        except BaseException:
            self.close()
            raise

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.port}"

    def close(self):
        if self.process:
            if self.process.stdin and not self.process.stdin.closed:
                try:
                    self.process.stdin.close()
                except BrokenPipeError:
                    pass
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
        if self.errors:
            self.errors.close()

    def __exit__(self, *_):
        self.close()


def browser_ip(page):
    page.goto("https://api.ipify.org?format=json", wait_until="domcontentloaded", timeout=30000)
    return str(ipaddress.ip_address(json.loads(page.locator("body").inner_text())["ip"]))


def wait_operator(page, context, tunnel):
    answers = queue.Queue()
    def read():
        try:
            answers.put(input("手动验证完成后按回车；输入 q 取消。关闭验证窗口也会撤掉临时通道："))
        except EOFError:
            answers.put("q")
    threading.Thread(target=read, daemon=True).start()
    while True:
        if not context.pages:
            return False
        if tunnel.process.poll() is not None:
            raise RuntimeError("SSH 通道已断开，未提交服务器探测")
        try:
            return answers.get_nowait().strip().lower() in {"", "y", "yes"}
        except queue.Empty:
            # Dispatch Playwright close events while terminal input is pending.
            page.wait_for_timeout(200)


def browser_session(node, expected, *, check_exit=False):
    from playwright.sync_api import sync_playwright, Error
    target = expected.get("target")
    stock = target["stock"] if target else None
    job = target["job"] if target else None
    exit_check = remote(node, "exit", stock=stock, job=job)
    if target and exit_check["route"]["signature"] != target["route"]["signature"]:
        raise RuntimeError("服务器出口配置已变化，请重新运行")
    profile_root = (REPO / ".runtime/browser-profiles").resolve()
    profile = Path(node.get("browser_profile") or profile_root / ("verify-server-" + node["host"])).expanduser().resolve()
    if not profile.is_relative_to(profile_root) or profile == profile_root:
        raise RuntimeError("验证浏览器必须使用本项目独立的私密配置文件目录")
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    profile.chmod(0o700)
    confirmed = False
    # Neutral checks never start an old verification profile that could restore
    # source tabs or background source pages.
    directory = tempfile.TemporaryDirectory(prefix="backfill-exit-") if check_exit else nullcontext(str(profile))
    with directory as browser_dir, Tunnel(node, exit_check["route"]) as tunnel, sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(browser_dir, channel="chrome",
            headless=check_exit, proxy={"server": tunnel.endpoint}, args=["--disable-quic"], timeout=20000)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            observed = browser_ip(page)
            if observed != exit_check["exit_ip"]:
                raise RuntimeError("浏览器与服务器出口不同，未访问股吧")
            print("已确认共同出口：" + observed, flush=True)
            if check_exit:
                return False
            page.goto(target["url"], wait_until="commit", timeout=30000)
            print("已打开原失败请求：" + target["url"], flush=True)
            print("在专用窗口完成验证；若已经显示正常帖子，也可按回车进行服务器单次探测。", flush=True)
            try:
                confirmed = wait_operator(page, context, tunnel)
            except Error:
                if context.pages:
                    raise
                confirmed = False
            if confirmed:
                after_browser = browser_ip(page)
                after_server = remote(node, "exit", stock=stock, job=job)
                if (after_browser != observed or after_server["exit_ip"] != observed
                        or after_server["route"]["signature"] != target["route"]["signature"]):
                    raise RuntimeError("验证期间出口发生变化，未提交服务器探测")
                target["exit_ip"] = observed
        finally:
            try:
                context.close()
            except Error:
                pass
    return confirmed


def run(node, args):
    view = remote(node, "audit")
    if args.check:
        print(json.dumps(view, ensure_ascii=False, indent=2))
        return 0
    target = choose_target(view, args.stock)
    if args.check_exit:
        try:
            browser_session(node, {"target": target}, check_exit=True)
        finally:
            print("本机测试浏览器和临时代理通道已清理。", flush=True)
        return 0
    if target is None:
        print("该节点目前没有验证码/身份核验阻断，无需开启验证浏览器。")
        return 0
    print(f"{node['host']} · {target['stock']} · 原阻断请求 #{target['request_id']}", flush=True)
    remote(node, "prepare", target=target)
    try:
        confirmed = browser_session(node, {"target": target})
    finally:
        print("本机专用浏览器和临时代理通道已清理，日常代理规则保持原样。", flush=True)
    if not confirmed:
        print("已取消；未安排新的服务器探测。")
        return 0
    queued = remote(node, "probe", target=target)
    print("服务器原失败请求的单次探测已排队，遵守现有请求间隔和冷却。", flush=True)
    deadline = time.monotonic() + args.probe_wait
    while time.monotonic() < deadline:
        result = remote(node, "result", target=target, **queued)
        if result["done"]:
            row = result["request"]
            if row["outcome"] not in {"real_data", "detail_unavailable"}:
                print(f"探测 #{row['id']} 未通过：{row['error'] or row['outcome']}。服务器保留停止状态。")
                return 1
            print(f"探测 #{row['id']} 通过：HTTP {row['http_status']}。", flush=True)
            if args.resume:
                remote(node, "resume", target=target)
                print(target["stock"] + " 已继续采集；其他股票保持各自状态。")
            else:
                print("服务器保持暂停，可在控制台点击本股“采集”；下次加 --resume 可在探测成功后继续。")
            return 0
        time.sleep(1)
    print("单次探测仍在等待/执行，请在控制台查看结果。未追加探测，也未自动继续。")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("node", nargs="?", help="36、43 或已配置的服务器 IP")
    parser.add_argument("--stock", help="指定验证股票；默认自动选择当前阻断任务")
    parser.add_argument("--resume", action="store_true", help="探测通过后继续该股票")
    checks = parser.add_mutually_exclusive_group()
    checks.add_argument("--check", action="store_true", help="只检查服务器当前状态")
    checks.add_argument("--check-exit", action="store_true", help="只验证共同出口并清理，不访问股吧")
    parser.add_argument("--probe-wait", type=int, default=120, help="等待一次服务器探测结果的秒数")
    args = parser.parse_args(argv)
    if args.stock and not re.fullmatch(r"[0-9]{6}", args.stock):
        parser.error("--stock 必须是六位股票代码")
    if args.probe_wait <= 0:
        parser.error("--probe-wait 必须为正整数")
    if not args.node:
        if not sys.stdin.isatty():
            parser.error("请指定节点36或43")
        args.node = input("选择验证节点：36 或 43：").strip()
    try:
        return run(node_config(args.node), args)
    except (KeyboardInterrupt, EOFError):
        print("\n已取消，本机临时验证资源已清理。")
        return 130
    except Exception as exc:
        print("未完成：" + str(exc), file=sys.stderr)
        return 1


def interrupt(*_):
    raise KeyboardInterrupt()


if __name__ == "__main__":
    for signum in (signal.SIGTERM, getattr(signal, "SIGHUP", signal.SIGTERM)):
        signal.signal(signum, interrupt)
    raise SystemExit(main())
