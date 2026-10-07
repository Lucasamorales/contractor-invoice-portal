"""Put one person in staging back to how they were before they ever logged in.

    uv run python -m app.scripts.reset_person --email someone@example.com
    uv run python -m app.scripts.reset_person --email ... --commit

For testing only, and it **refuses to run in production**. There, an invoice is
immutable and corrections are a void and a reissue (CLAUDE.md §2.3): the number
stays used, the history stays readable, and nothing is ever deleted. That rule
is the reason the portal can always answer what it paid and why, and no test
convenience is worth a script that could undo it.

Dry run by default. It prints exactly what it would remove, in the order it
would remove them, and writes nothing until ``--commit``.

Everything is deleted rather than the person being left with a wiped profile,
because the point is to test the first-login path: the row is recreated on
their next visit and they arrive with no fee, no bank details and no counter,
exactly as a real person does on day one.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from modules.invoices.config import Environment, Settings, get_settings
from modules.invoices.db.models import (
    AuditLog,
    BankDetail,
    Counter,
    Expense,
    Fee,
    Invoice,
    InvoiceLine,
    KudosAlias,
    KudosAward,
    KudosEvent,
    KudosSnapshot,
    Outbox,
    Person,
    PrintSnapshot,
)
from modules.invoices.db.session import get_sessionmaker


class ResetRefused(RuntimeError):
    """Refusing to touch something that must not be touched."""


def refuse_production(settings: Settings) -> None:
    if settings.environment is Environment.PRODUCTION:
        raise ResetRefused(
            "This deletes invoices. In production a mistake is a void and a "
            "reissue (CLAUDE.md 2.3), never a delete."
        )


def plan(session: Session, person: Person) -> list[tuple[str, int]]:
    """What would go, in the order it has to go.

    Children before parents: the foreign keys are ON DELETE RESTRICT on
    purpose, so a wrong order fails loudly rather than orphaning a row.
    """
    invoices = session.scalars(
        select(Invoice).where(Invoice.person_id == person.id)
    ).all()
    invoice_ids = [invoice.id for invoice in invoices]

    def count(model: type, column: Any, values: list | None = None) -> int:
        query = select(model)
        query = (
            query.where(column.in_(values))
            if values is not None
            else query.where(column == person.id)
        )
        return len(session.scalars(query).all())

    steps: list[tuple[str, int]] = []
    if invoice_ids:
        steps += [
            ("invoice_lines", count(InvoiceLine, InvoiceLine.invoice_id, invoice_ids)),
            ("expenses", count(Expense, Expense.invoice_id, invoice_ids)),
            (
                "print_snapshots",
                count(PrintSnapshot, PrintSnapshot.invoice_id, invoice_ids),
            ),
            ("outbox", len(_outbox_for(session, invoice_ids))),
        ]
    steps += [
        ("kudos_events (claim released)", count(KudosEvent, KudosEvent.person_id)),
        ("kudos_awards", count(KudosAward, KudosAward.person_id)),
        ("kudos_snapshots", count(KudosSnapshot, KudosSnapshot.person_id)),
        ("kudos_aliases", count(KudosAlias, KudosAlias.person_id)),
        ("invoices", len(invoices)),
        ("counters", count(Counter, Counter.person_id)),
        ("bank_details", count(BankDetail, BankDetail.person_id)),
        ("fees", count(Fee, Fee.person_id)),
        ("audit_log", len(_audit_for(session, person, invoice_ids))),
        ("person", 1),
    ]
    return steps


def _outbox_for(session: Session, invoice_ids: list[uuid.UUID]) -> list[Outbox]:
    """Outbox rows for these invoices.

    ``outbox.payload`` is JSON with no foreign key, so this matches on the id
    inside it rather than relying on the database to cascade.
    """
    wanted = {str(i) for i in invoice_ids}
    return [
        row
        for row in session.scalars(select(Outbox)).all()
        if str((row.payload or {}).get("invoice_id")) in wanted
    ]


def _audit_for(
    session: Session, person: Person, invoice_ids: list[uuid.UUID]
) -> list[AuditLog]:
    wanted = {str(person.id)} | {str(i) for i in invoice_ids}
    return [
        row
        for row in session.scalars(select(AuditLog)).all()
        if row.entity_id in wanted
    ]


def apply(session: Session, person: Person) -> None:
    invoices = session.scalars(
        select(Invoice).where(Invoice.person_id == person.id)
    ).all()
    invoice_ids = [invoice.id for invoice in invoices]

    if invoice_ids:
        for model, column in (
            (InvoiceLine, InvoiceLine.invoice_id),
            (Expense, Expense.invoice_id),
            (PrintSnapshot, PrintSnapshot.invoice_id),
        ):
            for row in session.scalars(select(model).where(column.in_(invoice_ids))):
                session.delete(row)
        for row in _outbox_for(session, invoice_ids):
            session.delete(row)

    # Kudos events are released rather than destroyed where they came from the
    # sheet: the fixture owns them, this person only claimed them.
    for event in session.scalars(
        select(KudosEvent).where(KudosEvent.person_id == person.id)
    ):
        event.person_id = None
        event.claimed_by_invoice_id = None

    for model, column in (
        (KudosAward, KudosAward.person_id),
        (KudosSnapshot, KudosSnapshot.person_id),
        (KudosAlias, KudosAlias.person_id),
    ):
        for row in session.scalars(select(model).where(column == person.id)):
            session.delete(row)

    session.flush()

    for invoice in invoices:
        session.delete(invoice)
    for model, column in (
        (Counter, Counter.person_id),
        (BankDetail, BankDetail.person_id),
        (Fee, Fee.person_id),
    ):
        for row in session.scalars(select(model).where(column == person.id)):
            session.delete(row)
    for row in _audit_for(session, person, invoice_ids):
        session.delete(row)

    session.flush()
    session.delete(person)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args(argv)

    settings = get_settings()
    try:
        refuse_production(settings)
    except ResetRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2

    factory = get_sessionmaker()
    with factory() as session:
        person = session.scalar(select(Person).where(Person.email == args.email))
        if person is None:
            print(f"no person with email {args.email}", file=sys.stderr)
            return 1

        print(f"{args.email} in {settings.environment.value}\n")
        for name, number in plan(session, person):
            print(f"  {number:>4}  {name}")

        if not args.commit:
            print("\nDry run. Nothing written. Re-run with --commit to apply.")
            return 0

        apply(session, person)
        session.commit()
        print("\nGone. They will be recreated as a new person on their next visit.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
