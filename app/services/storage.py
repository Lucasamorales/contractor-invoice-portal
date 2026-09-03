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
from datetime import timedelta
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

    def list_names(self, prefix: str) -> list[str]:
        """Object names under a prefix. Used by the backup's retention sweep."""
        ...

    def delete(self, name: str) -> None:
        """Remove one object by name."""
        ...


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

    def list_names(self, prefix: str) -> list[str]:
        base = self.root
        if not base.exists():
            return []
        return sorted(
            str(path.relative_to(base)).replace("\\", "/")
            for path in base.rglob("*")
            if path.is_file()
            and str(path.relative_to(base)).replace("\\", "/").startswith(prefix)
        )

    def delete(self, name: str) -> None:
        path = self._path(name)
        if path.exists():
            path.unlink()

    def clear(self) -> None:
        """Test helper. Never called by the application."""
        if self.root.exists():
            shutil.rmtree(self.root)


class GcsObjectStore:
    """The real store (R-STO-01 .. R-STO-04). GCS is the system of record.

    Authenticates with Cloud Run's attached service account. There are no
    downloaded key files anywhere in this system (CLAUDE.md §2.7), which is
    also why signing uses the IAM API rather than a private key on disk.
    """

    def __init__(
        self,
        bucket: str,
        *,
        client: object | None = None,
        signing_ttl_seconds: int = 900,
        service_account_email: str | None = None,
    ) -> None:
        if not bucket:
            raise StorageError("no bucket configured")
        if not 0 < signing_ttl_seconds <= 900:
            # R-STO-03 caps the lifetime of a signed URL at fifteen minutes.
            raise StorageError("signed URL TTL must be between 1 and 900 seconds")
        self.bucket_name = bucket
        self._ttl = signing_ttl_seconds
        self._service_account_email = service_account_email
        self._client = client

    @property
    def client(self) -> object:
        if self._client is None:
            # Imported here so the rest of the application — the seed, the
            # domain tests — does not need the GCS client to import.
            from google.cloud import storage

            self._client = storage.Client()
        return self._client

    def _blob(self, name: str) -> object:
        return self.client.bucket(self.bucket_name).blob(name)

    def put(self, name: str, data: bytes, *, content_type: str) -> str:
        blob = self._blob(name)
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{self.bucket_name}/{name}"

    def get(self, uri: str) -> bytes:
        from google.api_core import exceptions

        try:
            return self._blob(self._name_from(uri)).download_as_bytes()
        except exceptions.NotFound as exc:
            raise StorageError("object not found") from exc

    def exists(self, uri: str) -> bool:
        return bool(self._blob(self._name_from(uri)).exists())

    def _name_from(self, uri: str) -> str:
        """The object name inside our bucket, and refuse anything else.

        A URI that names a different bucket is either a bug or an attempt to
        read somebody else's data through our credentials.
        """
        if not uri.startswith("gs://"):
            raise StorageError("not a GCS URI")
        bucket, _, name = uri.removeprefix("gs://").partition("/")
        if bucket != self.bucket_name:
            raise StorageError("that object is not in this bucket")
        if not name:
            raise StorageError("no object name in the URI")
        return name

    def list_names(self, prefix: str) -> list[str]:
        return [
            blob.name
            for blob in self.client.list_blobs(self.bucket_name, prefix=prefix)
        ]

    def delete(self, name: str) -> None:
        import contextlib

        from google.api_core import exceptions

        # Already gone is the outcome we wanted.
        with contextlib.suppress(exceptions.NotFound):
            self._blob(name).delete()

    def signed_url(self, uri: str, *, method: str = "GET") -> str:
        """A V4 signed URL, short-lived (R-STO-03).

        Never logged and never emailed (CLAUDE.md §2.8). The caller is
        responsible for putting it only in a response body.
        """
        return str(
            self._blob(self._name_from(uri)).generate_signed_url(
                version="v4",
                expiration=timedelta(seconds=self._ttl),
                method=method,
                # Signing without a key file: Cloud Run's identity asks the IAM
                # API to sign on its behalf (R-SEC-01, CLAUDE.md §2.7).
                service_account_email=self._service_account_email,
                access_token=None,
            )
        )

    def signed_upload_url(self, name: str, *, content_type: str) -> str:
        """A URL the browser can PUT to directly (R-WIZ-04).

        Receipt bytes never pass through Cloud Run.
        """
        return str(
            self._blob(name).generate_signed_url(
                version="v4",
                expiration=timedelta(seconds=self._ttl),
                method="PUT",
                content_type=content_type,
                service_account_email=self._service_account_email,
                access_token=None,
            )
        )


def build_store(settings: object) -> ObjectStore:
    """The store for this environment.

    Production must have a bucket — ``assert_production_ready`` already refuses
    to start without one — so the local fallback cannot quietly ship.
    """
    bucket = getattr(settings, "gcs_bucket", "")
    if bucket:
        return GcsObjectStore(
            bucket,
            signing_ttl_seconds=getattr(settings, "signed_url_ttl_seconds", 900),
            service_account_email=getattr(settings, "service_account_email", None)
            or None,
        )
    return LocalObjectStore(Path("out/objects"))
