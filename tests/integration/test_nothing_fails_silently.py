"""An invoice must never fail quietly.

Leo's rule: the invoice must go. A missing file, a dead mail provider or a
half-finished submission are all things somebody can chase — an invoice that
simply never arrived is not, because nobody knows to look for it.

The trap these guard against is that the invoice table looks healthy in every
one of those cases. A stuck draft has a row. A failed email has an invoice
marked submitted. The period screen counts both as received.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from modules.invoices.config import Settings
from modules.invoices.db.models import Invoice, InvoiceStatus, Outbox, OutboxStatus
from modules.invoices.domain.period import Period
from modules.invoices.services import admin, jobs, mailer
from modules.invoices.services.crypto import Cipher, generate_key
from modules.invoices.services.invoicing import ExpenseInput, submit
from modules.invoices.services.storage import LocalObjectStore, StorageError
from tests.integration.conftest import (
    give_bank,
    give_counter,
    give_fee,
    give_kudos,
    make_person,
)

pytestmark = pytest.mark.integration

ROBIN = "robin@example.com"
IN_WINDOW = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
PERIOD = Period.parse("2026-09")


class Recorder:
    def __init__(self) -> None:
        self.sent: list[mailer.Message] = []

    def send(self, message: mailer.Message) -> None:
        self.sent.append(message)


class BrokenStore(LocalObjectStore):
    """Writes fine, cannot read back. A bucket permission gone wrong."""

    def get(self, uri: str) -> bytes:
        raise StorageError("object not found")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="staging",
        bank_encryption_key=generate_key(),
        csrf_secret="s",
        kudos_rate_pence=2000,
        finance_inbox=ROBIN,
        finance_emails=(ROBIN,),
    )


@pytest.fixture
def cipher(settings: Settings) -> Cipher:
    return Cipher.from_settings(settings)


def ready(factory, cipher, email="jane@example.com"):
    with factory() as session, session.begin():
        person = make_person(session, email=email)
        give_fee(session, person)
        give_bank(session, person, cipher)
        give_counter(session, person, 8)
        give_kudos(session, person, PERIOD.key, 3)
        return person.id


def issue(factory, cipher, settings, store, person_id, key="k"):
    return submit(
        factory,
        person_id=person_id,
        period=PERIOD,
        idempotency_key=key,
        now=IN_WINDOW,
        settings=settings,
        cipher=cipher,
        store=store,
        expenses=[ExpenseInput("travel", 3450)],
        render=lambda context: b"%PDF-1.7 fake",
    )


# --------------------------------------------------------------------------
# A missing file must not stop the email
# --------------------------------------------------------------------------


def test_the_email_still_goes_when_the_pdf_cannot_be_fetched(
    factory, settings, cipher, tmp_path
) -> None:
    """The figures are the invoice. The PDF is a convenience.

    Refusing to send because a file is missing turns a chaseable problem into
    an invisible one.
    """
    person_id = ready(factory, cipher)
    issue(factory, cipher, settings, LocalObjectStore(tmp_path / "o"), person_id)

    client = Recorder()
    with factory() as session, session.begin():
        report = mailer.dispatch(
            session,
            client=client,
            settings=settings,
            store=BrokenStore(tmp_path / "gone"),
        )

    assert report.sent == 1, "the email must go regardless"
    message = client.sent[0]
    assert message.attachments == ()
    # And it says so, rather than arriving mysteriously thin.
    assert "Could not be attached:" in message.text
    assert "the invoice PDF" in message.text
    # The figures are still all there.
    assert "£2,500.00" in message.text
    assert "£2,594.50" in message.text

    with factory() as session:
        assert session.scalars(select(Outbox)).one().status is OutboxStatus.SENT


def test_a_missing_receipt_does_not_cost_the_invoice_its_email(
    factory, settings, cipher, tmp_path
) -> None:
    store = LocalObjectStore(tmp_path / "o")
    person_id = ready(factory, cipher)
    invoice_id = issue(factory, cipher, settings, store, person_id)

    # A receipt recorded against the expense, but the file is not there.
    with factory() as session, session.begin():
        from modules.invoices.db.models import Expense

        expense = session.scalars(
            select(Expense).where(Expense.invoice_id == invoice_id)
        ).one()
        expense.receipt_uri = "file:///nowhere/receipt.jpg"

    client = Recorder()
    with factory() as session, session.begin():
        report = mailer.dispatch(session, client=client, settings=settings, store=store)

    assert report.sent == 1
    message = client.sent[0]
    assert len(message.attachments) == 1  # the PDF still made it
    assert "the receipt for travel" in message.text


# --------------------------------------------------------------------------
# A half-finished submission must not look finished
# --------------------------------------------------------------------------


def test_a_stuck_draft_is_surfaced_rather_than_sitting_there(
    factory, settings, cipher, tmp_path
) -> None:
    """R-SUB-09 leaves a draft when the render fails. Nothing used to look for
    it, and the person has every reason to think they had sent it."""
    person_id = ready(factory, cipher)

    def explode(context: dict) -> bytes:
        raise RuntimeError("weasyprint fell over")

    with pytest.raises(RuntimeError):
        submit(
            factory,
            person_id=person_id,
            period=PERIOD,
            idempotency_key="stuck",
            now=IN_WINDOW,
            settings=settings,
            cipher=cipher,
            store=LocalObjectStore(tmp_path / "o"),
            render=explode,
        )

    with factory() as session:
        invoice = session.scalars(select(Invoice)).one()
        assert invoice.status is InvoiceStatus.DRAFT

        # Fresh drafts are normal; a render takes a moment.
        fresh = admin.needs_attention(session, now=invoice.created_at)
        assert fresh.stuck_drafts == []

        # An hour later it is stuck, not in progress.
        later = admin.needs_attention(
            session, now=invoice.created_at + timedelta(hours=2)
        )
        assert [i.id for i in later.stuck_drafts] == [invoice.id]
        assert later.is_clear is False


def test_the_period_view_would_have_counted_that_as_received(
    factory, settings, cipher, tmp_path
) -> None:
    """Why the sweep exists at all: the obvious screen does not show it."""
    person_id = ready(factory, cipher)
    with pytest.raises(RuntimeError):
        submit(
            factory,
            person_id=person_id,
            period=PERIOD,
            idempotency_key="stuck",
            now=IN_WINDOW,
            settings=settings,
            cipher=cipher,
            store=LocalObjectStore(tmp_path / "o"),
            render=lambda c: (_ for _ in ()).throw(RuntimeError("boom")),
        )

    with factory() as session:
        view = admin.period_view(session, PERIOD, settings=settings)
        assert view.received == 1, "the period screen counts it as in"
        assert view.missing == [], "and does not list them as missing"


def test_mail_that_gave_up_is_surfaced(factory, settings, cipher, tmp_path) -> None:
    store = LocalObjectStore(tmp_path / "o")
    person_id = ready(factory, cipher)
    issue(factory, cipher, settings, store, person_id)

    class Dead:
        def send(self, message: mailer.Message) -> None:
            raise mailer.MailError("no", permanent=True)

    with factory() as session, session.begin():
        mailer.dispatch(session, client=Dead(), settings=settings, store=store)

    with factory() as session:
        attention = admin.needs_attention(session, now=IN_WINDOW)
        assert len(attention.failed_mail) == 1
        assert attention.is_clear is False


def test_the_summary_email_names_what_is_broken(
    factory, settings, cipher, tmp_path
) -> None:
    """R-JOB-03 is the one message Robin definitely reads."""
    store = LocalObjectStore(tmp_path / "o")
    person_id = ready(factory, cipher)
    with pytest.raises(RuntimeError):
        submit(
            factory,
            person_id=person_id,
            period=PERIOD,
            idempotency_key="stuck",
            now=IN_WINDOW,
            settings=settings,
            cipher=cipher,
            store=store,
            render=lambda c: (_ for _ in ()).throw(RuntimeError("boom")),
        )

    # created_at comes from the database's clock, and "stuck" is measured
    # against it — so with the summary's clock pinned to the 29th, a draft
    # written at the real time of day stops looking stuck the moment the real
    # date catches up. Pin the row too, or this passes until it does not.
    with factory() as session, session.begin():
        draft = session.scalars(
            select(Invoice).where(Invoice.status == InvoiceStatus.DRAFT)
        ).one()
        draft.created_at = IN_WINDOW

    with factory() as session, session.begin():
        jobs.summary(
            session, settings=settings, now=datetime(2026, 9, 29, 9, tzinfo=UTC)
        )

    client = Recorder()
    with factory() as session, session.begin():
        mailer.dispatch(session, client=client, settings=settings, store=store)

    body = next(m for m in client.sent if "Invoices for" in m.subject).text
    assert "Needs fixing" in body
    assert "never finished sending" in body


def test_a_clean_period_says_nothing_is_broken(factory, settings, cipher) -> None:
    """The sweep must not cry wolf, or it stops being read."""
    ready(factory, cipher)
    with factory() as session:
        assert admin.needs_attention(session, now=IN_WINDOW).stuck_drafts == []
        assert admin.needs_attention(session, now=IN_WINDOW).failed_mail == []


def test_a_migration_names_the_database_it_is_about_to_change(engine) -> None:
    """A hostname does not tell two environments apart.

    Staging and production can sit behind one endpoint with the branch chosen
    inside the connection string, which is how a migration aimed at production
    ran against staging and still announced a clean upgrade. What the guard
    prints now is the database itself.
    """
    from modules.invoices.db.migration_target import describe_target

    with engine.connect() as connection:
        described = describe_target(connection)

    assert "database " in described
    assert "holding" in described  # the people table exists here
    assert "at " in described or "never migrated" in described


def test_it_describes_an_empty_database_without_falling_over(engine) -> None:
    """Before the first migration there is no alembic_version and no people.

    Checking for each table beats catching the error: a failed statement
    poisons the transaction the migration is about to run in.
    """
    from sqlalchemy import text

    from modules.invoices.db.migration_target import describe_target

    with engine.connect() as connection:
        connection.execute(text("create schema if not exists empty_probe"))
        connection.execute(text("set search_path to empty_probe"))
        described = describe_target(connection)
        connection.execute(text("set search_path to public"))
        connection.rollback()

    assert "never migrated" in described
    assert "holding" not in described
