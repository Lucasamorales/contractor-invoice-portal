"""Alembic environment.

The database URL comes from settings, never from ``alembic.ini``: it is a
secret (R-SEC-02). Offline mode is supported so a migration can be reviewed as
SQL before it is run anywhere near production.
"""

from __future__ import annotations

import logging
import os
from logging.config import fileConfig
from urllib.parse import urlparse

from alembic import context
from sqlalchemy import engine_from_config, pool

from modules.invoices.db.migration_target import describe_target, migration_url
from modules.invoices.db.models import Base
from modules.invoices.db.session import resolve_trust_store

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# A database on this machine is disposable. Anything else belongs to other
# people and holds their invoices.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "db", "postgres", ""})

CONFIRM_VAR = "PORTAL_MIGRATE_HOST"


def guard_remote(url: str) -> None:
    """Refuse to migrate a database elsewhere unless it was named out loud.

    The URL can come from a ``.env`` nobody has read in weeks. An environment
    variable meant to override it can be silently invalid — a byte-order mark
    in front of the scheme is enough — and then the ``.env`` quietly wins and
    the migration lands somewhere it was never pointed.

    So the confirmation is not a yes/no flag: it has to *name the host*, and
    the name has to match. A stale variable, a typo or a copied command then
    fails closed and says which two hosts disagreed.
    """
    host = urlparse(url).hostname or ""
    if host in LOCAL_HOSTS:
        return

    named = os.environ.get(CONFIRM_VAR, "").strip()
    if named == host:
        # Alembic configures logging from alembic.ini before this runs, so the
        # confirmation goes where the rest of its output goes.
        logging.getLogger("alembic.runtime.migration").warning(
            "migrating remote host %s, confirmed by %s", host, CONFIRM_VAR
        )
        return

    mismatch = (
        f"but {CONFIRM_VAR} says:\n    {named}\n"
        if named
        else f"and {CONFIRM_VAR} is not set.\n"
    )
    raise SystemExit(
        "\nalembic is pointed at a remote database:\n"
        f"    {host}\n\n"
        f"{mismatch}\n"
        "If that is genuinely what you meant, name it:\n"
        f"    {CONFIRM_VAR}={host} uv run alembic upgrade head\n"
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting.

    Used to review a migration, and to verify it matches the models without
    needing a database at all. No guard: nothing is written anywhere.
    """
    context.configure(
        url=migration_url() or "postgresql+psycopg://offline/offline",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = migration_url()
    guard_remote(url)
    # The same trust-store handling the application uses. Production's URL asks
    # for sslrootcert=system, which libpq cannot open on Windows, so a
    # migration run from a laptop failed "certificate verify failed" while the
    # app, on the same URL, connected fine.
    url, connect_args = resolve_trust_store(url)
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = url
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )
    with connectable.connect() as connection:
        # Said out loud before anything is changed. The host alone cannot tell
        # two environments apart — they can share an endpoint, with the branch
        # chosen inside the connection string — so this names the database, its
        # revision and how many people are in it.
        logging.getLogger("alembic.runtime.migration").warning(
            "migrating %s", describe_target(connection)
        )
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
