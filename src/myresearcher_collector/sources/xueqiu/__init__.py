"""Approved Xueqiu v0.1 source boundary."""

from .browser_transport import (
    BrowserTransportError,
    ENTRY_URL,
    XueqiuBrowserTransport,
    XueqiuResponse,
    XueqiuTransport,
    redact_xueqiu_url,
)
from .collector import (
    CollectorConfig,
    InMemoryRawEvidenceStore,
    SOURCE,
    XUEQIU_BOOTSTRAP_MIN_PAGES,
    XueqiuAccessFailure,
    XueqiuCollector,
    XueqiuTransportFailure,
    symbol_for,
)
from .parser import (
    SCHEMA_VERSION,
    XueqiuPage,
    XueqiuPaginationError,
    XueqiuParseError,
    XueqiuSchemaMismatch,
    created_at_to_datetime,
    parse_item,
    parse_json,
    parse_page,
)

__all__ = [
    "BrowserTransportError", "CollectorConfig", "ENTRY_URL",
    "InMemoryRawEvidenceStore", "SCHEMA_VERSION", "SOURCE",
    "XUEQIU_BOOTSTRAP_MIN_PAGES", "XueqiuAccessFailure",
    "XueqiuBrowserTransport", "XueqiuCollector", "XueqiuPage",
    "XueqiuPaginationError", "XueqiuParseError", "XueqiuResponse",
    "XueqiuSchemaMismatch", "XueqiuTransport", "XueqiuTransportFailure",
    "created_at_to_datetime", "parse_item", "parse_json", "parse_page",
    "redact_xueqiu_url",
    "symbol_for",
]
