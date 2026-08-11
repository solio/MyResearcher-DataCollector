"""Explicit, conservative local raw-body retention maintenance."""

from __future__ import annotations

import sqlite3
import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .schema import utc_text


RAW_BODY_RETENTION_DAYS = 7


@dataclass(frozen=True)
class RetentionReport:
    scanned_objects: int
    eligible_objects: int
    purged_objects: int
    retained_recent: int
    retained_failure: int
    logical_bytes_eligible: int
    physical_bytes_purged: int
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["errors"] = list(self.errors)
        return value


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _verify_body(path: Path, digest: str, expected_size: int) -> int:
    """Verify the immutable body before considering a physical purge."""
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
            size += len(chunk)
    if size != expected_size or hasher.hexdigest() != digest:
        raise ValueError("PRESENT body integrity failure")
    return size


def _recover_tombstone(path: Path, tombstone: Path, body_state: str, errors: list[str], source: str, digest: str) -> None:
    """Recover the two safe post-rename states before scanning an object."""
    if body_state == "PURGED":
        if tombstone.exists():
            try:
                tombstone.unlink()
            except OSError as exc:
                errors.append(f"{source}:{digest}: purged tombstone cleanup failed: {type(exc).__name__}")
        return
    if tombstone.exists() and not path.exists():
        try:
            tombstone.rename(path)
        except OSError as exc:
            errors.append(f"{source}:{digest}: interrupted purge recovery failed: {type(exc).__name__}")
    elif tombstone.exists() and path.exists():
        # A previous recovery restored the body but could not remove its stale
        # tombstone.  PRESENT remains authoritative, so remove only the stale
        # marker.
        try:
            tombstone.unlink()
        except OSError as exc:
            errors.append(f"{source}:{digest}: stale purge tombstone cleanup failed: {type(exc).__name__}")


def purge_raw_bodies(
    *,
    db_path: str | Path,
    raw_data_dir: str | Path,
    retention_days: int = RAW_BODY_RETENTION_DAYS,
    dry_run: bool = True,
    confirm: bool = False,
    now: datetime | None = None,
) -> RetentionReport:
    """Report or purge eligible physical bodies; metadata is never deleted."""
    if not isinstance(retention_days, int) or isinstance(retention_days, bool) or retention_days < 0:
        raise ValueError("retention_days must be a non-negative integer")
    if not dry_run and not confirm:
        raise ValueError("physical purge requires explicit confirm")
    if dry_run and confirm:
        raise ValueError("dry_run and confirm are mutually exclusive")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    cutoff = current.astimezone(timezone.utc) - timedelta(days=retention_days)
    db_path = Path(db_path)
    raw_data_dir = Path(raw_data_dir)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    errors: list[str] = []
    scanned = eligible = purged = retained_recent = retained_failure = 0
    logical_eligible = physical_purged = 0
    try:
        objects = conn.execute(
            "SELECT source, content_sha256, body_state FROM raw_body_state ORDER BY source, content_sha256"
        ).fetchall()
        scanned = len(objects)
        for source, digest, body_state in objects:
            references = conn.execute(
                """SELECT e.evidence_id, e.fetched_at_utc, e.byte_size,
                          e.filesystem_path,
                          r.status,
                          EXISTS(SELECT 1 FROM collection_failures AS f
                                 WHERE f.evidence_id=e.evidence_id) AS has_failure
                     FROM raw_evidence AS e
                     JOIN collection_runs AS r ON r.run_id=e.run_id
                    WHERE r.source=? AND e.content_sha256=?
                    ORDER BY e.fetched_at_utc""",
                (source, digest),
            ).fetchall()
            if not references:
                errors.append(f"{source}:{digest}: no evidence references")
                continue
            path = (raw_data_dir / references[0][3]).resolve()
            source_root = (raw_data_dir / "raw" / source).resolve()
            if source_root not in path.parents or path.name != f"{digest}.body":
                errors.append(f"{source}:{digest}: unsafe filesystem path")
                continue
            tombstone = path.with_name(f"{digest}.body.purging")
            _recover_tombstone(path, tombstone, body_state, errors, source, digest)
            if body_state == "PURGED":
                continue
            try:
                file_size = path.stat().st_size
            except FileNotFoundError:
                errors.append(f"{source}:{digest}: PRESENT body is unexpectedly missing")
                continue
            try:
                file_size = _verify_body(path, digest, max(int(row[2]) for row in references))
            except (OSError, ValueError) as exc:
                errors.append(f"{source}:{digest}: {exc}")
                continue
            latest_fetched = max(_parse_utc(row[1]) for row in references)
            has_running = any(row[4] == "RUNNING" for row in references)
            has_failure = any(bool(row[5]) or row[4] in {"COLLECTION_FAILED", "SPEC_MISMATCH"} for row in references)
            if has_failure:
                retained_failure += 1
                continue
            if has_running or latest_fetched > cutoff:
                retained_recent += 1
                continue
            eligible += 1
            logical_eligible += max(int(row[2]) for row in references)
            if dry_run:
                continue
            try:
                path.rename(tombstone)
            except OSError as exc:
                errors.append(f"{source}:{digest}: purge rename failed: {type(exc).__name__}")
                continue
            try:
                conn.execute(
                    """UPDATE raw_body_state
                          SET body_state='PURGED', purged_at_utc=?, updated_at_utc=?
                        WHERE source=? AND content_sha256=? AND body_state='PRESENT'""",
                    (utc_text(current), utc_text(current), source, digest),
                )
                conn.commit()
            except Exception as exc:
                conn.rollback()
                try:
                    tombstone.rename(path)
                except OSError as restore_exc:
                    errors.append(
                        f"{source}:{digest}: state update failed: {type(exc).__name__}; "
                        f"body restore failed: {type(restore_exc).__name__}"
                    )
                else:
                    errors.append(f"{source}:{digest}: state update failed: {type(exc).__name__}")
                continue
            purged += 1
            try:
                tombstone.unlink()
            except OSError as exc:
                errors.append(f"{source}:{digest}: purged tombstone cleanup failed: {type(exc).__name__}")
            else:
                physical_purged += file_size
        return RetentionReport(
            scanned_objects=scanned,
            eligible_objects=eligible,
            purged_objects=purged,
            retained_recent=retained_recent,
            retained_failure=retained_failure,
            logical_bytes_eligible=logical_eligible,
            physical_bytes_purged=physical_purged,
            errors=tuple(errors),
        )
    finally:
        conn.close()
