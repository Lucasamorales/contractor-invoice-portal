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
from app.db.models import AuditLog, BankState, Fee, PayoutRegion, PersonStatus
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
    html = client.get("/admin").text
    assert "£2,500.00" not in html
    assert "&pound;&bull;&bull;&bull;" in html or "£•••" in html


def test_revealing_a_fee_shows_it_and_records_the_reveal(admin_client, factory) -> None:
    client, _, cipher = admin_client
    person_id = seed_person(factory, cipher)
    page = client.get("/admin")

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
    assert "0</strong> of <strong>2" in html.replace("\n", "")


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
    assert "large_expense" in html
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
    assert "UK &mdash; 1 payments" in html or "UK — 1 payments" in html
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
    assert "Waiting for a fee (1)" in html


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
