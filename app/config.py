"""Configuration. Every value comes from the environment; there are no literals
here that belong to a deployment (CLAUDE.md §5).

Secrets arrive from Secret Manager as environment variables (R-SEC-02) and are
held as ``SecretStr`` so they cannot be printed by accident.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from functools import lru_cache
from typing import Annotated

from pydantic import BaseModel, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(Enum):
    """R-ENV-01. Only these two exist."""

    STAGING = "staging"
    PRODUCTION = "production"


class BillTo(BaseModel):
    """The fixed bill-to block (R-PDF-03).

    Versioned: ``print_snapshot`` freezes the block that was current when an
    invoice was issued, so changing the company address later cannot rewrite an
    old invoice.
    """

    version: int
    name: str
    company: str
    address_lines: tuple[str, ...]

    def as_lines(self) -> tuple[str, ...]:
        return (self.name, self.company, *self.address_lines)


DEFAULT_BILL_TO = BillTo(
    version=1,
    name="Charlie Castellanos",
    company="Your Company Ltd",
    address_lines=("Example House", "1 Example Street", "Example Town EX1 2AB"),
)


class Settings(BaseSettings):
    """Everything the app needs to run, read from the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="PORTAL_",
        extra="forbid",
    )

    environment: Environment = Environment.STAGING

    # --- Identity (R-ACC-01, R-ACC-02, R-ACC-04) -------------------------------
    # The IAP JWT's expected audience, of the form
    # /projects/<number>/global/backendServices/<id>. Verified, never trusted
    # from a header the caller controls.
    iap_audience: str = ""
    workspace_domain: str = "example.com"
    # The finance group, checked in the app as well as at IAP (defence in depth).
    #
    # NoDecode because pydantic-settings otherwise tries to JSON-parse any
    # complex type coming from the environment, before field validators run.
    # It arrives as a comma-separated list, which is not JSON, and the
    # service refuses to start. Found by deploying, not by testing: no local
    # .env sets this.
    finance_emails: Annotated[tuple[str, ...], NoDecode] = ()

    # --- Data ------------------------------------------------------------------
    database_url: SecretStr = SecretStr("")
    # The same database on its direct, unpooled endpoint. Alembic uses this
    # when set: DDL through PgBouncer's transaction pooling is not something
    # to rely on. Falls back to database_url.
    migration_url: SecretStr = SecretStr("")

    # --- Money -----------------------------------------------------------------
    # 1 kudo = £20 (R-KUD-04). Config, never a literal in the code that uses it.
    kudos_rate_pence: int = 2000
    # The quarterly cash prizes (KUDOS.md §5). Config for the same reason as
    # the rate: these came out of a policy document and policies get revised.
    # The cash carries no kudos threshold — the 5+ and 3+ in the policy gate
    # the perks, which are out of scope.
    award_first_pence: int = 10000
    award_second_pence: int = 7500
    award_third_pence: int = 2500
    # Nothing earned before this is billable (R-KUD-12). 1 July 2026 is the
    # start of Q3, the first quarter not settled the old way. Everything
    # earlier is still ingested and still counts for the standings.
    kudos_claimable_from: date = date(2026, 7, 1)
    payment_terms_days: int = 14  # R-PDF-10
    # Staging only. R-PER-06 shuts the submit action before the 24th, which
    # is right in production and makes the thing untestable for three weeks
    # of every month. Production refuses to start with this on — see
    # assert_production_ready — so it cannot leak across.
    window_always_open: bool = False
    # An expense above this is flagged in the admin period view (R-ADM-04).
    # A threshold, not a limit: it asks Robin to look, it does not refuse.
    expense_flag_threshold_pence: int = 20000
    # A bank change this close to the window closing flags the invoice
    # rather than blocking it (R-BANK-06).
    bank_change_flag_hours: int = 72

    # --- Encryption (R-SEC-03) -------------------------------------------------
    # AES-GCM key, base64, from Secret Manager. The key id is stored alongside
    # every ciphertext so the key can be rotated without a mass re-encrypt.
    bank_encryption_key: SecretStr = SecretStr("")
    bank_encryption_key_id: str = "v1"
    # Signing key for CSRF tokens (R-SEC-11). Separate from the bank key:
    # a token signer and a payload cipher should not share a secret.
    csrf_secret: SecretStr = SecretStr("")

    # --- Cloud Scheduler (R-JOB-*) ---------------------------------------------
    # The OIDC audience the scheduler jobs are configured with, normally the
    # service URL, and the service account expected to have signed the token.
    # Both must be set or /tasks/* refuses: an unauthenticated endpoint that
    # runs the payroll reminders is worse than one that is switched off.
    scheduler_audience: str = ""
    scheduler_service_account: str = ""

    # --- External services -----------------------------------------------------
    gcs_bucket: str = ""
    # Cloud Run's attached identity. Needed to sign URLs through the IAM
    # API rather than with a key file (CLAUDE.md §2.7).
    service_account_email: str = ""
    kudos_sheet_id: str = ""
    # The tab and range read from that sheet. `Raw Data` is the event log and
    # is what the Google Chat announcer reads, so the two cannot drift apart.
    # Never a derived grid tab: those hold totals, a winner row and a second
    # table of givers underneath (R-KUD-07).
    kudos_sheet_range: str = "Raw Data!A:F"
    postmark_token: SecretStr = SecretStr("")
    mail_from: str = ""
    finance_inbox: str = ""
    drive_folder_id: str = ""

    # --- Uploads (R-WIZ-04) ----------------------------------------------------
    max_receipt_bytes: int = 10 * 1024 * 1024
    max_receipts_per_invoice: int = 10
    signed_url_ttl_seconds: int = 900  # R-STO-03: 15 minutes, never longer

    bill_to: BillTo = DEFAULT_BILL_TO

    @field_validator("finance_emails", mode="before")
    @classmethod
    def _split_emails(cls, value: object) -> object:
        """Accept a comma-separated list, which is how it arrives from env."""
        if isinstance(value, str):
            return tuple(
                part.strip().lower() for part in value.split(",") if part.strip()
            )
        return value

    @field_validator("signed_url_ttl_seconds")
    @classmethod
    def _cap_ttl(cls, value: int) -> int:
        """R-STO-03 caps signed-URL lifetime at fifteen minutes."""
        if not 0 < value <= 900:
            raise ValueError("signed URL TTL must be between 1 and 900 seconds")
        return value

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    def is_finance(self, email: str) -> bool:
        """R-ACC-04. Case-insensitive; the list is the app's half of the check."""
        return email.strip().lower() in self.finance_emails


@lru_cache
def get_settings() -> Settings:
    """Cached so the environment is read once per process."""
    return Settings()


class ConfigError(RuntimeError):
    """Configuration that would be unsafe to run with."""


def assert_production_ready(settings: Settings) -> None:
    """Fail fast rather than start half-configured.

    Called at startup. A production service missing its encryption key would
    otherwise happily accept bank details it cannot protect.
    """
    required: dict[str, object] = {
        "iap_audience": settings.iap_audience,
        "database_url": settings.database_url.get_secret_value(),
        "bank_encryption_key": settings.bank_encryption_key.get_secret_value(),
        "csrf_secret": settings.csrf_secret.get_secret_value(),
        "gcs_bucket": settings.gcs_bucket,
        "finance_emails": settings.finance_emails,
    }
    missing = sorted(name for name, value in required.items() if not value)
    if missing:
        raise ConfigError(f"missing required configuration: {', '.join(missing)}")

    if settings.window_always_open:
        # A staging convenience that would silently let somebody invoice for
        # the wrong month in production. Refusing to start is the only way to
        # be sure it never ships.
        raise ConfigError(
            "window_always_open is a staging-only setting and must not be set "
            "in production"
        )


class MailMode(Enum):
    """R-ENV-03: staging cannot send real email."""

    LIVE = "live"
    SANDBOX = "sandbox"


def mail_mode(settings: Settings) -> MailMode:
    return MailMode.LIVE if settings.is_production else MailMode.SANDBOX


def drive_writes_allowed(settings: Settings) -> bool:
    """R-ENV-03: staging cannot write to the real Drive folder."""
    return settings.is_production
