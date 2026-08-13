"""Deterministic parser for the approved Eastmoney Guba HTML surfaces."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse


SOURCE = "eastmoney_guba"
SCHEMA_VERSION = "eastmoney_guba.raw.v1"
SHANGHAI = timezone(timedelta(hours=8))
_ID_RE = re.compile(r"^[0-9]+$")
_HOSTS = {"guba.eastmoney.com", "caifuhao.eastmoney.com"}
_ACCESS_TITLE_RE = re.compile(r"<title[^>]*>\s*([^<]+?)\s*</title>", re.IGNORECASE)
_ACCESS_TITLES = {"身份核实", "访问验证", "安全验证", "人机验证"}
# emcaptcha is the captcha container id observed on the 2026-08-13 real
# 身份核实 shell (list,601888,f_51.html); the script markers above also
# match that shell, and emcaptcha holds when its assets differ.
_ACCESS_MARKERS = ("fd_guba_validate", "em_capt.js", "validate.js", "emcaptcha")


class GubaParseError(ValueError):
    """A response cannot be converted into a source item."""


class GubaSchemaMismatch(GubaParseError):
    """The current response no longer matches the approved source shape."""


class GubaDetailMismatch(GubaSchemaMismatch):
    """List identity and detail identity disagree."""


def is_access_block_page(html: str) -> bool:
    """Recognize the observed Eastmoney identity-verification HTML shell.

    This intentionally requires both a known verification title and a
    source-specific verification marker. A generic HTTP-200 HTML response
    without ``article_list`` therefore remains a schema mismatch.
    """
    if not isinstance(html, str) or not html:
        return False
    title_match = _ACCESS_TITLE_RE.search(html)
    if title_match is None:
        return False
    title = " ".join(title_match.group(1).split())
    lowered = html.lower()
    return title in _ACCESS_TITLES and any(marker in lowered for marker in _ACCESS_MARKERS)


@dataclass(frozen=True)
class GubaListItem:
    source_item_id: str
    requested_bar_code: str
    canonical_bar_code: str | None
    canonical_bar_name: str | None
    author_id: str | None
    author_name: str | None
    title: str | None
    published_at: datetime
    last_updated_at: datetime | None
    display_time: datetime | None
    url: str
    post_type: int
    post_state: int | None
    post_top_status: int | None
    read_count: int | None
    reply_count: int | None
    like_count: int | None
    forward_count: int | None
    source_post_id: str | None
    source_times_raw: dict[str, str | None]
    source_metadata: dict[str, Any]


@dataclass(frozen=True)
class GubaPage:
    requested_bar_code: str
    rows: tuple[GubaListItem, ...]
    out_of_scope_rows: tuple[dict[str, Any], ...]
    source_count: int | None
    source_time: str | None


@dataclass(frozen=True)
class GubaDetail:
    source_item_id: str
    canonical_bar_code: str | None
    canonical_bar_name: str | None
    author_id: str | None
    author_name: str | None
    title: str | None
    content: str
    published_at: datetime
    last_updated_at: datetime | None
    display_time: datetime | None
    post_type: int
    post_state: int | None
    post_top_status: int | None
    read_count: int | None
    reply_count: int | None
    like_count: int | None
    forward_count: int | None
    source_post_id: str | None
    source_times_raw: dict[str, str | None]
    source_metadata: dict[str, Any]


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        values = {key.lower(): value for key, value in attrs}
        post_id = values.get("data-postid")
        href = values.get("href")
        if not post_id and href:
            parsed = urlparse(urljoin("https://guba.eastmoney.com", href))
            match = re.fullmatch(r"/news,[A-Za-z0-9]+,([0-9]+)\.html", parsed.path)
            if (
                match is not None
                and parsed.scheme == "https"
                and parsed.hostname == "guba.eastmoney.com"
            ):
                post_id = match.group(1)
        if post_id and href and _ID_RE.fullmatch(post_id):
            self.links.setdefault(post_id, href)


def _embedded_json(html: str, variable: str) -> dict[str, Any]:
    marker = f"var {variable}="
    start = html.find(marker)
    if start < 0:
        raise GubaSchemaMismatch(f"missing embedded {variable}")
    payload = html[start + len(marker):].lstrip()
    try:
        value, _ = json.JSONDecoder().raw_decode(payload)
    except json.JSONDecodeError as exc:
        raise GubaSchemaMismatch(f"invalid embedded {variable}") from exc
    if not isinstance(value, dict):
        raise GubaSchemaMismatch(f"embedded {variable} is not an object")
    return value


def _id(value: Any, field: str) -> str:
    if isinstance(value, bool):
        raise GubaParseError(f"{field} must be decimal digits")
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise GubaParseError(f"{field} must be decimal digits")
    return value


def _optional_text(value: Any, *, preserve_empty: bool = False) -> str | None:
    if value is None:
        return None
    if value == "":
        return "" if preserve_empty else None
    if not isinstance(value, str):
        raise GubaParseError("text field has unexpected type")
    return value


def _optional_int(value: Any, field: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise GubaParseError(f"{field} has unexpected type")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise GubaParseError(f"{field} has unexpected type")


def parse_source_time(value: Any, field: str, *, required: bool) -> datetime | None:
    if value in (None, ""):
        if required:
            raise GubaParseError(f"{field} is required")
        return None
    if not isinstance(value, str):
        raise GubaParseError(f"{field} has unexpected type")
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SHANGHAI)
    except ValueError as exc:
        raise GubaParseError(f"{field} is not a valid source timestamp") from exc


def _optional_source_time(
    value: Any,
    field: str,
    field_errors: dict[str, str],
) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return parse_source_time(value, field, required=False)
    except GubaParseError:
        field_errors[field] = "invalid_source_timestamp"
        return None


def _source_url(href: str, source_item_id: str) -> str:
    url = urljoin("https://guba.eastmoney.com", href)
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _HOSTS:
        raise GubaSchemaMismatch("item URL is outside the Eastmoney allowlist")
    if parsed.hostname == "guba.eastmoney.com":
        expected = f"/news,"
        if not parsed.path.startswith(expected) or not parsed.path.endswith(f",{source_item_id}.html"):
            raise GubaSchemaMismatch("standard item URL does not contain source ID")
    return url


def _source_metadata(
    row: dict[str, Any],
    *,
    detail: bool = False,
    field_errors: dict[str, str] | None = None,
) -> dict[str, Any]:
    names = (
        "post_from_num", "post_has_pic", "post_has_video", "media_type",
        "bullish_bearish", "v_user_code", "post_comment_authority",
    )
    if detail:
        names += ("post_loc", "post_ip_address", "digest_type")
    metadata = {name: row.get(name) for name in names if name in row}
    promoted = {
        "post_id", "post_title", "stockbar_code", "stockbar_name", "user_id",
        "user_nickname", "post_click_count", "post_forward_count",
        "post_comment_count", "post_like_count", "post_publish_time",
        "post_last_time", "post_display_time", "post_type", "post_state",
        "post_top_status", "post_source_id", "post_content", "post_user",
        "post_guba", "post_loc", "post_ip_address", "digest_type",
        *names,
    }
    metadata["extra"] = {key: value for key, value in row.items() if key not in promoted}
    if field_errors:
        metadata["field_errors"] = dict(field_errors)
    return metadata


def _parse_item(row: dict[str, Any], requested_bar_code: str, links: dict[str, str]) -> GubaListItem | dict[str, Any]:
    if not isinstance(row, dict):
        raise GubaParseError("article_list row is not an object")
    post_type = _optional_int(row.get("post_type"), "post_type")
    if post_type is None:
        raise GubaParseError("post_type is required")
    source_item_id = _id(row.get("post_id"), "post_id")
    if post_type != 0:
        return row
    href = links.get(source_item_id)
    if not href:
        raise GubaSchemaMismatch(f"missing list URL for post {source_item_id}")
    published_raw = row.get("post_publish_time")
    field_errors: dict[str, str] = {}
    return GubaListItem(
        source_item_id=source_item_id,
        requested_bar_code=requested_bar_code,
        canonical_bar_code=_optional_text(row.get("stockbar_code")),
        canonical_bar_name=_optional_text(row.get("stockbar_name")),
        author_id=_optional_text(row.get("user_id")),
        author_name=_optional_text(row.get("user_nickname")),
        title=_optional_text(row.get("post_title"), preserve_empty=True),
        published_at=parse_source_time(published_raw, "post_publish_time", required=True),
        last_updated_at=_optional_source_time(row.get("post_last_time"), "post_last_time", field_errors),
        display_time=_optional_source_time(row.get("post_display_time"), "post_display_time", field_errors),
        url=_source_url(href, source_item_id),
        post_type=post_type,
        post_state=_optional_int(row.get("post_state"), "post_state"),
        post_top_status=_optional_int(row.get("post_top_status"), "post_top_status"),
        read_count=_optional_int(row.get("post_click_count"), "post_click_count"),
        reply_count=_optional_int(row.get("post_comment_count"), "post_comment_count"),
        like_count=_optional_int(row.get("post_like_count"), "post_like_count"),
        forward_count=_optional_int(row.get("post_forward_count"), "post_forward_count"),
        source_post_id=_optional_text(str(row.get("post_source_id")) if row.get("post_source_id") not in (None, "") else None),
        source_times_raw={
            "post_publish_time": published_raw if isinstance(published_raw, str) else None,
            "post_last_time": row.get("post_last_time") if isinstance(row.get("post_last_time"), str) else None,
            "post_display_time": row.get("post_display_time") if isinstance(row.get("post_display_time"), str) else None,
        },
        source_metadata=_source_metadata(row, field_errors=field_errors),
    )


def parse_list_page(html: str, requested_bar_code: str) -> GubaPage:
    """Parse one approved ``article_list`` page without executing JavaScript."""
    if not isinstance(html, str) or not html:
        raise GubaSchemaMismatch("list response is empty")
    payload = _embedded_json(html, "article_list")
    if payload.get("rc") != 1:
        raise GubaSchemaMismatch("list response is not source-success")
    rows = payload.get("re")
    if not isinstance(rows, list):
        raise GubaSchemaMismatch("article_list.re is not a list")
    links = _LinkParser()
    try:
        links.feed(html)
    except Exception as exc:
        raise GubaSchemaMismatch("list HTML links are malformed") from exc
    accepted: list[GubaListItem] = []
    out_of_scope: list[dict[str, Any]] = []
    for row in rows:
        parsed = _parse_item(row, requested_bar_code, links.links)
        if isinstance(parsed, dict):
            out_of_scope.append(parsed)
        else:
            accepted.append(parsed)
    source_count = _optional_int(payload.get("count"), "count")
    source_time = payload.get("time") if isinstance(payload.get("time"), str) else None
    return GubaPage(requested_bar_code, tuple(accepted), tuple(out_of_scope), source_count, source_time)


def parse_detail_page(html: str) -> GubaDetail:
    if not isinstance(html, str) or not html:
        raise GubaSchemaMismatch("detail response is empty")
    row = _embedded_json(html, "post_article")
    if not isinstance(row.get("post_guba"), dict):
        raise GubaSchemaMismatch("post_article.post_guba is missing")
    source_item_id = _id(row.get("post_id"), "post_id")
    post_type = _optional_int(row.get("post_type"), "post_type")
    if post_type != 0:
        raise GubaSchemaMismatch("detail is outside standard post_type=0 scope")
    content = row.get("post_content")
    if not isinstance(content, str):
        raise GubaParseError("post_content is required")
    published_raw = row.get("post_publish_time")
    guba = row["post_guba"]
    field_errors: dict[str, str] = {}
    return GubaDetail(
        source_item_id=source_item_id,
        canonical_bar_code=_optional_text(guba.get("stockbar_code")),
        canonical_bar_name=_optional_text(guba.get("stockbar_name")),
        author_id=_optional_text(row.get("post_user", {}).get("user_id") if isinstance(row.get("post_user"), dict) else None),
        author_name=_optional_text(row.get("post_user", {}).get("user_nickname") if isinstance(row.get("post_user"), dict) else None),
        title=_optional_text(row.get("post_title"), preserve_empty=True),
        content=content,
        published_at=parse_source_time(published_raw, "post_publish_time", required=True),
        last_updated_at=_optional_source_time(row.get("post_last_time"), "post_last_time", field_errors),
        display_time=_optional_source_time(row.get("post_display_time"), "post_display_time", field_errors),
        post_type=post_type,
        post_state=_optional_int(row.get("post_state"), "post_state"),
        post_top_status=_optional_int(row.get("post_top_status"), "post_top_status"),
        read_count=_optional_int(row.get("post_click_count"), "post_click_count"),
        reply_count=_optional_int(row.get("post_comment_count"), "post_comment_count"),
        like_count=_optional_int(row.get("post_like_count"), "post_like_count"),
        forward_count=_optional_int(row.get("post_forward_count"), "post_forward_count"),
        source_post_id=_optional_text(str(row.get("post_source_id")) if row.get("post_source_id") not in (None, "") else None),
        source_times_raw={
            "post_publish_time": published_raw if isinstance(published_raw, str) else None,
            "post_last_time": row.get("post_last_time") if isinstance(row.get("post_last_time"), str) else None,
            "post_display_time": row.get("post_display_time") if isinstance(row.get("post_display_time"), str) else None,
        },
        source_metadata=_source_metadata(row, detail=True, field_errors=field_errors),
    )


def merge_list_and_detail(item: GubaListItem, detail: GubaDetail) -> dict[str, Any]:
    if item.source_item_id != detail.source_item_id:
        raise GubaDetailMismatch("list/detail source_item_id mismatch")
    for name in ("author_id", "author_name", "canonical_bar_code", "published_at"):
        if getattr(item, name) != getattr(detail, name):
            raise GubaDetailMismatch(f"list/detail {name} mismatch")
    # Historical DOM detail pages can omit a title even though the list row
    # has it. An omitted detail title is not an identity contradiction; keep
    # the richer list observation. If both surfaces provide titles, they must
    # still agree exactly.
    if item.title and detail.title and item.title != detail.title:
        raise GubaDetailMismatch("list/detail title mismatch")
    source_metadata = {**item.source_metadata, **detail.source_metadata}
    source_metadata["extra"] = {
        **item.source_metadata.get("extra", {}),
        **detail.source_metadata.get("extra", {}),
    }
    source_metadata["field_errors"] = {
        **item.source_metadata.get("field_errors", {}),
        **detail.source_metadata.get("field_errors", {}),
    }
    return {
        "source": SOURCE,
        "source_item_id": item.source_item_id,
        "requested_bar_code": item.requested_bar_code,
        "canonical_bar_code": detail.canonical_bar_code,
        "canonical_bar_name": detail.canonical_bar_name,
        "author_id": detail.author_id,
        "author_name": detail.author_name,
        "title": detail.title or item.title,
        "content": detail.content,
        "published_at": detail.published_at,
        "last_updated_at": detail.last_updated_at,
        "display_time": detail.display_time,
        "url": item.url,
        "post_type": detail.post_type,
        "post_state": detail.post_state,
        "post_top_status": detail.post_top_status,
        "read_count": detail.read_count,
        "reply_count": detail.reply_count,
        "like_count": detail.like_count,
        "forward_count": detail.forward_count,
        "source_post_id": detail.source_post_id,
        "source_times_raw": detail.source_times_raw,
        "source_metadata": source_metadata,
    }
