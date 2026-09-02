"""Query functions. The only place raw queries live (CLAUDE.md §5).

Every function that touches personal data takes the identity-derived key, never
an id supplied by a caller (R-ACC-05, R-SEC-06).
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AuditLog,
    BankDetail,
    BankState,
    Counter,
    Fee,
    Invoice,
    InvoiceStatus,
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
