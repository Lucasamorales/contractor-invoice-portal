"""Engine and session factory.

R-SEC-07: connections to Postgres use TLS with ``verify-full``. Neon is a third
party on the public internet; ``require`` alone encrypts the traffic but does
not prove who is on the other end.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from urllib.parse import parse_qs, urlparse

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import ConfigError, Settings, get_settings

REQUIRED_SSLMODE = "verify-full"


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


def build_engine(settings: Settings) -> Engine:
    """One engine per process. Sixteen people do not need a large pool."""
    url = settings.database_url.get_secret_value()
    if not url:
        raise ConfigError("database_url is not configured")
    assert_tls(url, required=settings.is_production)
    return create_engine(
        url,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,  # Neon scales to zero; a stale connection is normal.
        future=True,
    )


@lru_cache
def get_engine() -> Engine:
    return build_engine(get_settings())


def get_sessionmaker() -> sessionmaker[Session]:
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
