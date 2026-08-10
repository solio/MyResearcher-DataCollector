"""Small persistence value objects used by the local storage boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class PublishedRaw:
    """A durable raw file, not yet a SQLite evidence row."""

    source: str
    sha256: str
    byte_size: int
    relative_path: str
    absolute_path: Path


@dataclass(frozen=True)
class SafeFrontier:
    """A Collector-declared contiguous frontier for persistence to validate."""

    watermark_utc: datetime | str
    all_required_persisted: bool = True
    unresolved_gaps: tuple[str, ...] = ()
