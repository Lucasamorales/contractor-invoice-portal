"""Query functions. The only place raw queries live (CLAUDE.md §5).

Every function that touches personal data takes the identity-derived key, never
an id supplied by a caller (R-ACC-05, R-SEC-06).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AuditLog,
    BankDetail,
    BankState,
    Counter,
    Expense,
    Fee,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    Outbox,
    OutboxStatus,
    Person,
    PersonStatus,
)


def get_person_by_email(session: Session, email: str) -> Person | None:
    """Resolve a verified IAP email to a person (R-ACC-03)."""
    return session.scalar(select(Person).where(Person.email == email.strip().lower()))


def create_pending_person(session: Session, email: str, display_name: str) -> Person:
    """R-ACC-03: an unknown but verified caller becomes a pending person.

    Pending means Robin has not set a fee yet, so they cannot invoice
    (R-WIZ-09). They are not turned away; they appear in his queue (R-ADM-07).
    """
    person = Person(
        email=email.strip().lower(),
        display_name=display_name,
        status=PersonStatus.PENDING,
    )
    session.add(person)
    session.flush()
    return person


def get_or_create_person(session: Session, email: str, display_name: str) -> Person:
    person = get_person_by_email(session, email)
    if person is not None:
        return person
    return create_pending_person(session, email, display_name)


def fee_for(session: Session, person_id: uuid.UUID, on: date) -> Fee | None:
    """The fee in force on a date (R-DATA-02).

    Effective-dated rather than current-valued, so a March invoice can still
    explain itself in December.
    """
    return session.scalar(
        select(Fee)
        .where(
            Fee.person_id == person_id,
            Fee.valid_from <= on,
            (Fee.valid_to.is_(None)) | (Fee.valid_to >= on),
        )
        .order_by(Fee.valid_from.desc())
        .limit(1)
    )


def active_bank_detail(session: Session, person_id: uuid.UUID) -> BankDetail | None:
    """The one row that may produce an invoice (R-BANK-02).

    An imported row is ``unverified`` and deliberately does not qualify:
    nothing in the spreadsheet is treated as true (R-SEED-10).
    """
    return session.scalar(
        select(BankDetail).where(
            BankDetail.person_id == person_id,
            BankDetail.state == BankState.ACTIVE,
        )
    )


def bank_history(session: Session, person_id: uuid.UUID) -> list[BankDetail]:
    """Every bank row for a person, newest first (R-WIZ-08).

    This is how somebody notices a change they did not make.
    """
    return list(
        session.scalars(
            select(BankDetail)
            .where(BankDetail.person_id == person_id)
            .order_by(BankDetail.created_at.desc())
        )
    )


def live_invoice_for_period(
    session: Session, person_id: uuid.UUID, period: str
) -> Invoice | None:
    """The non-void invoice for a period, if one exists (R-WIZ-09)."""
    return session.scalar(
        select(Invoice).where(
            Invoice.person_id == person_id,
            Invoice.period == period,
            Invoice.status != InvoiceStatus.VOID,
        )
    )


def invoice_by_idempotency_key(session: Session, key: str) -> Invoice | None:
    """R-SUB-02: a repeated submit returns the same invoice."""
    return session.scalar(select(Invoice).where(Invoice.idempotency_key == key))


def invoice_history(session: Session, person_id: uuid.UUID) -> list[Invoice]:
    return list(
        session.scalars(
            select(Invoice)
            .where(Invoice.person_id == person_id)
            .order_by(Invoice.number.desc())
        )
    )


def lock_counter(session: Session, person_id: uuid.UUID) -> Counter | None:
    """``SELECT ... FOR UPDATE`` on the counter row (R-NUM-02).

    The lock is held for the length of one short transaction. Nothing slow —
    no PDF, no GCS, no email — happens while it is held.
    """
    return session.scalar(
        select(Counter).where(Counter.person_id == person_id).with_for_update()
    )


def record_audit(
    session: Session,
    *,
    actor_email: str,
    action: str,
    entity: str,
    entity_id: str,
    before: dict | None = None,
    after: dict | None = None,
) -> AuditLog:
    """Append an audit row (R-DATA-11).

    The caller passes already-masked values. This function does not mask: a
    helper that silently redacts would make it impossible to tell, reading the
    call site, whether the masking happened.
    """
    entry = AuditLog(
        actor_email=actor_email,
        action=action,
        entity=entity,
        entity_id=entity_id,
        before=before,
        after=after,
    )
    session.add(entry)
    return entry


def queue_email(*, kind: str, payload: dict) -> Outbox:
    """Build an outbox row (R-DATA-10, R-SUB-06).

    Returned rather than added, so the caller decides which transaction it
    belongs to. Mail is queued inside the submit transaction and sent later by
    the dispatcher: a Postmark outage must not lose an invoice Postgres has
    already accepted.
    """
    return Outbox(kind=kind, payload=payload)


def invoice_lines(session: Session, invoice_id: uuid.UUID) -> list[InvoiceLine]:
    """The frozen breakdown for one invoice (R-DATA-07)."""
    return list(
        session.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice_id))
    )


# --- Admin read models (R-ADM-01 .. R-ADM-07) ------------------------------
# Every one of these is reachable only behind require_finance (R-ACC-04).


def all_people(session: Session) -> list[Person]:
    """Everyone, in a stable order. Nothing is ever deleted (R-ADM-09)."""
    return list(session.scalars(select(Person).order_by(Person.display_name)))


def fee_history(session: Session, person_id: uuid.UUID) -> list[Fee]:
    """Every fee ever set, newest first (R-ADM-02).

    Effective-dated rows, never updates, so this is the whole story of what
    someone was paid and when it changed.
    """
    return list(
        session.scalars(
            select(Fee)
            .where(Fee.person_id == person_id)
            .order_by(Fee.valid_from.desc(), Fee.created_at.desc())
        )
    )


def current_fee(session: Session, person_id: uuid.UUID) -> Fee | None:
    """The open-ended fee row, if there is one."""
    return session.scalar(
        select(Fee).where(Fee.person_id == person_id, Fee.valid_to.is_(None))
    )


def invoices_for_period(session: Session, period: str) -> list[Invoice]:
    """Every live invoice for a period (R-ADM-04, R-ADM-05)."""
    return list(
        session.scalars(
            select(Invoice)
            .where(Invoice.period == period, Invoice.status != InvoiceStatus.VOID)
            .order_by(Invoice.number)
        )
    )


def counters_for(session: Session, person_ids: list[uuid.UUID]) -> dict:
    """Counter rows keyed by person, for the go-live grid (R-ADM-06)."""
    if not person_ids:
        return {}
    rows = session.scalars(select(Counter).where(Counter.person_id.in_(person_ids)))
    return {row.person_id: row for row in rows}


def pending_bank_rows(session: Session) -> list[BankDetail]:
    """Proposed changes waiting on their owner (R-ADM-07, R-BANK-07)."""
    return list(
        session.scalars(
            select(BankDetail)
            .where(BankDetail.state == BankState.PENDING)
            .order_by(BankDetail.created_at.desc())
        )
    )


def unverified_bank_rows(session: Session) -> list[BankDetail]:
    """Imported rows nobody has confirmed yet (R-SEED-10, R-ADM-06)."""
    return list(
        session.scalars(
            select(BankDetail).where(BankDetail.state == BankState.UNVERIFIED)
        )
    )


def expenses_for_invoices(session: Session, invoice_ids: list[uuid.UUID]) -> list:
    """Expense rows across a period's invoices, for the over-threshold flag."""
    if not invoice_ids:
        return []
    return list(
        session.scalars(select(Expense).where(Expense.invoice_id.in_(invoice_ids)))
    )


def stuck_drafts(session: Session, *, older_than: datetime) -> list[Invoice]:
    """Invoices that took a number but never finished (R-SUB-09).

    A draft is normal for a few seconds while the PDF renders. One that is
    still a draft an hour later means the render or the upload failed and
    nobody retried, and the person is very likely to assume it was sent.
    """
    return list(
        session.scalars(
            select(Invoice)
            .where(
                Invoice.status == InvoiceStatus.DRAFT,
                Invoice.created_at < older_than,
            )
            .order_by(Invoice.created_at)
        )
    )


def failed_outbox(session: Session) -> list[Outbox]:
    """Mail that gave up (R-JOB-04).

    Postgres accepted the invoice and Postmark never delivered it. Nothing
    else in the system would notice.
    """
    return list(
        session.scalars(
            select(Outbox)
            .where(Outbox.status == OutboxStatus.FAILED)
            .order_by(Outbox.created_at)
        )
    )
