"""The member wizard, driven through real HTTP (R-WIZ-01 .. R-WIZ-09).

Uses the actual routes, templates and database. IAP sits in front of the
service in production, so identity is the one thing stubbed here — everything
downstream of it is real.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth import Identity, current_identity
from app.config import Settings, get_settings
from app.db.models import BankState, Invoice, InvoiceStatus, Outbox, Person
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

EMAIL = "jane@example.com"
OTHER = "sam@example.com"


@pytest.fixture
def wizard(
    engine: Engine, factory: sessionmaker[Session], tmp_path
) -> Iterator[tuple[TestClient, Settings, Cipher]]:
    settings = Settings(
        _env_file=None,
        environment="staging",
        bank_encryption_key=generate_key(),
        csrf_secret="test-csrf-secret",
        kudos_rate_pence=2000,
        finance_inbox="robin@example.com",
        finance_emails=(),
    )
    cipher = Cipher.from_settings(settings)
    store = LocalObjectStore(tmp_path / "objects")

    def fake_identity() -> Identity:
        return Identity(email=EMAIL, subject="sub-1", is_finance=False)

    def db() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[current_identity] = fake_identity
    app.dependency_overrides[get_session] = db
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_cipher] = lambda: cipher
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_sessionmaker] = lambda: factory
    app.dependency_overrides[get_renderer] = lambda: (
        lambda context: f"%PDF-1.7 invoice {context['number']}".encode()
    )

    with TestClient(app) as client:
        yield client, settings, cipher

    app.dependency_overrides.clear()


def token_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "no csrf token in the rendered form"
    return match.group(1)


def seed_ready(factory: sessionmaker[Session], cipher: Cipher, **kwargs) -> uuid.UUID:
    with factory() as session, session.begin():
        person = make_person(session, email=kwargs.get("email", EMAIL))
        give_fee(session, person)
        give_bank(session, person, cipher, **kwargs.get("bank", {}))
        give_counter(session, person, kwargs.get("next_number", 8))
        if kwargs.get("kudos") is not None:
            give_kudos(session, person, "2026-09", kwargs.get("kudos", 3))
        return person.id


# --------------------------------------------------------------------------
# Identity and access (R-ACC-03, R-ACC-05, R-SEC-06)
# --------------------------------------------------------------------------


def test_an_unknown_but_verified_caller_becomes_a_pending_person(
    wizard, factory
) -> None:
    client, _, _ = wizard
    response = client.get("/")
    assert response.status_code == 200
    with factory() as session:
        person = session.scalars(select(Person)).one()
        assert person.email == EMAIL
        assert person.status.value == "pending"


def test_the_page_greets_the_caller_by_name(wizard, factory, cipher) -> None:
    client, _, box = wizard
    seed_ready(factory, box)
    assert "Jane Smith" in client.get("/").text


def test_no_route_accepts_a_person_id_from_the_client(wizard, factory) -> None:
    """R-SEC-06: identity comes from the IAP assertion, never from the URL."""
    client, _, box = wizard
    seed_ready(factory, box)
    with factory() as session, session.begin():
        other = make_person(session, email=OTHER)
        other_id = other.id

    # There is no route shaped to take one, and guessing one changes nothing.
    for path in (f"/?person_id={other_id}", f"/me?person_id={other_id}"):
        response = client.get(path)
        assert response.status_code == 200
        assert OTHER not in response.text


def test_a_missing_iap_assertion_is_refused() -> None:
    """Without the override, the real dependency runs and rejects the call."""
    with TestClient(app) as bare:
        assert bare.get("/").status_code == 401


# --------------------------------------------------------------------------
# R-WIZ-01 Welcome
# --------------------------------------------------------------------------


def test_welcome_shows_the_fee_kudos_and_running_total(wizard, factory) -> None:
    client, _, box = wizard
    seed_ready(factory, box, kudos=3)
    html = client.get("/").text
    assert "£2,500.00" in html  # the fee
    assert "£60.00" in html  # 3 kudos at £20
    assert "£2,560.00" in html  # running total


def test_welcome_lists_what_is_blocking(wizard, factory) -> None:
    client, _, box = wizard
    with factory() as session, session.begin():
        person = make_person(session, complete_profile=False)
        give_bank(session, person, box, state=BankState.UNVERIFIED, verified=False)
    html = client.get("/").text
    assert "Confirm your bank details first." in html
    assert "We need a few details for your invoice letterhead." in html
    assert "set your fee yet" in html  # the apostrophe is HTML-escaped


def test_kudos_unavailable_is_said_out_loud(wizard, factory) -> None:
    # R-KUD-06: never quietly show zero.
    client, _, box = wizard
    seed_ready(factory, box, kudos=None)
    assert "unavailable" in client.get("/").text


# --------------------------------------------------------------------------
# R-WIZ-02 Your details
# --------------------------------------------------------------------------


def test_all_five_letterhead_fields_are_required(wizard, factory) -> None:
    client, _, box = wizard
    seed_ready(factory, box)
    form = client.get("/details")
    response = client.post(
        "/details",
        data={"csrf_token": token_from(form.text), "legal_name": "Jane"},
    )
    assert response.status_code == 422
    assert response.text.count("This appears on your invoice") == 4


def test_saving_details_records_them_and_moves_on(wizard, factory) -> None:
    client, _, box = wizard
    seed_ready(factory, box)
    form = client.get("/details")
    response = client.post(
        "/details",
        data={
            "csrf_token": token_from(form.text),
            "legal_name": "Jane Smith",
            "role": "Creative Director",
            "address": "12 Example Street\nLondon N1 1AA",
            "phone": "+44 7700 900000",
            "contact_email": EMAIL,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/bank"
    with factory() as session:
        person = session.scalars(select(Person)).one()
        assert person.role == "Creative Director"
        assert person.profile_confirmed_at is not None


# --------------------------------------------------------------------------
# R-SEC-11 CSRF
# --------------------------------------------------------------------------


def test_a_form_without_a_token_is_refused(wizard, factory) -> None:
    client, _, box = wizard
    seed_ready(factory, box)
    assert client.post("/details", data={"legal_name": "Jane"}).status_code == 403


def test_a_token_minted_for_someone_else_is_refused(wizard, factory) -> None:
    client, settings, box = wizard
    seed_ready(factory, box)
    from app.web import csrf

    stolen = csrf.issue(settings, OTHER)
    response = client.post("/details", data={"csrf_token": stolen, "legal_name": "J"})
    assert response.status_code == 403


# --------------------------------------------------------------------------
# R-WIZ-05 Bank details
# --------------------------------------------------------------------------


def test_bank_screen_masks_the_account_number(wizard, factory) -> None:
    client, _, box = wizard
    seed_ready(factory, box)
    html = client.get("/bank").text
    assert "12345678" not in html
    assert "••••5678" in html
    assert "23-08-01" in html  # the sort code prints in full, as on the invoice


def test_the_owner_confirms_their_own_details(wizard, factory) -> None:
    client, _, box = wizard
    seed_ready(factory, box, bank={"state": BankState.UNVERIFIED, "verified": False})
    page = client.get("/bank")
    assert "nobody has checked them" in page.text

    response = client.post(
        "/bank/confirm",
        data={"csrf_token": token_from(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303

    from app.db.models import AuditLog, BankDetail

    with factory() as session:
        detail = session.scalars(select(BankDetail)).one()
        assert detail.state is BankState.ACTIVE
        assert detail.verified_at is not None
        assert detail.verified_by == EMAIL
        # R-DATA-11: the change is audited in the same transaction.
        assert session.scalars(select(AuditLog)).one().action == "bank.confirm"


# --------------------------------------------------------------------------
# R-WIZ-03/06/07 Additional, preview and submit
# --------------------------------------------------------------------------


def full_run(client, extra: dict | None = None) -> tuple[str, str]:
    """Additional -> preview, returning the preview HTML and its form token."""
    page = client.get("/additional")
    data = {"csrf_token": token_from(page.text), "description": [], "amount": []}
    data.update(extra or {})
    preview = client.post("/preview", data=data)
    assert preview.status_code == 200, preview.text[:400]
    return preview.text, token_from(preview.text)


def test_preview_shows_the_real_invoice(wizard, factory) -> None:
    """R-WIZ-06 with R-PDF-01: the preview is the template that makes the PDF."""
    client, _, box = wizard
    seed_ready(factory, box, kudos=3)
    html, _ = full_run(client, {"description": ["travel"], "amount": ["34.50"]})
    assert "srcdoc" in html
    assert "Invoice No: 8" in html
    assert "ROLE: CREATIVE DIRECTOR" in html
    assert "3 kudos × £20 · travel £34.50" in html
    assert "£2,594.50" in html


def test_an_empty_additional_step_is_valid(wizard, factory) -> None:
    # R-WIZ-03: adding nothing is a legitimate answer and means zero.
    client, _, box = wizard
    seed_ready(factory, box, kudos=0)
    html, _ = full_run(client)
    assert "£0.00" in html


def test_a_bad_amount_is_explained_not_swallowed(wizard, factory) -> None:
    client, _, box = wizard
    seed_ready(factory, box, kudos=0)
    page = client.get("/additional")
    response = client.post(
        "/preview",
        data={
            "csrf_token": token_from(page.text),
            "description": ["travel"],
            "amount": ["thirty quid"],
        },
    )
    assert response.status_code == 422
    assert "is not an amount" in response.text


def test_submitting_from_the_preview_issues_the_invoice(wizard, factory) -> None:
    client, _, box = wizard
    seed_ready(factory, box, kudos=3)
    html, token = full_run(client, {"description": ["travel"], "amount": ["34.50"]})
    key = re.search(r'name="idempotency_key" value="([^"]+)"', html).group(1)

    response = client.post(
        "/submit",
        data={
            "csrf_token": token,
            "idempotency_key": key,
            "description": ["travel"],
            "amount": ["34.50"],
        },
    )
    assert response.status_code == 200
    assert "has gone to Robin" in response.text

    with factory() as session:
        invoice = session.scalars(select(Invoice)).one()
        assert invoice.status is InvoiceStatus.SUBMITTED
        assert invoice.number == 8
        assert invoice.total_pence == 259450
        assert session.scalar(select(func.count()).select_from(Outbox)) == 1


def test_pressing_submit_twice_produces_one_invoice(wizard, factory) -> None:
    """R-SUB-02 / R-WIZ-07 through the actual form."""
    client, _, box = wizard
    seed_ready(factory, box, kudos=0)
    html, token = full_run(client)
    key = re.search(r'name="idempotency_key" value="([^"]+)"', html).group(1)
    payload = {"csrf_token": token, "idempotency_key": key}

    first = client.post("/submit", data=payload)
    second = client.post("/submit", data=payload)
    assert first.status_code == second.status_code == 200

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Invoice)) == 1
        assert session.scalar(select(func.count()).select_from(Outbox)) == 1


def test_submitting_without_a_confirmed_bank_row_is_blocked(wizard, factory) -> None:
    client, _, box = wizard
    seed_ready(
        factory, box, kudos=0, bank={"state": BankState.UNVERIFIED, "verified": False}
    )
    page = client.get("/additional")
    response = client.post("/preview", data={"csrf_token": token_from(page.text)})
    assert "Confirm your bank details first." in response.text


# --------------------------------------------------------------------------
# R-WIZ-08 My details
# --------------------------------------------------------------------------


def test_my_details_shows_history_without_full_numbers(wizard, factory) -> None:
    client, _, box = wizard
    seed_ready(factory, box)
    html = client.get("/me").text
    assert "12345678" not in html
    assert "••••5678" in html
    assert "Changes to it" in html


def test_my_details_lists_issued_invoices(wizard, factory) -> None:
    client, _, box = wizard
    seed_ready(factory, box, kudos=0)
    html, token = full_run(client)
    key = re.search(r'name="idempotency_key" value="([^"]+)"', html).group(1)
    client.post("/submit", data={"csrf_token": token, "idempotency_key": key})

    page = client.get("/me").text
    assert "submitted" in page
    assert "2026-09" in page
