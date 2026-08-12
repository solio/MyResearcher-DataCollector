"""Approved Eastmoney Guba source adapter."""

from .acquisition import AcquiredDocument, BROWSER_DOM_SNAPSHOT, HTTP_RESPONSE

from .browser_transport import (
    EastmoneyBrowserBoundaryError,
    EastmoneyBrowserResponse,
    EastmoneyBrowserSocketTransport,
    EastmoneyBrowserTransport,
    EastmoneyBrowserTransportError,
)
from .collector import (
    BOOTSTRAP_MIN_PAGES,
    BackfillCollectionResult,
    CollectorConfig,
    EastmoneyGubaCollector,
    HttpResponse,
    InMemoryRawEvidenceStore,
    UrllibTransport,
)
from .existing_chrome import (
    EastmoneyExistingChromeDomTransport,
    ExistingChromeAcquisitionError,
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
    "BOOTSTRAP_MIN_PAGES",
    "AcquiredDocument",
    "BROWSER_DOM_SNAPSHOT",
    "BackfillCollectionResult",
    "CollectorConfig",
    "EastmoneyBrowserBoundaryError",
    "EastmoneyBrowserResponse",
    "EastmoneyBrowserSocketTransport",
    "EastmoneyBrowserTransport",
    "EastmoneyBrowserTransportError",
    "EastmoneyGubaCollector",
    "EastmoneyExistingChromeDomTransport",
    "ExistingChromeAcquisitionError",
    "GubaDetailMismatch",
    "GubaParseError",
    "GubaSchemaMismatch",
    "SCHEMA_VERSION",
    "HttpResponse",
    "HTTP_RESPONSE",
    "InMemoryRawEvidenceStore",
    "UrllibTransport",
    "parse_detail_page",
    "parse_list_page",
]
