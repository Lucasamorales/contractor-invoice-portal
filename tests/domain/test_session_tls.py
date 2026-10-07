"""Database TLS configuration (R-SEC-07).

These are the cases that only bite in a deployed container, which is where they
bit: the service came up, IAP let the person in, and then every page was a 500
because libpq could not verify Neon's certificate.
"""

from __future__ import annotations

import pytest

from modules.invoices.config import ConfigError, Environment, Settings
from modules.invoices.db.session import assert_tls, build_engine, resolve_trust_store

NEON = (
    "postgresql+psycopg://user:pass@ep-x.eu-central-1.aws.neon.tech/neondb"
    "?sslmode=verify-full&sslrootcert=system&channel_binding=require"
)


def test_system_becomes_a_bundle_that_exists() -> None:
    """ "system" means "however OpenSSL was built", which in psycopg's bundled
    libpq finds neither Debian's store nor Windows'."""
    url, connect_args = resolve_trust_store(NEON)

    assert "sslrootcert" not in url, "it must not stay in the URL as well"
    assert connect_args["sslrootcert"].endswith("cacert.pem")

    from pathlib import Path

    assert Path(connect_args["sslrootcert"]).is_file(), "the bundle must be real"


def test_everything_else_about_the_url_survives() -> None:
    url, _ = resolve_trust_store(NEON)
    assert "sslmode=verify-full" in url  # R-SEC-07 is not weakened
    assert "channel_binding=require" in url
    assert url.startswith("postgresql+psycopg://user:pass@")
    assert "/neondb" in url


def test_an_explicit_path_is_left_alone() -> None:
    """Somebody who named a file meant that file."""
    explicit = NEON.replace("sslrootcert=system", "sslrootcert=/etc/ssl/mine.pem")
    url, connect_args = resolve_trust_store(explicit)
    assert url == explicit
    assert connect_args == {}


def test_a_url_with_no_trust_store_is_left_alone() -> None:
    plain = "postgresql+psycopg://u:p@h/db?sslmode=verify-full"
    assert resolve_trust_store(plain) == (plain, {})


def test_the_windows_path_is_never_percent_encoded_into_a_query(
    monkeypatch,
) -> None:
    """A Windows path carries a colon and backslashes. Putting it in a query
    string is a second thing to get wrong, which is why it goes in
    connect_args."""
    import certifi

    monkeypatch.setattr(certifi, "where", lambda: r"C:\Users\x\certifi\cacert.pem")
    url, connect_args = resolve_trust_store(NEON)
    assert "%5C" not in url and "%3A" not in url
    assert connect_args["sslrootcert"] == r"C:\Users\x\certifi\cacert.pem"


def test_production_still_demands_verify_full() -> None:
    """The resolution must not become a way to weaken the check."""
    with pytest.raises(ConfigError, match="verify-full"):
        assert_tls("postgresql+psycopg://u:p@h/db?sslmode=require", required=True)


def test_a_production_engine_refuses_a_url_without_verify_full() -> None:
    settings = Settings(
        _env_file=None,
        environment=Environment.PRODUCTION,
        database_url="postgresql+psycopg://u:p@h/db?sslmode=require",
    )
    with pytest.raises(ConfigError):
        build_engine(settings)


def test_migrations_connect_the_way_the_app_does() -> None:
    """A migration run from a laptop against production failed "certificate
    verify failed" while the app connected fine on the same URL, because
    alembic built its own engine and never swapped out sslrootcert=system."""
    from pathlib import Path

    source = Path("app/db/migrations/env.py").read_text(encoding="utf-8")
    assert "resolve_trust_store(url)" in source
    assert "connect_args=connect_args" in source
