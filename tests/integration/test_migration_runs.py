"""A migration run for real, against a real database (R-DATA-12).

tests/test_migration.py renders the SQL offline and checks it against the
models. That cannot see whether the SQL is *committed*, and one afternoon it was
not: the banner that names the database before a migration ran its queries on
the migration's own connection, which opened a transaction first. Alembic treats
a transaction that is already open as somebody else's to commit, so every
upgrade printed "Running upgrade", exited cleanly and, when the connection
closed, rolled back. No error, and no tables.

So this does what the offline test cannot: upgrade a throwaway database, ask it
what it holds, and downgrade it again.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from core.config import get_settings
from tests.integration.conftest import _test_url

pytestmark = pytest.mark.integration

THROWAWAY = "portal_migration_check"

TABLES = (
    "select table_name from information_schema.tables where table_schema = 'public'"
)


@pytest.fixture
def throwaway_url(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """An empty database that exists only for this test.

    ``engine`` is requested only so the whole suite skips cleanly when there is
    no Postgres to talk to.
    """
    base = make_url(_test_url())
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'drop database if exists "{THROWAWAY}"'))
        conn.execute(text(f'create database "{THROWAWAY}"'))

    url = base.set(database=THROWAWAY).render_as_string(hide_password=False)
    # Environment variables outrank .env, so a developer's staging URL cannot
    # redirect this to a database that matters.
    monkeypatch.setenv("PORTAL_MIGRATION_URL", url)
    monkeypatch.setenv("PORTAL_DATABASE_URL", url)
    monkeypatch.setenv("PORTAL_ENVIRONMENT", "staging")
    get_settings.cache_clear()
    try:
        yield url
    finally:
        get_settings.cache_clear()
        with admin.connect() as conn:
            conn.execute(text(f'drop database if exists "{THROWAWAY}" with (force)'))
        admin.dispose()


def tables_in(url: str) -> set[str]:
    check = create_engine(url)
    try:
        with check.connect() as conn:
            return {row[0] for row in conn.execute(text(TABLES))}
    finally:
        check.dispose()


def test_an_upgrade_persists_and_a_downgrade_removes_it(throwaway_url: str) -> None:
    config = Config("alembic.ini")
    head = ScriptDirectory.from_config(config).get_current_head()

    command.upgrade(config, "head")

    present = tables_in(throwaway_url)
    assert {"people", "invoices", "bank_details", "outbox"} <= present, (
        "the upgrade reported success but left no schema behind"
    )
    check = create_engine(throwaway_url)
    try:
        with check.connect() as conn:
            assert conn.execute(
                text("select version_num from alembic_version")
            ).all() == [(head,)]
    finally:
        check.dispose()

    command.downgrade(config, "base")

    assert tables_in(throwaway_url) - {"alembic_version"} == set(), (
        "the downgrade left tables behind"
    )
