#!/usr/bin/env python3
"""Install and maintain the existing HTTP backfill app natively on Linux."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error

from console_cli import Client, main as console_main

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
UNIT = Path("/etc/systemd/system/http-backfill.service")
SERVICE = "http-backfill"
MARKER = "# HTTP_BACKFILL_NATIVE_CONFIG "
RUNTIME = Path("/opt/http-backfill-runtime")


def run(*args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def installed():
    if not UNIT.exists():
        return None
    content = UNIT.read_text()
    fields, environment, metadata = {}, {}, None
    in_service = False
    for line in content.splitlines():
        if line.startswith(MARKER):
            metadata = json.loads(line[len(MARKER):])
        elif line.startswith("["):
            in_service = line == "[Service]"
        elif in_service and "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            if key == "Environment":
                environment.update(part.split("=", 1) for part in shlex.split(value))
            else:
                fields[key] = value
    argv = [part.replace("%%", "%") for part in shlex.split(fields.get("ExecStart", ""))]
    working = fields.get("WorkingDirectory", "").replace("%%", "%")
    if (working != str(REPO) or fields.get("User", "root") != "root"
            or str(HERE / "server.py") not in argv or "EnvironmentFile" in fields):
        raise ValueError("已有 http-backfill.service 不是本仓库支持的原生服务，拒绝覆盖")
    allowed = {"PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "BACKFILL_API_ONLY", "BACKFILL_FLEET_SYNC_ENABLED"}
    if set(environment) - allowed:
        raise ValueError("已有服务包含额外环境配置，拒绝安装时丢弃；请先核对服务配置")
    def option(name):
        return argv[argv.index(name) + 1]
    config = {"repo": str(REPO), "python": argv[0], "data_dir": option("--data-dir"),
              "host": option("--host"), "port": int(option("--port")),
              "api_only": int(environment.get("BACKFILL_API_ONLY", "0")),
              "fleet_sync": int(environment.get("BACKFILL_FLEET_SYNC_ENABLED", "1"))}
    if metadata is not None and metadata != config:
        raise ValueError("服务安装身份与实际运行参数不一致，拒绝覆盖")
    return config


def suitable_python(path):
    if not path:
        return False
    try:
        result = subprocess.run([str(path), "-c",
            "import ssl,sqlite3,zoneinfo,sys;sys.exit(0 if sys.version_info >= (3,11) else 1)"],
            capture_output=True, timeout=10)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def bootstrap_python():
    target = {"x86_64": "x86_64", "aarch64": "aarch64"}.get(platform.machine())
    if not target:
        raise ValueError("此架构请自行安装 Python 3.11+ 后使用 --python")
    target += "-unknown-linux-gnu"
    asset = f"uv-{target}.tar.gz"
    url = f"https://github.com/astral-sh/uv/releases/download/0.13.0/{asset}"
    with tempfile.TemporaryDirectory(prefix="http-backfill-python-") as folder:
        archive, checksum = Path(folder) / asset, Path(folder) / "checksum"
        for source, destination in [(url, archive), (url + ".sha256", checksum)]:
            run("curl", "--fail", "--location", "--silent", "--show-error",
                "--connect-timeout", "10", "--max-time", "120", "--output", destination, source)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != checksum.read_text().split()[0]:
            raise ValueError("uv 归档校验失败")
        uv = RUNTIME / "bin/uv"
        uv.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as tar:
            member = tar.getmember(f"uv-{target}/uv")
            if not member.isfile() or member.size > 100 * 1024 * 1024:
                raise ValueError("uv 归档格式无效")
            with tar.extractfile(member) as source:
                binary = source.read()
        atomic_write(uv, binary, 0o755)
    environment = {**os.environ, "UV_PYTHON_INSTALL_DIR": str(RUNTIME / "python"),
                   "UV_PYTHON_BIN_DIR": str(RUNTIME / "bin")}
    run(uv, "python", "install", "3.12", env=environment)
    python = subprocess.check_output([str(uv), "python", "find", "--managed-python", "3.12"],
                                     env=environment, text=True).strip()
    if not suitable_python(python):
        raise ValueError("独立 Python 安装后校验失败")
    return python


def config_for(args):
    old = installed()
    config = old or {"repo": str(REPO), "python": None, "data_dir": "/var/lib/http-backfill",
                     "host": "127.0.0.1", "port": 8790, "api_only": 1, "fleet_sync": 0}
    config = dict(config)
    for field in ["python", "data_dir", "host", "port"]:
        value = getattr(args, field, None)
        if value is not None:
            config[field] = str(Path(value).absolute()) if field in {"python", "data_dir"} else value
    if getattr(args, "console", False):
        config["api_only"] = 0
    if old and Path(config["data_dir"]).resolve() != Path(old["data_dir"]).resolve():
        raise ValueError("已有节点的数据目录不能在安装时更换；请沿用 " + old["data_dir"])
    if getattr(args, "python", None) and not suitable_python(config["python"]):
        raise ValueError("指定的 Python 需要 3.11+，并提供 ssl/sqlite3/zoneinfo")
    if not suitable_python(config["python"]):
        for candidate in [RUNTIME / "bin/python3", shutil.which("python3")]:
            if suitable_python(candidate):
                config["python"] = str(candidate)
                break
        else:
            if not getattr(args, "install_python", False):
                raise ValueError("需要 Python 3.11+；可使用 install --install-python 安装独立运行时，或 --python 指定已有解释器")
            config["python"] = bootstrap_python()
    ipaddress.IPv4Address(config["host"])
    if type(config["port"]) is not int or not 1 <= config["port"] <= 65535:
        raise ValueError("端口必须为 1～65535")
    if any(type(config[key]) is not int or config[key] not in {0, 1} for key in ["api_only", "fleet_sync"]):
        raise ValueError("API/fleet 模式必须为 0 或 1")
    for key in ["repo", "python", "data_dir"]:
        if any(char in config[key] for char in ["\n", "\r", "\x00", "$"]):
            raise ValueError("路径不能包含换行、NUL 或美元符号")
    sys.path.insert(0, str(REPO / "src"))
    from unified_store import guard_runtime_path
    guard_runtime_path(Path(config["data_dir"]))
    if (Path(config["data_dir"]) / "experiment.sqlite3").exists():
        raise ValueError("先停止旧服务并使用 migrate_storage.py 迁移旧库；原生安装不会删除或迁移数据库")
    return config


def quote(value):
    # systemd parses quoted arguments and expands percent specifiers separately.
    return json.dumps(str(value).replace("%", "%%"), ensure_ascii=False)


def unit_text(config):
    values = {"REPO": config["repo"], "PYTHON": config["python"], "SERVER": HERE / "server.py",
              "DATA_DIR": config["data_dir"], "HOST": config["host"], "PORT": config["port"],
              "API_ONLY": config["api_only"], "FLEET_SYNC": config["fleet_sync"]}
    template = (HERE / "deploy/collector-console.service").read_text()
    for key, value in values.items():
        rendered = str(value).replace("%", "%%") if key in {"REPO", "API_ONLY", "FLEET_SYNC"} else quote(value)
        template = template.replace("@" + key + "@", rendered)
    return MARKER + json.dumps(config, ensure_ascii=False, sort_keys=True) + "\n" + template


def atomic_write(path, content, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(content.encode() if isinstance(content, str) else content)
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def active():
    return subprocess.run(["systemctl", "is-active", "--quiet", SERVICE]).returncode == 0


def api_url(config):
    host = "127.0.0.1" if config["host"] == "0.0.0.0" else config["host"]
    return f"http://{host}:{config['port']}"


def cli(config, action, remainder):
    sys.argv = ["console_cli.py", "--data-dir", config["data_dir"], "--url", api_url(config), action, *remainder]
    return console_main()


def check_health(config):
    deadline = time.monotonic() + 15
    while True:
        try:
            Client(api_url(config), Path(config["data_dir"])).get("status")
            return
        except (OSError, ValueError):
            if time.monotonic() >= deadline:
                raise ValueError("服务 API 未就绪；请查看 journalctl -u http-backfill")
            time.sleep(0.5)


def check_service_identity():
    result = subprocess.run(["systemctl", "show", SERVICE, "-p", "FragmentPath", "-p", "DropInPaths", "-p", "LoadState"],
                            capture_output=True, text=True)
    properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if result.returncode and properties.get("LoadState") != "not-found":
        raise ValueError("systemd 当前不可用，无法安装或控制服务")
    fragment, dropins = properties.get("FragmentPath"), properties.get("DropInPaths")
    if (fragment and Path(fragment) != UNIT) or dropins:
        raise ValueError("已有服务使用其他 unit/覆盖配置，拒绝覆盖")


def install(config):
    old = installed()
    was_active = active()
    if was_active and not old:
        raise ValueError("已有活动服务缺少本应用安装身份，拒绝覆盖")
    if old and was_active and config != old:
        raise ValueError("修改服务运行参数前请先 stop；当前服务和任务未改动")
    wrappers = {}
    for action in ["status", "logs"]:
        path = Path("/usr/local/bin/backfill-" + action)
        if path.exists() and str(HERE / "console_cli.py") not in path.read_text():
            raise ValueError(str(path) + " 已存在且不属于本应用，拒绝覆盖")
        args = [config["python"], "-B", HERE / "console_cli.py", "--data-dir", config["data_dir"],
                "--url", api_url(config), action]
        wrappers[path] = "#!/bin/sh\nexec " + shlex.join(list(map(str, args))) + ' "$@"\n'
    rendered = unit_text(config)
    with tempfile.TemporaryDirectory(prefix="http-backfill-unit-") as folder:
        planned = Path(folder) / "http-backfill.service"
        planned.write_text(rendered)
        run("systemd-analyze", "verify", planned)
    data = Path(config["data_dir"])
    if not data.exists():
        data.mkdir(parents=True, mode=0o700)
    atomic_write(UNIT, rendered, 0o644)
    for path, content in wrappers.items():
        atomic_write(path, content, 0o755)
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", SERVICE)
    if not was_active:
        run("systemctl", "start", SERVICE)
    check_health(config)
    print("原生服务已安装；" + ("正在运行的服务未重启，任务继续。" if was_active else "任务遵守首启/重启暂停规则。"))


def update(config):
    if active():
        client = Client(api_url(config), Path(config["data_dir"]))
        client.request("control", method="POST", body={"action": "pause"})
        deadline = time.monotonic() + 40
        while client.get("status").get("request_inflight"):
            if time.monotonic() >= deadline:
                raise ValueError("当前请求未结束；服务尚未重启，请稍后重试")
            time.sleep(0.5)
        run("systemctl", "stop", SERVICE, timeout=60)
    install(config)
    print("已使用当前代码更新。任务保持暂停/阻断；检查 status 后按股 collect 或 probe。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    deploy = sub.add_parser("install", help="安装后台服务和状态/日志命令")
    deploy.add_argument("--python")
    deploy.add_argument("--install-python", action="store_true")
    deploy.add_argument("--data-dir")
    deploy.add_argument("--host")
    deploy.add_argument("--port", type=int)
    deploy.add_argument("--console", action="store_true")
    deploy.add_argument("--dry-run", action="store_true")
    for action in ["update", "start", "stop", "service-logs"]:
        sub.add_parser(action)
    for action in ["status", "logs", "collect", "pause", "probe", "configure", "start-page", "interval"]:
        item = sub.add_parser(action, add_help=False)
        item.add_argument("arguments", nargs=argparse.REMAINDER)
    actions = {"status", "logs", "collect", "pause", "probe", "configure", "start-page", "interval"}
    args = (argparse.Namespace(command=sys.argv[1], arguments=sys.argv[2:])
            if len(sys.argv) > 1 and sys.argv[1] in actions else parser.parse_args())
    try:
        mutating = args.command in {"install", "update", "start", "stop"}
        if mutating and not getattr(args, "dry_run", False):
            if platform.system() != "Linux" or os.geteuid() != 0:
                raise ValueError("安装和服务控制需要 Linux root；状态/日志命令不需要安装 Docker")
            for required in ["systemctl", "systemd-analyze", "curl"]:
                if not shutil.which(required):
                    raise ValueError("缺少 " + required + "；请先安装对应系统组件")
            check_service_identity()
        if getattr(args, "dry_run", False) and args.install_python:
            raise ValueError("dry-run 不安装 Python；请用已有 --python 预览")
        config = config_for(args)
        if getattr(args, "dry_run", False):
            print(unit_text(config))
        elif args.command == "install":
            install(config)
        elif args.command == "update":
            if not installed():
                raise ValueError("请先 install，再 update")
            update(config)
        elif args.command in {"start", "stop"}:
            if not installed():
                raise ValueError("请先 install")
            run("systemctl", args.command, SERVICE)
            if args.command == "start":
                check_health(config)
        elif args.command == "service-logs":
            run("journalctl", "-u", SERVICE, "-f")
        else:
            return cli(config, args.command, args.arguments)
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, IndexError, KeyError, subprocess.SubprocessError) as exc:
        print("原生部署/管理失败：" + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
