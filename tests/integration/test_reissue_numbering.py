"""A voided invoice gives its number back (R-NUM-05).

September's first cycle produced four voids and three permanent gaps — 34, 32
and 47 — each of which somebody would have had to explain to an accountant.
The rule is now: the next number is the lowest one no live invoice holds.

These tests hold the two halves that must never come apart — the number does
come back, and two *live* invoices still can never share one — and the things
that had to move with it: the replacement must not overwrite the document it
replaces, and somebody has to be told.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from core.config import Settings
from core.services.storage import LocalObjectStore
from modules.invoices.db import repo
from modules.invoices.db.models import Invoice, InvoiceStatus, Outbox
from modules.invoices.domain.period import Period
from modules.invoices.services.invoicing import OUTBOX_INVOICE_VOIDED, submit, void
from tests.integration.conftest import IN_WINDOW, ready_person

pytestmark = pytest.mark.integration

PERIOD = Period.parse("2026-09")
ROBIN = "robin@example.com"


def send(factory, settings, cipher, store, fake_pdf, person_id) -> uuid.UUID:
    return submit(
        factory,
        person_id=person_id,
        period=PERIOD,
        idempotency_key=str(uuid.uuid4()),
        now=IN_WINDOW,
        settings=settings,
        cipher=cipher,
        store=store,
        render=fake_pdf,
    )


def cancel(factory, settings, invoice_id, *, reason="wrong figures") -> None:
    with factory() as session, session.begin():
        void(
            session,
            session.get(Invoice, invoice_id),
            actor_email=ROBIN,
            now=IN_WINDOW,
            reason=reason,
            settings=settings,
        )


def numbers(session, person_id) -> list[tuple[int, str]]:
    rows = session.scalars(
        select(Invoice)
        .where(Invoice.person_id == person_id)
        .order_by(Invoice.created_at)
    ).all()
    return [(row.number, row.status.value) for row in rows]


def test_a_cancelled_invoice_gives_its_number_back(
    factory, settings, cipher, store, fake_pdf
) -> None:
    """The whole point: void 10 and the replacement is 10, not 11."""
    person_id = ready_person(factory, cipher, next_number=10, kudos=0)
    first = send(factory, settings, cipher, store, fake_pdf, person_id)
    cancel(factory, settings, first)
    second = send(factory, settings, cipher, store, fake_pdf, person_id)

    with factory() as session:
        assert numbers(session, person_id) == [(10, "void"), (10, "submitted")]
        assert session.get(Invoice, first).replaced_by_id == second


def test_the_counter_walks_back_past_an_earlier_void_too(
    factory, settings, cipher, store, fake_pdf
) -> None:
    """34 void, 35 live, then 35 cancelled: the next one is 34, not 35.

    Eze's case exactly. Cancelling the replacement has to give back both
    numbers in order, or he fills the second gap and keeps the first.
    """
    person_id = ready_person(factory, cipher, next_number=34, kudos=0)
    first = send(factory, settings, cipher, store, fake_pdf, person_id)

    # Cancel it the way it happened before the rule changed: the number stayed
    # spent, so the replacement took 35.
    with factory() as session, session.begin():
        invoice = session.get(Invoice, first)
        invoice.status = InvoiceStatus.VOID
        invoice.voided_at = IN_WINDOW
    second = send(factory, settings, cipher, store, fake_pdf, person_id)
    with factory() as session:
        assert session.get(Invoice, second).number == 35

    cancel(factory, settings, second, reason="reissued to include a late kudo")
    third = send(factory, settings, cipher, store, fake_pdf, person_id)

    with factory() as session:
        assert session.get(Invoice, third).number == 34


def test_two_live_invoices_can_never_share_a_number(
    factory, settings, cipher, store, fake_pdf
) -> None:
    """The relaxed index still forbids the thing that actually matters."""
    person_id = ready_person(factory, cipher, next_number=10, kudos=0)
    send(factory, settings, cipher, store, fake_pdf, person_id)

    with factory() as session:
        live = session.scalar(
            select(func.count())
            .select_from(Invoice)
            .where(
                Invoice.person_id == person_id,
                Invoice.status != InvoiceStatus.VOID,
                Invoice.number == 10,
            )
        )
        assert live == 1
        assert repo.live_invoice_numbers(session, person_id) == {10}


def test_the_number_never_falls_below_where_they_started(
    factory, settings, cipher, store, fake_pdf
) -> None:
    """Alexandra's 63 was set by hand after a stale spreadsheet. A void must
    not walk the sequence back into invoices issued out of Word."""
    person_id = ready_person(factory, cipher, next_number=63, kudos=0)
    first = send(factory, settings, cipher, store, fake_pdf, person_id)
    cancel(factory, settings, first)

    with factory() as session:
        assert repo.lock_counter(session, person_id).next_number == 63


def test_cancelling_tells_the_person_and_accounts(
    factory, settings: Settings, cipher, store, fake_pdf
) -> None:
    """R-SUB-11. The gap in the numbering used to be the signal; now this is."""
    person_id = ready_person(factory, cipher, next_number=10, kudos=0)
    first = send(factory, settings, cipher, store, fake_pdf, person_id)
    cancel(factory, settings, first, reason="bank details changed")

    with factory() as session:
        row = session.scalar(select(Outbox).where(Outbox.kind == OUTBOX_INVOICE_VOIDED))
        assert row is not None
        assert row.payload["number"] == 10
        assert row.payload["reason"] == "bank details changed"
        assert row.payload["to_person"] == "jane@example.com"
        assert row.payload["to_finance"] == settings.finance_inbox
        assert "Cancelled: invoice 10" in row.payload["subject"]
        # No figure in a void notice. It is not the invoice email and does not
        # inherit that email's exception (CLAUDE.md §2.8).
        assert "total_pence" not in row.payload


def test_the_replacement_says_it_is_a_resend(
    factory, settings, cipher, store, fake_pdf
) -> None:
    person_id = ready_person(factory, cipher, next_number=10, kudos=0)
    first = send(factory, settings, cipher, store, fake_pdf, person_id)
    cancel(factory, settings, first)
    send(factory, settings, cipher, store, fake_pdf, person_id)

    with factory() as session:
        subjects = [
            row.payload["subject"]
            for row in session.scalars(
                select(Outbox).where(Outbox.kind == "invoice_submitted")
            ).all()
        ]
    assert sorted("(resent)" in subject for subject in subjects) == [False, True]


def test_the_replacement_does_not_overwrite_what_was_sent(
    factory, settings, cipher, store: LocalObjectStore, fake_pdf
) -> None:
    """Both documents have to survive: the void row still points at the first,
    and answering "what did we send on the day" with the replacement would be
    worse than the gap this change removes."""
    person_id = ready_person(factory, cipher, next_number=10, kudos=0)
    first = send(factory, settings, cipher, store, fake_pdf, person_id)
    cancel(factory, settings, first)
    second = send(factory, settings, cipher, store, fake_pdf, person_id)

    with factory() as session:
        one = session.get(Invoice, first)
        two = session.get(Invoice, second)
        assert one.pdf_uri != two.pdf_uri
        assert store.exists(one.pdf_uri)
        assert store.exists(two.pdf_uri)


def test_the_saved_file_is_still_named_after_the_invoice(
    factory, settings, cipher, store, fake_pdf
) -> None:
    """The object name carries the invoice id now; a person's download must
    not (R-PDF-15)."""
    from modules.invoices.services.invoicing import download_name

    person_id = ready_person(factory, cipher, next_number=10, kudos=0)
    invoice_id = send(factory, settings, cipher, store, fake_pdf, person_id)

    with factory() as session:
        name = download_name(session, session.get(Invoice, invoice_id))
    assert name.startswith("September 2026 — Jane Smith — £")
    assert str(invoice_id) not in name
