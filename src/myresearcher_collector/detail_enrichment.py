"""Bounded, resumable detail enrichment for current post rows."""
from __future__ import annotations

import random
import time
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
) -> dict[str, object]:
    store = SimplePostStore(db_path)
    candidates = store.conn.execute(
        """SELECT source_item_id,url,title,published_at FROM posts
           WHERE source='eastmoney_guba' AND stock_code=? AND content IS NULL
             AND length(trim(title))=40 AND url IS NOT NULL
           ORDER BY published_at""", (stock_code,)
    ).fetchall()
    requested = len(candidates)
    success = 0
    failures: list[dict[str, str]] = []
    samples: list[dict[str, object]] = []
    stopped = False
    try:
        for index, (item_id, url, title, _published) in enumerate(candidates):
            if index and max_delay > 0:
                sleep_fn(jitter_fn(min_delay, max_delay))
            try:
                response = transport.get(url, timeout=30.0)
                body = _body(response)
                html = body.decode("utf-8", errors="replace")
                if is_access_block_page(html):
                    failures.append({"source_item_id": str(item_id), "reason": "access_block"})
                    stopped = True
                    break
                detail = parse_detail_page(html)
                if detail.source_item_id != str(item_id) or not detail.content.strip():
                    raise GubaParseError("detail identity/content invalid")
                store.update_content("eastmoney_guba", str(item_id), detail.content)
                success += 1
                if len(samples) < 10:
                    samples.append({"source_item_id": str(item_id), "title": title,
                                    "title_length": len(title.strip()), "content": detail.content,
                                    "content_length": len(detail.content), "url": url})
            except ExistingChromeAcquisitionError as exc:
                failures.append({"source_item_id": str(item_id), "reason": str(getattr(exc, "kind", "browser_failure"))})
                if getattr(exc, "kind", "") in {"access_block", "challenge", "browser_blocked"}:
                    stopped = True
                    break
            except Exception as exc:
                failures.append({"source_item_id": str(item_id), "reason": f"{type(exc).__name__}: {exc}"})
        remaining = store.conn.execute(
            """SELECT count(*) FROM posts WHERE source='eastmoney_guba' AND stock_code=?
               AND content IS NULL AND length(trim(title))=40""", (stock_code,)
        ).fetchone()[0]
        return {"requested": requested, "success": success, "failed": len(failures),
                "content_filled": success, "candidates_remaining": int(remaining),
                "stopped": stopped, "failures": failures, "samples": samples}
    finally:
        store.close()
