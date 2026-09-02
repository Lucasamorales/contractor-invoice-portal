"""Admin routes (R-ADM-01 .. R-ADM-09).

Every route here depends on ``require_finance`` (R-ACC-04): IAP restricts the
path to the finance IAM group, and the app checks again, so a misconfigured
IAP rule is not the only thing standing between someone and the payroll.

There is no generic table editor anywhere in this module (R-ADM-08). Every
write is a typed form with validation and an audit row in the same transaction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import FinanceIdentity
from app.config import Settings, get_settings
from app.db import repo
from app.db.models import BankDetail, Person
from app.db.session import get_session
from app.domain.money import MoneyError, parse_gbp_to_pence
from app.domain.period import Period
from app.services import admin
from app.services.crypto import Cipher, field_aad
from app.web import csrf
from app.web.member import get_cipher
from app.web.templates import templates

router = APIRouter(prefix="/admin", tags=["admin"])

DbSession = Annotated[Session, Depends(get_session)]
Config = Annotated[Settings, Depends(get_settings)]
Box = Annotated[Cipher, Depends(get_cipher)]


def _now() -> datetime:
    return datetime.now(UTC)


def _context(
    request: Request, identity: FinanceIdentity, settings: Settings
) -> dict[str, object]:
    return {
        "request": request,
        "is_finance": True,
        "actor": identity.email,
        "csrf_token": csrf.issue(settings, identity.email),
        "period": Period.containing(_now()),
    }


def _period_from(query: str | None) -> Period:
    """A period from the query string, defaulting to the current one."""
    if not query:
        return Period.containing(_now())
    return Period.parse(query)


def _get_person(session: Session, person_id: uuid.UUID) -> Person:
    person = session.get(Person, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="No such person.")
    return person


# --------------------------------------------------------------------------
# R-ADM-01 People
# --------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def people(
    request: Request,
    identity: FinanceIdentity,
    session: DbSession,
    settings: Config,
) -> HTMLResponse:
    """Everyone, with fees masked until asked for.

    A screenshare of this page should not leak the payroll, so the figures are
    rendered as bullets and revealed one at a time (R-ADM-01).
    """
    now = _now()
    period = Period.containing(now)
    context = _context(request, identity, settings)
    context["rows"] = admin.people_overview(session, period, on=now.date())
    return templates.TemplateResponse(
        request=request, name="admin/people.html", context=context
    )


@router.post("/people/{person_id}/reveal-fee", response_class=HTMLResponse)
async def reveal_fee(
    request: Request,
    person_id: uuid.UUID,
    identity: FinanceIdentity,
    session: DbSession,
    settings: Config,
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Reveal one fee, and record that it was revealed (R-ADM-01).

    Returns an HTMX fragment rather than a page: the row updates in place.
    """
    csrf.require_request(settings, identity.email, request, csrf_token)
    person = _get_person(session, person_id)
    fee = repo.fee_for(session, person_id, on=_now().date())

    repo.record_audit(
        session,
        actor_email=identity.email,
        action="fee.reveal",
        entity="people",
        entity_id=str(person_id),
        after={"revealed": True},
    )
    session.commit()

    return templates.TemplateResponse(
        request=request,
        name="admin/_fee_value.html",
        context={
            "request": request,
            "person": person,
            "fee_pence": fee.fee_pence if fee else None,
            "revealed": True,
        },
    )


# --------------------------------------------------------------------------
# R-ADM-02 One person
# --------------------------------------------------------------------------


@router.get("/people/{person_id}", response_class=HTMLResponse)
async def person_detail(
    request: Request,
    person_id: uuid.UUID,
    identity: FinanceIdentity,
    session: DbSession,
    settings: Config,
    cipher: Box,
) -> HTMLResponse:
    person = _get_person(session, person_id)
    context = _context(request, identity, settings)
    context.update(
        {
            "person": person,
            "fees": repo.fee_history(session, person_id),
            "banks": admin.masked_bank_summary(session, person, cipher),
            "invoices": repo.invoice_history(session, person_id),
            "today": _now().date(),
        }
    )
    return templates.TemplateResponse(
        request=request, name="admin/person.html", context=context
    )


# response_model=None: the handler re-renders the page on a validation
# failure and redirects on success, a union FastAPI cannot model.
@router.post("/people/{person_id}/fee", response_model=None)
async def set_fee(
    request: Request,
    person_id: uuid.UUID,
    identity: FinanceIdentity,
    session: DbSession,
    settings: Config,
    cipher: Box,
    amount: Annotated[str, Form()] = "",
    valid_from: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """R-ADM-03. Effective-dated, note required, audited."""
    csrf.require(settings, identity.email, csrf_token)
    person = _get_person(session, person_id)

    errors: list[str] = []
    fee_pence = 0
    starts = _now().date()
    try:
        fee_pence = parse_gbp_to_pence(amount)
    except MoneyError as exc:
        errors.append(str(exc))
    try:
        starts = date.fromisoformat(valid_from) if valid_from else starts
    except ValueError:
        errors.append("The start date must be a real date.")

    if not errors:
        try:
            admin.set_fee(
                session,
                person=person,
                fee_pence=fee_pence,
                valid_from=starts,
                note=note,
                actor_email=identity.email,
            )
            session.commit()
            return RedirectResponse(
                f"/admin/people/{person_id}", status_code=status.HTTP_303_SEE_OTHER
            )
        except admin.AdminError as exc:
            session.rollback()
            errors.append(str(exc))

    context = _context(request, identity, settings)
    context.update(
        {
            "person": person,
            "fees": repo.fee_history(session, person_id),
            "banks": admin.masked_bank_summary(session, person, cipher),
            "invoices": repo.invoice_history(session, person_id),
            "today": _now().date(),
            "errors": errors,
        }
    )
    return templates.TemplateResponse(
        request=request,
        name="admin/person.html",
        context=context,
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


@router.post("/people/{person_id}/deactivate")
async def deactivate(
    person_id: uuid.UUID,
    identity: FinanceIdentity,
    session: DbSession,
    settings: Config,
    reason: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """R-ADM-09. History is preserved; nothing is ever deleted."""
    csrf.require(settings, identity.email, csrf_token)
    person = _get_person(session, person_id)
    admin.deactivate(
        session, person=person, actor_email=identity.email, reason=reason.strip()
    )
    session.commit()
    return RedirectResponse(
        f"/admin/people/{person_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/bank/{bank_id}/reveal", response_class=HTMLResponse)
async def reveal_bank(
    request: Request,
    bank_id: uuid.UUID,
    identity: FinanceIdentity,
    session: DbSession,
    settings: Config,
    cipher: Box,
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Show one bank row in full (R-ACC-06), and audit the reveal.

    Robin pays these invoices and the numbers are printed on every PDF he
    receives, so reading them is legitimate. Reading them silently is not.
    """
    csrf.require_request(settings, identity.email, request, csrf_token)
    detail = session.get(BankDetail, bank_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="No such bank row.")

    from app.services.invoicing import ENCRYPTED_FIELDS

    values: dict[str, str] = {}
    for name in ENCRYPTED_FIELDS:
        stored = getattr(detail, f"{name}_enc")
        values[name] = (
            cipher.decrypt(stored, aad=field_aad(str(detail.person_id), name))
            if stored
            else ""
        )

    repo.record_audit(
        session,
        actor_email=identity.email,
        action="bank.reveal",
        entity="bank_details",
        entity_id=str(bank_id),
        after={"revealed": True},
    )
    session.commit()

    return templates.TemplateResponse(
        request=request,
        name="admin/_bank_value.html",
        context={"request": request, "bank": values, "revealed": True},
    )


# --------------------------------------------------------------------------
# R-ADM-04 Period
# --------------------------------------------------------------------------


@router.get("/period", response_class=HTMLResponse)
async def period_view(
    request: Request,
    identity: FinanceIdentity,
    session: DbSession,
    settings: Config,
    month: str | None = None,
) -> HTMLResponse:
    period = _period_from(month)
    context = _context(request, identity, settings)
    context["view"] = admin.period_view(session, period, settings=settings)
    context["period"] = period
    return templates.TemplateResponse(
        request=request, name="admin/period.html", context=context
    )


# --------------------------------------------------------------------------
# R-ADM-05 Payment run
# --------------------------------------------------------------------------


@router.get("/payment-run", response_class=HTMLResponse)
async def payment_run(
    request: Request,
    identity: FinanceIdentity,
    session: DbSession,
    settings: Config,
    cipher: Box,
    month: str | None = None,
) -> HTMLResponse:
    """The period's invoices split into the two batches Robin pays separately.

    On screen the account numbers are masked; the CSV he pays from carries them
    in full, and taking that export is audited.
    """
    period = _period_from(month)
    context = _context(request, identity, settings)
    context["run"] = admin.payment_run(session, period, cipher=cipher)
    context["period"] = period
    return templates.TemplateResponse(
        request=request, name="admin/payment_run.html", context=context
    )


@router.post("/payment-run.csv", response_class=PlainTextResponse)
async def payment_run_csv(
    identity: FinanceIdentity,
    session: DbSession,
    settings: Config,
    cipher: Box,
    month: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
) -> PlainTextResponse:
    """R-ADM-05. A POST, not a GET: exporting the payroll is an action.

    The audit row is written before the file is handed over.
    """
    csrf.require(settings, identity.email, csrf_token)
    period = _period_from(month or None)
    run = admin.payment_run(session, period, cipher=cipher)

    repo.record_audit(
        session,
        actor_email=identity.email,
        action="payment_run.export",
        entity="period",
        entity_id=period.key,
        after={"uk_lines": len(run.uk), "intl_lines": len(run.intl)},
    )
    session.commit()

    return PlainTextResponse(
        admin.payment_run_csv(run),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="payment-run-{period.key}.csv"'
            )
        },
    )


# --------------------------------------------------------------------------
# R-ADM-06 Data check and R-ADM-07 Queue
# --------------------------------------------------------------------------


@router.get("/data-check", response_class=HTMLResponse)
async def data_check(
    request: Request,
    identity: FinanceIdentity,
    session: DbSession,
    settings: Config,
) -> HTMLResponse:
    """The go-live grid: four ticks per person, plus what the seed flagged."""
    context = _context(request, identity, settings)
    context["rows"] = admin.data_check(session, on=_now().date())
    return templates.TemplateResponse(
        request=request, name="admin/data_check.html", context=context
    )


@router.get("/queue", response_class=HTMLResponse)
async def queue(
    request: Request,
    identity: FinanceIdentity,
    session: DbSession,
    settings: Config,
) -> HTMLResponse:
    context = _context(request, identity, settings)
    context["queue"] = admin.queue(session, on=_now().date())
    return templates.TemplateResponse(
        request=request, name="admin/queue.html", context=context
    )
