"""The backup job and the object stores (R-JOB-05, R-STO-01 .. R-STO-04).

No GCS and no Postgres: the dump is injected and the store is local. What is
tested is the part that would lose data if it were wrong — what gets written,
what gets deleted, and what never reaches a log.
"""

from __future__ import annotations

import gzip
import logging
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from modules.invoices.config import Settings
from modules.invoices.services import backup
from modules.invoices.services.storage import (
    GcsObjectStore,
    LocalObjectStore,
    StorageError,
    build_store,
)

DUMP = b"-- PostgreSQL database dump\nCREATE TABLE people (...);\n" * 50


@pytest.fixture
def store(tmp_path: Path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "objects")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://u:p@host/db?sslmode=verify-full",
    )


# --------------------------------------------------------------------------
# The backup (R-JOB-05)
# --------------------------------------------------------------------------


def test_a_backup_is_written_compressed_under_its_date(store, settings) -> None:
    report = backup.run(
        settings=settings,
        store=store,
        now=datetime(2026, 9, 3, 2, 0, tzinfo=UTC),
        dump=lambda: DUMP,
    )
    assert report.object_name == "backups/2026-09-03.sql.gz"  # R-STO-02
    assert store.exists(f"file://{(store.root / report.object_name).as_posix()}")
    stored = (store.root / report.object_name).read_bytes()
    assert gzip.decompress(stored) == DUMP
    assert len(stored) < len(DUMP), "it should actually be compressed"


def test_running_twice_in_a_day_overwrites_rather_than_accumulating(
    store, settings
) -> None:
    """Scheduler retries, and a double fire must not fill the bucket."""
    for _ in range(3):
        backup.run(
            settings=settings,
            store=store,
            now=datetime(2026, 9, 3, 2, 0, tzinfo=UTC),
            dump=lambda: DUMP,
        )
    assert store.list_names("backups/") == ["backups/2026-09-03.sql.gz"]


def test_backups_older_than_thirty_days_are_pruned(store, settings) -> None:
    for day in ("2026-07-01", "2026-08-03", "2026-08-20", "2026-09-02"):
        store.put(f"backups/{day}.sql.gz", b"old", content_type="application/gzip")

    report = backup.run(
        settings=settings,
        store=store,
        now=datetime(2026, 9, 3, 2, 0, tzinfo=UTC),
        dump=lambda: DUMP,
    )

    remaining = store.list_names("backups/")
    # 30 days before 3 September is 4 August.
    assert "backups/2026-07-01.sql.gz" not in remaining
    assert "backups/2026-08-03.sql.gz" not in remaining
    assert "backups/2026-08-20.sql.gz" in remaining
    assert "backups/2026-09-02.sql.gz" in remaining
    assert "backups/2026-09-03.sql.gz" in remaining
    assert report.deleted == 2


def test_pruning_only_ever_touches_things_that_look_like_backups(store) -> None:
    """A misconfigured bucket must not turn this into a delete-everything job."""
    keep = [
        "backups/notes.txt",
        "backups/2026-08-01.sql",  # not gzipped: not ours
        "invoices/2026-08/an invoice.pdf",
        "receipts/abc/def/ghi.png",
    ]
    for name in keep:
        store.put(name, b"x", content_type="application/octet-stream")
    store.put("backups/2020-01-01.sql.gz", b"x", content_type="application/gzip")

    deleted = backup.prune(store, today=date(2026, 9, 3))

    assert deleted == 1
    for name in keep:
        assert name in store.list_names(""), f"{name} should have been left alone"


def test_a_failed_dump_raises_rather_than_writing_an_empty_backup(
    store, settings
) -> None:
    """An empty file under today's date is worse than no file: it looks fine."""

    def explode() -> bytes:
        raise backup.BackupError("pg_dump failed (1): could not connect")

    with pytest.raises(backup.BackupError):
        backup.run(settings=settings, store=store, dump=explode)
    assert store.list_names("backups/") == []


def test_no_database_configured_is_refused(store) -> None:
    with pytest.raises(backup.BackupError):
        backup.run(settings=Settings(_env_file=None), store=store, dump=lambda: DUMP)


def test_the_password_never_reaches_the_command_line(settings) -> None:
    """argv is visible to anything that can list processes."""
    environment = backup.libpq_environment(
        "postgresql+psycopg://neondb_owner:sup3rs3cret@ep-x.neon.tech/neondb"
        "?sslmode=verify-full&channel_binding=require"
    )
    assert environment["PGPASSWORD"] == "sup3rs3cret"
    assert environment["PGHOST"] == "ep-x.neon.tech"
    assert environment["PGUSER"] == "neondb_owner"
    assert environment["PGDATABASE"] == "neondb"
    # R-SEC-07: the dump connects with the same guarantees as the app.
    assert environment["PGSSLMODE"] == "verify-full"
    assert environment["PGCHANNELBINDING"] == "require"


def test_a_url_encoded_password_is_decoded(settings) -> None:
    environment = backup.libpq_environment(
        "postgresql+psycopg://user:p%40ss%2Fword@host/db"
    )
    assert environment["PGPASSWORD"] == "p@ss/word"


def test_the_log_line_carries_sizes_not_contents(store, settings, caplog) -> None:
    """A dump is the whole database, including every fee."""
    with caplog.at_level(logging.INFO):
        backup.run(
            settings=settings,
            store=store,
            now=datetime(2026, 9, 3, 2, 0, tzinfo=UTC),
            dump=lambda: DUMP,
        )
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "backups/2026-09-03.sql.gz" in logged
    assert "CREATE TABLE" not in logged
    assert "sup3rs3cret" not in logged


# --------------------------------------------------------------------------
# The stores (R-STO-01 .. R-STO-04)
# --------------------------------------------------------------------------


def test_a_gcs_uri_for_another_bucket_is_refused() -> None:
    """Either a bug, or an attempt to read somebody else's data with our
    credentials."""
    store = GcsObjectStore("portal-invoices-production", client=object())
    assert (
        store._name_from("gs://portal-invoices-production/invoices/x.pdf")
        == "invoices/x.pdf"
    )
    for bad in (
        "gs://someone-elses-bucket/secrets.txt",
        "file:///etc/passwd",
        "https://example.com/x",
        "gs://portal-invoices-production/",
    ):
        with pytest.raises(StorageError):
            store._name_from(bad)


def test_a_signed_url_cannot_be_configured_to_outlive_fifteen_minutes() -> None:
    """R-STO-03 caps the TTL, and the cap is enforced where the URL is made."""
    with pytest.raises(StorageError):
        GcsObjectStore("b", client=object(), signing_ttl_seconds=3600)
    with pytest.raises(StorageError):
        GcsObjectStore("b", client=object(), signing_ttl_seconds=0)
    assert GcsObjectStore("b", client=object(), signing_ttl_seconds=900)


def test_an_empty_bucket_name_is_refused() -> None:
    with pytest.raises(StorageError):
        GcsObjectStore("", client=object())


def test_the_store_is_local_only_when_no_bucket_is_configured() -> None:
    assert isinstance(build_store(Settings(_env_file=None)), LocalObjectStore)
    configured = Settings(_env_file=None, gcs_bucket="portal-invoices-staging")
    built = build_store(configured)
    assert isinstance(built, GcsObjectStore)
    assert built.bucket_name == "portal-invoices-staging"


def test_a_local_object_name_cannot_escape_the_root(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    with pytest.raises(StorageError):
        store.put("../../escaped.txt", b"x", content_type="text/plain")


def test_local_listing_is_prefix_scoped(store) -> None:
    store.put("backups/a.sql.gz", b"1", content_type="application/gzip")
    store.put("invoices/2026-09/b.pdf", b"2", content_type="application/pdf")
    assert store.list_names("backups/") == ["backups/a.sql.gz"]
    assert store.list_names("invoices/") == ["invoices/2026-09/b.pdf"]
    assert len(store.list_names("")) == 2


def test_the_wizard_uses_the_bucket_when_one_is_configured() -> None:
    """The route dependency must go through ``build_store``.

    It once returned a hardcoded local store, so a deployed service wrote
    invoice PDFs into its own container filesystem and the submit failed at
    the last step, with the bucket sitting there unused. Asserting the wiring
    rather than the branch is the point: build_store was already correct.
    """
    from modules.invoices.web.member import get_store

    assert isinstance(get_store(Settings(_env_file=None)), LocalObjectStore)
    deployed = get_store(Settings(_env_file=None, gcs_bucket="portal-invoices-staging"))
    assert isinstance(deployed, GcsObjectStore)
    assert deployed.bucket_name == "portal-invoices-staging"
