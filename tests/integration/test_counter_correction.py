"""Correcting a counter seeded from a wrong spreadsheet number (R-NUM-05).

The real case: the spreadsheet said Alexandra's last invoice was 34 when she
had reached 62, so the portal offered her 35. The correction has to move her
counter forward without touching anybody else, and it must be impossible to
use the same tool to wind a counter back or to hand out a number twice.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, Counter, Invoice, InvoiceStatus, Person
from app.scripts.set_counter import ACTION, CorrectionRefused, correct
from tests.integration.conftest import give_counter, make_person

pytestmark = pytest.mark.integration

FLOR = "alexandra@example.com"
OTHER = "marco@example.com"
ACCOUNTS = "accounts@example.com"
REASON = "the spreadsheet's INVOICE N was out of date"


def seed(factory, *, next_number: int = 35) -> uuid.UUID:
    with factory() as session, session.begin():
        person = make_person(session, email=FLOR)
        give_counter(session, person, next_number)
        return person.id


def issued(session, person_id: uuid.UUID, number: int) -> Invoice:
    """An invoice already holding a number, so collisions can be tested."""
    invoice = Invoice(
        person_id=person_id,
        period="2026-08",
        number=number,
        status=InvoiceStatus.SUBMITTED,
        remuneration_pence=250000,
        additional_pence=0,
        total_pence=250000,
        idempotency_key=f"{person_id}:2026-08:{number}",
        issued_at=datetime.now(UTC),
    )
    session.add(invoice)
    session.flush()
    return invoice


def test_moves_the_counter_forward_and_records_who_and_why(factory):
    person_id = seed(factory)

    with factory() as session, session.begin():
        correction = correct(
            session, email=FLOR, last_issued=62, actor=ACCOUNTS, reason=REASON
        )
        assert correction.before == 35
        assert correction.after == 63

    with factory() as session:
        assert session.get(Counter, person_id).next_number == 63
        entry = session.scalar(select(AuditLog).where(AuditLog.action == ACTION))
        assert entry.actor_email == ACCOUNTS
        assert entry.entity_id == str(person_id)
        assert entry.before == {"next_number": 35}
        assert entry.after["next_number"] == 63
        assert entry.after["reason"] == REASON


def test_nobody_else_is_touched(factory):
    seed(factory)
    with factory() as session, session.begin():
        other = make_person(session, email=OTHER)
        give_counter(session, other, 7)
        # The whole point of this tool rather than a re-import: everyone else
        # keeps their counter *and* their confirmed profile.
        other.profile_confirmed_at = datetime.now(UTC)
        other_id = other.id

    with factory() as session, session.begin():
        correct(session, email=FLOR, last_issued=62, actor=ACCOUNTS, reason=REASON)

    with factory() as session:
        assert session.get(Counter, other_id).next_number == 7
        assert session.get(Person, other_id).profile_confirmed_at is not None


def test_refuses_to_wind_a_counter_back(factory):
    seed(factory, next_number=70)

    with (
        factory() as session,
        session.begin(),
        pytest.raises(CorrectionRefused, match="only ever moves forward"),
    ):
        correct(session, email=FLOR, last_issued=62, actor=ACCOUNTS, reason=REASON)

    with factory() as session:
        assert session.scalar(select(Counter)).next_number == 70
        assert session.scalars(select(AuditLog)).all() == []


def test_refuses_a_number_this_person_has_already_used(factory):
    person_id = seed(factory)
    with factory() as session, session.begin():
        # A number can be spent without the counter proving it: this invoice
        # was voided, and R-NUM-05 does not give its number back.
        invoice = issued(session, person_id, 63)
        invoice.status = InvoiceStatus.VOID
        invoice.voided_at = datetime.now(UTC)

    with (
        factory() as session,
        session.begin(),
        pytest.raises(CorrectionRefused, match="already spent"),
    ):
        correct(session, email=FLOR, last_issued=62, actor=ACCOUNTS, reason=REASON)


def test_refuses_an_unknown_person(factory):
    with (
        factory() as session,
        session.begin(),
        pytest.raises(CorrectionRefused, match="no person"),
    ):
        correct(
            session,
            email="nobody@example.com",
            last_issued=62,
            actor=ACCOUNTS,
            reason=REASON,
        )


def test_creates_the_row_for_somebody_who_has_never_invoiced(factory):
    with factory() as session, session.begin():
        person = make_person(session, email=FLOR)
        person_id = person.id

    with factory() as session, session.begin():
        correction = correct(
            session, email=FLOR, last_issued=62, actor=ACCOUNTS, reason=REASON
        )
        assert correction.created is True

    with factory() as session:
        assert session.get(Counter, person_id).next_number == 63
