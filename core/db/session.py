"""Engine and session factory.

R-SEC-07: connections to Postgres use TLS with ``verify-full``. Neon is a third
party on the public internet; ``require`` alone encrypts the traffic but does
not prove who is on the other end.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from core.config import ConfigError, Settings, get_settings

REQUIRED_SSLMODE = "verify-full"

# libpq's own word for "use the operating system's CA store". What that
# resolves to depends on how the OpenSSL underneath it was built, and the
# libpq bundled in psycopg's binary wheel does not find Debian's store or
# Windows'. It fails as 'certificate verify failed', which reads like a
# certificate problem rather than a configuration one.
SYSTEM_TRUST_STORE = "system"


def assert_tls(database_url: str, *, required: bool) -> None:
    """Refuse a production URL that is not pinned to ``verify-full`` (R-SEC-07).

    Local development against a container on loopback has nothing to verify, so
    the check applies only where it means something.
    """
    if not required:
        return
    query = parse_qs(urlparse(database_url).query)
    sslmode = (query.get("sslmode") or [""])[0]
    if sslmode != REQUIRED_SSLMODE:
        raise ConfigError(
            f"production database URL must set sslmode={REQUIRED_SSLMODE}, "
            f"got {sslmode or 'nothing'}"
        )


def resolve_trust_store(database_url: str) -> tuple[str, dict[str, str]]:
    """Turn ``sslrootcert=system`` into a bundle that actually exists.

    ``verify-full`` needs a CA to verify against (R-SEC-07), and every way of
    naming one is environment-specific: libpq's default path exists on neither
    Windows nor a slim container, and ``system`` depends on how the OpenSSL
    underneath was built. It fails as "certificate verify failed", which reads
    like a certificate problem rather than a configuration one.

    certifi ships the Mozilla bundle with the application, so it is the same
    set of roots on a laptop, in the container and in CI.

    Returned as a connection argument rather than rewritten into the URL: a
    Windows path carries a colon and backslashes, and percent-encoding those
    into a query string is a second thing to get wrong.

    Only ``system`` is replaced. An explicit path is left alone — somebody who
    named a file meant that file.
    """
    parsed = urlparse(database_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (query.get("sslrootcert") or [""])[0] != SYSTEM_TRUST_STORE:
        return database_url, {}

    import certifi

    query.pop("sslrootcert")
    stripped = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
    return stripped, {"sslrootcert": certifi.where()}


def build_engine(settings: Settings) -> Engine:
    """One engine per process. Sixteen people do not need a large pool."""
    url = settings.database_url.get_secret_value()
    if not url:
        raise ConfigError("database_url is not configured")
    assert_tls(url, required=settings.is_production)
    url, connect_args = resolve_trust_store(url)
    return create_engine(
        url,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,  # Neon scales to zero; a stale connection is normal.
        future=True,
        connect_args=connect_args,
    )


@lru_cache
def get_engine() -> Engine:
    return build_engine(get_settings())


def get_sessionmaker() -> sessionmaker[Session]:
    """The session factory.

    A FastAPI dependency as well as a plain function, so a route that needs
    to run its own transactions — the submit path in R-SUB-03..05 — can be
    pointed at a different database in a test without reaching for a global.
    """
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a session that is always closed.

    Commits are explicit: the transaction boundaries in R-SUB-03 and R-SUB-05
    are part of the specification, not something a middleware should decide.
    """
    factory = get_sessionmaker()
    session = factory()
    try:
        yield session
    finally:
        session.close()
