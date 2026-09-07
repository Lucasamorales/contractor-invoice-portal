"""The member wizard (R-WIZ-01 .. R-WIZ-09).

Server-rendered Jinja with HTMX, one form per step. No SPA, no build step.

Expenses are carried through the wizard in the form itself rather than saved as
a server-side draft. That is not a shortcut: ``expenses.invoice_id`` is NOT
NULL (R-DATA-05), so an expense cannot exist before the invoice does, and an
abandoned wizard leaves nothing behind to clean up.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from sqlalchemy.orm import Session, sessionmaker

from app.auth import CurrentIdentity
from app.config import Settings, get_settings
from app.db import repo
from app.db.models import BankState, Counter, Invoice, Person
from app.db.session import get_session, get_sessionmaker
from app.domain.formatting import name_from_email
from app.domain.iban import mask
from app.domain.money import MoneyError, parse_gbp_to_pence
from app.domain.period import Period, can_submit, closed_message
from app.services import bank_changes, receipts
from app.services.crypto import Cipher
from app.services.invoicing import (
    ExpenseInput,
    Readiness,
    SubmissionBlocked,
    decrypt_bank_block,
    prepare,
    readiness,
    submit,
)
from app.services.pdf import Letterhead, build_context, render_html, render_pdf
from app.services.storage import ObjectStore, StorageError, build_store
from app.web import csrf
from app.web.ratelimit import bank_limiter, submit_limiter
from app.web.templates import templates

router = APIRouter()
logger = logging.getLogger(__name__)

DbSession = Annotated[Session, Depends(get_session)]
Config = Annotated[Settings, Depends(get_settings)]
Factory = Annotated["sessionmaker[Session]", Depends(get_sessionmaker)]

MAX_ITEMS = 10  # R-WIZ-04 caps receipts at ten; the items they attach to match.


def _now() -> datetime:
    return datetime.now(UTC)


def get_store(settings: Config) -> ObjectStore:
    """The object store: GCS when a bucket is configured, local otherwise.

    ``build_store`` makes that choice. It used to be bypassed here by a
    hardcoded local store, which meant a deployed service happily tried to
    write invoice PDFs into its own container filesystem and failed —
    a read-only directory standing in for a bucket that existed the whole time.
    """
    return build_store(settings)


def get_cipher(settings: Config) -> Cipher:
    return Cipher.from_settings(settings)


def get_renderer() -> Callable[[dict], bytes]:
    """The PDF renderer.

    Injected rather than called directly so a test can drive the whole submit
    route without WeasyPrint's native Pango and Cairo libraries present. The
    template those libraries render is covered by tests/test_invoice_render.py.
    """
    return render_pdf


def current_person(identity: CurrentIdentity, session: DbSession) -> Person:
    """The caller's own row, created on first login (R-ACC-03).

    Derived from the verified IAP email and nothing else (R-ACC-05, R-SEC-06).
    No route takes a person id from the client.
    """
    person = repo.get_or_create_person(
        session,
        email=identity.email,
        display_name=name_from_email(identity.email),
    )
    # Rows created before that derivation existed hold the bare local part of
    # the address, so the app greeted them as "leo" and the payment run
    # listed "leo" as the payee. Nothing else ever writes display_name, so a
    # row still holding it verbatim was never anybody's decision to keep.
    if person.display_name == identity.email.partition("@")[0]:
        person.display_name = name_from_email(identity.email)
    session.commit()
    return person


CurrentPerson = Annotated[Person, Depends(current_person)]


def _base_context(
    request: Request,
    person: Person,
    identity: CurrentIdentity,
    settings: Settings,
) -> dict[str, object]:
    now = _now()
    # R-PER-06 shuts submission before the 24th. Staging can override that, or
    # the whole thing is untestable for three weeks in four; production refuses
    # to start with the override on.
    open_now = can_submit(now) or settings.window_always_open
    return {
        "request": request,
        "person": person,
        "period": Period.containing(now),
        "is_finance": identity.is_finance,
        "csrf_token": csrf.issue(settings, identity.email),
        "window_open": open_now,
        "window_message": None if open_now else closed_message(now),
        "window_forced": settings.window_always_open and not can_submit(now),
    }


def _read_state(
    session: Session, person: Person, settings: Settings
) -> tuple[Period, Readiness]:
    now = _now()
    period = Period.containing(now)
    return period, readiness(
        session, person=person, period=period, now=now, settings=settings
    )


# --------------------------------------------------------------------------
# R-WIZ-01 Welcome
# --------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def welcome(
    request: Request,
    identity: CurrentIdentity,
    person: CurrentPerson,
    session: DbSession,
    settings: Config,
) -> HTMLResponse:
    """Greeting, the period, the fee, the kudos and the running total.

    Every figure is read-only here. The only thing a person can do from this
    screen is start, or fix whatever is blocking them.
    """
    period, state = _read_state(session, person, settings)
    context = _base_context(request, person, identity, settings)
    context.update({"state": state, "period": period})
    return templates.TemplateResponse(
        request=request, name="welcome.html", context=context
    )


# --------------------------------------------------------------------------
# R-WIZ-02 Your details
# --------------------------------------------------------------------------


@router.get("/details", response_class=HTMLResponse)
async def details_form(
    request: Request,
    identity: CurrentIdentity,
    person: CurrentPerson,
    settings: Config,
) -> HTMLResponse:
    context = _base_context(request, person, identity, settings)
    context["errors"] = {}
    return templates.TemplateResponse(
        request=request, name="details.html", context=context
    )


# response_model=None: the handler returns a rendered form on validation
# failure and a redirect on success, and FastAPI cannot model that union.
@router.post("/details", response_model=None)
async def save_details(
    request: Request,
    identity: CurrentIdentity,
    person: CurrentPerson,
    session: DbSession,
    settings: Config,
    legal_name: Annotated[str, Form()] = "",
    role: Annotated[str, Form()] = "",
    address: Annotated[str, Form()] = "",
    phone: Annotated[str, Form()] = "",
    contact_email: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """All five are required because all five print on the invoice (R-PDF-02)."""
    csrf.require(settings, identity.email, csrf_token)

    fields = {
        "legal_name": legal_name.strip(),
        "role": role.strip(),
        "address": address.strip(),
        "phone": phone.strip(),
        "contact_email": contact_email.strip(),
    }
    errors = {
        name: "This appears on your invoice, so we need it."
        for name, value in fields.items()
        if not value
    }
    if "@" not in fields["contact_email"] and not errors.get("contact_email"):
        errors["contact_email"] = "That doesn't look like an email address."

    if errors:
        context = _base_context(request, person, identity, settings)
        context.update({"errors": errors, "submitted": fields})
        return templates.TemplateResponse(
            request=request,
            name="details.html",
            context=context,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    for name, value in fields.items():
        setattr(person, name, value)
    person.profile_confirmed_at = _now()
    session.commit()

    return RedirectResponse("/bank", status_code=status.HTTP_303_SEE_OTHER)


# --------------------------------------------------------------------------
# R-WIZ-05 / R-WIZ-08 Bank details
# --------------------------------------------------------------------------


def _masked_bank(person: Person, session: Session, cipher: Cipher) -> dict[str, str]:
    """The current row, account number and IBAN masked to last four (R-WIZ-05)."""
    detail = repo.active_bank_detail(session, person.id)
    if detail is None:
        detail = next((row for row in repo.bank_history(session, person.id)), None)
    if detail is None:
        return {}

    block = decrypt_bank_block(
        {
            "account_name": detail.account_name,
            "bank_name": detail.bank_name,
            "account_number_enc": detail.account_number_enc,
            "sort_code_enc": detail.sort_code_enc,
            "iban_enc": detail.iban_enc,
            "swift_bic_enc": detail.swift_bic_enc,
        },
        person_id=person.id,
        cipher=cipher,
    )
    return {
        "id": str(detail.id),
        "state": detail.state.value,
        "source": detail.source.value,
        "account_name": block.account_name,
        "bank_name": block.bank_name,
        "sort_code": block.sort_code or "",
        # Never the full number on a screen someone might be sharing.
        "account_number": mask(block.account_number or ""),
        "iban": mask(block.iban or ""),
        "swift_bic": block.swift_bic or "",
        "confirmed": "yes" if detail.verified_at else "no",
    }


@router.get("/bank", response_class=HTMLResponse)
async def bank_form(
    request: Request,
    identity: CurrentIdentity,
    person: CurrentPerson,
    session: DbSession,
    settings: Config,
    cipher: Annotated[Cipher, Depends(get_cipher)],
) -> HTMLResponse:
    context = _base_context(request, person, identity, settings)
    context["bank"] = _masked_bank(person, session, cipher)
    context["pending"] = _pending_proposal(person, session, cipher)
    return templates.TemplateResponse(
        request=request, name="bank.html", context=context
    )


def _pending_proposal(
    person: Person, session: Session, cipher: Cipher
) -> dict[str, str] | None:
    """A correction Robin has proposed, waiting on this person (R-BANK-07).

    It is shown alongside the current row rather than instead of it, so the
    person can see exactly what they would be agreeing to change.
    """
    pending = next(
        (
            row
            for row in repo.bank_history(session, person.id)
            if row.state is BankState.PENDING
        ),
        None,
    )
    if pending is None:
        return None
    block = decrypt_bank_block(
        {
            "account_name": pending.account_name,
            "bank_name": pending.bank_name,
            "account_number_enc": pending.account_number_enc,
            "sort_code_enc": pending.sort_code_enc,
            "iban_enc": pending.iban_enc,
            "swift_bic_enc": pending.swift_bic_enc,
        },
        person_id=person.id,
        cipher=cipher,
    )
    return {
        "id": str(pending.id),
        "account_name": block.account_name,
        "bank_name": block.bank_name,
        "sort_code": block.sort_code or "",
        "account_number": mask(block.account_number or ""),
        "iban": mask(block.iban or ""),
    }


@router.post("/bank/confirm")
async def confirm_bank(
    request: Request,
    identity: CurrentIdentity,
    person: CurrentPerson,
    session: DbSession,
    settings: Config,
    cipher: Annotated[Cipher, Depends(get_cipher)],
    detail_id: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """The owner confirms their own row, making it payable (R-WIZ-05).

    This and nothing else can put a row into ``active``. There is no admin
    route that reaches it: an admin may propose a change, never confirm one
    (CLAUDE.md §2.2, R-BANK-07).
    """
    csrf.require(settings, identity.email, csrf_token)

    if detail_id:
        target = uuid.UUID(detail_id)
    else:
        latest = next(iter(repo.bank_history(session, person.id)), None)
        if latest is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="There are no bank details on file to confirm.",
            )
        target = latest.id

    try:
        bank_changes.confirm(
            session,
            person=person,
            detail_id=target,
            cipher=cipher,
            now=_now(),
            actor_email=identity.email,
            finance_inbox=settings.finance_inbox,
        )
    except bank_changes.BankChangeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message
        ) from exc

    session.commit()
    return RedirectResponse("/additional", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/bank/change", response_class=HTMLResponse)
async def bank_change_form(
    request: Request,
    identity: CurrentIdentity,
    person: CurrentPerson,
    session: DbSession,
    settings: Config,
    cipher: Annotated[Cipher, Depends(get_cipher)],
) -> HTMLResponse:
    context = _base_context(request, person, identity, settings)
    context.update(
        {"bank": _masked_bank(person, session, cipher), "errors": [], "entry": {}}
    )
    return templates.TemplateResponse(
        request=request, name="bank_change.html", context=context
    )


# response_model=None: re-renders the form on a validation failure, redirects
# on success.
@router.post("/bank/change", response_model=None)
async def submit_bank_change(
    request: Request,
    identity: CurrentIdentity,
    person: CurrentPerson,
    session: DbSession,
    settings: Config,
    cipher: Annotated[Cipher, Depends(get_cipher)],
    account_name: Annotated[str, Form()] = "",
    bank_name: Annotated[str, Form()] = "",
    sort_code: Annotated[str, Form()] = "",
    account_number: Annotated[str, Form()] = "",
    account_number_again: Annotated[str, Form()] = "",
    iban: Annotated[str, Form()] = "",
    swift_bic: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """A person changes their own bank details (R-BANK-03, R-BANK-05).

    Creates a new row and closes the old one. The account number is typed
    twice, and both the person and Robin are emailed the last four digits of
    what changed.
    """
    csrf.require(settings, identity.email, csrf_token)
    # R-SEC-08, keyed by the verified identity rather than anything the client
    # sends, so it cannot be sidestepped by varying a header.
    bank_limiter.check(identity.email)

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
        bank_changes.change_own_details(
            session,
            person=person,
            entry=entry,
            cipher=cipher,
            now=_now(),
            actor_email=identity.email,
            finance_inbox=settings.finance_inbox,
        )
    except bank_changes.BankChangeError as exc:
        session.rollback()
        context = _base_context(request, person, identity, settings)
        context.update(
            {
                "bank": _masked_bank(person, session, cipher),
                "errors": [exc.message, *exc.errors],
                # Never echo the account number back; the sort code and names
                # are safe and save re-typing everything.
                "entry": {
                    "account_name": account_name,
                    "bank_name": bank_name,
                    "sort_code": sort_code,
                    "swift_bic": swift_bic,
                },
            }
        )
        return templates.TemplateResponse(
            request=request,
            name="bank_change.html",
            context=context,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    session.commit()
    return RedirectResponse("/bank", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/me", response_class=HTMLResponse)
async def my_details(
    request: Request,
    identity: CurrentIdentity,
    person: CurrentPerson,
    session: DbSession,
    settings: Config,
    cipher: Annotated[Cipher, Depends(get_cipher)],
) -> HTMLResponse:
    """Permanent, not a one-time step (R-WIZ-08).

    Shows the current row and the history of changes to it, as last-four pairs.
    This is how a person notices a change they did not make.
    """
    history = []
    previous: str | None = None
    for row in reversed(repo.bank_history(session, person.id)):
        current = mask(
            decrypt_bank_block(
                {
                    "account_number_enc": row.account_number_enc,
                    "account_name": row.account_name,
                    "bank_name": row.bank_name,
                },
                person_id=person.id,
                cipher=cipher,
            ).account_number
            or ""
        )
        history.append(
            {
                "at": row.created_at,
                "from": previous,
                "to": current,
                "source": row.source.value,
                "state": row.state.value,
            }
        )
        previous = current
    history.reverse()

    context = _base_context(request, person, identity, settings)
    context.update(
        {
            "bank": _masked_bank(person, session, cipher),
            "history": history,
            "invoices": repo.invoice_history(session, person.id),
        }
    )
    return templates.TemplateResponse(request=request, name="me.html", context=context)


def _content_disposition(filename: str) -> str:
    """An ``attachment`` header that survives R-PDF-15's filename.

    That filename contains em dashes, and a bare ``filename=`` parameter is
    Latin-1 only, so a browser would mangle it or drop the header. RFC 6266
    wants both forms: a plain one for old clients and a percent-encoded UTF-8
    one for everything current.
    """
    ascii_name = unicodedata.normalize("NFKD", filename.replace("—", "-"))
    ascii_name = ascii_name.encode("ascii", "ignore").decode("ascii")
    # A legal name is free text and this ends up inside a quoted header value.
    ascii_name = re.sub(r"[^A-Za-z0-9 ._-]", "", ascii_name).strip() or "invoice.pdf"
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


@router.get("/invoices/{invoice_id}/pdf")
async def download_invoice(
    request: Request,
    invoice_id: uuid.UUID,
    identity: CurrentIdentity,
    person: CurrentPerson,
    session: DbSession,
    settings: Config,
    store: Annotated[ObjectStore, Depends(get_store)],
) -> Response:
    """A person's own copy of their own invoice.

    NOT COVERED BY A REQUIREMENT. The spec's only route to the PDF is the
    emailed copy in R-SUB-08, which leaves a contractor whose mail bounced, or
    who deleted it, with no way back to their own document. Raised as a gap
    rather than worked around quietly (CLAUDE.md 8).

    Scoped to the owner inside the query (CLAUDE.md 2.1). Somebody else's
    invoice id is answered exactly like an id that never existed, so this
    cannot be used to learn that another person invoiced at all.
    """
    invoice = repo.invoice_for_person(session, person.id, invoice_id)

    if invoice is None or invoice.pdf_uri is None:
        context = _base_context(request, person, identity, settings)
        context["blocked"] = (
            # Two states, two honest answers. Telling the owner their own
            # invoice is still a draft is the point: a draft holding a number
            # is the failure mode we most want a person to notice.
            "That invoice has not been sent yet, so there is no PDF of it. "
            "Go back to the start and finish sending it."
            if invoice is not None
            else "We have no such invoice."
        )
        return templates.TemplateResponse(
            request=request, name="blocked.html", context=context, status_code=404
        )

    try:
        pdf = store.get(invoice.pdf_uri)
    except StorageError:
        # The row says there is a PDF and the bucket disagrees. That is a data
        # problem rather than a user error, so it is logged loudly and allowed
        # to become a 500 instead of a polite empty page. The invoice id is
        # enough to find it; the object URI is not written to the log.
        logger.error(
            "invoice %s is marked sent but its PDF is not in the store", invoice.id
        )
        raise

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": _content_disposition(
                invoice.pdf_uri.rsplit("/", 1)[-1]
            ),
            # It carries a full account number, so it must not sit in a proxy
            # or a shared browser cache (CLAUDE.md 2.8).
            "Cache-Control": "private, no-store",
        },
    )


# --------------------------------------------------------------------------
# R-WIZ-03 Additional
# --------------------------------------------------------------------------


@router.get("/additional", response_class=HTMLResponse)
async def additional_form(
    request: Request,
    identity: CurrentIdentity,
    person: CurrentPerson,
    session: DbSession,
    settings: Config,
) -> HTMLResponse:
    """Kudos read-only; the person adds any number of items. Empty means £0."""
    period, state = _read_state(session, person, settings)
    context = _base_context(request, person, identity, settings)
    context.update(
        {
            "state": state,
            "period": period,
            "items": [],
            "errors": [],
            "max_items": MAX_ITEMS,
        }
    )
    return templates.TemplateResponse(
        request=request, name="additional.html", context=context
    )


async def _parse_items(request: Request) -> tuple[list[ExpenseInput], list[str]]:
    """Read the repeated description/amount pairs off the form."""
    form = await request.form()
    descriptions = [str(value) for value in form.getlist("description")]
    amounts = [str(value) for value in form.getlist("amount")]
    # One per row, blank where nothing was attached. Written by the upload
    # that already happened, so the file itself never rides on this form.
    receipts = [str(value) for value in form.getlist("receipt_uri")]

    items: list[ExpenseInput] = []
    errors: list[str] = []
    while len(receipts) < len(descriptions):
        receipts.append("")

    for description, amount, receipt in zip(
        descriptions, amounts, receipts, strict=False
    ):
        description = description.strip()
        amount = amount.strip()
        if not description and not amount:
            continue  # an untouched blank row
        if not description:
            errors.append(f"An amount of {amount} has no description.")
            continue
        try:
            pence = parse_gbp_to_pence(amount)
        except MoneyError:
            errors.append(f"{description}: {amount!r} is not an amount.")
            continue
        if pence < 0:
            errors.append(f"{description}: an amount cannot be negative.")
            continue
        if pence == 0:
            # An item worth nothing is a mistake rather than a choice: it
            # prints on the invoice as a zero line and means nothing to
            # anybody reading it. Leaving the row empty is how you add none.
            errors.append(f"{description}: an amount of zero is not an expense.")
            continue
        items.append(
            ExpenseInput(
                description=description,
                amount_pence=pence,
                receipt_uri=receipt.strip() or None,
            )
        )

    if len(items) > MAX_ITEMS:
        errors.append(f"That is more than {MAX_ITEMS} items.")
    return items, errors


# --------------------------------------------------------------------------
# R-WIZ-04 Receipts
# --------------------------------------------------------------------------


@router.post("/receipts", response_class=JSONResponse)
async def upload_receipt(
    request: Request,
    identity: CurrentIdentity,
    person: CurrentPerson,
    settings: Config,
    store: Annotated[ObjectStore, Depends(get_store)],
    file: Annotated[UploadFile, File()],
) -> JSONResponse:
    """Accept one receipt, check it, and park it until the invoice exists.

    DEVIATES FROM R-WIZ-04, which says the bytes go from the browser straight
    to GCS on a signed URL and never through Cloud Run. They cannot: R-SEC-10
    requires EXIF to be stripped, and HEIC has to be converted or Robin cannot
    open half of them. Both need the bytes, so a signed-URL upload would only
    move the moment the server touches them to after they are already in the
    bucket, unchecked. At ten files of ten megabytes this saves nothing and
    costs the guarantee that nothing unvalidated is ever stored. Raised rather
    than worked around quietly (CLAUDE.md 8).

    The file lands under the pending prefix. It becomes part of an invoice only
    at submit, because ``expenses.invoice_id`` is NOT NULL (R-DATA-05) and the
    invoice does not exist yet.
    """
    csrf.require_request(settings, identity.email, request, "")

    data = await file.read()
    try:
        prepared = receipts.prepare(data, filename=file.filename or "")
    except receipts.ReceiptRejected as exc:
        # The person's own mistake, and fixable: say what is wrong plainly.
        return JSONResponse(
            {"error": exc.message}, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY
        )

    receipt_id = str(uuid.uuid4())
    name = receipts.pending_name(str(person.id), receipt_id, prepared.extension)
    uri = store.put(name, prepared.content, content_type=prepared.content_type)

    # Never the URI: R-SEC-05 and CLAUDE.md 2.8 forbid one in a log line.
    logger.info(
        "receipt %s accepted for %s (%s, %d bytes, converted=%s)",
        receipt_id,
        person.id,
        prepared.kind.value,
        len(prepared.content),
        prepared.converted,
    )
    return JSONResponse(
        {
            "uri": uri,
            "name": file.filename or "receipt",
            "converted": prepared.converted,
            "kind": prepared.kind.value,
        }
    )


# --------------------------------------------------------------------------
# R-WIZ-06 Preview
# --------------------------------------------------------------------------


@router.post("/preview", response_class=HTMLResponse)
async def preview(
    request: Request,
    identity: CurrentIdentity,
    person: CurrentPerson,
    session: DbSession,
    settings: Config,
    cipher: Annotated[Cipher, Depends(get_cipher)],
) -> HTMLResponse:
    """The invoice rendered from the same template that produces the PDF.

    What they approve here is what Robin receives (R-PDF-01).
    """
    form = await request.form()
    csrf.require(settings, identity.email, str(form.get(csrf.FIELD_NAME) or ""))

    items, errors = await _parse_items(request)
    period, state = _read_state(session, person, settings)
    context = _base_context(request, person, identity, settings)

    if errors:
        context.update(
            {
                "state": state,
                "period": period,
                "items": items,
                "errors": errors,
                "max_items": MAX_ITEMS,
            }
        )
        return templates.TemplateResponse(
            request=request,
            name="additional.html",
            context=context,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    try:
        prepared = prepare(
            session,
            person=person,
            period=period,
            now=_now(),
            settings=settings,
            expenses=items,
            kudos_zero_acknowledged=bool(form.get("kudos_zero_acknowledged")),
        )
    except SubmissionBlocked as blocked:
        context.update({"state": state, "period": period, "blocked": blocked.message})
        return templates.TemplateResponse(
            request=request, name="blocked.html", context=context
        )

    bank_block = decrypt_bank_block(
        {
            "account_name": prepared.bank.account_name,
            "bank_name": prepared.bank.bank_name,
            "account_number_enc": prepared.bank.account_number_enc,
            "sort_code_enc": prepared.bank.sort_code_enc,
            "iban_enc": prepared.bank.iban_enc,
            "swift_bic_enc": prepared.bank.swift_bic_enc,
        },
        person_id=person.id,
        cipher=cipher,
    )

    # The number this invoice would take, shown so the preview is honest.
    counter = session.get(Counter, person.id)

    invoice_context = build_context(
        number=counter.next_number if counter else 1,
        period=period,
        issued_at=_now(),
        letterhead=Letterhead(
            legal_name=person.legal_name or "",
            role=person.role or "",
            address_lines=tuple(
                line.strip()
                for line in (person.address or "").splitlines()
                if line.strip()
            ),
            phone=person.phone or "",
            contact_email=person.contact_email or "",
        ),
        bill_to_lines=settings.bill_to.as_lines(),
        bank=bank_block,
        payout_region=(person.payout_region.value if person.payout_region else "UK"),
        totals=prepared.totals,
    )

    context.update(
        {
            "state": state,
            "period": period,
            "items": items,
            "invoice": invoice_context,
            # The real invoice, rendered by the template that produces the PDF
            # (R-WIZ-06, R-PDF-01). It goes into an isolated iframe so the app's
            # own stylesheet cannot change how the invoice looks.
            "invoice_html": render_html(invoice_context),
            # The rail beside the sheet shows the same two figures the sheet
            # does, so a mismatch would be visible rather than plausible.
            "number": counter.next_number if counter else 1,
            "totals": prepared.totals,
            # R-SUB-01: the key is generated when the preview is rendered.
            "idempotency_key": str(uuid.uuid4()),
            "kudos_zero_acknowledged": bool(form.get("kudos_zero_acknowledged")),
        }
    )
    return templates.TemplateResponse(
        request=request, name="preview.html", context=context
    )


# --------------------------------------------------------------------------
# R-WIZ-07 Submit
# --------------------------------------------------------------------------


@router.post("/submit", response_class=HTMLResponse)
async def do_submit(
    request: Request,
    identity: CurrentIdentity,
    person: CurrentPerson,
    session: DbSession,
    settings: Config,
    cipher: Annotated[Cipher, Depends(get_cipher)],
    store: Annotated[ObjectStore, Depends(get_store)],
    render: Annotated[Callable[[dict], bytes], Depends(get_renderer)],
    factory: Factory,
) -> HTMLResponse:
    """One button, one idempotency key, no second number (R-SUB-01, R-SUB-02)."""
    form = await request.form()
    csrf.require(settings, identity.email, str(form.get(csrf.FIELD_NAME) or ""))
    submit_limiter.check(identity.email)  # R-SEC-08

    idempotency_key = str(
        form.get("idempotency_key") or request.headers.get("Idempotency-Key") or ""
    )
    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing idempotency key. Go back and preview again.",
        )

    items, errors = await _parse_items(request)
    now = _now()
    period = Period.containing(now)
    context = _base_context(request, person, identity, settings)

    if errors:
        context["blocked"] = " ".join(errors)
        return templates.TemplateResponse(
            request=request, name="blocked.html", context=context
        )

    person_id = person.id
    session.close()  # submit runs its own short transactions (R-SUB-03..05)

    try:
        invoice_id = submit(
            factory,
            person_id=person_id,
            period=period,
            idempotency_key=idempotency_key,
            now=now,
            settings=settings,
            cipher=cipher,
            store=store,
            expenses=items,
            kudos_zero_acknowledged=bool(form.get("kudos_zero_acknowledged")),
            render=render,
        )
    except SubmissionBlocked as blocked:
        context["blocked"] = blocked.message
        return templates.TemplateResponse(
            request=request, name="blocked.html", context=context
        )

    with factory() as fresh:
        invoice = fresh.get(Invoice, invoice_id)
        context.update({"invoice": invoice, "period": period})
        return templates.TemplateResponse(
            request=request, name="submitted.html", context=context
        )
