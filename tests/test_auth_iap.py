"""IAP assertion verification (R-ACC-01, R-ACC-02, R-SEC-06).

The rest of the suite replaces ``current_identity`` with a fixed identity, so
nothing there proves an assertion is actually checked. These tests sign real
ES256 assertions with a key made for the run and serve the matching public key
from a local stand-in for Google's key endpoint. The verification code, and
the httpx transport it fetches keys through, run unchanged. Nothing leaves the
machine.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from google.auth import jwt
from google.auth.crypt import es256
from starlette.requests import Request

from app import auth
from app.config import Settings
from app.transport import HttpxRequest

AUDIENCE = "/projects/000000000000/locations/europe-west2/services/portal"
KEY_ID = "test-key-1"


def _keypair() -> tuple[bytes, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private, public


@pytest.fixture(autouse=True)
def _logger_enabled(monkeypatch) -> None:
    """Keep this module's logger live whatever ran earlier in the session.

    tests/test_migration.py runs Alembic in-process, and Alembic's
    ``fileConfig`` disables every logger that already exists. In production
    migrations run as their own process, so only the test order is affected.
    """
    monkeypatch.setattr(logging.getLogger("app.auth"), "disabled", False)


@pytest.fixture
def iap(monkeypatch):
    """A signer for assertions, with its public key served where IAP's would be."""
    private, public = _keypair()
    requested: list[str] = []

    def keys(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, json={KEY_ID: public.decode()})

    transport = HttpxRequest()
    transport._client = httpx.Client(transport=httpx.MockTransport(keys))
    monkeypatch.setattr(auth, "_transport", transport)

    signer = es256.ES256Signer.from_string(private, key_id=KEY_ID)

    def sign(**overrides) -> str:
        now = dt.datetime.now(dt.UTC)
        claims = {
            "iss": auth.IAP_ISSUER,
            "aud": AUDIENCE,
            "sub": "accounts.google.com:1234567890",
            "email": "jane@example.com",
            "iat": int(now.timestamp()),
            "exp": int((now + dt.timedelta(minutes=5)).timestamp()),
        }
        claims.update(overrides)
        return jwt.encode(signer, claims).decode()

    sign.requested = requested
    return sign


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        iap_audience=AUDIENCE,
        workspace_domain="example.com",
        finance_emails="accounts@example.com",
    )


def test_a_valid_assertion_yields_the_callers_email_and_subject(iap, settings) -> None:
    email, subject = auth.verify_iap_assertion(iap(), settings)
    assert email == "jane@example.com"
    assert subject == "accounts.google.com:1234567890"
    assert iap.requested == [auth.IAP_CERTS_URL], "keys come from IAP's endpoint only"


def test_the_email_is_lower_cased_and_trimmed(iap, settings) -> None:
    email, _ = auth.verify_iap_assertion(iap(email="  Jane@Example.com "), settings)
    assert email == "jane@example.com"


def test_an_assertion_for_another_service_is_refused(iap, settings, caplog) -> None:
    """The audience is what stops a token minted for one backend opening another."""
    other = "/projects/000000000000/locations/europe-west2/services/other"
    with (
        caplog.at_level(logging.WARNING, logger="app.auth"),
        pytest.raises(auth.IdentityError, match="rejected"),
    ):
        auth.verify_iap_assertion(iap(aud=other), settings)
    # The mismatch is the commonest reason nobody can log in on a new
    # deployment, so both audiences are named; the assertion itself never is.
    assert other in caplog.text
    assert AUDIENCE in caplog.text
    assert iap(aud=other) not in caplog.text


def test_an_expired_assertion_is_refused(iap, settings) -> None:
    past = int((dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)).timestamp())
    with pytest.raises(auth.IdentityError, match="rejected"):
        auth.verify_iap_assertion(iap(iat=past - 300, exp=past), settings)


def test_an_assertion_signed_by_another_key_is_refused(iap, settings) -> None:
    stranger, _ = _keypair()
    forged = jwt.encode(
        es256.ES256Signer.from_string(stranger, key_id=KEY_ID),
        {
            "iss": auth.IAP_ISSUER,
            "aud": AUDIENCE,
            "sub": "x",
            "email": "jane@example.com",
            "iat": int(dt.datetime.now(dt.UTC).timestamp()),
            "exp": int(dt.datetime.now(dt.UTC).timestamp()) + 300,
        },
    ).decode()
    with pytest.raises(auth.IdentityError, match="rejected"):
        auth.verify_iap_assertion(forged, settings)


def test_a_valid_signature_from_the_wrong_issuer_is_refused(iap, settings) -> None:
    with pytest.raises(auth.IdentityError, match="wrong issuer"):
        auth.verify_iap_assertion(iap(iss="https://accounts.google.com"), settings)


@pytest.mark.parametrize("missing", [{"email": ""}, {"sub": ""}])
def test_an_assertion_without_email_or_subject_is_refused(
    iap, settings, missing
) -> None:
    with pytest.raises(auth.IdentityError, match="no email"):
        auth.verify_iap_assertion(iap(**missing), settings)


def test_a_caller_outside_the_workspace_domain_is_refused(iap, settings) -> None:
    """R-ACC-02. A valid Google identity is not enough; it has to be ours."""
    with pytest.raises(auth.IdentityError, match="workspace domain"):
        auth.verify_iap_assertion(iap(email="jane@other.test"), settings)


def test_a_lookalike_domain_is_refused(iap, settings) -> None:
    with pytest.raises(auth.IdentityError, match="workspace domain"):
        auth.verify_iap_assertion(iap(email="jane@example.com.other.test"), settings)


def test_garbage_is_refused_without_being_logged(iap, settings, caplog) -> None:
    with (
        caplog.at_level(logging.WARNING, logger="app.auth"),
        pytest.raises(auth.IdentityError, match="rejected"),
    ):
        auth.verify_iap_assertion("not-a-jwt", settings)
    assert "<unreadable>" in caplog.text
    assert "not-a-jwt" not in caplog.text


def test_nothing_is_verified_when_the_audience_is_not_configured(iap) -> None:
    """An empty audience would accept an assertion for any backend at all."""
    unset = Settings(_env_file=None, iap_audience="")
    with pytest.raises(auth.IdentityError, match="not configured"):
        auth.verify_iap_assertion(iap(), unset)
    assert iap.requested == []


def _request(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw})


def test_the_dependency_turns_a_valid_assertion_into_an_identity(iap, settings) -> None:
    identity = asyncio.run(
        auth.current_identity(_request({auth.IAP_HEADER: iap()}), settings)
    )
    assert identity == auth.Identity(
        email="jane@example.com",
        subject="accounts.google.com:1234567890",
        is_finance=False,
    )


def test_a_finance_address_is_marked_as_finance(iap, settings) -> None:
    assertion = iap(email="accounts@example.com")
    identity = asyncio.run(
        auth.current_identity(_request({auth.IAP_HEADER: assertion}), settings)
    )
    assert identity.is_finance is True


def test_a_request_that_did_not_come_through_iap_gets_401(settings) -> None:
    with pytest.raises(HTTPException) as refused:
        asyncio.run(auth.current_identity(_request({}), settings))
    assert refused.value.status_code == 401
    assert "through IAP" in refused.value.detail


def test_identity_is_never_taken_from_anything_else_the_client_sent(settings) -> None:
    """R-SEC-06. A header a caller can set is not identity."""
    spoofed = _request(
        {
            "X-Goog-Authenticated-User-Email": "accounts@example.com",
            "X-Forwarded-Email": "accounts@example.com",
            "Cookie": "email=accounts@example.com",
        }
    )
    with pytest.raises(HTTPException) as refused:
        asyncio.run(auth.current_identity(spoofed, settings))
    assert refused.value.status_code == 401


def test_a_rejected_assertion_gets_401_without_saying_why(iap, settings) -> None:
    request = _request({auth.IAP_HEADER: iap(email="jane@other.test")})
    with pytest.raises(HTTPException) as refused:
        asyncio.run(auth.current_identity(request, settings))
    assert refused.value.status_code == 401
    assert refused.value.detail == "Could not verify your identity."


def test_the_key_endpoint_answer_is_passed_through_unchanged() -> None:
    """The transport hands google-auth the status, headers and body as received."""
    body = json.dumps({"k": "v"}).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, content=body, headers={"X-Probe": "1"})

    transport = HttpxRequest()
    transport._client = httpx.Client(transport=httpx.MockTransport(handler))
    response = transport("https://keys.test/public_key")
    assert response.status == 200
    assert response.data == body
    assert response.headers["x-probe"] == "1"
