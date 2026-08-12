"""Truthful acquisition evidence shared by Eastmoney access methods."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


HTTP_RESPONSE = "http_response"
BROWSER_DOM_SNAPSHOT = "browser_dom_snapshot"


@dataclass(frozen=True)
class AcquiredDocument:
    """Immutable bytes actually consumed by the parser plus observed provenance.

    A DOM snapshot is deliberately not represented as an HTTP response.  Its
    HTTP-only fields remain unavailable instead of being fabricated.
    """

    payload: bytes
    request_url: str
    observed_url: str
    capture_method: str
    fetched_at: datetime
    http_status: int | None = None
    content_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def body(self) -> bytes:
        return self.payload

    @property
    def status_code(self) -> int | None:
        return self.http_status

    @property
    def headers(self) -> dict[str, str]:
        return {} if self.content_type is None else {"content-type": self.content_type}

    @property
    def final_url(self) -> str:
        return self.observed_url

    @property
    def text(self) -> str:
        return self.payload.decode("utf-8", errors="replace")
