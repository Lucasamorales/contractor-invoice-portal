"""Configuration parsing.

The env-var cases here are the ones that only fail in a deployed container:
locally nothing sets them, so a bug sits invisible until Cloud Run refuses to
start.
"""

from __future__ import annotations

import pytest

from core.config import ConfigError, Environment, Settings, assert_production_ready
from core.services.crypto import generate_key


def test_finance_emails_arrive_as_a_comma_separated_list(monkeypatch) -> None:
    """Cloud Run passes it as one string.

    pydantic-settings JSON-decodes complex types from the environment before
    field validators run, so without NoDecode this raises and the service never
    starts. That is exactly how it failed on the first deploy.
    """
    monkeypatch.setenv(
        "PORTAL_FINANCE_EMAILS",
        "julian@example.com,charlie@example.com",
    )
    settings = Settings(_env_file=None)
    assert settings.finance_emails == (
        "julian@example.com",
        "charlie@example.com",
    )


def test_the_list_tolerates_spacing_and_case(monkeypatch) -> None:
    monkeypatch.setenv(
        "PORTAL_FINANCE_EMAILS", " Julian@Example.com , charlie@x.com "
    )
    settings = Settings(_env_file=None)
    assert settings.is_finance("julian@example.com") is True
    assert settings.is_finance("JULIAN@EXAMPLE.COM") is True
    assert settings.is_finance("someone.else@example.com") is False


def test_an_empty_list_lets_nobody_into_admin(monkeypatch) -> None:
    monkeypatch.setenv("PORTAL_FINANCE_EMAILS", "")
    settings = Settings(_env_file=None)
    assert settings.finance_emails == ()
    assert settings.is_finance("julian@example.com") is False


def test_a_tuple_still_works_for_tests() -> None:
    settings = Settings(_env_file=None, finance_emails=("a@b.com",))
    assert settings.is_finance("a@b.com") is True


def production(**overrides) -> Settings:
    values = {
        "environment": Environment.PRODUCTION,
        "iap_audience": "https://portal.example",
        "database_url": "postgresql+psycopg://u:p@h/d?sslmode=verify-full",
        "bank_encryption_key": generate_key(),
        "csrf_secret": "x" * 64,
        "gcs_bucket": "portal-invoices-production",
        "finance_emails": ("accounts@example.com",),
        "finance_inbox": "accounts@example.com",
        # Every email links back to the portal, so production will not start
        # without somewhere for those links to go.
        "app_url": "https://portal.example",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_a_fully_configured_production_passes() -> None:
    assert_production_ready(production())


@pytest.mark.parametrize(
    "missing",
    [
        "iap_audience",
        "database_url",
        "bank_encryption_key",
        "csrf_secret",
        "gcs_bucket",
        "finance_inbox",
    ],
)
def test_production_refuses_to_start_half_configured(missing: str) -> None:
    with pytest.raises(ConfigError, match=missing):
        assert_production_ready(production(**{missing: ""}))


def test_production_refuses_the_staging_window_override() -> None:
    """R-PER-06 exists to stop somebody invoicing for the wrong month.

    The override makes staging testable on any day. Production starting with it
    would quietly undo the rule, so it does not start at all.
    """
    with pytest.raises(ConfigError, match="staging-only"):
        assert_production_ready(production(window_always_open=True))


def test_staging_may_have_the_override() -> None:
    settings = Settings(_env_file=None, window_always_open=True)
    assert settings.is_production is False
    assert settings.window_always_open is True


def test_kudos_ineligible_emails_arrive_as_a_comma_separated_list(monkeypatch) -> None:
    """R-KUD-20, configured the same way as finance_emails."""
    monkeypatch.setenv(
        "PORTAL_KUDOS_INELIGIBLE_EMAILS",
        " Remy@example.com , charlie@example.com",
    )
    settings = Settings(_env_file=None)
    assert settings.kudos_eligible("REMY@example.com") is False
    assert settings.kudos_eligible("charlie@example.com") is False
    assert settings.kudos_eligible("jane@example.com") is True


def test_nobody_is_ineligible_by_default() -> None:
    assert Settings(_env_file=None).kudos_eligible("anyone@example.com")
