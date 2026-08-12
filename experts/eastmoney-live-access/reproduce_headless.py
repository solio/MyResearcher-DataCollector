#!/usr/bin/env python3
"""Bounded, non-GUI reproduction of the Eastmoney Guba live-access proof.

This diagnostic launches an ordinary installed Chrome/Chromium in headless
mode.  It does not override the User-Agent, impersonate a TLS fingerprint,
load a stealth plugin, use a proxy, solve a challenge, or read browser
cookies/storage.  The temporary browser profile is deleted on exit.

The default budget is one list navigation plus one detail navigation.  Output
is sanitized JSON: visited URLs, page identity, and the parsed post list.  Raw
HTML and full post content are not written.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import selectors
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from myresearcher_collector.sources.eastmoney_guba.collector import (  # noqa: E402
    EastmoneyGubaCollector,
)
from myresearcher_collector.sources.eastmoney_guba.parser import (  # noqa: E402
    GubaParseError,
    GubaSchemaMismatch,
    is_access_block_page,
    merge_list_and_detail,
    parse_detail_page,
    parse_list_page,
)


DEFAULT_CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
TITLE_RE = re.compile(r"<title[^>]*>\s*(.*?)\s*</title>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class ReproductionFailure(RuntimeError):
    status: str
    message: str
    exit_code: int
    visited_urls: tuple[str, ...] = ()

    def __str__(self) -> str:
        return self.message


def _stock_code(value: str) -> str:
    if len(value) != 6 or not value.isdigit():
        raise argparse.ArgumentTypeError("stock code must be six decimal digits")
    return value


def _positive_timeout(value: str) -> float:
    parsed = float(value)
    if parsed < 5:
        raise argparse.ArgumentTypeError("timeout must be at least 5 seconds")
    return parsed


def _safe_interval(value: str) -> float:
    parsed = float(value)
    if parsed < 2.5:
        raise argparse.ArgumentTypeError("request interval must be at least 2.5 seconds")
    return parsed


def _find_chrome(explicit: str | None) -> str:
    candidates = [explicit, os.environ.get("EASTMONEY_CHROME_PATH")]
    candidates.extend(DEFAULT_CHROME_CANDIDATES)
    candidates.extend(shutil.which(name) for name in ("google-chrome", "chromium", "chromium-browser"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    raise ReproductionFailure(
        "ENVIRONMENT_ERROR",
        "Chrome/Chromium was not found; pass --chrome or EASTMONEY_CHROME_PATH",
        2,
    )


def _chrome_version(executable: str) -> str:
    try:
        result = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return (result.stdout or result.stderr).strip() or "unknown"


def _dump_dom(executable: str, profile: str, url: str, timeout: float) -> str:
    command = [
        executable,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        f"--user-data-dir={profile}",
        "--dump-dom",
        url,
    ]
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        raise ReproductionFailure(
            "ENVIRONMENT_ERROR", "headless browser could not be started", 2
        ) from exc

    stdout = bytearray()
    stderr = bytearray()
    streams = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    streams.register(process.stdout, selectors.EVENT_READ, stdout)
    streams.register(process.stderr, selectors.EVENT_READ, stderr)
    deadline = time.monotonic() + timeout
    complete_document = False
    try:
        while streams.get_map() and time.monotonic() < deadline:
            events = streams.select(timeout=min(0.25, max(0.0, deadline - time.monotonic())))
            for key, _ in events:
                chunk = os.read(key.fileobj.fileno(), 65536)
                if chunk:
                    key.data.extend(chunk)
                    if key.data is stdout and b"</html>" in stdout.lower():
                        complete_document = True
                else:
                    streams.unregister(key.fileobj)
            if complete_document:
                break
            if process.poll() is not None and not events:
                break
    finally:
        streams.close()
        if process.poll() is None:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    try:
                        if os.name == "posix":
                            os.killpg(process.pid, signal.SIGKILL)
                        else:
                            process.kill()
                    except OSError:
                        pass
                    process.wait(timeout=3)

    html = stdout.decode("utf-8", errors="replace")
    if not complete_document or not html.strip():
        diagnostic = stderr.decode("utf-8", errors="replace").strip().splitlines()
        suffix = f": {diagnostic[-1]}" if diagnostic else ""
        raise ReproductionFailure(
            "TRANSPORT_ERROR",
            f"headless browser did not return a complete page for {url}{suffix}",
            3,
        )
    if is_access_block_page(html):
        raise ReproductionFailure(
            "ACCESS_BLOCK",
            f"Eastmoney returned an identity-verification page for {url}",
            4,
        )
    return html


def _title(html: str) -> str | None:
    match = TITLE_RE.search(html)
    if not match:
        return None
    return " ".join(match.group(1).split())


def _post_dict(row: Any) -> dict[str, Any]:
    return {
        "source_item_id": row.source_item_id,
        "title": row.title,
        "author_id": row.author_id,
        "author_name": row.author_name,
        "published_at": row.published_at.isoformat(),
        "last_updated_at": row.last_updated_at.isoformat() if row.last_updated_at else None,
        "read_count": row.read_count,
        "reply_count": row.reply_count,
        "like_count": row.like_count,
        "forward_count": row.forward_count,
        "post_type": row.post_type,
        "url": row.url,
    }


def _out_of_scope_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_item_id": str(row.get("post_id")) if row.get("post_id") is not None else None,
        "post_type": row.get("post_type"),
        "title": row.get("post_title"),
    }


def _markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Eastmoney Headless Reproduction",
        "",
        f"- Status: `{result['status']}`",
        f"- Captured at: `{result['captured_at']}`",
        f"- Browser: `{result['browser']['version']}`",
        "",
        "## Visited URLs",
        "",
    ]
    lines.extend(f"- {url}" for url in result["visited_urls"])
    for page in result["pages"]:
        lines.extend(
            [
                "",
                f"## Page {page['page_number']} post list",
                "",
                f"Title: `{page['title']}`",
                "",
                "| ID | Published (+08:00) | Replies | Reads | Title | URL |",
                "| --- | --- | ---: | ---: | --- | --- |",
            ]
        )
        for post in page["posts"]:
            safe_title = (post["title"] or "").replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {post['source_item_id']} | {post['published_at']} | "
                f"{post['reply_count'] if post['reply_count'] is not None else ''} | "
                f"{post['read_count'] if post['read_count'] is not None else ''} | "
                f"{safe_title} | {post['url']} |"
            )
    return "\n".join(lines) + "\n"


def _write_output(path: str | None, content: str) -> None:
    if not path:
        return
    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")


def reproduce(args: argparse.Namespace) -> dict[str, Any]:
    executable = _find_chrome(args.chrome)
    visited_urls: list[str] = []
    pages: list[dict[str, Any]] = []
    signatures: list[tuple[str, ...]] = []
    first_row = None

    with tempfile.TemporaryDirectory(prefix="eastmoney-headless-") as profile:
        for page_number in range(1, args.pages + 1):
            if visited_urls:
                time.sleep(args.request_interval)
            url = EastmoneyGubaCollector.list_url(args.stock, page_number)
            visited_urls.append(url)
            try:
                html = _dump_dom(executable, profile, url, args.timeout)
            except ReproductionFailure as failure:
                raise ReproductionFailure(
                    failure.status,
                    failure.message,
                    failure.exit_code,
                    tuple(visited_urls),
                ) from failure
            try:
                parsed = parse_list_page(html, args.stock)
            except (GubaSchemaMismatch, GubaParseError) as exc:
                raise ReproductionFailure(
                    "SOURCE_SCHEMA_MISMATCH", f"list parser rejected {url}: {exc}", 5
                ) from exc
            signature = tuple(row.source_item_id for row in parsed.rows)
            if signatures and signature and signature == signatures[-1]:
                raise ReproductionFailure(
                    "PAGINATION_NOT_PROGRESSING",
                    f"page {page_number} has the same complete in-scope ID sequence as page {page_number - 1}",
                    6,
                )
            signatures.append(signature)
            if first_row is None and parsed.rows:
                first_row = parsed.rows[0]
            pages.append(
                {
                    "page_number": page_number,
                    "url": url,
                    "title": _title(html),
                    "article_list_rc": 1,
                    "source_count": parsed.source_count,
                    "source_time": parsed.source_time,
                    "in_scope_count": len(parsed.rows),
                    "out_of_scope_count": len(parsed.out_of_scope_rows),
                    "posts": [_post_dict(row) for row in parsed.rows],
                    "out_of_scope_posts": [
                        _out_of_scope_dict(row) for row in parsed.out_of_scope_rows
                    ],
                }
            )

        detail_proof = None
        if args.with_detail:
            if first_row is None:
                raise ReproductionFailure(
                    "SOURCE_SCHEMA_MISMATCH", "list page contained no standard post_type=0 row", 5
                )
            time.sleep(args.request_interval)
            visited_urls.append(first_row.url)
            try:
                detail_html = _dump_dom(executable, profile, first_row.url, args.timeout)
            except ReproductionFailure as failure:
                raise ReproductionFailure(
                    failure.status,
                    failure.message,
                    failure.exit_code,
                    tuple(visited_urls),
                ) from failure
            try:
                detail = parse_detail_page(detail_html)
                merged = merge_list_and_detail(first_row, detail)
            except (GubaSchemaMismatch, GubaParseError) as exc:
                raise ReproductionFailure(
                    "SOURCE_SCHEMA_MISMATCH",
                    f"detail parser or list/detail identity check failed for {first_row.url}: {exc}",
                    5,
                ) from exc
            detail_proof = {
                "url": first_row.url,
                "page_title": _title(detail_html),
                "source_item_id": merged["source_item_id"],
                "list_detail_id_match": first_row.source_item_id == detail.source_item_id,
                "title": merged["title"],
                "content_length": len(merged["content"]),
            }

    return {
        "status": "PASS",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "backend": "standard-chrome-headless-dump-dom",
        "browser": {
            "executable": executable,
            "version": _chrome_version(executable),
            "headless": True,
            "temporary_profile": True,
        },
        "constraints": {
            "user_agent_override": False,
            "tls_fingerprint_impersonation": False,
            "stealth_plugin": False,
            "proxy": False,
            "captcha_solving": False,
            "cookie_or_storage_export": False,
            "minimum_request_interval_seconds": args.request_interval,
        },
        "stock_code": args.stock,
        "visited_urls": visited_urls,
        "pages": pages,
        "detail_proof": detail_proof,
    }


def _failure_payload(
    failure: ReproductionFailure, *, stock_code: str | None = None
) -> dict[str, Any]:
    return {
        "status": failure.status,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "stock_code": stock_code,
        "visited_urls": list(failure.visited_urls),
        "message": failure.message,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock", type=_stock_code, default="601012")
    parser.add_argument("--pages", type=int, choices=(1, 2), default=1)
    parser.add_argument("--timeout", type=_positive_timeout, default=30.0)
    parser.add_argument(
        "--request-interval", type=_safe_interval, default=3.0,
        help="seconds between navigations; minimum 2.5",
    )
    parser.add_argument("--chrome", help="absolute Chrome/Chromium executable path")
    parser.add_argument(
        "--with-detail", action=argparse.BooleanOptionalAction, default=True,
        help="verify the first standard post detail (default: enabled)",
    )
    parser.add_argument("--json-out", help="optional sanitized JSON output path")
    parser.add_argument("--markdown-out", help="optional Markdown post-list output path")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = reproduce(args)
    except ReproductionFailure as failure:
        payload = _failure_payload(failure, stock_code=args.stock)
        rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        _write_output(args.json_out, rendered)
        print(rendered, end="")
        return failure.exit_code
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    _write_output(args.json_out, rendered)
    _write_output(args.markdown_out, _markdown(result))
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
