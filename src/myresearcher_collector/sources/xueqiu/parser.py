"""Deterministic parser for the approved Xueqiu discussion JSON response."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from myresearcher_collector.models import XueqiuSourceItem


SOURCE = "xueqiu"
SCHEMA_VERSION = "xueqiu.raw.v1"


class XueqiuParseError(ValueError):
    """A response or item cannot be converted to the source contract."""


class XueqiuSchemaMismatch(XueqiuParseError):
    """The response shape no longer matches the approved source contract."""


class XueqiuPaginationError(XueqiuParseError):
    """Pagination metadata or continuity is invalid."""


@dataclass(frozen=True)
class XueqiuPage:
    items: tuple[dict[str, Any], ...]
    count: int
    max_page: int
    page: int


_PROMOTED = {
    "id", "description", "title", "created_at", "target", "user",
    "fav_count", "reply_count", "retweet_count",
}


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise XueqiuSchemaMismatch(f"{field} must be an object")
    return value


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise XueqiuParseError(f"{field} is required")
    return value


def _source_id(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        raise XueqiuParseError("id is required")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value:
        return value
    raise XueqiuParseError("id must be a non-empty string or integer")


def _required_count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise XueqiuParseError(f"{field} must be a non-negative integer")
    return value


def created_at_to_datetime(value: Any) -> datetime:
    """Convert source Unix epoch milliseconds to canonical UTC."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise XueqiuParseError("created_at must be Unix epoch milliseconds")
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise XueqiuParseError("created_at is outside the supported range") from exc


def parse_json(payload: bytes | str | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(payload, Mapping):
        value = dict(payload)
    else:
        raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise XueqiuSchemaMismatch("response is not valid JSON") from exc
    if not isinstance(value, dict):
        raise XueqiuSchemaMismatch("response must be a JSON object")
    return value


def parse_page(payload: bytes | str | Mapping[str, Any]) -> XueqiuPage:
    """Parse one page while retaining raw item mappings for the collector."""
    value = parse_json(payload)
    rows = value.get("list")
    if not isinstance(rows, list):
        raise XueqiuSchemaMismatch("response.list must be a list")
    count = _required_count(value.get("count"), "count")
    max_page = _required_count(value.get("maxPage"), "maxPage")
    page = _required_count(value.get("page"), "page")
    if page < 1 or max_page < 1:
        raise XueqiuPaginationError("page and maxPage must be positive")
    for row in rows:
        if not isinstance(row, dict):
            raise XueqiuSchemaMismatch("list item must be an object")
    return XueqiuPage(tuple(rows), count, max_page, page)


def parse_item(
    row: Mapping[str, Any],
    stock_code: str,
    *,
    collected_at: datetime,
    raw_ref: dict[str, str],
    final_url: str | None = None,
) -> XueqiuSourceItem:
    """Map exactly the frozen source fields; no semantic content cleaning."""
    source_item_id = _source_id(row.get("id"))
    user = _object(row.get("user"), "user")
    author_id = user.get("id")
    if isinstance(author_id, bool) or author_id is None or (
        not isinstance(author_id, (str, int)) or str(author_id) == ""
    ):
        raise XueqiuParseError("user.id is required")
    author_name = _required_text(user.get("screen_name"), "user.screen_name")
    description = row.get("description")
    if not isinstance(description, str):
        raise XueqiuParseError("description is required")
    title = row.get("title")
    if title is not None and not isinstance(title, str):
        raise XueqiuParseError("title must be a string or null")
    url = _required_text(row.get("target"), "target")
    published_at = created_at_to_datetime(row.get("created_at"))
    like_count = _required_count(row.get("fav_count"), "fav_count")
    reply_count = _required_count(row.get("reply_count"), "reply_count")
    forward_count = _required_count(row.get("retweet_count"), "retweet_count")
    metadata = {
        key: value for key, value in row.items() if key not in _PROMOTED
    }
    return XueqiuSourceItem(
        source=SOURCE,
        schema_version=SCHEMA_VERSION,
        source_item_id=source_item_id,
        requested_bar_code=stock_code,
        canonical_bar_code=stock_code,
        canonical_bar_name=None,
        author_id=str(author_id),
        author_name=author_name,
        title=title,
        content=description,
        published_at=published_at,
        last_updated_at=None,
        display_time=None,
        url=url,
        post_type=0,
        post_state=None,
        post_top_status=None,
        read_count=None,
        reply_count=reply_count,
        like_count=like_count,
        forward_count=forward_count,
        source_post_id=None,
        collected_at=collected_at,
        source_times_raw={"created_at": str(row["created_at"])},
        source_metadata=metadata,
        raw_ref=dict(raw_ref),
        final_url=final_url,
    )

