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
    parse_detail_page,
    parse_list_page,
)

__all__ = [
    "CollectorConfig",
    "EastmoneyGubaCollector",
    "GubaDetailMismatch",
    "GubaParseError",
    "GubaSchemaMismatch",
    "HttpResponse",
    "InMemoryRawEvidenceStore",
    "UrllibTransport",
    "parse_detail_page",
    "parse_list_page",
]
