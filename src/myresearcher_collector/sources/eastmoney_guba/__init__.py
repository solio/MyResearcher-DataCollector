"""Approved Eastmoney Guba source adapter."""

from .collector import (
    CollectorConfig,
    EastmoneyGubaCollector,
    HttpResponse,
    InMemoryRawEvidenceStore,
    UrllibTransport,
)
from .parser import (
    GubaDetailMismatch,
    GubaParseError,
    GubaSchemaMismatch,
    SCHEMA_VERSION,
    parse_detail_page,
    parse_list_page,
)

__all__ = [
    "CollectorConfig",
    "EastmoneyGubaCollector",
    "GubaDetailMismatch",
    "GubaParseError",
    "GubaSchemaMismatch",
    "SCHEMA_VERSION",
    "HttpResponse",
    "InMemoryRawEvidenceStore",
    "UrllibTransport",
    "parse_detail_page",
    "parse_list_page",
]
