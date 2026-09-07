"""Admin routes (R-ADM-01 .. R-ADM-09).

Every route here depends on ``require_finance`` (R-ACC-04): IAP restricts the
path to the finance IAM group, and the app checks again, so a misconfigured
IAP rule is not the only thing standing between someone and the payroll.

There is no generic table editor anywhere in this module (R-ADM-08). Every
write is a typed form with validation and an audit row in the same transaction.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from sqlalchemy.orm import Session

from app.auth import FinanceIdentity
from app.config import Settings, get_settings
from app.db import repo
from app.db.models import BankDetail, Invoice, Outbox, OutboxStatus, Person
from app.db.session import get_session
from app.domain.kudos import Quarter
from app.domain.money import MoneyError, format_gbp, parse_gbp_to_pence
from app.domain.period import Period, window_state
from app.services import admin, bank_changes, invoicing, kudos_admin
from app.services.crypto import Cipher, field_aad
from app.services.storage import ObjectStore, StorageError
from app.web import csrf
from app.web.member import _content_disposition as member_disposition
from app.web.member import get_cipher, get_store
from app.web.templates import templates

router = APIRouter(prefix="/admin", tags=["admin"])
logger = logging.getLogger(__name__)

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


def _quarter_from(query: str | None) -> Quarter:
    """A quarter from the query string, defaulting to the one just finished.

    Just finished, not the current one: a prize is awarded after the quarter
    ends, so that is the page somebody opening this screen wants.
    """
    if query:
        year, _, number = query.partition("-Q")
        return Quarter(int(year), int(number))
    now = _now().date()
    current = Quarter.containing(now)
    if current.number == 1:
        return Quarter(current.year - 1, 4)
    return Quarter(current.year, current.number - 1)


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
async def overview(
    request: Request,
    identity: FinanceIdentity,
    session: DbSession,
    settings: Config,
) -> HTMLResponse:
    """The landing screen: who has not invoiced, and what is stuck.

    People used to be here, which is a directory rather than a task. Between
    the 24th and the 29th those are the only two questions.

    Introduces no new metric. Every figure comes from ``period_view`` (R-ADM-04)
    or ``needs_attention``, so nothing on it can disagree with the screen it
    links to.
    """
    now = _now()
    period = Period.containing(now)
    view = admin.period_view(session, period, settings=settings)
    attention = admin.needs_attention(session, now=now)

    context = _context(request, identity, settings)
    context.update(
        {
            "view": view,
            "attention": attention,
            "period": period,
            "window": window_state(now).value,
            "received": [row for row in view.rows if row.invoice],
            "received_pence": sum(
                row.invoice.total_pence for row in view.rows if row.invoice
            ),
            "unfinished": attention.stuck_drafts,
            "flagged": view.flagged,
        }
    )
    return templates.TemplateResponse(
        request=request, name="admin/overview.html", context=context
    )


@router.get("/people", response_class=HTMLResponse)
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


def _person_page(
    request: Request,
    identity: FinanceIdentity,
    session: Session,
    settings: Settings,
    cipher: Cipher,
    person: Person,
    *,
    errors: list[str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """The person page. Shared so every handler that renders it agrees."""
    now = _now()
    period = Period.containing(now)
    context = _context(request, identity, settings)
    context.update(
        {
            "person": person,
            "fees": repo.fee_history(session, person.id),
            "banks": admin.masked_bank_summary(session, person, cipher),
            "invoices": repo.invoice_history(session, person.id),
            "today": now.date(),
            "errors": errors or [],
            # The three things somebody opens this page to check, so the
            # common visit needs no scrolling at all.
            "current_fee": repo.fee_for(session, person.id, on=now.date()),
            "current_bank": repo.active_bank_detail(session, person.id),
            "this_invoice": repo.live_invoice_for_period(
                session, person.id, period.key
            ),
            "period": period,
        }
    )
    return templates.TemplateResponse(
        request=request,
        name="admin/person.html",
        context=context,
        status_code=status_code,
    )


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
    return _person_page(request, identity, session, settings, cipher, person)


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

    return _person_page(
        request,
        identity,
        session,
        settings,
        cipher,
        person,
        errors=errors,
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


@router.post("/outbox/{row_id}/retry")
async def retry_mail(
    row_id: uuid.UUID,
    identity: FinanceIdentity,
    session: DbSession,
    settings: Config,
    csrf_token: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Put a stopped email back in the queue (R-JOB-04).

    Only a failure that cannot succeed as it stands ever stops — a bad address,
    a message too large — so this is used after fixing the cause. There is no
    matching "give up": the only way a message leaves the queue is by being
    sent.
    """
    csrf.require(settings, identity.email, csrf_token)
    row = session.get(Outbox, row_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such message."
        )

    row.status = OutboxStatus.PENDING
    row.attempts = 0
    row.next_attempt_at = None
    row.last_error = None
    repo.record_audit(
        session,
        actor_email=identity.email,
        action="outbox.retry",
        entity="outbox",
        entity_id=str(row.id),
        after={"kind": row.kind},
    )
    session.commit()
    return RedirectResponse("/admin/queue", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/invoices/{invoice_id}/void")
async def void_invoice(
    invoice_id: uuid.UUID,
    identity: FinanceIdentity,
    session: DbSession,
    settings: Config,
    reason: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """R-SUB-10. The other half of "an invoice is immutable" (CLAUDE.md 2.3).

    Figures are never updated, so a wrong invoice is voided and reissued. The
    number is not released (R-NUM-05) and the row is not deleted: the gap in
    somebody's sequence is a fact, and the reason is what explains it later.
    """
    csrf.require(settings, identity.email, csrf_token)
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="We have no such invoice."
        )
    if not reason.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A void needs a reason.",
        )

    invoicing.void(
        session,
        invoice,
        actor_email=identity.email,
        now=_now(),
        reason=reason.strip(),
    )
    session.commit()
    return RedirectResponse(
        f"/admin/people/{invoice.person_id}", status_code=status.HTTP_303_SEE_OTHER
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
# Finance downloads an invoice PDF
# --------------------------------------------------------------------------


@router.get("/invoices/{invoice_id}/pdf")
async def download_invoice(
    request: Request,
    invoice_id: uuid.UUID,
    identity: FinanceIdentity,
    session: DbSession,
    settings: Config,
    store: Annotated[ObjectStore, Depends(get_store)],
) -> Response:
    """Any invoice, for the people who pay them.

    NOT COVERED BY A REQUIREMENT, like its member-side twin. The spec gives
    finance two routes to a PDF — the email in R-SUB-07 and the Drive mirror in
    R-STO-04 — and until both exist nobody in finance can see one at all.

    Unscoped by design, and that is the whole difference from the member
    route: finance may read any invoice (R-ACC-06), because they pay them and
    the same figures are already on every PDF they hold. Reading is audited,
    because reading is legitimate and reading unrecorded is not.
    """
    invoice = session.get(Invoice, invoice_id)
    if invoice is None or invoice.pdf_uri is None:
        context = _context(request, identity, settings)
        context["blocked"] = (
            "That invoice has no PDF. A draft never finished sending."
            if invoice is not None
            else "We have no such invoice."
        )
        return templates.TemplateResponse(
            request=request,
            name="admin/blocked.html",
            context=context,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    repo.record_audit(
        session,
        actor_email=identity.email,
        action="invoice.download",
        entity="invoices",
        entity_id=str(invoice.id),
        after={"number": invoice.number, "period": invoice.period},
    )
    session.commit()

    try:
        pdf = store.get(invoice.pdf_uri)
    except StorageError:
        logger.error(
            "invoice %s is marked sent but its PDF is not in the store", invoice.id
        )
        raise

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": member_disposition(
                invoice.pdf_uri.rsplit("/", 1)[-1]
            ),
            # It carries somebody's full account number (CLAUDE.md 2.8).
            "Cache-Control": "private, no-store",
        },
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
    context["attention"] = admin.needs_attention(session, now=_now())
    # For the "who is this?" form on an unmapped kudos name.
    context["people"] = repo.all_people(session)
    return templates.TemplateResponse(
        request=request, name="admin/queue.html", context=context
    )


# --------------------------------------------------------------------------
# R-BANK-07 / R-BANK-08 Propose and flag back
# --------------------------------------------------------------------------


# response_model=None: re-renders the person page on a validation failure.
@router.post("/people/{person_id}/bank/propose", response_model=None)
async def propose_bank_change(
    request: Request,
    person_id: uuid.UUID,
    identity: FinanceIdentity,
    session: DbSession,
    settings: Config,
    cipher: Box,
    account_name: Annotated[str, Form()] = "",
    bank_name: Annotated[str, Form()] = "",
    sort_code: Annotated[str, Form()] = "",
    account_number: Annotated[str, Form()] = "",
    account_number_again: Annotated[str, Form()] = "",
    iban: Annotated[str, Form()] = "",
    swift_bic: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """Propose a correction (R-BANK-07).

    Lands ``pending``. It does not touch what is payable and it never can:
    only the owner can confirm it. This route is the reason a taken-over admin
    account cannot redirect the payroll.
    """
    csrf.require(settings, identity.email, csrf_token)
    person = _get_person(session, person_id)

    entry = bank_changes.Entry(
        account_name=account_name,
        bank_name=bank_name,
        sort_code=sort_code,
        account_number=account_number,
        account_number_again=account_number_again,
        iban=iban,
        swift_bic=swift_bic,
    )
    try:
        bank_changes.propose_change(
            session,
            person=person,
            entry=entry,
            cipher=cipher,
            now=_now(),
            actor_email=identity.email,
        )
    except bank_changes.BankChangeError as exc:
        session.rollback()
        return _person_page(
            request,
            identity,
            session,
            settings,
            cipher,
            person,
            errors=[exc.message, *exc.errors],
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    session.commit()
    return RedirectResponse(
        f"/admin/people/{person_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/bank/{bank_id}/flag", response_model=None)
async def flag_bank_row(
    request: Request,
    bank_id: uuid.UUID,
    identity: FinanceIdentity,
    session: DbSession,
    settings: Config,
    cipher: Box,
    reason: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """Flag a row back to its owner with what needs fixing (R-BANK-08).

    Sends them an email. Changes nothing about what is payable: flagging is a
    request, not an edit.
    """
    csrf.require(settings, identity.email, csrf_token)
    detail = session.get(BankDetail, bank_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="No such bank row.")
    person = _get_person(session, detail.person_id)

    try:
        bank_changes.flag_back(
            session,
            detail_id=bank_id,
            reason=reason,
            actor_email=identity.email,
        )
    except bank_changes.BankChangeError as exc:
        session.rollback()
        return _person_page(
            request,
            identity,
            session,
            settings,
            cipher,
            person,
            errors=[exc.message],
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    session.commit()
    return RedirectResponse(
        f"/admin/people/{person.id}", status_code=status.HTTP_303_SEE_OTHER
    )


# --------------------------------------------------------------------------
def _kudos_prizes(settings: Settings) -> dict[str, str]:
    """What each place pays, formatted, for the script beside the dropdown.

    The prize follows the place somebody selects rather than the place the
    counts suggest, and on a tie those are different. Sending the amounts
    rather than the pence keeps money formatting in one place (CLAUDE.md §6).
    """
    return {
        str(place): format_gbp(kudos_admin.cash_for(place, settings))
        for place in kudos_admin.PLACES
    }


# R-KUD-13 / R-KUD-17 — the two kudos decisions a person has to make
# --------------------------------------------------------------------------


@router.get("/kudos", response_class=HTMLResponse)
async def kudos_quarter(
    request: Request,
    identity: FinanceIdentity,
    session: DbSession,
    settings: Config,
    quarter: str | None = None,
) -> HTMLResponse:
    """A quarter's standings, and where the policy needs a human.

    Shows the leaderboard and the cash each place carries. It awards nothing:
    the tiebreakers are judgements, so the button is the decision.
    """
    period = _quarter_from(quarter)
    context = _context(request, identity, settings)
    context["view"] = kudos_admin.quarter_view(session, period, settings=settings)
    context["quarter"] = period
    context["unmapped"] = repo.unresolved_kudos_names(session)
    context["kudos_rate_pence"] = settings.kudos_rate_pence
    context["prizes"] = _kudos_prizes(settings)
    return templates.TemplateResponse(
        request=request, name="admin/kudos.html", context=context
    )


@router.post("/kudos/{quarter}/award", response_model=None)
async def award_quarter(
    request: Request,
    quarter: str,
    identity: FinanceIdentity,
    session: DbSession,
    settings: Config,
    person_id: Annotated[str, Form()] = "",
    place: Annotated[str, Form()] = "",
    kudos_count: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """Record one prize. Audited, and refused twice for the same person."""
    csrf.require(settings, identity.email, csrf_token)
    period = _quarter_from(quarter)

    errors: list[str] = []
    try:
        person = _get_person(session, uuid.UUID(person_id))
        kudos_admin.award(
            session,
            person=person,
            quarter=period,
            place=int(place),
            kudos_count=int(kudos_count or 0),
            settings=settings,
            actor_email=identity.email,
            note=note,
        )
        session.commit()
        return RedirectResponse(
            f"/admin/kudos?quarter={period.key}", status_code=status.HTTP_303_SEE_OTHER
        )
    except (ValueError, kudos_admin.KudosAdminError) as exc:
        session.rollback()
        errors.append(str(exc))

    context = _context(request, identity, settings)
    context["view"] = kudos_admin.quarter_view(session, period, settings=settings)
    context["quarter"] = period
    context["unmapped"] = repo.unresolved_kudos_names(session)
    context["kudos_rate_pence"] = settings.kudos_rate_pence
    context["prizes"] = _kudos_prizes(settings)
    context["errors"] = errors
    return templates.TemplateResponse(
        request=request,
        name="admin/kudos.html",
        context=context,
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


@router.post("/kudos/map", response_model=None)
async def map_kudos_name(
    request: Request,
    identity: FinanceIdentity,
    session: DbSession,
    settings: Config,
    sheet_name: Annotated[str, Form()] = "",
    person_id: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """Say who a name in the sheet is, or that it is nobody (R-KUD-13).

    An empty ``person_id`` means ignore: somebody who has left, or staff who do
    not invoice here. Their kudos stay on the record either way.
    """
    csrf.require(settings, identity.email, csrf_token)

    errors: list[str] = []
    try:
        person = _get_person(session, uuid.UUID(person_id)) if person_id else None
        kudos_admin.map_name(
            session,
            sheet_name=sheet_name,
            person=person,
            actor_email=identity.email,
            note=note,
        )
        session.commit()
        return RedirectResponse("/admin/queue", status_code=status.HTTP_303_SEE_OTHER)
    except (ValueError, kudos_admin.KudosAdminError) as exc:
        session.rollback()
        errors.append(str(exc))

    context = _context(request, identity, settings)
    context["queue"] = admin.queue(session, on=_now().date())
    context["attention"] = admin.needs_attention(session, now=_now())
    context["people"] = repo.all_people(session)
    context["errors"] = errors
    return templates.TemplateResponse(
        request=request,
        name="admin/queue.html",
        context=context,
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )
