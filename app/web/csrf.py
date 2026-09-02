"""CSRF protection for every state-changing form (R-SEC-11).

IAP authenticates the request, but it does so with a cookie the browser sends
on cross-site requests too. Without a token, a page on another origin could
make a logged-in contractor change their own bank details.

The token is an HMAC over the caller's identity and a coarse time window, so
there is nothing to store server-side and nothing to keep in sync across Cloud
Run instances. It is bound to the person: a token issued to one contractor is
not valid for another.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import HTTPException, Request, status

from app.config import Settings

FIELD_NAME = "csrf_token"
HEADER_NAME = "X-CSRF-Token"

# Tokens stay valid across this window and the one before it, so a form left
# open over a coffee break still submits.
WINDOW_SECONDS = 8 * 60 * 60


class CsrfError(Exception):
    """The token was missing, malformed, expired, or for someone else."""


def _sign(secret: str, identity: str, window: int) -> str:
    message = f"{identity}:{window}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _current_window(now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // WINDOW_SECONDS)


def issue(settings: Settings, identity: str, *, now: float | None = None) -> str:
    """Mint a token for this person. Rendered into every form."""
    secret = settings.csrf_secret.get_secret_value()
    if not secret:
        raise CsrfError("csrf_secret is not configured")
    window = _current_window(now)
    return f"{window}.{_sign(secret, identity, window)}"


def verify(
    settings: Settings, identity: str, token: str | None, *, now: float | None = None
) -> None:
    """Raise ``CsrfError`` unless the token was issued to this person recently."""
    secret = settings.csrf_secret.get_secret_value()
    if not secret:
        raise CsrfError("csrf_secret is not configured")
    if not token:
        raise CsrfError("no csrf token")

    stamp, _, digest = token.partition(".")
    if not digest or not stamp.isdigit():
        raise CsrfError("malformed csrf token")

    window = int(stamp)
    if window not in (_current_window(now), _current_window(now) - 1):
        raise CsrfError("csrf token has expired")

    # compare_digest, not ==: a timing comparison on a MAC is a real leak.
    if not hmac.compare_digest(digest, _sign(secret, identity, window)):
        raise CsrfError("csrf token does not match")


def require(settings: Settings, identity: str, token: str | None) -> None:
    """The form-handler entry point. Turns a failure into a 403."""
    try:
        verify(settings, identity, token)
    except CsrfError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your form expired. Go back, reload the page and try again.",
        ) from exc


def token_from_request(request: Request, form_value: str | None = None) -> str | None:
    """The token, from the form field or the header HTMX sets on every request.

    Both carry the same value; a full form post sends the field, an HTMX
    fragment sends the header.
    """
    return form_value or request.headers.get(HEADER_NAME)


def require_request(
    settings: Settings, identity: str, request: Request, form_value: str | None = None
) -> None:
    """``require`` for a handler that may be called either way."""
    require(settings, identity, token_from_request(request, form_value))
