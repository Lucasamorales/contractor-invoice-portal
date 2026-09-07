"""The admin portal, driven through real HTTP (R-ADM-01 .. R-ADM-09).

The access-control tests here matter more than the rest: this is the only part
of the application that can see everybody's money.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth import Identity, current_identity
from app.config import Settings, get_settings
from app.db.models import (
    AuditLog,
    BankState,
    Fee,
    Invoice,
    InvoiceStatus,
    PayoutRegion,
    PersonStatus,
)
from app.db.session import get_session, get_sessionmaker
from app.main import app
from app.services.crypto import Cipher, generate_key
from app.services.storage import LocalObjectStore
from app.web.member import get_cipher, get_renderer, get_store
from tests.integration.conftest import (
    give_bank,
    give_counter,
    give_fee,
    give_kudos,
    make_person,
)

pytestmark = pytest.mark.integration

ROBIN = "robin@example.com"
JANE = "jane@example.com"


@pytest.fixture
def admin_client(
    engine: Engine, factory: sessionmaker[Session], tmp_path
) -> Iterator[tuple[TestClient, Settings, Cipher]]:
    settings = Settings(
        _env_file=None,
        environment="staging",
        bank_encryption_key=generate_key(),
        csrf_secret="test-csrf-secret",
        kudos_rate_pence=2000,
        finance_inbox=ROBIN,
        finance_emails=(ROBIN,),
        expense_flag_threshold_pence=20000,
    )
    cipher = Cipher.from_settings(settings)

    def db() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[current_identity] = lambda: Identity(
        email=ROBIN, subject="robin", is_finance=True
    )
    app.dependency_overrides[get_session] = db
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_cipher] = lambda: cipher
    app.dependency_overrides[get_store] = lambda: LocalObjectStore(tmp_path / "obj")
    app.dependency_overrides[get_sessionmaker] = lambda: factory
    app.dependency_overrides[get_renderer] = lambda: lambda c: b"%PDF-1.7"

    with TestClient(app) as client:
        yield client, settings, cipher

    app.dependency_overrides.clear()


def token_from(html: str) -> str:
    """The token, from a form field or from the hx-headers on <body>."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if match is None:
        match = re.search(r"X-CSRF-Token&#34;: &#34;([^&]+)&#34;", html)
    if match is None:
        match = re.search(r'X-CSRF-Token": "([^"]+)"', html)
    assert match, "no csrf token rendered"
    return match.group(1)


def seed_person(factory, cipher, **kwargs):
    with factory() as session, session.begin():
        person = make_person(
            session,
            email=kwargs.get("email", JANE),
            region=kwargs.get("region", PayoutRegion.UK),
        )
        if kwargs.get("fee", True):
            give_fee(session, person)
        give_bank(
            session,
            person,
            cipher,
            state=kwargs.get("bank_state", BankState.ACTIVE),
            verified=kwargs.get("verified", True),
        )
        give_counter(session, person, kwargs.get("next_number", 8))
        give_kudos(session, person, "2026-09", 0)
        return person.id


# --------------------------------------------------------------------------
# Access control (R-ACC-04)
# --------------------------------------------------------------------------


def test_a_non_finance_caller_is_refused_everywhere(
    engine: Engine, factory: sessionmaker[Session]
) -> None:
    """R-ACC-04, checked in the app and not only at IAP.

    Every admin route, not a sample: a single unguarded one would be enough.
    """
    settings = Settings(
        _env_file=None,
        bank_encryption_key=generate_key(),
        csrf_secret="s",
        finance_emails=(ROBIN,),
    )

    def db() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[current_identity] = lambda: Identity(
        email=JANE, subject="jane", is_finance=False
    )
    app.dependency_overrides[get_session] = db
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            for path in (
                "/admin",
                "/admin/",
                "/admin/period",
                "/admin/payment-run",
                "/admin/data-check",
                "/admin/queue",
            ):
                assert client.get(path).status_code == 403, path
            for path in (
                "/admin/payment-run.csv",
                "/admin/people/00000000-0000-0000-0000-000000000001/fee",
                "/admin/people/00000000-0000-0000-0000-000000000001/deactivate",
                "/admin/people/00000000-0000-0000-0000-000000000001/reveal-fee",
                "/admin/bank/00000000-0000-0000-0000-000000000001/reveal",
            ):
                assert client.post(path, data={}).status_code == 403, path
    finally:
        app.dependency_overrides.clear()


def test_every_admin_route_is_behind_the_finance_dependency() -> None:
    """A structural check, so a new route cannot be added without the guard."""
    from app.auth import require_finance
    from app.web import admin as admin_routes

    def calls(dependencies) -> list:
        found = []
        for dependency in dependencies:
            found.append(dependency.call)
            found.extend(calls(dependency.dependencies))
        return found

    for route in admin_routes.router.routes:
        assert require_finance in calls(route.dependant.dependencies), (
            f"{route.path} is not behind require_finance"
        )


# --------------------------------------------------------------------------
# R-ADM-01 People, with fees masked
# --------------------------------------------------------------------------


def test_fees_are_masked_on_the_people_list(admin_client, factory) -> None:
    client, _, cipher = admin_client
    seed_person(factory, cipher)
    html = client.get("/admin/people").text
    assert "£2,500.00" not in html
    assert "&pound;&bull;&bull;&bull;" in html or "£•••" in html


def test_revealing_a_fee_shows_it_and_records_the_reveal(admin_client, factory) -> None:
    client, _, cipher = admin_client
    person_id = seed_person(factory, cipher)
    page = client.get("/admin/people")

    response = client.post(
        f"/admin/people/{person_id}/reveal-fee",
        headers={"X-CSRF-Token": token_from(page.text)},
    )
    assert response.status_code == 200
    assert "£2,500.00" in response.text

    with factory() as session:
        entry = session.scalars(
            select(AuditLog).where(AuditLog.action == "fee.reveal")
        ).one()
        assert entry.actor_email == ROBIN
        assert entry.entity_id == str(person_id)


def test_revealing_a_fee_needs_a_token(admin_client, factory) -> None:
    client, _, cipher = admin_client
    person_id = seed_person(factory, cipher)
    assert (
        client.post(f"/admin/people/{person_id}/reveal-fee", data={}).status_code == 403
    )


# --------------------------------------------------------------------------
# R-ADM-03 Set fee
# --------------------------------------------------------------------------


def test_setting_a_fee_closes_the_old_row_rather_than_updating_it(
    admin_client, factory
) -> None:
    """R-DATA-02: a March invoice must still explain itself in December."""
    client, _, cipher = admin_client
    person_id = seed_person(factory, cipher)
    page = client.get(f"/admin/people/{person_id}")

    response = client.post(
        f"/admin/people/{person_id}/fee",
        data={
            "csrf_token": token_from(page.text),
            "amount": "3000",
            "valid_from": "2026-10-01",
            "note": "rate review",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with factory() as session:
        fees = session.scalars(
            select(Fee).where(Fee.person_id == person_id).order_by(Fee.valid_from)
        ).all()
        assert len(fees) == 2
        assert fees[0].fee_pence == 250000
        assert fees[0].valid_to == date(2026, 9, 30)  # closed the day before
        assert fees[1].fee_pence == 300000
        assert fees[1].valid_to is None
        assert fees[1].set_by == ROBIN
        assert fees[1].note == "rate review"


def test_a_fee_without_a_note_is_refused(admin_client, factory) -> None:
    client, _, cipher = admin_client
    person_id = seed_person(factory, cipher)
    page = client.get(f"/admin/people/{person_id}")
    response = client.post(
        f"/admin/people/{person_id}/fee",
        data={
            "csrf_token": token_from(page.text),
            "amount": "3000",
            "valid_from": "2026-10-01",
            "note": "   ",
        },
    )
    assert response.status_code == 422
    assert "note is required" in response.text


def test_a_fee_change_is_audited_with_the_amounts_masked_to_a_summary(
    admin_client, factory
) -> None:
    client, _, cipher = admin_client
    person_id = seed_person(factory, cipher)
    page = client.get(f"/admin/people/{person_id}")
    client.post(
        f"/admin/people/{person_id}/fee",
        data={
            "csrf_token": token_from(page.text),
            "amount": "3000",
            "valid_from": "2026-10-01",
            "note": "rate review",
        },
        follow_redirects=False,
    )
    with factory() as session:
        entry = session.scalars(
            select(AuditLog).where(AuditLog.action == "fee.set")
        ).one()
        assert entry.before["fee"] == "£2,500.00"
        assert entry.after["fee"] == "£3,000.00"
        assert entry.after["note"] == "rate review"


def test_setting_a_fee_activates_a_pending_person(admin_client, factory) -> None:
    """R-ADM-03, and the other half of R-ACC-03."""
    client, _, _cipher = admin_client
    with factory() as session, session.begin():
        person = make_person(session, email="new@example.com")
        person.status = PersonStatus.PENDING
        person_id = person.id

    page = client.get(f"/admin/people/{person_id}")
    client.post(
        f"/admin/people/{person_id}/fee",
        data={
            "csrf_token": token_from(page.text),
            "amount": "1800",
            "valid_from": "2026-09-01",
            "note": "starting rate",
        },
        follow_redirects=False,
    )
    with factory() as session:
        from app.db.models import Person

        assert session.get(Person, person_id).status is PersonStatus.ACTIVE


# --------------------------------------------------------------------------
# R-ADM-02 / R-ACC-06 Bank details
# --------------------------------------------------------------------------


def test_bank_details_are_masked_until_revealed(admin_client, factory) -> None:
    client, _, cipher = admin_client
    person_id = seed_person(factory, cipher)
    html = client.get(f"/admin/people/{person_id}").text
    assert "12345678" not in html
    assert "••••5678" in html


def test_revealing_bank_details_is_audited(admin_client, factory) -> None:
    """R-ACC-06: Robin may read them in full. Silently is the part that is not
    allowed."""
    client, _, cipher = admin_client
    person_id = seed_person(factory, cipher)
    page = client.get(f"/admin/people/{person_id}")
    bank_id = re.search(r'hx-post="/admin/bank/([0-9a-f-]+)/reveal"', page.text).group(
        1
    )

    response = client.post(
        f"/admin/bank/{bank_id}/reveal",
        headers={"X-CSRF-Token": token_from(page.text)},
    )
    assert response.status_code == 200
    assert "12345678" in response.text
    assert "23-08-01" in response.text

    with factory() as session:
        entry = session.scalars(
            select(AuditLog).where(AuditLog.action == "bank.reveal")
        ).one()
        assert entry.actor_email == ROBIN


def test_no_admin_route_can_make_a_bank_row_payable(admin_client, factory) -> None:
    """CLAUDE.md §2.2 and R-BANK-07, asserted structurally.

    An admin may read, review, flag and propose. The absence of a confirm route
    is the control, so the test looks for its absence.
    """
    from app.web import admin as admin_routes

    paths = [route.path for route in admin_routes.router.routes]
    assert not [p for p in paths if "confirm" in p or "verify" in p]

    client, _, cipher = admin_client
    person_id = seed_person(
        factory, cipher, bank_state=BankState.UNVERIFIED, verified=False
    )
    # The member route exists, but it acts on the caller's own row only.
    assert client.post(f"/admin/people/{person_id}/verify-bank").status_code == 404


# --------------------------------------------------------------------------
# R-ADM-04 Period
# --------------------------------------------------------------------------


def test_period_view_counts_expected_against_received(admin_client, factory) -> None:
    client, _, cipher = admin_client
    seed_person(factory, cipher, email="a@example.com")
    seed_person(factory, cipher, email="b@example.com")
    html = client.get("/admin/period?month=2026-09").text
    # Rendered as "0 of 2 received": assert the figures, not the
    # markup, so a restyle cannot break a data assertion.
    assert "of 2 received" in " ".join(html.split())
    assert ">0</strong>" in html


def test_period_view_flags_a_large_expense(admin_client, factory) -> None:
    from app.domain.period import Period
    from app.services.invoicing import ExpenseInput, submit

    client, settings, cipher = admin_client
    person_id = seed_person(factory, cipher)
    submit(
        factory,
        person_id=person_id,
        period=Period.parse("2026-09"),
        idempotency_key="k1",
        now=datetime(2026, 9, 24, 10, tzinfo=UTC),
        settings=settings,
        cipher=cipher,
        store=LocalObjectStore(__import__("pathlib").Path("out/objects")),
        expenses=[ExpenseInput("a very expensive laptop", 250000)],
        render=lambda c: b"%PDF",
    )
    html = client.get("/admin/period?month=2026-09").text
    # The chip prints the flag kind with the underscore turned into a space.
    assert "large expense" in html
    assert "a very expensive laptop" in html


# --------------------------------------------------------------------------
# R-ADM-05 Payment run
# --------------------------------------------------------------------------


def test_payment_run_splits_uk_and_international(admin_client, factory) -> None:
    from pathlib import Path

    from app.domain.period import Period
    from app.services.invoicing import submit

    client, settings, cipher = admin_client
    uk = seed_person(factory, cipher, email="uk@example.com")
    intl = seed_person(
        factory, cipher, email="intl@example.com", region=PayoutRegion.INTL
    )
    for index, person_id in enumerate((uk, intl)):
        submit(
            factory,
            person_id=person_id,
            period=Period.parse("2026-09"),
            idempotency_key=f"key-{index}",
            now=datetime(2026, 9, 24, 10, tzinfo=UTC),
            settings=settings,
            cipher=cipher,
            store=LocalObjectStore(Path("out/objects")),
            render=lambda c: b"%PDF",
        )

    html = client.get("/admin/payment-run?month=2026-09").text
    assert "1 payment" in html and "payments" not in html
    assert "International" in html
    # Masked on screen.
    assert "12345678" not in html
    assert "••••5678" in html


def test_exporting_the_payment_run_is_a_post_and_is_audited(
    admin_client, factory
) -> None:
    from pathlib import Path

    from app.domain.period import Period
    from app.services.invoicing import submit

    client, settings, cipher = admin_client
    person_id = seed_person(factory, cipher)
    submit(
        factory,
        person_id=person_id,
        period=Period.parse("2026-09"),
        idempotency_key="k",
        now=datetime(2026, 9, 24, 10, tzinfo=UTC),
        settings=settings,
        cipher=cipher,
        store=LocalObjectStore(Path("out/objects")),
        render=lambda c: b"%PDF",
    )

    page = client.get("/admin/payment-run?month=2026-09")
    response = client.post(
        "/admin/payment-run.csv",
        data={"csrf_token": token_from(page.text), "month": "2026-09"},
    )
    assert response.status_code == 200
    assert "payment-run-2026-09.csv" in response.headers["content-disposition"]

    body = response.text
    assert body.splitlines()[0].startswith("batch,payee,legal name")
    # The file Robin pays from carries the real numbers (R-ACC-06).
    assert "12345678" in body
    assert "23-08-01" in body
    assert "UK," in body

    with factory() as session:
        entry = session.scalars(
            select(AuditLog).where(AuditLog.action == "payment_run.export")
        ).one()
        assert entry.actor_email == ROBIN
        assert entry.entity_id == "2026-09"


# --------------------------------------------------------------------------
# R-ADM-06 / R-ADM-07 / R-ADM-09
# --------------------------------------------------------------------------


def test_data_check_shows_four_ticks(admin_client, factory) -> None:
    client, _, cipher = admin_client
    seed_person(factory, cipher)
    html = client.get("/admin/data-check").text
    assert "Fee set" in html
    assert "Bank confirmed" in html
    assert "1</strong> of <strong>1" in html.replace("\n", "")


def test_data_check_flags_an_unconfirmed_import(admin_client, factory) -> None:
    client, _, cipher = admin_client
    seed_person(factory, cipher, bank_state=BankState.UNVERIFIED, verified=False)
    html = client.get("/admin/data-check").text
    assert "not confirmed" in html or "not yet checked" in html.lower()


def test_queue_lists_people_without_a_fee(admin_client, factory) -> None:
    client, _, cipher = admin_client
    seed_person(factory, cipher, fee=False)
    html = client.get("/admin/queue").text
    assert "Waiting for you" in html and "&mdash; 1" in html


def test_deactivating_preserves_history_and_audits(admin_client, factory) -> None:
    """R-ADM-09: nothing is ever deleted."""
    client, _, cipher = admin_client
    person_id = seed_person(factory, cipher)
    page = client.get(f"/admin/people/{person_id}")
    client.post(
        f"/admin/people/{person_id}/deactivate",
        data={"csrf_token": token_from(page.text), "reason": "left the studio"},
        follow_redirects=False,
    )
    with factory() as session:
        from app.db.models import Person

        person = session.get(Person, person_id)
        assert person.status is PersonStatus.INACTIVE
        assert person.legal_name == "Jane Smith"  # history intact
        entry = session.scalars(
            select(AuditLog).where(AuditLog.action == "person.deactivate")
        ).one()
        assert entry.after["reason"] == "left the studio"


# --------------------------------------------------------------------------
# Finance downloads a PDF
# --------------------------------------------------------------------------


def _written_pdf(session) -> str:
    """A real file on disk, because the admin store is a LocalObjectStore."""
    import tempfile
    from pathlib import Path

    del session
    path = Path(tempfile.mkdtemp()) / "2026-09 - Jane Smith - 8.pdf"
    path.write_bytes(b"%PDF-1.7 finance copy")
    return path.as_posix()


def test_finance_can_download_anybody_s_invoice(admin_client, factory) -> None:
    """R-ACC-06. They pay these; the numbers are on every PDF they hold."""
    client, _, cipher = admin_client
    person_id = seed_person(factory, cipher)
    with factory() as session, session.begin():
        invoice = Invoice(
            person_id=person_id,
            period="2026-09",
            number=8,
            status=InvoiceStatus.SUBMITTED,
            remuneration_pence=100000,
            additional_pence=0,
            total_pence=100000,
            pdf_uri="file://" + str(_written_pdf(session)),
            idempotency_key="finance-download",
        )
        session.add(invoice)
        session.flush()
        invoice_id = invoice.id

    response = client.get(f"/admin/invoices/{invoice_id}/pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert response.headers["cache-control"] == "private, no-store"


def test_downloading_an_invoice_is_audited(admin_client, factory) -> None:
    """Reading is legitimate; reading unrecorded is not (CLAUDE.md 2.2)."""
    client, _, cipher = admin_client
    person_id = seed_person(factory, cipher)
    with factory() as session, session.begin():
        invoice = Invoice(
            person_id=person_id,
            period="2026-09",
            number=9,
            status=InvoiceStatus.SUBMITTED,
            remuneration_pence=1,
            additional_pence=0,
            total_pence=1,
            pdf_uri="file://" + str(_written_pdf(session)),
            idempotency_key="finance-audit",
        )
        session.add(invoice)
        session.flush()
        invoice_id = invoice.id

    client.get(f"/admin/invoices/{invoice_id}/pdf")

    with factory() as session:
        actions = [
            row.action
            for row in session.scalars(select(AuditLog)).all()
            if row.entity_id == str(invoice_id)
        ]
        assert "invoice.download" in actions


def test_an_invoice_with_no_pdf_says_so(admin_client, factory) -> None:
    client, _, cipher = admin_client
    person_id = seed_person(factory, cipher)
    with factory() as session, session.begin():
        invoice = Invoice(
            person_id=person_id,
            period="2026-09",
            number=10,
            status=InvoiceStatus.DRAFT,
            remuneration_pence=1,
            additional_pence=0,
            total_pence=1,
            idempotency_key="finance-nopdf",
        )
        session.add(invoice)
        session.flush()
        invoice_id = invoice.id

    response = client.get(f"/admin/invoices/{invoice_id}/pdf")
    assert response.status_code == 404
    assert "never finished sending" in response.text


def test_voiding_frees_the_month_but_never_the_number(admin_client, factory) -> None:
    """R-SUB-10, R-NUM-05, CLAUDE.md 2.3.

    Figures are never edited, so a wrong invoice is voided and reissued. The
    void is the only correction there is, and until this route existed the
    service that performs it could not be reached from the app at all.
    """
    client, _, cipher = admin_client
    person_id = seed_person(factory, cipher)
    with factory() as session:
        invoice = Invoice(
            person_id=person_id,
            period="2026-09",
            number=4,
            status=InvoiceStatus.SUBMITTED,
            remuneration_pence=100000,
            additional_pence=0,
            total_pence=100000,
            idempotency_key="to-be-voided",
        )
        session.add(invoice)
        session.commit()
        invoice_id = invoice.id

    page = client.get(f"/admin/people/{person_id}")
    assert "Void" in page.text

    response = client.post(
        f"/admin/invoices/{invoice_id}/void",
        data={"csrf_token": token_from(page.text), "reason": "wrong month"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with factory() as session:
        voided = session.get(Invoice, invoice_id)
        assert voided.status is InvoiceStatus.VOID
        assert voided.voided_at is not None
        # The row stays, and so does its number.
        assert voided.number == 4
        entry = session.scalars(
            select(AuditLog).where(AuditLog.action == "invoice.void")
        ).one()
        assert entry.after["reason"] == "wrong month"

    # The month is free again, which is what makes a reissue possible.
    from app.db import repo

    with factory() as session:
        assert repo.live_invoice_for_period(session, person_id, "2026-09") is None


def test_a_void_without_a_reason_is_refused(admin_client, factory) -> None:
    """In six months the reason is the only thing that explains the gap."""
    client, _, cipher = admin_client
    person_id = seed_person(factory, cipher)
    with factory() as session:
        invoice = Invoice(
            person_id=person_id,
            period="2026-09",
            number=5,
            status=InvoiceStatus.SUBMITTED,
            remuneration_pence=1,
            additional_pence=0,
            total_pence=1,
            idempotency_key="no-reason",
        )
        session.add(invoice)
        session.commit()
        invoice_id = invoice.id

    page = client.get(f"/admin/people/{person_id}")
    response = client.post(
        f"/admin/invoices/{invoice_id}/void",
        data={"csrf_token": token_from(page.text), "reason": "  "},
        follow_redirects=False,
    )
    assert response.status_code == 422
    with factory() as session:
        assert session.get(Invoice, invoice_id).status is InvoiceStatus.SUBMITTED


def test_a_stopped_email_can_be_put_back_in_the_queue(admin_client, factory) -> None:
    """R-JOB-04. The only way a message leaves the queue is by being sent, so
    the counterpart to a permanent failure is a retry, never a dismissal."""
    from app.db.models import Outbox, OutboxStatus

    client, _, _cipher = admin_client
    with factory() as session:
        row = Outbox(
            kind="invoice_submitted",
            status=OutboxStatus.FAILED,
            attempts=3,
            last_error="MailError: recipient rejected",
            payload={"marker": "test"},
        )
        session.add(row)
        session.commit()
        row_id = row.id

    page = client.get("/admin/queue")
    assert "Try again" in page.text

    response = client.post(
        f"/admin/outbox/{row_id}/retry",
        data={"csrf_token": token_from(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with factory() as session:
        back = session.get(Outbox, row_id)
        assert back.status is OutboxStatus.PENDING
        assert back.attempts == 0
        assert back.last_error is None
        assert back.next_attempt_at is None
        assert session.scalars(
            select(AuditLog).where(AuditLog.action == "outbox.retry")
        ).one()


def test_there_is_no_way_to_dismiss_an_unsent_email() -> None:
    """Asserted on the markup, because "we gave up quietly" is exactly the
    outcome this design exists to prevent."""
    from pathlib import Path

    queue = Path("app/templates/admin/queue.html").read_text(encoding="utf-8")
    for wording in ("Give up", "Dismiss", "Ignore this email"):
        assert wording not in queue


def test_only_one_panel_opens_and_an_unknown_one_opens_nothing(
    admin_client, factory
) -> None:
    """Disclosure is a single scalar in the URL, so "one at a time" is free and
    a parameter can never put arbitrary text on the page."""
    client, _, cipher = admin_client
    person_id = seed_person(factory, cipher)

    plain = client.get(f"/admin/people/{person_id}").text
    assert 'id="panel-propose"' not in plain
    assert 'id="panel-flag"' not in plain

    opened = client.get(f"/admin/people/{person_id}?panel=propose").text
    assert 'id="panel-propose"' in opened
    assert 'id="panel-flag"' not in opened

    # Not in the allowed set, so nothing opens and nothing is echoed back.
    junk = client.get(f"/admin/people/{person_id}?panel=<script>alert(1)</script>")
    assert junk.status_code == 200
    assert "alert(1)" not in junk.text
    assert 'id="panel-propose"' not in junk.text


def test_the_void_panel_only_names_this_persons_own_live_invoice(
    admin_client, factory
) -> None:
    """The heading names an invoice, so the number is looked up rather than
    printed from the query string."""
    client, _, cipher = admin_client
    person_id = seed_person(factory, cipher)
    with factory() as session:
        session.add(
            Invoice(
                person_id=person_id,
                period="2026-09",
                number=7,
                status=InvoiceStatus.SUBMITTED,
                remuneration_pence=100000,
                additional_pence=0,
                total_pence=100000,
                idempotency_key="panel-void",
            )
        )
        session.commit()

    opened = client.get(f"/admin/people/{person_id}?void=7").text
    assert "Void invoice 7" in opened

    # A number they do not have opens nothing at all.
    missing = client.get(f"/admin/people/{person_id}?void=999").text
    assert 'id="panel-void"' not in missing

    unreal = client.get(f"/admin/people/{person_id}?void=nine")
    assert unreal.status_code == 200
    assert 'id="panel-void"' not in unreal.text


def test_a_failed_fee_reopens_its_own_panel_with_the_error(
    admin_client, factory
) -> None:
    """A validation failure used to put its error in a list at the top of the
    page, which scrolls away from the field that is wrong."""
    client, _, cipher = admin_client
    person_id = seed_person(factory, cipher)
    page = client.get(f"/admin/people/{person_id}")

    response = client.post(
        f"/admin/people/{person_id}/fee",
        data={
            "csrf_token": token_from(page.text),
            "amount": "not money",
            "valid_from": "2026-09-01",
            "note": "trying",
        },
        follow_redirects=False,
    )
    assert response.status_code == 422
    assert 'class="panel-error"' in response.text
    assert 'id="panel-fee"' in response.text


def test_the_page_offers_no_way_to_make_a_bank_row_payable() -> None:
    """R-BANK-*, CLAUDE.md 2.2. The layout changed; the boundary did not."""
    from pathlib import Path

    markup = Path("app/templates/admin/person.html").read_text(encoding="utf-8")
    assert "/bank/confirm" not in markup
    for wording in ("Make payable", "Confirm the account", "Approve"):
        assert wording not in markup
    assert "You may read, never confirm" in markup
