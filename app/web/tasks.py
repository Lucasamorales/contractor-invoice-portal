"""Scheduler endpoints (R-JOB-01 .. R-JOB-06).

All are ``POST /tasks/*``, called by Cloud Scheduler with an OIDC token that
this app verifies. They sit outside IAP — Scheduler is not a browser and has
no IAP session — so the token check is the only thing standing between these
and the open internet. It is not optional, and a missing configuration makes
them refuse rather than run.

Every job is idempotent (R-JOB-*): Scheduler retries on a non-2xx, and a
double fire must not double-send.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from google.auth.exceptions import GoogleAuthError
from google.oauth2 import id_token
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_session
from app.services import jobs, mailer
from app.services.storage import ObjectStore
from app.transport import HttpxRequest
from app.web.member import get_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])

DbSession = Annotated[Session, Depends(get_session)]
Config = Annotated[Settings, Depends(get_settings)]

_transport = HttpxRequest()


def verify_scheduler(
    settings: Config,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Verify the OIDC token Cloud Scheduler sends, or refuse.

    The token's audience is our own service URL, so a token minted for some
    other service cannot be replayed here. The service account that signed it
    must be the one we expect.
    """
    if not settings.scheduler_audience or not settings.scheduler_service_account:
        # Refusing is the safe failure. An unauthenticated endpoint that runs
        # the payroll reminders is worse than one that is switched off.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduler authentication is not configured.",
        )

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This endpoint is for Cloud Scheduler.",
        )

    token = authorization.removeprefix("Bearer ").strip()
    try:
        claims = id_token.verify_oauth2_token(
            token, _transport, audience=settings.scheduler_audience
        )
    except (GoogleAuthError, ValueError) as exc:
        # The token is a bearer credential and is never logged.
        logger.warning("scheduler token rejected: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not verify the caller.",
        ) from exc

    email = str(claims.get("email", "")).strip().lower()
    if email != settings.scheduler_service_account.strip().lower():
        logger.warning("scheduler token from unexpected account")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="That account may not run scheduled jobs.",
        )
    if not claims.get("email_verified", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Unverified caller."
        )
    return email


Scheduler = Annotated[str, Depends(verify_scheduler)]


def _now() -> datetime:
    return datetime.now(UTC)


@router.post("/outbox")
async def run_outbox(
    caller: Scheduler,
    session: DbSession,
    settings: Config,
    store: Annotated[ObjectStore, Depends(get_store)],
) -> dict[str, object]:
    """R-JOB-04. Every five minutes.

    In staging the client is the sandbox, which records and discards
    (R-ENV-03), so this endpoint is safe to run in either environment.
    """
    del caller
    client = mailer.build_client(settings)
    with session.begin():
        report = mailer.dispatch(
            session, client=client, settings=settings, store=store, now=_now()
        )
    if report.failed:
        # R-JOB-04 says alert. Logged at error so it surfaces in Cloud Logging;
        # the message carries no payload.
        logger.error("outbox: %d messages gave up", report.failed)
    return report.as_dict()


@router.post("/remind")
async def run_remind(
    caller: Scheduler, session: DbSession, settings: Config
) -> dict[str, object]:
    """R-JOB-02. The 24th to everyone, the 27th to those still missing."""
    del caller
    with session.begin():
        report = jobs.remind(session, settings=settings, now=_now())
    return report.as_dict()


@router.post("/summary")
async def run_summary(
    caller: Scheduler, session: DbSession, settings: Config
) -> dict[str, object]:
    """R-JOB-03. The 29th, to the finance group."""
    del caller
    with session.begin():
        report = jobs.summary(session, settings=settings, now=_now())
    return report.as_dict()


@router.post("/sync-kudos")
async def run_sync_kudos(
    caller: Scheduler, session: DbSession, settings: Config
) -> dict[str, object]:
    """R-JOB-01.

    Not built: the kudos sheet's file ID and column layout have not been
    supplied (SPEC §19). The endpoint exists so the Scheduler job can be
    created now and starts working when the sheet is configured, and it says
    plainly that it did nothing rather than reporting success.
    """
    del caller, session
    if not settings.kudos_sheet_id:
        return {
            "job": "sync-kudos",
            "queued": 0,
            "skipped": 0,
            "detail": "no kudos sheet configured; nothing synced",
        }
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="The kudos sync is not built yet.",
    )


@router.post("/backup")
async def run_backup(caller: Scheduler, settings: Config) -> dict[str, object]:
    """R-JOB-05. Nightly pg_dump to GCS, 30 days retained.

    Not built: it needs the bucket. This is the backup we rely on rather than
    Neon's history, so it should not be left long.
    """
    del caller
    if not settings.gcs_bucket:
        return {
            "job": "backup",
            "queued": 0,
            "skipped": 0,
            "detail": "no bucket configured; nothing backed up",
        }
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="The backup job is not built yet.",
    )


@router.post("/drive-mirror")
async def run_drive_mirror(caller: Scheduler, settings: Config) -> dict[str, object]:
    """R-JOB-06.

    Not built: which Shared Drive to use has not been decided (SPEC §19), and
    R-STO-05 requires the folder be restricted to the finance group before
    anything is copied into it.
    """
    del caller
    if not settings.drive_folder_id:
        return {
            "job": "drive-mirror",
            "queued": 0,
            "skipped": 0,
            "detail": "no Drive folder configured; nothing mirrored",
        }
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="The Drive mirror is not built yet.",
    )
