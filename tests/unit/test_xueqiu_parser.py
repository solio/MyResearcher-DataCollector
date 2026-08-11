"""Deterministic parser coverage for the approved Xueqiu field contract."""

from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path

import pytest

from myresearcher_collector.sources.xueqiu import (
    CollectorConfig,
    XueqiuCollector,
    XueqiuParseError,
    XueqiuSchemaMismatch,
    XueqiuResponse,
    created_at_to_datetime,
    parse_item,
    parse_page,
    redact_xueqiu_url,
    symbol_for,
)


FIXTURES = Path(__file__).parents[1] / "fixtures" / "xueqiu"


def payload(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text())


def test_symbol_mapping_is_a_share_only() -> None:
    assert symbol_for("600519") == "SH600519"
    assert symbol_for("000001") == "SZ000001"
    with pytest.raises(ValueError):
        symbol_for("HK00700")
    with pytest.raises(ValueError):
        symbol_for("900001")


def test_browser_provenance_redacts_challenge_query_values() -> None:
    value = redact_xueqiu_url(
        "https://xueqiu.com/query/v1/symbol/search/status.json?symbol=SH600519&page=1&xq_a_token=secret&signature=secret"
    )
    assert value == "https://xueqiu.com/query/v1/symbol/search/status.json?symbol=SH600519&page=1"
    assert "secret" not in (value or "")


def test_page_and_item_mapping_preserve_html_and_time() -> None:
    page = parse_page(payload("page_1.json"))
    item = parse_item(
        page.items[0],
        "600519",
        collected_at=created_at_to_datetime(1700001000000),
        raw_ref={"list": "memory://list/page"},
    )
    assert item.source_item_id == "910001"
    assert item.author_id == "810001"
    assert item.author_name == "synthetic-author-1"
    assert item.content == "<p>synthetic body one 😀</p>"
    assert item.published_at == created_at_to_datetime(1700000000000)
    assert item.published_at.tzinfo == timezone.utc
    assert item.like_count == 1
    assert item.reply_count == 2
    assert item.forward_count == 3
    assert item.raw_ref == {"list": "memory://list/page"}


def test_nullable_title_and_duplicate_rows_are_not_cleaned_or_rejected() -> None:
    page = parse_page(payload("page_1.json"))
    assert page.items[1]["title"] is None
    duplicate_page = dict(payload("page_1.json"))
    duplicate_page["list"] = list(page.items) + [page.items[0]]
    assert len(parse_page(duplicate_page).items) == 3


@pytest.mark.parametrize(
    "row, message",
    [
        ({"description": "x"}, "id"),
        ({"id": 1, "description": "x", "user": {}}, "user.id"),
        ({"id": 1, "description": "x", "user": {"id": 2}}, "screen_name"),
        ({"id": 1, "description": "x", "target": "https://xueqiu.com/1", "user": {"id": 2, "screen_name": "u"}, "created_at": "bad", "fav_count": 0, "reply_count": 0, "retweet_count": 0}, "created_at"),
    ],
)
def test_missing_or_invalid_required_fields_fail(row: dict[str, object], message: str) -> None:
    with pytest.raises(XueqiuParseError, match=message):
        parse_item(
            row,
            "600519",
            collected_at=created_at_to_datetime(1700001000000),
            raw_ref={"list": "memory://list/page"},
        )


def test_empty_valid_result_and_missing_list_are_distinct() -> None:
    empty = {"list": [], "count": 0, "maxPage": 1, "page": 1}
    assert parse_page(empty).items == ()
    with pytest.raises(XueqiuSchemaMismatch):
        parse_page(payload("missing_list.json"))


@pytest.mark.parametrize("status", [401, 403])
def test_access_status_is_failure_not_no_data(status: int) -> None:
    class Transport:
        def get(self, url: str, *, timeout: float):
            del url, timeout
            return XueqiuResponse(status, b"<html>challenge</html>", {"content-type": "text/html"})

    result = XueqiuCollector(
        Transport(),
        config=CollectorConfig(min_interval_seconds=3.0),
        sleep_fn=lambda _: None,
    ).collect("600519", max_pages=2)
    assert result.status.value not in {"SUCCESS", "NO_NEW_DATA"}
