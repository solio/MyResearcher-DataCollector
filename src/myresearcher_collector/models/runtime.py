"""Source-isolated runtime and raw-record models.

These models intentionally stop before the provisional Collector → DataClean
envelope. They provide deterministic developer/test boundaries only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class CollectionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    NO_NEW_DATA = "NO_NEW_DATA"
    PARTIAL_COLLECTION = "PARTIAL_COLLECTION"
    COLLECTION_FAILED = "COLLECTION_FAILED"
    SPEC_MISMATCH = "SPEC_MISMATCH"
    CANCELLED = "CANCELLED"


@dataclass
class RuntimeCounters:
    requests_total: int = 0
    requests_success: int = 0
    requests_failed: int = 0
    pages_requested: int = 0
    pages_success: int = 0
    pages_failed: int = 0
    records_received: int = 0
    records_parsed: int = 0
    records_failed: int = 0
    records_out_of_scope: int = 0
    duplicate_records: int = 0
    details_requested: int = 0
    details_success: int = 0
    details_failed: int = 0
    identity_content_drifts: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
        }


@dataclass(frozen=True)
class SourceItem:
    """An accepted source item at the shared raw persistence boundary.

    The column-shaped fields are retained for compatibility with the frozen
    SQLite schema.  Source adapters may leave source-irrelevant fields null;
    ``raw_ref`` remains an opaque mapping owned by the adapter/integration.
    """

    source: str
    schema_version: str
    source_item_id: str
    requested_bar_code: str
    canonical_bar_code: str | None
    canonical_bar_name: str | None
    author_id: str | None
    author_name: str | None
    title: str | None
    content: str
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
    collected_at: datetime
    source_times_raw: dict[str, str | None]
    source_metadata: dict[str, Any]
    raw_ref: dict[str, str]
    observation_version: int = 1
    final_url: str | None = None

    @property
    def identity_key(self) -> tuple[str, str]:
        return self.source, self.source_item_id


@dataclass(frozen=True)
class GubaSourceItem(SourceItem):
    """An accepted standard Eastmoney Guba top-level post."""


@dataclass(frozen=True)
class XueqiuSourceItem(SourceItem):
    """An accepted Xueqiu top-level discussion post."""


@dataclass
class CollectionResult:
    status: CollectionStatus
    items: list[SourceItem] = field(default_factory=list)
    counters: RuntimeCounters = field(default_factory=RuntimeCounters)
    failures: list[str] = field(default_factory=list)
    stop_reason: str | None = None
    watermark: datetime | None = None
    # Explicit declaration from the source runtime, never inferred by storage.
    safe_frontier: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "items": [item.__dict__.copy() for item in self.items],
            "counters": self.counters.as_dict(),
            "failures": list(self.failures),
            "stop_reason": self.stop_reason,
            "watermark": self.watermark.isoformat() if self.watermark else None,
            "safe_frontier": self.safe_frontier.isoformat() if self.safe_frontier else None,
        }
