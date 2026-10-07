"""Where a migration lands, and what it says before it changes anything.

Written after a migration aimed at production ran against staging: the
production URL was exported, but ``.env`` sets ``PORTAL_MIGRATION_URL`` for local
development and a migration prefers that setting, so the file quietly won. The
output said "Running upgrade 0004 -> 0005" and named no database.
"""

from __future__ import annotations

import os

import pytest

from core.config import Settings, get_settings
from core.db.migration_target import ENVIRONMENT_VAR, migration_settings, migration_url

STAGING_URL = "postgresql+psycopg://user:pw@staging.example/neondb"
PRODUCTION_URL = "postgresql+psycopg://user:pw@production.example/neondb"


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """A .env in the working directory, as a developer's laptop has."""
    (tmp_path / ".env").write_text(
        f"PORTAL_MIGRATION_URL={STAGING_URL}\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_production_never_reads_the_local_env_file(env_file, monkeypatch) -> None:
    """The exported URL has to win, or a production migration goes to staging.

    Production credentials are break-glass and live in Secret Manager
    (R-SEC-09); a file on a laptop has no business in that path.
    """
    monkeypatch.setenv(ENVIRONMENT_VAR, "production")
    monkeypatch.setenv("PORTAL_DATABASE_URL", PRODUCTION_URL)

    assert migration_url(migration_settings()) == PRODUCTION_URL


def test_staging_still_uses_the_env_file(env_file, monkeypatch) -> None:
    """Local development against staging is the whole point of the file."""
    monkeypatch.setenv(ENVIRONMENT_VAR, "staging")

    assert migration_url(migration_settings()) == STAGING_URL


def test_the_direct_endpoint_wins_where_one_is_set() -> None:
    """DDL through PgBouncer's transaction pooling is not to be relied on."""
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://user:pw@pooled.example/neondb",
        migration_url=PRODUCTION_URL,
    )
    assert migration_url(settings) == PRODUCTION_URL


def test_the_pooled_url_is_the_fallback() -> None:
    settings = Settings(_env_file=None, database_url=PRODUCTION_URL)
    assert migration_url(settings) == PRODUCTION_URL


def test_an_unset_environment_is_not_treated_as_production(
    env_file, monkeypatch
) -> None:
    """Failing open here would break every local migration."""
    monkeypatch.delenv(ENVIRONMENT_VAR, raising=False)
    assert os.environ.get(ENVIRONMENT_VAR) is None

    assert migration_url(migration_settings()) == STAGING_URL
