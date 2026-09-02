"""Object storage for invoices and receipts (R-STO-01 .. R-STO-04).

GCS is the system of record. The bucket does not exist yet, so this module
defines the interface the submit path depends on and ships a local
implementation for development and tests. The GCS implementation lands with
the bucket, in build order step 6.

Keeping submit behind this boundary means the transaction structure of
R-SUB-03..05 is testable now, without waiting for infrastructure.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Protocol


class StorageError(RuntimeError):
    """An object could not be stored or fetched."""


class ObjectStore(Protocol):
    """What the submit path needs from storage, and nothing more."""

    def put(self, name: str, data: bytes, *, content_type: str) -> str:
        """Store bytes under ``name`` and return the URI to record on the row."""

    def get(self, uri: str) -> bytes:
        """Fetch bytes back, for re-sending or re-rendering."""

    def exists(self, uri: str) -> bool: ...


class LocalObjectStore:
    """Development and test storage: a directory on disk.

    Deliberately not a stand-in for GCS in production. It has no versioning, no
    access control and no signed URLs, so ``app/config.py`` requires a bucket
    before a production service will start.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, name: str) -> Path:
        # Object names are built by the application from a period, a legal name
        # and a number. Refuse anything that could climb out of the root anyway.
        candidate = (self.root / name).resolve()
        root = self.root.resolve()
        if not candidate.is_relative_to(root):
            raise StorageError("object name escapes the storage root")
        return candidate

    def put(self, name: str, data: bytes, *, content_type: str) -> str:
        del content_type  # Recorded by GCS; a filesystem has nowhere to put it.
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"file://{path.as_posix()}"

    def get(self, uri: str) -> bytes:
        path = Path(uri.removeprefix("file://"))
        if not path.exists():
            raise StorageError("object not found")
        return path.read_bytes()

    def exists(self, uri: str) -> bool:
        return Path(uri.removeprefix("file://")).exists()

    def clear(self) -> None:
        """Test helper. Never called by the application."""
        if self.root.exists():
            shutil.rmtree(self.root)
