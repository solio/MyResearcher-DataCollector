#!/usr/bin/env python3
"""Keep one browser alive across the existing per-stock backfill CLI calls."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path
import signal
import socket
import sys
import time

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
MAX_MESSAGE = 64 * 1024


def read_message(connection):
    data = bytearray()
    while b"\n" not in data:
        chunk = connection.recv(4096)
        if not chunk:
            raise RuntimeError("浏览器会话进程已断开，停止本轮采集")
        data.extend(chunk)
        if len(data) > MAX_MESSAGE:
            raise ValueError("浏览器会话请求过大")
    message = json.loads(bytes(data).split(b"\n", 1)[0])
    if not isinstance(message, dict):
        raise ValueError("浏览器会话消息无效")
    return message


class BorrowedTransport:
    """Per-stock cleanup returns the lease; the batch retains its browser."""
    def __init__(self, owner, interval):
        self.owner, self.interval = owner, interval

    def __getattr__(self, name):
        return getattr(self.owner.transport, name)

    def get(self, url, *, timeout):
        if self.owner.last_finished is not None:
            delay = self.interval - (time.monotonic() - self.owner.last_finished)
            if delay > 0:
                time.sleep(delay)
        try:
            return self.owner.transport.get(url, timeout=timeout)
        finally:
            self.owner.last_finished = time.monotonic()

    def close(self):
        pass


class BrowserSession:
    def __init__(self, factory=None, execute=None):
        from myresearcher_collector.cli.main import _browser_socket_transport, execute_backfill_cli
        self.factory = factory or _browser_socket_transport
        self.execute = execute or execute_backfill_cli
        self.transport = self.settings = self.last_finished = None
        self.halted = False

    def run(self, request):
        from myresearcher_collector.cli.main import build_parser, _json_default
        if self.halted:
            raise RuntimeError("上一只股票未成功，本轮浏览器会话已停止；不会继续请求其他股票")
        args = build_parser().parse_args(request["argv"])
        if (args.command != "backfill" or args.source != "eastmoney_guba"
                or not args.confirm_live or not args.list_only
                or args.acquisition_mode not in {"managed-chromium", "chrome-cdp"}):
            raise ValueError("共享浏览器只支持当前股吧列表 backfill")
        settings = (args.acquisition_mode, args.profile_dir, args.list_click_paging)
        if self.transport is not None and settings != self.settings:
            raise ValueError("同一批次不能更换浏览器会话配置")
        report_path, error_path = Path(request["report"]), Path(request["error"])
        with report_path.open("w", encoding="utf-8") as report_file, error_path.open("w", encoding="utf-8", buffering=1) as error_file:
            try:
                with redirect_stdout(error_file), redirect_stderr(error_file):
                    reused = self.transport is not None
                    if self.transport is None:
                        self.transport = self.factory(args)
                        self.settings = settings
                    reset = getattr(self.transport, "reset_run_observations", None)
                    if callable(reset):
                        reset()
                    print(f"browser_session=shared reused={int(reused)} stock={args.stock}", file=sys.stderr, flush=True)
                    result = self.execute(args, transport=BorrowedTransport(self, args.min_interval))
                    result["browser_session"] = {"shared": True, "reused": reused}
                json.dump(result, report_file, ensure_ascii=False, indent=2, default=_json_default)
                report_file.write("\n")
                self.halted = result.get("status") != "SUCCESS"
                return {"exit_code": 1 if self.halted else 0}
            except Exception as exc:
                self.halted = True
                print(f"共享浏览器 backfill 失败：{exc}", file=error_file, flush=True)
                return {"exit_code": 1}

    def close(self):
        if self.transport is not None:
            transport, self.transport = self.transport, None
            transport.close()


def serve(path):
    path = Path(path)
    session = BrowserSession()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    def stop(_signal, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        server.bind(str(path))
        os.chmod(path, 0o600)
        server.listen(1)
        while True:
            connection, _ = server.accept()
            with connection:
                result = session.run(read_message(connection))
                connection.sendall(json.dumps(result).encode() + b"\n")
    except KeyboardInterrupt:
        pass
    finally:
        try:
            session.close()
        finally:
            server.close()
            path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("serve", "call"))
    parser.add_argument("--socket", required=True)
    parser.add_argument("--report")
    parser.add_argument("--error")
    command = sys.argv[1:]
    boundary = command.index("--") if "--" in command else len(command)
    args = parser.parse_args(command[:boundary])
    if args.mode == "serve":
        serve(args.socket)
        return 0
    argv = command[boundary + 1:]
    if not args.report or not args.error or not argv:
        parser.error("call requires report, error and backfill arguments")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(args.socket)
        connection.sendall(json.dumps({"argv": argv, "report": args.report, "error": args.error}).encode() + b"\n")
        return read_message(connection)["exit_code"]


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"浏览器会话异常：{exc}", file=sys.stderr)
        raise SystemExit(1)
