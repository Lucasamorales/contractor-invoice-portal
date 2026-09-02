"""Member routes.

Build order step 1 is deliberately small: someone logs in through IAP and sees
their own name. The wizard (R-WIZ-01..09) is built on top of this once the
invoice itself is right.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.auth import CurrentIdentity
from app.db import repo
from app.db.session import get_session
from app.domain.period import Period, can_submit, closed_message
from app.web.templates import templates

router = APIRouter()

DbSession = Annotated[Session, Depends(get_session)]


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    identity: CurrentIdentity,
    session: DbSession,
) -> HTMLResponse:
    """The landing page for a member.

    A verified caller with no ``people`` row is created as pending rather than
    turned away (R-ACC-03): they appear in Robin's queue and are told what is
    missing, which is the whole point of replacing the phone call.
    """
    person = repo.get_or_create_person(
        session,
        email=identity.email,
        display_name=identity.email.partition("@")[0],
    )
    session.commit()

    now = datetime.now(UTC)
    period = Period.containing(now)

    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "person": person,
            "period": period,
            "can_submit": can_submit(now),
            "window_message": None if can_submit(now) else closed_message(now),
            "is_finance": identity.is_finance,
        },
    )
