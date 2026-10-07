"""Identity from Cloud IAP (R-ACC-01, R-ACC-02, R-ACC-04, R-SEC-06).

The app writes no login code. IAP terminates authentication and hands us a
signed assertion; our whole job is to verify that assertion properly and to
refuse every other claim of identity.

Nothing here reads a query parameter, a form field or a cookie. If a caller
could set it, it is not identity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from google.auth.exceptions import GoogleAuthError
from google.oauth2 import id_token

from modules.invoices.config import Settings, get_settings
from modules.invoices.transport import HttpxRequest

logger = logging.getLogger(__name__)

IAP_HEADER = "X-Goog-IAP-JWT-Assertion"
IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
IAP_ISSUER = "https://cloud.google.com/iap"

_transport = HttpxRequest()


@dataclass(frozen=True, slots=True)
class Identity:
    """A verified caller. The only source of truth for who is asking."""

    email: str
    subject: str
    is_finance: bool


class IdentityError(Exception):
    """The assertion was absent, malformed, or not for us."""


def verify_iap_assertion(assertion: str, settings: Settings) -> tuple[str, str]:
    """Verify the IAP JWT and return ``(email, subject)``.

    Checks the signature against Google's IAP public keys, the audience against
    our own backend service, and the issuer. An assertion that fails any of
    these is rejected outright rather than downgraded to anonymous.
    """
    if not settings.iap_audience:
        raise IdentityError("IAP audience is not configured")

    try:
        claims = id_token.verify_token(
            assertion,
            _transport,
            audience=settings.iap_audience,
            certs_url=IAP_CERTS_URL,
        )
    except (GoogleAuthError, ValueError) as exc:
        # The assertion itself is never logged: it is a bearer credential.
        # The audience it carries is an identifier, and getting it wrong is the
        # single most likely reason nobody can log in on a fresh deployment, so
        # it is worth saying out loud rather than leaving as a mystery 401.
        logger.warning(
            "IAP assertion rejected (%s). It carried audience %r; this service "
            "expects %r.",
            type(exc).__name__,
            _unverified_audience(assertion),
            settings.iap_audience,
        )
        raise IdentityError(f"IAP assertion rejected: {type(exc).__name__}") from exc

    if claims.get("iss") != IAP_ISSUER:
        raise IdentityError("IAP assertion has the wrong issuer")

    email = str(claims.get("email", "")).strip().lower()
    subject = str(claims.get("sub", "")).strip()
    if not email or not subject:
        raise IdentityError("IAP assertion carries no email")

    domain = email.rpartition("@")[2]
    if domain != settings.workspace_domain:
        raise IdentityError("caller is not on the Your Company workspace domain")

    return email, subject


def _unverified_audience(assertion: str) -> str:
    """Read the ``aud`` claim without verifying anything.

    For diagnostics only, and used only on a path that has already decided to
    refuse. Nothing is trusted from it.
    """
    import base64
    import json

    try:
        payload = assertion.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return str(json.loads(base64.urlsafe_b64decode(payload)).get("aud", ""))
    except (ValueError, IndexError, KeyError):
        return "<unreadable>"


async def current_identity(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Identity:
    """FastAPI dependency: the verified caller, or 401.

    Every route that touches personal data depends on this. There is no code
    path that derives a person from anything the client sent (R-SEC-06).
    """
    assertion = request.headers.get(IAP_HEADER)
    if not assertion:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No IAP assertion. This service must be reached through IAP.",
        )
    try:
        email, subject = verify_iap_assertion(assertion, settings)
    except IdentityError as exc:
        logger.warning("identity rejected: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not verify your identity.",
        ) from exc

    return Identity(email=email, subject=subject, is_finance=settings.is_finance(email))


async def require_finance(
    identity: Annotated[Identity, Depends(current_identity)],
) -> Identity:
    """Dependency for ``/admin/*`` (R-ACC-04).

    IAP already restricts the path to the finance IAM group. This is the second
    check, in the app, so a misconfigured IAP rule is not the only thing
    standing between someone and the payroll.
    """
    if not identity.is_finance:
        logger.warning("non-finance access to admin refused for %s", identity.email)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This area is limited to the finance group.",
        )
    return identity


# The dependency aliases routes use, so no route repeats the wiring.
CurrentIdentity = Annotated[Identity, Depends(current_identity)]
FinanceIdentity = Annotated[Identity, Depends(require_finance)]
