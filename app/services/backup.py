"""Nightly database backup (R-JOB-05).

``pg_dump`` to GCS, thirty days retained. R-JOB-05 is explicit that this is the
backup we rely on, not Neon's own history: Neon is a third party on a free
tier, and a backup you do not hold is a backup you are hoping for.

The dump is the whole database, including the encrypted bank columns. It is
ciphertext there and stays ciphertext here — restoring it needs the key from
Secret Manager, which is the point (R-SEC-03).
"""

from __future__ import annotations

import gzip
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from app.config import Settings
from app.services.storage import ObjectStore, StorageError

logger = logging.getLogger(__name__)

RETENTION_DAYS = 30
PG_DUMP_TIMEOUT_SECONDS = 300

_BACKUP_NAME = re.compile(r"^backups/(\d{4}-\d{2}-\d{2})\.sql\.gz$")


class BackupError(RuntimeError):
    """The backup did not happen. Always worth a human's attention."""


@dataclass(slots=True)
class BackupReport:
    job: str = "backup"
    object_name: str = ""
    bytes_written: int = 0
    deleted: int = 0
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "job": self.job,
            "object": self.object_name,
            "bytes": self.bytes_written,
            "deleted": self.deleted,
            "detail": self.detail,
        }


def object_name_for(day: date) -> str:
    """R-STO-02: ``backups/{date}.sql.gz``."""
    return f"backups/{day.isoformat()}.sql.gz"


def libpq_environment(sqlalchemy_url: str) -> dict[str, str]:
    """Turn the connection URL into libpq environment variables.

    The password never becomes a command-line argument: argv is visible to
    anything that can list processes, and this one is the database password.
    libpq reads PGPASSWORD and friends from the environment instead.
    """
    from urllib.parse import parse_qs, unquote, urlparse

    parsed = urlparse(
        sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)
    )
    if not parsed.hostname:
        raise BackupError("the database URL has no host")

    environment: dict[str, str] = {
        "PGHOST": parsed.hostname,
        "PGDATABASE": unquote(parsed.path.lstrip("/")) or "postgres",
        "PGCONNECT_TIMEOUT": "15",
    }
    if parsed.port:
        environment["PGPORT"] = str(parsed.port)
    if parsed.username:
        environment["PGUSER"] = unquote(parsed.username)
    if parsed.password:
        environment["PGPASSWORD"] = unquote(parsed.password)

    query = parse_qs(parsed.query)
    # R-SEC-07: the dump connects with the same TLS guarantees as the app.
    for key, variable in (
        ("sslmode", "PGSSLMODE"),
        ("sslrootcert", "PGSSLROOTCERT"),
        ("channel_binding", "PGCHANNELBINDING"),
    ):
        value = (query.get(key) or [""])[0]
        if value:
            environment[variable] = value
    return environment


def run_pg_dump(database_url: str, *, timeout: int = PG_DUMP_TIMEOUT_SECONDS) -> bytes:
    """Dump the database. Returns the uncompressed SQL."""
    executable = shutil.which("pg_dump")
    if executable is None:
        raise BackupError("pg_dump is not installed in this image")

    environment = {**os.environ, **libpq_environment(database_url)}

    try:
        # A fixed argv with no shell, and nothing sensitive in it.
        completed = subprocess.run(  # noqa: S603
            [executable, "--no-owner", "--no-privileges", "--format=plain"],
            capture_output=True,
            timeout=timeout,
            check=False,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        raise BackupError(f"pg_dump timed out after {timeout}s") from exc

    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", "replace").strip().splitlines()
        summary = stderr[0][:200] if stderr else "no output"
        # Belt and braces in case a message ever echoes a URL back.
        summary = re.sub(r"postgresql://[^\s]+", "postgresql://***", summary)
        raise BackupError(f"pg_dump failed ({completed.returncode}): {summary}")

    if not completed.stdout:
        raise BackupError("pg_dump produced nothing")
    return completed.stdout


def prune(store: ObjectStore, *, today: date, keep_days: int = RETENTION_DAYS) -> int:
    """Delete backups older than the retention window. Returns how many.

    Only touches objects whose names match the backup pattern exactly, so a
    misconfigured bucket cannot turn this into a delete-everything job.
    """
    lister = getattr(store, "list_names", None)
    remover = getattr(store, "delete", None)
    if lister is None or remover is None:
        return 0

    cutoff = today - timedelta(days=keep_days)
    deleted = 0
    for name in lister("backups/"):
        match = _BACKUP_NAME.match(name)
        if not match:
            continue
        try:
            stamp = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if stamp < cutoff:
            remover(name)
            deleted += 1
    return deleted


def run(
    *,
    settings: Settings,
    store: ObjectStore,
    now: datetime | None = None,
    dump: object = None,
) -> BackupReport:
    """Take a backup and prune old ones (R-JOB-05).

    Idempotent by name: running twice on the same day overwrites that day's
    object rather than accumulating, so a double fire from Scheduler is safe.
    """
    moment = now or datetime.now(UTC)
    today = moment.date()

    url = settings.database_url.get_secret_value()
    if not url:
        raise BackupError("no database configured")

    dumper = dump or (lambda: run_pg_dump(url))
    raw = dumper()
    compressed = gzip.compress(raw, compresslevel=6)

    name = object_name_for(today)
    try:
        store.put(name, compressed, content_type="application/gzip")
    except StorageError as exc:
        raise BackupError(f"could not write the backup: {exc}") from exc

    deleted = prune(store, today=today)

    # Sizes and counts only. A backup's contents are the whole database.
    logger.info(
        "backup written: %s, %d bytes compressed from %d, %d pruned",
        name,
        len(compressed),
        len(raw),
        deleted,
    )
    return BackupReport(
        object_name=name,
        bytes_written=len(compressed),
        deleted=deleted,
        detail=f"{len(raw)} bytes dumped, {len(compressed)} stored",
    )
