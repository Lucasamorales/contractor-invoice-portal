"""The submit transaction (R-SUB-01 .. R-SUB-10).

The shape of this module is the specification's, not a convenience: three
phases, two short transactions, and nothing slow inside either of them.

    1. Lock the counter, allocate the number, write the draft and its frozen
       breakdown, release the lock (R-SUB-03). Nothing here talks to GCS,
       WeasyPrint or Postmark.
    2. Render and upload, outside any transaction (R-SUB-04).
    3. Mark it submitted and queue the email (R-SUB-05).

If phase 2 fails, the invoice stays a draft with its number still allocated,
and a retry resumes from there (R-SUB-09). Numbers are never reused, so a gap
is preferable to a duplicate (R-NUM-05).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import repo
from app.db.models import (
    BankDetail,
    BankState,
    Counter,
    Expense,
    ExpenseKind,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    KudosSnapshot,
    Person,
    PrintSnapshot,
)
from app.db.models import LineKind as DbLineKind
from app.domain.formatting import month_heading
from app.domain.invoice import InvoiceLine as DomainLine
from app.domain.invoice import InvoiceTotals, LineKind, compose, detail_line
from app.domain.numbering import advance
from app.domain.period import Period, is_late
from app.services.crypto import Cipher, field_aad
from app.services.pdf import BankBlock, Letterhead, build_context, render_pdf
from app.services.storage import ObjectStore

logger = logging.getLogger(__name__)

ENCRYPTED_FIELDS = ("account_number", "sort_code", "iban", "swift_bic")

OUTBOX_INVOICE_SUBMITTED = "invoice_submitted"


class SubmissionBlocked(Exception):
    """Submission is not possible, with the exact copy the wizard shows.

    Every reason here is one a person can act on, which is why R-WIZ-09
    specifies the wording rather than leaving it to the implementation.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True, slots=True)
class ExpenseInput:
    """One item the person added on the Additional step (R-WIZ-03)."""

    description: str
    amount_pence: int
    kind: ExpenseKind = ExpenseKind.EXPENSE
    receipt_uri: str | None = None


@dataclass(frozen=True, slots=True)
class Prepared:
    """Everything needed to issue, gathered and checked before any write."""

    person: Person
    period: Period
    fee_pence: int
    kudos_count: int
    kudos_rate_pence: int
    bank: BankDetail
    totals: InvoiceTotals


def profile_is_complete(person: Person) -> bool:
    """All five letterhead fields present (R-WIZ-02, R-PDF-02)."""
    return all(
        (
            person.legal_name,
            person.role,
            person.address,
            person.phone,
            person.contact_email,
        )
    )


def prepare(
    session: Session,
    *,
    person: Person,
    period: Period,
    now: datetime,
    settings: Settings,
    expenses: Sequence[ExpenseInput] = (),
    kudos_zero_acknowledged: bool = False,
) -> Prepared:
    """Gather the figures and refuse if anything is missing (R-WIZ-09).

    Raises ``SubmissionBlocked`` rather than returning a partial result: there
    is no such thing as a nearly-valid invoice.
    """
    if not profile_is_complete(person):
        raise SubmissionBlocked(
            "profile_incomplete",
            "We need a few details for your invoice letterhead.",
        )

    fee = repo.fee_for(session, person.id, on=now.date())
    if fee is None:
        raise SubmissionBlocked(
            "no_fee", "Robin hasn't set your fee yet — he's been notified."
        )

    bank = repo.active_bank_detail(session, person.id)
    if bank is None or bank.state is not BankState.ACTIVE or bank.verified_at is None:
        # R-BANK-02: an imported row is unverified and cannot produce an
        # invoice, however clean it looked in the spreadsheet (R-SEED-10).
        raise SubmissionBlocked("bank_unconfirmed", "Confirm your bank details first.")

    snapshot = session.get(KudosSnapshot, (person.id, period.key))
    if snapshot is None and not kudos_zero_acknowledged:
        # R-KUD-06: never silently invoice £0 of kudos.
        raise SubmissionBlocked(
            "kudos_unavailable",
            "We couldn't read your kudos count. Confirm you want to invoice "
            "without it, or try again shortly.",
        )

    kudos_count = snapshot.kudos_count if snapshot else 0
    kudos_rate = snapshot.rate_pence if snapshot else settings.kudos_rate_pence

    totals = compose(
        fee_pence=fee.fee_pence,
        kudos_count=kudos_count,
        kudos_rate_pence=kudos_rate,
        expenses=[
            (item.description, item.amount_pence)
            for item in expenses
            if item.kind is ExpenseKind.EXPENSE
        ],
        other=[
            (item.description, item.amount_pence)
            for item in expenses
            if item.kind is ExpenseKind.OTHER
        ],
    )

    return Prepared(
        person=person,
        period=period,
        fee_pence=fee.fee_pence,
        kudos_count=kudos_count,
        kudos_rate_pence=kudos_rate,
        bank=bank,
        totals=totals,
    )


def _letterhead(person: Person) -> Letterhead:
    return Letterhead(
        legal_name=person.legal_name or "",
        role=person.role or "",
        address_lines=tuple(
            line.strip() for line in (person.address or "").splitlines() if line.strip()
        ),
        phone=person.phone or "",
        contact_email=person.contact_email or "",
    )


def _bank_block_ciphertext(bank: BankDetail) -> dict[str, object]:
    """The bank block as stored in ``print_snapshot`` (R-DATA-09).

    R-DATA-09 wants exactly what was printed, so the invoice can be re-rendered
    identically and Robin can always answer which account was paid. CLAUDE.md
    §2.5 says the database never holds these four fields in the clear.

    Both hold here: the snapshot carries the *ciphertext*, copied verbatim from
    the bank row. Re-rendering decrypts it exactly as the original render did,
    and a breach of the database still yields nothing payable.
    """
    return {
        "bank_detail_id": str(bank.id),
        "encryption_key_id": bank.encryption_key_id,
        "account_name": bank.account_name,
        "bank_name": bank.bank_name,
        "account_number_enc": bank.account_number_enc,
        "sort_code_enc": bank.sort_code_enc,
        "iban_enc": bank.iban_enc,
        "swift_bic_enc": bank.swift_bic_enc,
    }


def decrypt_bank_block(
    block: dict[str, object], *, person_id: uuid.UUID, cipher: Cipher
) -> BankBlock:
    """Turn a stored snapshot back into printable values.

    The plaintext exists only for the length of a render and is never logged
    (R-SEC-05).
    """
    values: dict[str, str | None] = {}
    for name in ENCRYPTED_FIELDS:
        stored = block.get(f"{name}_enc")
        values[name] = (
            cipher.decrypt(str(stored), aad=field_aad(str(person_id), name))
            if stored
            else None
        )
    return BankBlock(
        account_name=str(block.get("account_name") or ""),
        bank_name=str(block.get("bank_name") or ""),
        **values,  # type: ignore[arg-type]
    )


def allocate_draft(
    session: Session,
    prepared: Prepared,
    *,
    idempotency_key: str,
    now: datetime,
    settings: Settings,
    expenses: Sequence[ExpenseInput] = (),
) -> Invoice:
    """Phase 1 (R-SUB-03). Must be called inside a transaction.

    The counter row is locked for the duration (R-NUM-02). Nothing slow happens
    while the lock is held, so two people submitting at the same second wait on
    each other for microseconds, not for a PDF render.
    """
    counter = repo.lock_counter(session, prepared.person.id)
    if counter is None:
        # Seeded for everyone in the spreadsheet (R-SEED-09); a person created
        # at first login starts at 1.
        counter = Counter(person_id=prepared.person.id, next_number=1)
        session.add(counter)
        session.flush()

    number = counter.next_number

    invoice = Invoice(
        person_id=prepared.person.id,
        period=prepared.period.key,
        number=number,
        status=InvoiceStatus.DRAFT,
        is_late=is_late(now),
        remuneration_pence=prepared.totals.remuneration_pence,
        additional_pence=prepared.totals.additional_pence,
        total_pence=prepared.totals.total_pence,
        detail_note=detail_line(prepared.totals) or None,
        currency="GBP",
        idempotency_key=idempotency_key,
    )
    session.add(invoice)
    session.flush()

    # R-DATA-07: the breakdown, frozen. Whatever the fee table or the kudos
    # sheet say later, this is what the invoice meant.
    for line in prepared.totals.lines:
        session.add(
            InvoiceLine(
                invoice_id=invoice.id,
                kind=DbLineKind(line.kind.value),
                label=line.label,
                qty=line.qty,
                unit_pence=line.unit_pence,
                amount_pence=line.amount_pence,
            )
        )

    for item in expenses:
        session.add(
            Expense(
                invoice_id=invoice.id,
                kind=item.kind,
                description=item.description,
                amount_pence=item.amount_pence,
                receipt_uri=item.receipt_uri,
            )
        )

    session.add(
        PrintSnapshot(
            invoice_id=invoice.id,
            letterhead=_letterhead(prepared.person).as_dict(),
            bill_to={
                "version": settings.bill_to.version,
                "lines": list(settings.bill_to.as_lines()),
            },
            bank_block=_bank_block_ciphertext(prepared.bank),
        )
    )

    counter.next_number = advance(number)
    return invoice


def render_and_store(
    session: Session,
    invoice: Invoice,
    *,
    cipher: Cipher,
    store: ObjectStore,
    render: Callable[[dict], bytes] = render_pdf,
) -> str:
    """Phase 2 (R-SUB-04). Outside any transaction.

    Everything is read from the print snapshot rather than from live rows, so
    a re-render months later produces the same document.
    """
    snapshot = session.get(PrintSnapshot, invoice.id)
    if snapshot is None:
        raise RuntimeError(f"invoice {invoice.id} has no print snapshot")

    person = session.get(Person, invoice.person_id)
    if person is None:
        raise RuntimeError(f"invoice {invoice.id} has no person")

    period = Period.parse(invoice.period)
    letterhead = Letterhead(
        legal_name=str(snapshot.letterhead["legal_name"]),
        role=str(snapshot.letterhead["role"]),
        address_lines=tuple(snapshot.letterhead["address_lines"]),
        phone=str(snapshot.letterhead["phone"]),
        contact_email=str(snapshot.letterhead["contact_email"]),
    )
    bank = decrypt_bank_block(
        snapshot.bank_block, person_id=invoice.person_id, cipher=cipher
    )

    context = build_context(
        number=invoice.number,
        period=period,
        issued_at=invoice.issued_at or datetime.now(UTC),
        letterhead=letterhead,
        bill_to_lines=tuple(snapshot.bill_to["lines"]),
        bank=bank,
        payout_region=(person.payout_region.value if person.payout_region else "UK"),
        totals=_totals_from_rows(session, invoice),
    )
    pdf = render(context)

    name = (
        f"invoices/{period.key}/"
        f"{period.key} — {letterhead.legal_name} — {invoice.number}.pdf"
    )
    return store.put(name, pdf, content_type="application/pdf")


def _totals_from_rows(session: Session, invoice: Invoice) -> InvoiceTotals:
    """Rebuild the printed figures from the frozen rows, never from a live fee.

    The breakdown comes from ``invoice_lines`` (R-DATA-07) so the R-PDF-09
    detail line reads the same on a re-render as it did on the day, even if the
    fee, the kudos rate or an expense has changed since.
    """
    return InvoiceTotals(
        remuneration_pence=invoice.remuneration_pence,
        additional_pence=invoice.additional_pence,
        total_pence=invoice.total_pence,
        lines=tuple(
            DomainLine(
                kind=LineKind(row.kind.value),
                label=row.label,
                qty=row.qty,
                unit_pence=row.unit_pence,
                amount_pence=row.amount_pence,
            )
            for row in repo.invoice_lines(session, invoice.id)
        ),
    )


def finalise(
    session: Session,
    invoice: Invoice,
    *,
    pdf_uri: str,
    now: datetime,
    settings: Settings,
) -> None:
    """Phase 3 (R-SUB-05). Must be called inside a transaction.

    The email is queued, never sent here: a Postmark outage must not lose an
    invoice Postgres has already accepted (R-SUB-06).
    """
    invoice.status = InvoiceStatus.SUBMITTED
    invoice.pdf_uri = pdf_uri
    invoice.issued_at = now
    invoice.due_at = now + timedelta(days=settings.payment_terms_days)
    invoice.is_late = is_late(now)

    person = session.get(Person, invoice.person_id)
    period = Period.parse(invoice.period)
    legal_name = (person.legal_name or person.display_name) if person else ""

    session.add(
        repo.queue_email(
            kind=OUTBOX_INVOICE_SUBMITTED,
            payload={
                "invoice_id": str(invoice.id),
                # R-SUB-07: no fee figure in the subject.
                "subject": (
                    f"Invoice {invoice.number} — {legal_name} — {period.label}"
                ),
                "to_finance": settings.finance_inbox,
                # R-SUB-08: a copy goes to the person.
                "to_person": (person.contact_email or person.email) if person else "",
                "period": period.key,
                "number": invoice.number,
                "heading": month_heading(period),
                # The body carries the breakdown regardless of what the PDF
                # collapses into "Additional" (R-SUB-07). Fees are plaintext by
                # design (R-SEC-04); no bank detail goes anywhere near this.
                "remuneration_pence": invoice.remuneration_pence,
                "additional_pence": invoice.additional_pence,
                "total_pence": invoice.total_pence,
                "detail": invoice.detail_note or "",
                "is_late": invoice.is_late,
            },
        )
    )


def _resolve_lost_race(
    factory: sessionmaker[Session],
    exc: IntegrityError,
    *,
    person_id: uuid.UUID,
    period: Period,
    idempotency_key: str,
) -> uuid.UUID:
    """Turn an expected concurrency conflict into the invoice that did win.

    Two constraints can fire here and they mean opposite things:

    ``uq_invoices_person_period_live`` and the ``idempotency_key`` unique are
    races we expect — someone double-clicked, or two tabs submitted at once.
    The right answer is the invoice that was actually created.

    ``uq_invoices_person_number`` is different. R-NUM-03 calls it the backstop,
    and if it ever fires the counter lock in R-NUM-02 has failed to do its job.
    That is a bug and it must surface as a 500, not be smoothed over.
    """
    constraint = str(getattr(exc.orig, "diag", None) and exc.orig.diag.constraint_name)
    if constraint == "uq_invoices_person_number":
        raise exc

    with factory() as session:
        winner = repo.invoice_by_idempotency_key(session, idempotency_key)
        if winner is None:
            winner = repo.live_invoice_for_period(session, person_id, period.key)
        if winner is None:
            raise exc
        logger.info(
            "submit race resolved to invoice %s for period %s",
            winner.id,
            period.key,
        )
        return winner.id


def submit(
    factory: sessionmaker[Session],
    *,
    person_id: uuid.UUID,
    period: Period,
    idempotency_key: str,
    now: datetime,
    settings: Settings,
    cipher: Cipher,
    store: ObjectStore,
    expenses: Sequence[ExpenseInput] = (),
    kudos_zero_acknowledged: bool = False,
    render: Callable[[dict], bytes] = render_pdf,
) -> uuid.UUID:
    """Issue an invoice. Returns its id.

    Idempotent on ``idempotency_key`` (R-SUB-02, R-NUM-06): a repeat returns the
    same invoice and never allocates a second number.
    """
    # --- Phase 1: allocate, in one short transaction (R-SUB-03) -------------
    try:
        with factory() as session, session.begin():
            existing = repo.invoice_by_idempotency_key(session, idempotency_key)
            if existing is not None:
                if existing.status is InvoiceStatus.SUBMITTED:
                    return existing.id
                # A draft means phase 2 or 3 failed last time. Resume it rather
                # than allocating a second number (R-SUB-09).
                invoice_id = existing.id
            else:
                person = session.get(Person, person_id)
                if person is None:
                    raise SubmissionBlocked(
                        "unknown_person", "We don't know who you are."
                    )

                live = repo.live_invoice_for_period(session, person_id, period.key)
                if live is not None:
                    # R-WIZ-09: show them the one they already have.
                    return live.id

                prepared = prepare(
                    session,
                    person=person,
                    period=period,
                    now=now,
                    settings=settings,
                    expenses=expenses,
                    kudos_zero_acknowledged=kudos_zero_acknowledged,
                )
                invoice = allocate_draft(
                    session,
                    prepared,
                    idempotency_key=idempotency_key,
                    now=now,
                    settings=settings,
                    expenses=expenses,
                )
                invoice_id = invoice.id
    except IntegrityError as exc:
        # Two submissions raced past the read above. The database settled it,
        # which is the point of having the constraint there rather than here.
        # The losing transaction rolled back whole, so its number was never
        # taken and the counter did not move.
        invoice_id = _resolve_lost_race(
            factory,
            exc,
            person_id=person_id,
            period=period,
            idempotency_key=idempotency_key,
        )

    # --- Phase 2: render and upload, no transaction held (R-SUB-04) ---------
    with factory() as session:
        invoice = session.get(Invoice, invoice_id)
        if invoice is None:
            raise RuntimeError(f"invoice {invoice_id} vanished after allocation")
        if invoice.issued_at is None:
            invoice.issued_at = now
        pdf_uri = render_and_store(
            session, invoice, cipher=cipher, store=store, render=render
        )

    # --- Phase 3: mark submitted and queue the email (R-SUB-05) -------------
    with factory() as session, session.begin():
        invoice = session.get(Invoice, invoice_id)
        if invoice is None:
            raise RuntimeError(f"invoice {invoice_id} vanished before finalising")
        if invoice.status is InvoiceStatus.SUBMITTED:
            return invoice_id  # A concurrent retry got there first.
        finalise(session, invoice, pdf_uri=pdf_uri, now=now, settings=settings)

    return invoice_id


def void(
    session: Session,
    invoice: Invoice,
    *,
    actor_email: str,
    now: datetime,
    reason: str,
) -> None:
    """Void an invoice (R-SUB-10, CLAUDE.md §2.3).

    Corrections are void and reissue. A submitted invoice's figures are never
    updated, and voiding does not release the number (R-NUM-05) — the reissue
    takes the next one and the gap stays.
    """
    if invoice.status is InvoiceStatus.VOID:
        return
    before = {"status": invoice.status.value, "number": invoice.number}
    invoice.status = InvoiceStatus.VOID
    invoice.voided_at = now
    repo.record_audit(
        session,
        actor_email=actor_email,
        action="invoice.void",
        entity="invoices",
        entity_id=str(invoice.id),
        before=before,
        after={"status": "void", "reason": reason},
    )
