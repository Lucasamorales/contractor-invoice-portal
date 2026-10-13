"""Putting one staging person back to "never logged in" (app/scripts/reset_person.py).

It deletes invoices, which nothing else in the portal is allowed to do, so the
things worth proving are that it removes everything belonging to that person in
an order the foreign keys accept, that it leaves everybody else exactly as they
were, that a dry run writes nothing, and that production refuses it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.db.models import (
    AuditLog,
    BankDetail,
    Counter,
    Fee,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    KudosAlias,
    KudosEvent,
    LineKind,
    Outbox,
    Person,
)
from app.scripts import reset_person
from tests.integration.conftest import give_bank, give_counter, give_fee, make_person

pytestmark = pytest.mark.integration

TARGET = "jane@example.com"
BYSTANDER = "ben@example.com"


def _invoice(session, person: Person, number: int) -> Invoice:
    invoice = Invoice(
        person_id=person.id,
        period="2026-08",
        number=number,
        status=InvoiceStatus.SUBMITTED,
        remuneration_pence=250000,
        additional_pence=0,
        total_pence=250000,
        idempotency_key=f"{person.id}:2026-08:{number}",
        issued_at=datetime.now(UTC),
    )
    session.add(invoice)
    session.flush()
    session.add(
        InvoiceLine(
            invoice_id=invoice.id,
            kind=LineKind.FEE,
            label="Monthly fee",
            qty=1,
            unit_pence=250000,
            amount_pence=250000,
        )
    )
    session.add(Outbox(kind="invoice", payload={"invoice_id": str(invoice.id)}))
    session.add(
        AuditLog(
            actor_email=person.email,
            action="invoice.submitted",
            entity="invoice",
            entity_id=str(invoice.id),
        )
    )
    session.flush()
    return invoice


def _everything_for(session, cipher, email: str) -> uuid.UUID:
    """A person with one of each thing the reset has to remove."""
    person = make_person(session, email=email)
    give_fee(session, person)
    give_bank(session, person, cipher)
    give_counter(session, person, 9)
    invoice = _invoice(session, person, 8)
    session.add(
        KudosEvent(
            event_key=f"key-{email}",
            person_id=person.id,
            receiver_name="Jane Smith",
            giver_name="Ben Branch",
            awarded_on=date(2026, 8, 3),
            claimed_by_invoice_id=invoice.id,
        )
    )
    session.add(
        KudosAlias(
            normalised_name=f"alias {email}",
            display_name="Jane Smith",
            person_id=person.id,
            confirmed_by="accounts@example.com",
        )
    )
    session.add(
        AuditLog(
            actor_email="accounts@example.com",
            action="fee.set",
            entity="person",
            entity_id=str(person.id),
        )
    )
    session.flush()
    return person.id


def _count(session, model, **where) -> int:
    query = select(func.count()).select_from(model)
    for column, value in where.items():
        query = query.where(getattr(model, column) == value)
    return session.scalar(query)


@pytest.fixture
def seeded(factory, cipher) -> tuple[uuid.UUID, uuid.UUID]:
    with factory() as session, session.begin():
        return (
            _everything_for(session, cipher, TARGET),
            _everything_for(session, cipher, BYSTANDER),
        )


def test_the_plan_lists_every_table_in_deletion_order(factory, seeded) -> None:
    target, _ = seeded
    with factory() as session:
        steps = reset_person.plan(session, session.get(Person, target))
    assert steps == [
        ("invoice_lines", 1),
        ("expenses", 0),
        ("print_snapshots", 0),
        ("outbox", 1),
        ("kudos_events (claim released)", 1),
        ("kudos_awards", 0),
        ("kudos_snapshots", 0),
        ("kudos_aliases", 1),
        ("invoices", 1),
        ("counters", 1),
        ("bank_details", 1),
        ("fees", 1),
        ("audit_log", 2),
        ("person", 1),
    ]


def test_someone_who_never_invoiced_has_no_invoice_steps(factory) -> None:
    with factory() as session, session.begin():
        person = make_person(session, email="new@example.com", complete_profile=False)
        names = [name for name, _ in reset_person.plan(session, person)]
    assert "invoice_lines" not in names
    assert names[-1] == "person"


def test_applying_removes_the_person_and_everything_of_theirs(factory, seeded) -> None:
    target, _ = seeded
    with factory() as session, session.begin():
        reset_person.apply(session, session.get(Person, target))

    with factory() as session:
        assert session.get(Person, target) is None
        for model in (Invoice, Counter, BankDetail, Fee, KudosAlias):
            assert _count(session, model, person_id=target) == 0, model.__name__
        assert _count(session, AuditLog, entity_id=str(target)) == 0


def test_a_kudo_is_released_rather_than_deleted(factory, seeded) -> None:
    """The kudo happened; only the claim on it belonged to the person removed."""
    target, _ = seeded
    with factory() as session, session.begin():
        reset_person.apply(session, session.get(Person, target))

    with factory() as session:
        event = session.scalar(
            select(KudosEvent).where(KudosEvent.event_key == f"key-{TARGET}")
        )
        assert event is not None
        assert event.person_id is None
        assert event.claimed_by_invoice_id is None


def test_nobody_else_loses_anything(factory, seeded) -> None:
    target, bystander = seeded
    with factory() as session, session.begin():
        reset_person.apply(session, session.get(Person, target))

    with factory() as session:
        assert session.get(Person, bystander) is not None
        for model in (Invoice, Counter, BankDetail, Fee, KudosAlias, KudosEvent):
            assert _count(session, model, person_id=bystander) == 1, model.__name__
        invoice = session.scalar(select(Invoice).where(Invoice.person_id == bystander))
        assert _count(session, InvoiceLine, invoice_id=invoice.id) == 1
        assert _count(session, AuditLog, entity_id=str(invoice.id)) == 1
        outbox = session.scalars(select(Outbox)).all()
        assert [row.payload["invoice_id"] for row in outbox] == [str(invoice.id)]


def _run(monkeypatch, factory, settings: Settings, *argv: str) -> int:
    monkeypatch.setattr(reset_person, "get_settings", lambda: settings)
    monkeypatch.setattr(reset_person, "get_sessionmaker", lambda: factory)
    return reset_person.main(list(argv))


STAGING = Settings(_env_file=None, environment="staging")


def test_a_dry_run_prints_the_plan_and_writes_nothing(
    monkeypatch, factory, seeded, capsys
) -> None:
    target, _ = seeded
    assert _run(monkeypatch, factory, STAGING, "--email", TARGET) == 0
    out = capsys.readouterr().out
    assert f"{TARGET} in staging" in out
    assert "Dry run. Nothing written." in out
    with factory() as session:
        assert session.get(Person, target) is not None
        assert _count(session, Invoice, person_id=target) == 1


def test_commit_applies_it(monkeypatch, factory, seeded, capsys) -> None:
    target, bystander = seeded
    assert _run(monkeypatch, factory, STAGING, "--email", TARGET, "--commit") == 0
    assert "Gone." in capsys.readouterr().out
    with factory() as session:
        assert session.get(Person, target) is None
        assert session.get(Person, bystander) is not None


def test_an_unknown_email_is_reported_and_nothing_changes(
    monkeypatch, factory, seeded, capsys
) -> None:
    code = _run(
        monkeypatch, factory, STAGING, "--email", "nobody@example.com", "--commit"
    )
    assert code == 1
    assert "no person with email nobody@example.com" in capsys.readouterr().err
    with factory() as session:
        assert _count(session, Person) == 2


def test_production_refuses_even_with_commit(
    monkeypatch, factory, seeded, capsys
) -> None:
    """In production a mistake is a void and a reissue, never a delete."""
    target, _ = seeded
    production = Settings(_env_file=None, environment="production")
    code = _run(monkeypatch, factory, production, "--email", TARGET, "--commit")
    assert code == 2
    assert "never a delete" in capsys.readouterr().err
    with factory() as session:
        assert session.get(Person, target) is not None
