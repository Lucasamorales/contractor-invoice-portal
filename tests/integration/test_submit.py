"""The submit path against a real Postgres (R-TEST-02).

Covers the full path, concurrent submits allocating exactly one number,
idempotent replay, void and reissue, and the blocks in R-WIZ-09.
"""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import (
    BankState,
    Counter,
    Expense,
    ExpenseKind,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    Outbox,
    OutboxStatus,
    PrintSnapshot,
)
from app.domain.period import Period
from app.services.crypto import Cipher
from app.services.invoicing import (
    ExpenseInput,
    SubmissionBlocked,
    decrypt_bank_block,
    submit,
    void,
)
from app.services.storage import LocalObjectStore
from tests.integration.conftest import (
    IN_WINDOW,
    LATE,
    give_bank,
    give_counter,
    give_fee,
    give_kudos,
    make_person,
    ready_person,
)

pytestmark = pytest.mark.integration

PERIOD = Period.parse("2026-09")


def do_submit(  # a shim so each test reads as the one thing it is testing
    factory, settings, cipher, store, render, person_id, **kwargs: object
):
    return submit(
        factory,
        person_id=person_id,
        period=PERIOD,
        idempotency_key=kwargs.pop("key", str(uuid.uuid4())),
        now=kwargs.pop("now", IN_WINDOW),
        settings=settings,
        cipher=cipher,
        store=store,
        render=render,
        **kwargs,
    )


# --------------------------------------------------------------------------
# The full path (R-SUB-01 .. R-SUB-05)
# --------------------------------------------------------------------------


def test_full_submit_path(
    factory: sessionmaker[Session],
    settings: Settings,
    cipher: Cipher,
    store: LocalObjectStore,
    fake_pdf,
) -> None:
    person_id = ready_person(factory, cipher, next_number=8, kudos=3)
    invoice_id = do_submit(
        factory,
        settings,
        cipher,
        store,
        fake_pdf,
        person_id,
        expenses=[ExpenseInput("travel", 3450)],
    )

    with factory() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.status is InvoiceStatus.SUBMITTED
        assert invoice.number == 8  # continues the seeded sequence (R-NUM-01)
        assert invoice.period == "2026-09"
        assert invoice.is_late is False
        assert invoice.currency == "GBP"

        # Fee 2500.00, kudos 3 x 20 = 60.00, travel 34.50.
        assert invoice.remuneration_pence == 250000
        assert invoice.additional_pence == 6000 + 3450
        assert invoice.total_pence == 250000 + 9450
        assert invoice.detail_note == "3 kudos × £20\ntravel £34.50"

        # R-PDF-10: due fourteen days after issue.
        assert (invoice.due_at - invoice.issued_at).days == 14

        # The counter moved exactly once (R-NUM-02).
        assert session.get(Counter, invoice.person_id).next_number == 9


def test_the_breakdown_is_frozen_onto_the_invoice(
    factory, settings, cipher, store, fake_pdf
) -> None:
    person_id = ready_person(factory, cipher, kudos=3)
    invoice_id = do_submit(
        factory,
        settings,
        cipher,
        store,
        fake_pdf,
        person_id,
        expenses=[ExpenseInput("travel", 3450)],
    )
    with factory() as session:
        lines = session.scalars(
            select(InvoiceLine).where(InvoiceLine.invoice_id == invoice_id)
        ).all()
        kinds = {line.kind.value: line for line in lines}
        assert set(kinds) == {"fee", "kudos", "expense"}
        # R-KUD-05: the count and the rate are both frozen, so editing the
        # sheet in November cannot rewrite October.
        assert kinds["kudos"].qty == 3
        assert kinds["kudos"].unit_pence == 2000
        assert kinds["expense"].amount_pence == 3450


def test_expenses_are_recorded_against_the_invoice(
    factory, settings, cipher, store, fake_pdf
) -> None:
    person_id = ready_person(factory, cipher, kudos=0)
    invoice_id = do_submit(
        factory,
        settings,
        cipher,
        store,
        fake_pdf,
        person_id,
        expenses=[
            ExpenseInput("travel", 3450),
            ExpenseInput("equipment", 7500, kind=ExpenseKind.OTHER),
        ],
    )
    with factory() as session:
        rows = session.scalars(
            select(Expense).where(Expense.invoice_id == invoice_id)
        ).all()
        assert {row.description for row in rows} == {"travel", "equipment"}


def test_print_snapshot_holds_ciphertext_not_plaintext(
    factory, settings, cipher, store, fake_pdf
) -> None:
    """R-DATA-09 with CLAUDE.md §2.5.

    The snapshot must reproduce the invoice exactly, and the database must
    never hold a payable number in the clear. It holds the ciphertext.
    """
    person_id = ready_person(factory, cipher)
    invoice_id = do_submit(factory, settings, cipher, store, fake_pdf, person_id)

    with factory() as session:
        snapshot = session.get(PrintSnapshot, invoice_id)
        blob = str(snapshot.bank_block)
        assert "12345678" not in blob
        assert "230801" not in blob

        # And it still renders back to the real values.
        bank = decrypt_bank_block(
            snapshot.bank_block, person_id=person_id, cipher=cipher
        )
        assert bank.account_number == "12345678"
        assert bank.sort_code == "230801"
        assert bank.account_name == "Jane Smith"


def test_letterhead_and_bill_to_are_frozen(
    factory, settings, cipher, store, fake_pdf
) -> None:
    person_id = ready_person(factory, cipher)
    invoice_id = do_submit(factory, settings, cipher, store, fake_pdf, person_id)
    with factory() as session:
        snapshot = session.get(PrintSnapshot, invoice_id)
        assert snapshot.letterhead["legal_name"] == "Jane Smith"
        assert snapshot.letterhead["address_lines"] == [
            "12 Example Street",
            "London N1 1AA",
        ]
        assert snapshot.bill_to["version"] == 1
        assert "Your Company Ltd" in snapshot.bill_to["lines"]


def test_the_pdf_is_stored_and_recorded(
    factory, settings, cipher, store, fake_pdf
) -> None:
    person_id = ready_person(factory, cipher)
    invoice_id = do_submit(factory, settings, cipher, store, fake_pdf, person_id)
    with factory() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.pdf_uri
        assert store.exists(invoice.pdf_uri)
        # R-PDF-15 / R-STO-02.
        assert "invoices/2026-09/" in invoice.pdf_uri
        assert "2026-09 — Jane Smith — 8.pdf" in invoice.pdf_uri


def test_the_email_is_queued_never_sent(
    factory, settings, cipher, store, fake_pdf
) -> None:
    """R-SUB-06: a Postmark outage must not lose an accepted invoice."""
    person_id = ready_person(factory, cipher, kudos=3)
    do_submit(
        factory,
        settings,
        cipher,
        store,
        fake_pdf,
        person_id,
        expenses=[ExpenseInput("travel", 3450)],
    )
    with factory() as session:
        row = session.scalars(select(Outbox)).one()
        assert row.status is OutboxStatus.PENDING
        assert row.attempts == 0
        payload = row.payload
        assert payload["subject"] == "Invoice 8 — Jane Smith — September 2026"
        # R-SUB-07: no fee figure in the subject.
        assert "2500" not in payload["subject"]
        assert "£" not in payload["subject"]
        # R-SUB-08: a copy goes to the person as well as to finance.
        assert payload["to_finance"] == "robin@example.com"
        assert payload["to_person"] == "jane@example.com"
        # The body carries the breakdown regardless of the PDF.
        assert payload["remuneration_pence"] == 250000
        assert payload["detail"] == "3 kudos × £20\ntravel £34.50"


def test_no_bank_detail_reaches_the_outbox(
    factory, settings, cipher, store, fake_pdf
) -> None:
    # CLAUDE.md §2.8: never email a full account number.
    person_id = ready_person(factory, cipher)
    do_submit(factory, settings, cipher, store, fake_pdf, person_id)
    with factory() as session:
        payload = str(session.scalars(select(Outbox)).one().payload)
        assert "12345678" not in payload
        assert "230801" not in payload


# --------------------------------------------------------------------------
# Idempotency (R-SUB-02, R-NUM-06)
# --------------------------------------------------------------------------


def test_replaying_the_same_key_returns_the_same_invoice(
    factory, settings, cipher, store, fake_pdf
) -> None:
    person_id = ready_person(factory, cipher)
    key = "the-same-key"
    first = do_submit(factory, settings, cipher, store, fake_pdf, person_id, key=key)
    second = do_submit(factory, settings, cipher, store, fake_pdf, person_id, key=key)

    assert first == second
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Invoice)) == 1
        assert session.get(Counter, person_id).next_number == 9  # moved once
        assert session.scalar(select(func.count()).select_from(Outbox)) == 1


def test_a_second_submission_for_the_period_returns_the_existing_invoice(
    factory, settings, cipher, store, fake_pdf
) -> None:
    # R-WIZ-09: show them the one they already have.
    person_id = ready_person(factory, cipher)
    first = do_submit(factory, settings, cipher, store, fake_pdf, person_id)
    second = do_submit(factory, settings, cipher, store, fake_pdf, person_id)
    assert first == second
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Invoice)) == 1


# --------------------------------------------------------------------------
# Concurrency (R-TEST-02, R-NUM-02, R-NUM-03)
# --------------------------------------------------------------------------


def test_concurrent_submits_allocate_exactly_one_number(
    factory, settings, cipher, store, fake_pdf
) -> None:
    """Two tabs, two idempotency keys, one instant.

    Exactly one invoice, exactly one number consumed. The counter lock
    serialises the allocation and the partial unique index refuses the second
    invoice for the period; the loser's whole transaction rolls back, so its
    number is never taken.
    """
    person_id = ready_person(factory, cipher, next_number=8)

    results: list[uuid.UUID] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def worker(key: str) -> None:
        try:
            barrier.wait(timeout=10)
            results.append(
                do_submit(
                    factory, settings, cipher, store, fake_pdf, person_id, key=key
                )
            )
        except BaseException as exc:  # noqa: BLE001 - recorded and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"key-{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == [], f"unexpected errors: {errors}"
    assert len(results) == 2
    assert results[0] == results[1], "both submissions must resolve to one invoice"

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Invoice)) == 1
        invoice = session.scalars(select(Invoice)).one()
        assert invoice.number == 8
        # Exactly one number consumed, not two.
        assert session.get(Counter, person_id).next_number == 9


def test_two_people_may_hold_the_same_number(
    factory, settings, cipher, store, fake_pdf
) -> None:
    # R-NUM-01: numbers are per person and continue their own sequence.
    a = ready_person(factory, cipher, email="a@example.com", next_number=8)
    b = ready_person(factory, cipher, email="b@example.com", next_number=8)
    do_submit(factory, settings, cipher, store, fake_pdf, a)
    do_submit(factory, settings, cipher, store, fake_pdf, b)
    with factory() as session:
        numbers = session.scalars(select(Invoice.number)).all()
        assert sorted(numbers) == [8, 8]


# --------------------------------------------------------------------------
# Void and reissue (R-SUB-10, R-NUM-05)
# --------------------------------------------------------------------------


def test_void_then_reissue_takes_the_next_number_and_leaves_a_gap(
    factory, settings, cipher, store, fake_pdf
) -> None:
    person_id = ready_person(factory, cipher, next_number=8)
    first = do_submit(factory, settings, cipher, store, fake_pdf, person_id)

    with factory() as session, session.begin():
        invoice = session.get(Invoice, first)
        void(
            session,
            invoice,
            actor_email="robin@example.com",
            now=IN_WINDOW,
            reason="wrong expense attached",
        )

    reissued = do_submit(factory, settings, cipher, store, fake_pdf, person_id)

    with factory() as session:
        old = session.get(Invoice, first)
        new = session.get(Invoice, reissued)
        assert old.status is InvoiceStatus.VOID
        assert old.voided_at is not None
        assert old.number == 8
        # R-NUM-05: voiding does not release the number.
        assert new.number == 9
        assert new.status is InvoiceStatus.SUBMITTED


def test_voiding_writes_an_audit_row(
    factory, settings, cipher, store, fake_pdf
) -> None:
    from app.db.models import AuditLog

    person_id = ready_person(factory, cipher)
    invoice_id = do_submit(factory, settings, cipher, store, fake_pdf, person_id)
    with factory() as session, session.begin():
        void(
            session,
            session.get(Invoice, invoice_id),
            actor_email="robin@example.com",
            now=IN_WINDOW,
            reason="duplicate",
        )
    with factory() as session:
        entry = session.scalars(select(AuditLog)).one()
        assert entry.action == "invoice.void"
        assert entry.actor_email == "robin@example.com"
        assert entry.after["reason"] == "duplicate"


# --------------------------------------------------------------------------
# The blocks (R-WIZ-09, R-BANK-02, R-KUD-06)
# --------------------------------------------------------------------------


def test_no_fee_blocks_with_the_specified_message(
    factory, settings, cipher, store, fake_pdf
) -> None:
    with factory() as session, session.begin():
        person = make_person(session)
        give_bank(session, person, cipher)
        give_counter(session, person)
        give_kudos(session, person, "2026-09", 0)
        person_id = person.id

    with pytest.raises(SubmissionBlocked) as caught:
        do_submit(factory, settings, cipher, store, fake_pdf, person_id)
    assert caught.value.reason == "no_fee"
    assert caught.value.message == (
        "Robin hasn't set your fee yet — he's been notified."
    )


def test_an_unverified_imported_bank_row_cannot_produce_an_invoice(
    factory, settings, cipher, store, fake_pdf
) -> None:
    """R-BANK-02 with R-SEED-10 — the reason the seed marks everything
    unverified however clean it looked."""
    with factory() as session, session.begin():
        person = make_person(session)
        give_fee(session, person)
        give_bank(session, person, cipher, state=BankState.UNVERIFIED, verified=False)
        give_counter(session, person)
        give_kudos(session, person, "2026-09", 0)
        person_id = person.id

    with pytest.raises(SubmissionBlocked) as caught:
        do_submit(factory, settings, cipher, store, fake_pdf, person_id)
    assert caught.value.reason == "bank_unconfirmed"
    assert caught.value.message == "Confirm your bank details first."


def test_an_incomplete_profile_blocks(
    factory, settings, cipher, store, fake_pdf
) -> None:
    with factory() as session, session.begin():
        person = make_person(session, complete_profile=False)
        give_fee(session, person)
        give_bank(session, person, cipher)
        give_counter(session, person)
        person_id = person.id

    with pytest.raises(SubmissionBlocked) as caught:
        do_submit(factory, settings, cipher, store, fake_pdf, person_id)
    assert caught.value.reason == "profile_incomplete"


def test_missing_kudos_blocks_unless_explicitly_acknowledged(
    factory, settings, cipher, store, fake_pdf
) -> None:
    # R-KUD-06: never silently invoice £0 of kudos.
    person_id = ready_person(factory, cipher, kudos=None)

    with pytest.raises(SubmissionBlocked) as caught:
        do_submit(factory, settings, cipher, store, fake_pdf, person_id)
    assert caught.value.reason == "kudos_unavailable"

    invoice_id = do_submit(
        factory,
        settings,
        cipher,
        store,
        fake_pdf,
        person_id,
        kudos_zero_acknowledged=True,
    )
    with factory() as session:
        assert session.get(Invoice, invoice_id).additional_pence == 0


# --------------------------------------------------------------------------
# Lateness (R-PER-04, R-PER-05)
# --------------------------------------------------------------------------


def test_a_late_submission_is_accepted_and_flagged(
    factory, settings, cipher, store, fake_pdf
) -> None:
    """R-PER-04: nobody is ever locked out. A locked door is just a phone call
    to Robin, which is the thing this system removes."""
    person_id = ready_person(factory, cipher, next_number=8)
    invoice_id = do_submit(
        factory, settings, cipher, store, fake_pdf, person_id, now=LATE
    )
    with factory() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.status is InvoiceStatus.SUBMITTED
        assert invoice.is_late is True
        # R-PER-05: a late submission still takes the next number in sequence.
        assert invoice.number == 8


# --------------------------------------------------------------------------
# Failure part-way through (R-SUB-09)
# --------------------------------------------------------------------------


def test_a_failed_render_leaves_a_draft_that_a_retry_resumes(
    factory, settings, cipher, store, fake_pdf
) -> None:
    person_id = ready_person(factory, cipher, next_number=8)

    def exploding_render(context: dict) -> bytes:
        raise RuntimeError("weasyprint fell over")

    key = "retry-me"
    with pytest.raises(RuntimeError, match="weasyprint"):
        do_submit(
            factory, settings, cipher, store, exploding_render, person_id, key=key
        )

    with factory() as session:
        invoice = session.scalars(select(Invoice)).one()
        assert invoice.status is InvoiceStatus.DRAFT
        assert invoice.number == 8  # the number stays allocated
        assert invoice.pdf_uri is None
        assert session.scalar(select(func.count()).select_from(Outbox)) == 0

    # The retry reuses the same draft rather than taking another number.
    invoice_id = do_submit(
        factory, settings, cipher, store, fake_pdf, person_id, key=key
    )
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Invoice)) == 1
        invoice = session.get(Invoice, invoice_id)
        assert invoice.status is InvoiceStatus.SUBMITTED
        assert invoice.number == 8
        assert session.get(Counter, person_id).next_number == 9
