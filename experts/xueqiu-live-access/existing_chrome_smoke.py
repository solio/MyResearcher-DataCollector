#!/usr/bin/env python3
"""Minimal production-compatible Xueqiu smoke through normal user Chrome."""

from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone

from myresearcher_collector.sources.xueqiu.dom_parser import parse_dom_page
from myresearcher_collector.sources.xueqiu.dom_transport import (
    create_xueqiu_dom_transport,
)


def main() -> int:
    rng = random.Random()
    transport = create_xueqiu_dom_transport("existing-chrome")
    report: dict[str, object] = {
        "source": "xueqiu",
        "stock_code": "601012",
        "entry_url": "https://xueqiu.com/S/SH601012",
        "acquisition_mode": transport.acquisition_mode,
        "profile_mode": "existing-user",
        "result": "FAIL",
    }
    try:
        transport.open_stock("601012")
        now = datetime.now(timezone.utc)
        first_payload = transport.read_current_page()
        first = parse_dom_page(first_payload["items"], page_no=1, now=now)
        if not first.items or len(first.active_ids) != len(set(first.active_ids)):
            raise RuntimeError("page 1 IDs are empty or duplicated")
        if any(not item.content for item in first.items):
            raise RuntimeError("page 1 has unreadable content")

        page_delay = rng.uniform(3.0, 10.0)
        time.sleep(page_delay)
        second_payload = transport.goto_page(2, previous_ids=first.active_ids)
        second = parse_dom_page(second_payload["items"], page_no=2, now=now)
        if not second.items or len(second.active_ids) != len(set(second.active_ids)):
            raise RuntimeError("page 2 IDs are empty or duplicated")
        if first.active_ids == second.active_ids:
            raise RuntimeError("page 2 did not change IDs")

        report.update({
            "post_dom_loaded": transport.post_dom_loaded,
            "goto_count": transport.main_goto_count,
            "navigation_chain": transport.frame_navigation_urls,
            "page_delay_seconds": round(page_delay, 3),
            "page1": {"count": len(first.items), "ids": list(first.active_ids)},
            "page2": {"count": len(second.items), "ids": list(second.active_ids)},
            "page_ids_overlap": len(set(first.active_ids) & set(second.active_ids)),
        })

        modified = next(
            (item for item in second.items if item.requires_detail_timestamp),
            None,
        )
        detail_report: dict[str, object] = {"encountered": modified is not None}
        if modified is not None:
            detail_delay = rng.uniform(3.0, 10.0)
            time.sleep(detail_delay)
            detail = transport.read_detail_created_at(modified.url)
            transport.assert_current_page(2, expected_ids=second.active_ids)
            detail_report.update({
                "status_id": modified.status_id,
                "resolved_id": str(detail.get("id")),
                "created_at_present": detail.get("created_at") is not None,
                "delay_seconds": round(detail_delay, 3),
                "main_page_preserved": True,
            })

        report.update({"modified_detail": detail_report, "result": "PASS"})
        return 0
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        return 1
    finally:
        transport.close()
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
