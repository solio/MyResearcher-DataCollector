"""Parser for the Expert-verified public Xueqiu stock-page DOM shape."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse


SHANGHAI = timezone(timedelta(hours=8))
_MODIFIED_RE = re.compile(r"^\s*修改于")
_ABSOLUTE_RE = re.compile(r"(?:(\d{4})[-/])?(\d{1,2})[-/](\d{1,2})\s+(\d{1,2}):(\d{2})")
_RELATIVE_RE = re.compile(r"^(\d+)\s*(分钟前|小时前|天前)")


class XueqiuDomParseError(ValueError):
    """A DOM item or page does not contain the approved public shape."""


@dataclass(frozen=True)
class XueqiuDomItem:
    status_id: str
    author_id: str | None
    author_name: str | None
    url: str
    content: str
    title: str | None
    time_text_observed: str
    published_at: datetime | None
    read_count: int | None = None
    reply_count: int | None = None
    like_count: int | None = None
    forward_count: int | None = None
    edited_at: datetime | None = None

    @property
    def requires_detail_timestamp(self) -> bool:
        return self.published_at is None


@dataclass(frozen=True)
class XueqiuDomPage:
    page_no: int
    items: tuple[XueqiuDomItem, ...]
    active_ids: tuple[str, ...]


def _optional_count(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, str) and not value.strip().isdigit():
        # Display abbreviations such as ``1.2万`` are not losslessly
        # convertible to the integer posts contract; retain them as NULL.
        return None
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def parse_time_text(value: str, *, now: datetime) -> datetime | None:
    """Parse only unambiguous list timestamps; never treat edited text as publish time."""
    text = " ".join(value.split())
    if not text or _MODIFIED_RE.search(text):
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local_now = now.astimezone(SHANGHAI)
    match = _ABSOLUTE_RE.search(text)
    if match:
        year, month, day, hour, minute = match.groups()
        try:
            return datetime(
                int(year or local_now.year), int(month), int(day), int(hour), int(minute),
                tzinfo=SHANGHAI,
            ).astimezone(timezone.utc)
        except ValueError as exc:
            raise XueqiuDomParseError("invalid DOM timestamp") from exc
    if text.startswith("今天"):
        clock = re.search(r"(\d{1,2}):(\d{2})", text)
        if clock:
            return local_now.replace(hour=int(clock.group(1)), minute=int(clock.group(2)), second=0, microsecond=0).astimezone(timezone.utc)
    if text.startswith("昨天"):
        clock = re.search(r"(\d{1,2}):(\d{2})", text)
        if clock:
            value = local_now - timedelta(days=1)
            return value.replace(hour=int(clock.group(1)), minute=int(clock.group(2)), second=0, microsecond=0).astimezone(timezone.utc)
    relative = _RELATIVE_RE.match(text)
    if relative:
        amount, unit = int(relative.group(1)), relative.group(2)
        delta = timedelta(minutes=amount) if unit == "分钟前" else timedelta(hours=amount) if unit == "小时前" else timedelta(days=amount)
        return (now.astimezone(timezone.utc) - delta)
    return None


def _text(value: Any, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise XueqiuDomParseError(f"{field} must be text")
    if not value.strip():
        if nullable:
            return None
        raise XueqiuDomParseError(f"{field} is required")
    return value


def parse_dom_item(raw: Mapping[str, Any], *, now: datetime) -> XueqiuDomItem:
    status_id = _text(raw.get("status_id"), "status_id")
    url = _text(raw.get("url"), "url")
    content = _text(raw.get("content"), "content")
    time_text = _text(raw.get("time_text_observed"), "time_text_observed")
    author_name = _text(raw.get("author_name"), "author_name", nullable=True)
    author_id = _text(raw.get("author_id"), "author_id", nullable=True)
    title = _text(raw.get("title"), "title", nullable=True)
    absolute_url = urljoin("https://xueqiu.com", str(url))
    if urlparse(absolute_url).hostname not in {"xueqiu.com", "www.xueqiu.com"}:
        raise XueqiuDomParseError("detail URL is outside the Xueqiu host boundary")
    return XueqiuDomItem(
        status_id=str(status_id), author_id=author_id, author_name=author_name,
        url=absolute_url, content=str(content), title=title,
        time_text_observed=str(time_text), published_at=parse_time_text(str(time_text), now=now),
        read_count=_optional_count(raw.get("read_count")),
        reply_count=_optional_count(raw.get("reply_count")),
        like_count=_optional_count(raw.get("like_count")),
        forward_count=_optional_count(raw.get("forward_count")),
    )


def parse_dom_page(raw_items: list[Mapping[str, Any]], *, page_no: int, now: datetime) -> XueqiuDomPage:
    if page_no < 1:
        raise XueqiuDomParseError("page_no must be positive")
    items = tuple(parse_dom_item(item, now=now) for item in raw_items)
    return XueqiuDomPage(page_no, items, tuple(item.status_id for item in items))


def parse_detail_status(raw: Mapping[str, Any], *, now: datetime | None = None) -> tuple[str, datetime, datetime | None]:
    raw_id = raw.get("id")
    if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)) or not str(raw_id).strip():
        raise XueqiuDomParseError("SNOWMAN_STATUS.id is required")
    status_id = str(raw_id)
    created_ms = raw.get("created_at")
    if isinstance(created_ms, bool) or not isinstance(created_ms, (int, float)):
        raise XueqiuDomParseError("SNOWMAN_STATUS.created_at is required")
    try:
        created = datetime.fromtimestamp(float(created_ms) / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise XueqiuDomParseError("SNOWMAN_STATUS.created_at is invalid") from exc
    edited = None
    edited_ms = raw.get("edited_at")
    if edited_ms not in (None, ""):
        if isinstance(edited_ms, bool) or not isinstance(edited_ms, (int, float)):
            raise XueqiuDomParseError("SNOWMAN_STATUS.edited_at is invalid")
        edited = datetime.fromtimestamp(float(edited_ms) / 1000, tz=timezone.utc)
    return str(status_id), created, edited
