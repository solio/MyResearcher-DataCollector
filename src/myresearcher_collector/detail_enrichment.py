"""Bounded, resumable detail enrichment for current post rows."""
from __future__ import annotations

import random
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .simple_store import SimplePostStore
from .sources.eastmoney_guba.existing_chrome import ExistingChromeAcquisitionError
from .sources.eastmoney_guba.parser import GubaParseError, is_access_block_page, parse_detail_page


def _body(response) -> bytes:
    value = getattr(response, "payload", None)
    if value is None:
        value = getattr(response, "body", None)
    if value is None:
        value = getattr(response, "content", None)
    if value is None:
        value = getattr(response, "text", "")
    return value.encode("utf-8") if isinstance(value, str) else bytes(value)


def execute_detail_enrichment(
    *, db_path: str | Path, stock_code: str, transport,
    clock: Callable[[], object] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    jitter_fn: Callable[[float, float], float] = random.uniform,
    min_delay: float = 3.0,
    max_delay: float = 10.0,
    challenge_wait_seconds: float = 180.0,
    challenge_retries: int = 3,
    log_path: str | Path | None = None,
) -> dict[str, object]:
    store = SimplePostStore(db_path)
    candidates = store.conn.execute(
        """SELECT source_item_id,url,title,published_at FROM posts
           WHERE source='eastmoney_guba' AND stock_code=? AND content IS NULL
             AND length(trim(title))=40 AND url IS NOT NULL
           ORDER BY published_at""", (stock_code,)
    ).fetchall()
    requested = len(candidates)
    run_id = uuid.uuid4().hex
    log_file = Path(log_path) if log_path is not None else Path("runtime/logs/eastmoney-detail-enrichment.jsonl")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    seq = 0
    access_blocks = 0
    success_since_last_block = 0
    last_block_monotonic: float | None = None
    windows: list[dict[str, object]] = []
    current_window: dict[str, object] | None = None

    def write_log(source_item_id: str, result: str, started: float, *, event: str | None = None, **extra: object) -> None:
        nonlocal seq
        seq += 1
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "run_id": run_id, "seq": seq, "source_item_id": str(source_item_id),
            "sleep_before_sec": None, "request_duration_sec": round(time.monotonic() - started, 3),
            "result": result, "success_since_last_block": success_since_last_block,
            "elapsed_since_last_block_sec": (round(time.monotonic() - last_block_monotonic, 3) if last_block_monotonic is not None else None),
        }
        if event is not None:
            row["event"] = event
        row.update(extra)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    success = 0
    failures: list[dict[str, str]] = []
    samples: list[dict[str, object]] = []
    stopped = False
    try:
        for index, (item_id, url, title, _published) in enumerate(candidates):
            sleep_before = 0.0
            if index and max_delay > 0:
                sleep_before = jitter_fn(min_delay, max_delay)
                sleep_fn(sleep_before)
            try:
                request_started = time.monotonic()
                response = None
                body = b""
                html = ""
                for attempt in range(max(1, challenge_retries + 1)):
                    request_started = time.monotonic()
                    response = transport.get(url, timeout=30.0)
                    body = _body(response)
                    html = body.decode("utf-8", errors="replace")
                    if not is_access_block_page(html):
                        break
                    access_blocks += 1
                    write_log(str(item_id), "access_block", request_started, sleep_before_sec=round(sleep_before, 3))
                    now = time.monotonic()
                    if current_window is not None:
                        current_window["elapsed_seconds"] = round(now - float(current_window["_started_monotonic"]), 3)
                        current_window.pop("_started_monotonic", None)
                        windows.append(current_window)
                    current_window = {"success_count": success_since_last_block, "_started_monotonic": now}
                    success_since_last_block = 0
                    last_block_monotonic = now
                    if attempt >= challenge_retries:
                        break
                    print(
                        f"access block for {item_id}; complete visible Chrome verification "
                        f"within {challenge_wait_seconds:.0f}s; polling current DOM every 5s",
                        file=sys.stderr, flush=True,
                    )
                    deadline = time.monotonic() + max(0.0, challenge_wait_seconds)
                    current = getattr(transport, "current_document", None)
                    if not callable(current):
                        sleep_fn(max(0.0, challenge_wait_seconds))
                        continue
                    while time.monotonic() < deadline:
                        sleep_fn(min(5.0, max(0.0, deadline - time.monotonic())))
                        try:
                            candidate = current()
                            candidate_body = _body(candidate)
                            candidate_html = candidate_body.decode("utf-8", errors="replace")
                            if not is_access_block_page(candidate_html):
                                response, body, html = candidate, candidate_body, candidate_html
                                write_log(str(item_id), "manual_verification_resumed", request_started, event="manual_verification_resumed")
                                break
                        except Exception:
                            continue
                    if html and not is_access_block_page(html):
                        break
                if is_access_block_page(html):
                    failures.append({"source_item_id": str(item_id), "reason": "access_block"})
                    stopped = True
                    break
                detail = parse_detail_page(html)
                if detail.source_item_id != str(item_id) or not detail.content.strip():
                    raise GubaParseError("detail identity/content invalid")
                store.update_content("eastmoney_guba", str(item_id), detail.content)
                success += 1
                success_since_last_block += 1
                write_log(str(item_id), "success", request_started, sleep_before_sec=round(sleep_before, 3))
                if len(samples) < 10:
                    samples.append({"source_item_id": str(item_id), "title": title,
                                    "title_length": len(title.strip()), "content": detail.content,
                                    "content_length": len(detail.content), "url": url})
            except ExistingChromeAcquisitionError as exc:
                write_log(str(item_id), "fetch_failure", request_started, sleep_before_sec=round(sleep_before, 3))
                failures.append({"source_item_id": str(item_id), "reason": str(getattr(exc, "kind", "browser_failure"))})
                if getattr(exc, "kind", "") in {"access_block", "challenge", "browser_blocked"}:
                    stopped = True
                    break
            except Exception as exc:
                write_log(str(item_id), "parse_failure", request_started, sleep_before_sec=round(sleep_before, 3))
                failures.append({"source_item_id": str(item_id), "reason": f"{type(exc).__name__}: {exc}"})
        remaining = store.conn.execute(
            """SELECT count(*) FROM posts WHERE source='eastmoney_guba' AND stock_code=?
               AND content IS NULL AND length(trim(title))=40""", (stock_code,)
        ).fetchone()[0]
        if current_window is not None:
            current_window["elapsed_seconds"] = round(time.monotonic() - float(current_window["_started_monotonic"]), 3)
            current_window.pop("_started_monotonic", None)
            windows.append(current_window)
        return {"run_id": run_id, "requested": requested, "success": success, "failed": len(failures),
                "content_filled": success, "candidates_remaining": int(remaining),
                "stopped": stopped, "access_block_count": access_blocks,
                "challenge_windows": windows, "jsonl_path": str(log_file),
                "failures": failures, "samples": samples}
    finally:
        store.close()
