"""Content-addressed, no-clobber raw response storage."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .models import PublishedRaw


class RawStoreError(RuntimeError):
    """The raw store cannot prove safe publication or reference integrity."""


class RawBodyPurged(RawStoreError):
    """The evidence metadata remains, but its retained body has expired."""


class RawEvidenceStore:
    """Publish immutable response bytes under a local data directory."""

    def __init__(self, data_dir: str | os.PathLike[str], source: str = "eastmoney_guba") -> None:
        self.data_dir = Path(data_dir)
        self.source = source
        self.raw_dir = self.data_dir / "raw" / source
        self.tmp_dir = self.data_dir / "raw" / ".tmp"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _digest(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    def _final_path(self, digest: str) -> Path:
        return self.raw_dir / f"{digest}.body"

    def publish(
        self,
        run_id: str,
        ordinal: int,
        payload: bytes,
        *,
        expected_sha256: str | None = None,
        expected_size: int | None = None,
    ) -> PublishedRaw:
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise TypeError("raw payload must be bytes-like")
        body = bytes(payload)
        digest = self._digest(body)
        size = len(body)
        if expected_sha256 is not None and expected_sha256 != digest:
            raise RawStoreError("raw SHA-256 mismatch")
        if expected_size is not None and expected_size != size:
            raise RawStoreError("raw byte-size mismatch")

        final_path = self._final_path(digest)
        temp_path = self.tmp_dir / f"{run_id}-{ordinal}.partial"
        try:
            with temp_path.open("xb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            if temp_path.stat().st_size != size:
                raise RawStoreError("raw temporary byte-size mismatch")

            if final_path.exists():
                self._verify_path(final_path, digest, size)
                temp_path.unlink(missing_ok=True)
            else:
                # link() is the no-clobber publish primitive. os.replace is
                # deliberately not used because it would overwrite a target.
                try:
                    os.link(temp_path, final_path)
                except FileExistsError:
                    self._verify_path(final_path, digest, size)
                except OSError as exc:
                    raise RawStoreError("safe no-clobber raw publish unsupported") from exc
                finally:
                    temp_path.unlink(missing_ok=True)
                directory_fd = os.open(final_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            return PublishedRaw(
                source=self.source,
                sha256=digest,
                byte_size=size,
                relative_path=str(final_path.relative_to(self.data_dir)),
                absolute_path=final_path,
            )
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _verify_path(path: Path, digest: str, size: int) -> None:
        try:
            stat_size = path.stat().st_size
        except FileNotFoundError as exc:
            raise RawStoreError("referenced raw file is missing") from exc
        if stat_size != size:
            raise RawStoreError("referenced raw file size mismatch")
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        if hasher.hexdigest() != digest:
            raise RawStoreError("referenced raw file hash mismatch")

    def verify(self, relative_path: str, digest: str, size: int) -> Path:
        candidate = (self.data_dir / relative_path).resolve()
        raw_root = (self.data_dir / "raw").resolve()
        if raw_root not in candidate.parents:
            raise RawStoreError("raw path escapes data directory")
        self._verify_path(candidate, digest, size)
        return candidate
