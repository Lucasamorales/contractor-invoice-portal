"""Which database a migration is about to change, and how it says so.

Separate from ``migrations/env.py`` because that file is loaded by Alembic
rather than imported, so nothing in it can be tested. What is here decides
where a migration lands, which is worth a test.

Both parts exist because of one afternoon: a migration aimed at production ran
against staging instead, announced "Running upgrade 0004 -> 0005", and looked
like a success. Nothing in the output named the database it had changed.
"""

from __future__ import annotations

import os

from sqlalchemy import Connection

from modules.invoices.config import Environment, Settings, get_settings

ENVIRONMENT_VAR = "PORTAL_ENVIRONMENT"


def migration_settings() -> Settings:
    """Settings for a migration, ignoring ``.env`` in production.

    ``.env`` holds staging's credentials for local development, and
    ``PORTAL_MIGRATION_URL`` is one of them. Because a migration prefers that
    setting over ``PORTAL_DATABASE_URL``, exporting a production URL was not
    enough: the file quietly won and the migration went to staging.

    Production credentials are break-glass (R-SEC-09) and live in Secret
    Manager. A file on somebody's laptop has no business in that path, so when
    the environment says production the file is not read at all.
    """
    if os.environ.get(ENVIRONMENT_VAR, "").strip().lower() == (
        Environment.PRODUCTION.value
    ):
        return Settings(_env_file=None)
    return get_settings()


def migration_url(settings: Settings | None = None) -> str:
    """The direct endpoint when one is configured, otherwise the pooled one.

    DDL through PgBouncer's transaction pooling is not something to rely on,
    which is why the direct endpoint wins where it is set.
    """
    settings = settings or migration_settings()
    return (
        settings.migration_url.get_secret_value()
        or settings.database_url.get_secret_value()
    )


def describe_target(connection: Connection) -> str:
    """What is on the other end, in terms that tell two environments apart.

    Both databases are called ``neondb``, so a name proves nothing on its own.
    Its revision and how many people are in it do: seventeen real people is
    production, ten invented ones is staging.

    Every lookup checks the table exists first rather than catching the error.
    A failed statement poisons the transaction it is in, and this runs inside
    the one the migration is about to use.
    """
    parts = [
        f"database {connection.exec_driver_sql('select current_database()').scalar()}"
    ]

    if _exists(connection, "alembic_version"):
        revision = connection.exec_driver_sql(
            "select version_num from alembic_version"
        ).scalar()
        parts.append(f"at {revision or 'no revision'}")
    else:
        parts.append("never migrated")

    if _exists(connection, "people"):
        people = connection.exec_driver_sql("select count(*) from people").scalar()
        parts.append(f"holding {people} people")

    return ", ".join(parts)


def _exists(connection: Connection, table: str) -> bool:
    """Unqualified on purpose: the check and the query that follows it have to
    resolve the same way, and both go through ``search_path``. Asking about
    ``public.people`` and then counting ``people`` is the same bug in two
    lines."""
    return (
        connection.exec_driver_sql(f"select to_regclass('{table}')").scalar()
        is not None
    )
