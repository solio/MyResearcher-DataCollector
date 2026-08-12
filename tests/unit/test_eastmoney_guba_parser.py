"""Deterministic parser tests for the approved Eastmoney Guba shape."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from myresearcher_collector.sources.eastmoney_guba.parser import (
    GubaDetailMismatch,
    GubaParseError,
    GubaSchemaMismatch,
    merge_list_and_detail,
    parse_detail_page,
    parse_list_page,
)


FIXTURES = Path(__file__).parents[1] / "fixtures" / "eastmoney_guba"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_list_parser_preserves_scope_and_source_times() -> None:
    page = parse_list_page(fixture("list_page_1.html"), "600001")

    assert [row.source_item_id for row in page.rows] == ["1001"]
    assert len(page.out_of_scope_rows) == 1
    assert page.out_of_scope_rows[0]["post_type"] == 20
    row = page.rows[0]
    assert row.url == "https://guba.eastmoney.com/news,600001,1001.html"
    assert row.published_at.isoformat() == "2026-08-10T10:00:00+08:00"
    assert row.last_updated_at.isoformat() == "2026-08-10T10:05:00+08:00"
    assert row.published_at != row.last_updated_at
    assert row.read_count == 0
    assert row.reply_count == 0


def test_cross_bar_and_nullable_fields_remain_explicit() -> None:
    html = fixture("list_page_1.html").replace(
        '"user_id":"9001","user_nickname":"author-one"',
        '"user_id":null,"user_nickname":null',
    ).replace(
        '"post_click_count":0,"post_forward_count":0,"post_comment_count":0',
        '"post_forward_count":0,"post_comment_count":0',
        1,
    )
    page = parse_list_page(html, "600002")
    row = page.rows[0]
    assert row.requested_bar_code == "600002"
    assert row.canonical_bar_code == "600001"
    assert row.author_id is None
    assert row.author_name is None
    assert row.read_count is None


def test_empty_title_and_optional_invalid_times_are_preserved_as_field_state() -> None:
    html = fixture("list_page_1.html").replace(
        '"post_title":"synthetic post one"', '"post_title":""', 1
    ).replace(
        '"post_last_time":"2026-08-10 10:05:00"', '"post_last_time":"invalid"', 1
    ).replace(
        '"post_display_time":"2026-08-10 10:00:00"', '"post_display_time":"also-invalid"', 1
    )
    row = parse_list_page(html, "600001").rows[0]
    assert row.title == ""
    assert row.last_updated_at is None
    assert row.display_time is None
    assert row.source_metadata["field_errors"] == {
        "post_last_time": "invalid_source_timestamp",
        "post_display_time": "invalid_source_timestamp",
    }


def test_source_metadata_extra_is_retained() -> None:
    html = fixture("list_page_1.html").replace(
        '"post_source_id":""', '"post_source_id":"","synthetic_extra":{"flag":true}', 1
    )
    row = parse_list_page(html, "600001").rows[0]
    assert row.source_metadata["extra"] == {"synthetic_extra": {"flag": True}}


def test_detail_parser_and_merge_preserve_empty_body_without_title_fallback() -> None:
    page = parse_list_page(fixture("list_page_1.html"), "600001")
    detail_html = fixture("detail_1001.html").replace(
        "synthetic body one", ""
    )
    detail = parse_detail_page(detail_html)
    merged = merge_list_and_detail(page.rows[0], detail)

    assert merged["content"] == ""
    assert merged["title"] == "synthetic post one"


def test_detail_empty_title_uses_list_title_for_real_historical_shape() -> None:
    page = parse_list_page(fixture("list_page_1.html"), "600001")
    detail_html = fixture("detail_1001.html").replace(
        '"post_title":"synthetic post one"', '"post_title":""', 1
    )
    merged = merge_list_and_detail(page.rows[0], parse_detail_page(detail_html))

    assert merged["title"] == "synthetic post one"


def test_malformed_embedded_payload_is_schema_mismatch() -> None:
    with pytest.raises(GubaSchemaMismatch):
        parse_list_page(fixture("malformed_page.html"), "600001")


def test_missing_identity_is_rejected_without_fallback() -> None:
    html = fixture("list_page_1.html").replace('"post_id":1001', '"post_id":""')
    with pytest.raises(GubaParseError):
        parse_list_page(html, "600001")


def test_detail_identity_mismatch_is_rejected() -> None:
    page = parse_list_page(fixture("list_page_1.html"), "600001")
    detail_html = fixture("detail_1001.html").replace('"post_id":1001', '"post_id":9999')
    with pytest.raises(GubaDetailMismatch):
        merge_list_and_detail(page.rows[0], parse_detail_page(detail_html))


def test_invalid_publish_time_is_rejected() -> None:
    html = fixture("list_page_1.html").replace(
        "2026-08-10 10:00:00", "2026-02-30 10:00:00", 1
    )
    with pytest.raises(GubaParseError):
        parse_list_page(html, "600001")
