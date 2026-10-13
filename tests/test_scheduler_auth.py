"""The OIDC check on the scheduler endpoints (R-JOB-*, R-SEC-06).

The integration tests either expect a refusal or replace ``verify_scheduler``
outright, so the accepting path, and each separate reason for refusing, was
never run. Here a token is signed with an RSA key made for the run and the
matching certificate is served from a local stand-in for Google's certificate
endpoint, so the real verification code decides. Nothing leaves the machine.
"""

from __future__ import annotations

import datetime as dt
import logging

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import HTTPException
from google.auth import crypt, jwt

from app.config import Settings
from app.transport import HttpxRequest
from app.web import tasks

AUDIENCE = "https://invoices-tasks.test"
ACCOUNT = "scheduler@your-gcp-project.iam.gserviceaccount.com"
KEY_ID = "test-cert-1"


def _key_and_certificate() -> tuple[bytes, str]:
    """An RSA key and a self-signed certificate for it, as Google publishes them."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "scheduler.test")])
    now = dt.datetime.now(dt.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(hours=1))
        .sign(key, hashes.SHA256())
    )
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return private, certificate.public_bytes(serialization.Encoding.PEM).decode()


@pytest.fixture(autouse=True)
def _logger_enabled(monkeypatch) -> None:
    """Keep this module's logger live whatever ran earlier in the session.

    tests/test_migration.py runs Alembic in-process, and Alembic's
    ``fileConfig`` disables every logger that already exists. In production
    migrations run as their own process, so only the test order is affected.
    """
    monkeypatch.setattr(logging.getLogger("app.web.tasks"), "disabled", False)


@pytest.fixture
def scheduler(monkeypatch):
    """Signs tokens the way Cloud Scheduler's would be, verifiable locally."""
    private, certificate = _key_and_certificate()

    def certs(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={KEY_ID: certificate})

    transport = HttpxRequest()
    transport._client = httpx.Client(transport=httpx.MockTransport(certs))
    monkeypatch.setattr(tasks, "_transport", transport)

    signer = crypt.RSASigner.from_string(private, key_id=KEY_ID)

    def token(**overrides) -> str:
        now = int(dt.datetime.now(dt.UTC).timestamp())
        claims = {
            "iss": "https://accounts.google.com",
            "aud": AUDIENCE,
            "sub": "1234567890",
            "email": ACCOUNT,
            "email_verified": True,
            "iat": now,
            "exp": now + 300,
        }
        claims.update(overrides)
        return "Bearer " + jwt.encode(signer, claims).decode()

    return token


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        scheduler_audience=AUDIENCE,
        scheduler_service_account=ACCOUNT,
    )


def test_the_expected_service_account_is_let_through(scheduler, settings) -> None:
    assert tasks.verify_scheduler(settings, scheduler()) == ACCOUNT


def test_the_account_is_compared_without_regard_to_case(scheduler, settings) -> None:
    caller = tasks.verify_scheduler(settings, scheduler(email=ACCOUNT.upper()))
    assert caller == ACCOUNT


def test_a_valid_google_token_from_another_account_is_forbidden(
    scheduler, settings, caplog
) -> None:
    """Any Google identity can mint a token for our audience; only one may run jobs."""
    with (
        caplog.at_level(logging.WARNING, logger="app.web.tasks"),
        pytest.raises(HTTPException) as refused,
    ):
        tasks.verify_scheduler(settings, scheduler(email="someone@other.test"))
    assert refused.value.status_code == 403
    assert "unexpected account" in caplog.text
    assert "someone@other.test" not in caplog.text


def test_an_unverified_email_claim_is_forbidden(scheduler, settings) -> None:
    with pytest.raises(HTTPException) as refused:
        tasks.verify_scheduler(settings, scheduler(email_verified=False))
    assert refused.value.status_code == 403
    assert refused.value.detail == "Unverified caller."


def test_a_token_for_another_service_is_refused(scheduler, settings) -> None:
    """The audience is what stops a token minted for one service being replayed here."""
    with pytest.raises(HTTPException) as refused:
        tasks.verify_scheduler(settings, scheduler(aud="https://elsewhere.test"))
    assert refused.value.status_code == 401
    assert refused.value.detail == "Could not verify the caller."


def test_an_expired_token_is_refused(scheduler, settings) -> None:
    past = int(dt.datetime.now(dt.UTC).timestamp()) - 3600
    with pytest.raises(HTTPException) as refused:
        tasks.verify_scheduler(settings, scheduler(iat=past - 300, exp=past))
    assert refused.value.status_code == 401


def test_a_token_from_a_non_google_issuer_is_refused(scheduler, settings) -> None:
    with pytest.raises(HTTPException) as refused:
        tasks.verify_scheduler(settings, scheduler(iss="https://issuer.other.test"))
    assert refused.value.status_code == 401


def test_a_rejected_token_is_never_logged(scheduler, settings, caplog) -> None:
    token = scheduler(aud="https://elsewhere.test")
    with (
        caplog.at_level(logging.WARNING, logger="app.web.tasks"),
        pytest.raises(HTTPException),
    ):
        tasks.verify_scheduler(settings, token)
    assert "scheduler token rejected" in caplog.text
    assert token.removeprefix("Bearer ") not in caplog.text


@pytest.mark.parametrize("header", [None, "", "Basic abc", "token-without-scheme"])
def test_a_request_without_a_bearer_token_is_refused(settings, header) -> None:
    with pytest.raises(HTTPException) as refused:
        tasks.verify_scheduler(settings, header)
    assert refused.value.status_code == 401
    assert refused.value.detail == "This endpoint is for Cloud Scheduler."


@pytest.mark.parametrize(
    "unset",
    [
        {"scheduler_audience": "", "scheduler_service_account": ACCOUNT},
        {"scheduler_audience": AUDIENCE, "scheduler_service_account": ""},
    ],
)
def test_the_endpoints_are_switched_off_until_both_settings_exist(
    scheduler, unset
) -> None:
    """An unauthenticated endpoint that sends the reminders is worse than none."""
    with pytest.raises(HTTPException) as refused:
        tasks.verify_scheduler(Settings(_env_file=None, **unset), scheduler())
    assert refused.value.status_code == 503
