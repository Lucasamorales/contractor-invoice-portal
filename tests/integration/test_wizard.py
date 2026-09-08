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
from app.services.storage import LocalObjectStore, StorageError
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
    assert "Not available yet" in client.get("/").text


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
    """Nothing was outstanding, so this is an edit rather than a first run:
    it goes back to My details instead of starting an invoice."""
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
    assert response.headers["location"] == "/me"
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
    # The screen must say the details are unverified, however it words it.
    assert "Nothing has verified them" in page.text

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
    assert "3 kudos × £20" in html
    assert "travel £34.50" in html
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
    assert "is with accounts" in response.text

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
    assert "Submitted" in page
    assert "2026-09" in page


# --------------------------------------------------------------------------
# Downloading your own copy
#
# NOT COVERED BY A REQUIREMENT. R-SUB-08 puts a copy in the person's inbox and
# that is the spec's only route to it, so a bounced or deleted email left
# somebody with no way back to their own invoice. Flagged as a spec gap.
# --------------------------------------------------------------------------


def download_link(html: str) -> str:
    match = re.search(r'href="(/invoices/[0-9a-f-]+/pdf)"', html)
    assert match, "the sent screen offers no way to download the invoice"
    return match.group(1)


def test_the_owner_can_download_their_own_invoice(wizard, factory) -> None:
    client, _, box = wizard
    seed_ready(factory, box, kudos=0)
    html, token = full_run(client)
    key = re.search(r'name="idempotency_key" value="([^"]+)"', html).group(1)
    sent = client.post("/submit", data={"csrf_token": token, "idempotency_key": key})

    response = client.get(download_link(sent.text))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    # R-PDF-15's filename, in both the plain and the UTF-8 form (RFC 6266).
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "2026-09 - Jane Smith - 8.pdf" in disposition
    assert "filename*=UTF-8''" in disposition
    # It carries a full account number: no proxy or shared cache (CLAUDE.md 2.8).
    assert response.headers["cache-control"] == "private, no-store"


def test_you_cannot_download_somebody_elses_invoice(wizard, factory) -> None:
    """CLAUDE.md 2.1, from the other side.

    The answer must be the same as for an id that never existed, or the status
    code alone tells you whether Sam invoiced this month.
    """
    client, _, box = wizard
    seed_ready(factory, box, kudos=0)
    with factory() as session, session.begin():
        other = make_person(session, email=OTHER)
        session.flush()
        theirs = Invoice(
            person_id=other.id,
            period="2026-09",
            number=1,
            status=InvoiceStatus.SUBMITTED,
            remuneration_pence=100000,
            additional_pence=0,
            total_pence=100000,
            pdf_uri="file:///nowhere/theirs.pdf",
            idempotency_key="theirs",
        )
        session.add(theirs)
        session.flush()
        theirs_id = theirs.id

    mine = client.get(f"/invoices/{theirs_id}/pdf")
    absent = client.get(f"/invoices/{uuid.uuid4()}/pdf")

    assert mine.status_code == absent.status_code == 404
    assert "We have no such invoice." in mine.text
    assert mine.text == absent.text


def test_a_draft_says_so_rather_than_offering_nothing(wizard, factory) -> None:
    """A draft holding a number is the failure we most want noticed."""
    client, _, box = wizard
    person_id = seed_ready(factory, box, kudos=0)
    with factory() as session, session.begin():
        stuck = Invoice(
            person_id=person_id,
            period="2026-09",
            number=8,
            status=InvoiceStatus.DRAFT,
            remuneration_pence=100000,
            additional_pence=0,
            total_pence=100000,
            idempotency_key="stuck",
        )
        session.add(stuck)
        session.flush()
        stuck_id = stuck.id

    response = client.get(f"/invoices/{stuck_id}/pdf")

    assert response.status_code == 404
    assert "has not been sent yet" in response.text


def test_a_pdf_missing_from_the_store_is_loud(wizard, factory, caplog) -> None:
    """The row says sent, the bucket says nothing. That is data loss, not a 404.

    It must reach a log with enough to find the invoice and nothing that
    R-SEC-05 forbids — no object URI.
    """
    client, _, box = wizard
    person_id = seed_ready(factory, box, kudos=0)
    with factory() as session, session.begin():
        orphan = Invoice(
            person_id=person_id,
            period="2026-09",
            number=8,
            status=InvoiceStatus.SUBMITTED,
            remuneration_pence=100000,
            additional_pence=0,
            total_pence=100000,
            pdf_uri="file:///gone/2026-09 - Jane Smith - 8.pdf",
            idempotency_key="orphan",
        )
        session.add(orphan)
        session.flush()
        orphan_id = orphan.id

    with caplog.at_level("ERROR"), pytest.raises(StorageError):
        client.get(f"/invoices/{orphan_id}/pdf")

    assert str(orphan_id) in caplog.text
    assert "/gone/" not in caplog.text


# --------------------------------------------------------------------------
# R-WIZ-04 Receipts
# --------------------------------------------------------------------------

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)


def upload(client, content: bytes, name: str = "receipt.png"):
    page = client.get("/additional")
    return client.post(
        "/receipts",
        files={"file": (name, content, "application/octet-stream")},
        headers={"X-CSRF-Token": token_from(page.text)},
    )


def test_a_receipt_is_checked_and_parked_before_the_invoice_exists(
    wizard, factory
) -> None:
    """R-WIZ-04. It cannot be attached yet: expenses.invoice_id is NOT NULL."""
    client, _, box = wizard
    seed_ready(factory, box, kudos=0)

    response = upload(client, PNG)

    assert response.status_code == 200
    body = response.json()
    # Parked under the top-level pending prefix, not under receipts/, so the
    # lifecycle rule cannot reach an attached one.
    assert "pending-receipts/" in body["uri"]
    assert body["kind"] == "image/png"


def test_a_file_we_cannot_read_is_refused_with_a_reason(wizard, factory) -> None:
    """R-SEC-10: the magic bytes decide, not the filename."""
    client, _, box = wizard
    seed_ready(factory, box, kudos=0)

    response = upload(client, b"MZ\x90\x00 this is an executable", "invoice.pdf")

    assert response.status_code == 422
    assert "could not read that file" in response.json()["error"]


def test_an_empty_file_is_refused(wizard, factory) -> None:
    client, _, box = wizard
    seed_ready(factory, box, kudos=0)
    assert upload(client, b"").status_code == 422


def test_a_receipt_survives_preview_and_lands_on_the_invoice(wizard, factory) -> None:
    """The whole path: upload, carry through preview, promote at submit.

    The uri moves out of the pending prefix, which is what makes it safe from
    the bucket's lifecycle rule and what the email attaches.
    """
    client, _, box = wizard
    seed_ready(factory, box, kudos=0)

    uri = upload(client, PNG).json()["uri"]
    html, token = full_run(
        client,
        {"description": ["travel"], "amount": ["34.50"], "receipt_uri": [uri]},
    )
    assert uri in html  # carried through the preview as a hidden field

    key = re.search(r'name="idempotency_key" value="([^"]+)"', html).group(1)
    sent = client.post(
        "/submit",
        data={
            "csrf_token": token,
            "idempotency_key": key,
            "description": ["travel"],
            "amount": ["34.50"],
            "receipt_uri": [uri],
        },
    )
    assert sent.status_code == 200

    with factory() as session:
        from app.db.models import Expense

        expense = session.scalars(select(Expense)).one()
        assert expense.receipt_uri is not None
        assert "pending-receipts/" not in expense.receipt_uri
        # R-STO-02: receipts/{person}/{invoice}/{uuid}.{ext}
        assert "/receipts/" in expense.receipt_uri or expense.receipt_uri.startswith(
            "file://"
        )
        assert expense.receipt_uri.endswith(".png")


def test_an_expense_without_a_receipt_is_still_fine(wizard, factory) -> None:
    """Most expenses will not have one, and R-WIZ-03 never demanded it."""
    client, _, box = wizard
    seed_ready(factory, box, kudos=0)
    html, token = full_run(
        client, {"description": ["coffee"], "amount": ["3.00"], "receipt_uri": [""]}
    )
    key = re.search(r'name="idempotency_key" value="([^"]+)"', html).group(1)
    sent = client.post(
        "/submit",
        data={
            "csrf_token": token,
            "idempotency_key": key,
            "description": ["coffee"],
            "amount": ["3.00"],
            "receipt_uri": [""],
        },
    )
    assert sent.status_code == 200
    with factory() as session:
        from app.db.models import Expense

        assert session.scalars(select(Expense)).one().receipt_uri is None


def test_the_theme_is_a_cookie_and_survives_a_navigation(wizard, factory) -> None:
    """The CSP forbids the inline script that normally sets a theme before
    first paint, so the choice is rendered by the server instead. It has to
    reach every page, not only the one the toggle was pressed on."""
    client, _, cipher = wizard
    seed_ready(factory, cipher)

    page = client.get("/")
    # Nobody has chosen: no attribute, so prefers-color-scheme decides.
    assert "data-theme=" not in page.text
    assert 'action="/theme"' in page.text

    response = client.post(
        "/theme",
        data={"csrf_token": token_from(page.text), "next": "/me"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/me"
    assert client.cookies.get("theme") == "night"

    assert 'data-theme="night"' in client.get("/").text
    assert 'data-theme="night"' in client.get("/me").text

    back = client.post(
        "/theme",
        data={"csrf_token": token_from(client.get("/").text), "next": "/"},
        follow_redirects=False,
    )
    assert back.status_code == 303
    assert client.cookies.get("theme") == "day"
    assert 'data-theme="day"' in client.get("/").text


def test_the_toggle_still_works_without_scripting(wizard, factory) -> None:
    """theme.js makes the press instant by flipping the page and writing the
    cookie in the background. It is an enhancement, not the mechanism: the
    form has to stay a real post that a browser with no scripting can make.
    """
    client, _, cipher = wizard
    seed_ready(factory, cipher)
    page = client.get("/")

    # A real form with a real action, not a button wired up by script.
    assert '<form method="post" action="/theme"' in page.text
    assert 'type="submit"' in page.text
    # And the enhancement is a separate file, never inline (the CSP forbids
    # inline, which is the whole reason the theme is server-rendered).
    assert "/static/theme.js" in page.text
    assert "onclick" not in page.text


def test_the_theme_cookie_is_readable_by_the_script_that_owns_it(
    wizard, factory
) -> None:
    """The toggle writes the cookie itself so that pressing it needs no
    request — the page changes on the press, and a request would have to
    finish afterwards, so leaving the page cancelled it and the next screen
    came back the way it was.

    A browser refuses a document.cookie write over an httponly cookie of the
    same name, so this one must not be httponly. It holds the word "day" or
    "night" and nothing else.
    """
    client, _, cipher = wizard
    seed_ready(factory, cipher)
    page = client.get("/")

    response = client.post(
        "/theme",
        data={"csrf_token": token_from(page.text), "next": "/"},
        follow_redirects=False,
    )
    header = response.headers["set-cookie"]
    assert "theme=night" in header
    assert "HttpOnly" not in header
    assert "samesite=lax" in header.lower()
    assert "path=/" in header.lower()


def test_the_theme_form_cannot_redirect_off_the_app(wizard, factory) -> None:
    client, _, cipher = wizard
    seed_ready(factory, cipher)

    page = client.get("/")
    response = client.post(
        "/theme",
        data={
            "csrf_token": token_from(page.text),
            "next": "https://evil.example/steal",
        },
        follow_redirects=False,
    )
    assert response.headers["location"] == "/"


def _day_one(factory, cipher) -> None:
    """Somebody as they are on their first visit: accounts has set a fee, the
    bank rows came across unconfirmed, the letterhead is empty."""
    with factory() as session, session.begin():
        person = make_person(session, complete_profile=False)
        give_fee(session, person)
        give_counter(session, person, 1)
        give_bank(session, person, cipher, state=BankState.UNVERIFIED, verified=False)


LETTERHEAD = {
    "legal_name": "Jane Smith",
    "role": "Engineer",
    "address": "1 Invented Street",
    "phone": "+44 7700 900000",
    "contact_email": "jane@example.com",
}


@pytest.fixture
def window_open(wizard):
    """The submission window forced open.

    Where the chain ends depends on it: with the window shut there is nothing
    to go on to, so it stops at This month, which says when it opens.
    """
    client, settings, cipher = wizard
    app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={"window_always_open": True}
    )
    yield client, settings, cipher


def test_the_first_run_is_one_run_whichever_order_you_start_in(
    window_open, factory
) -> None:
    """Each screen used to name its own successor, which worked in one order
    and not the other. Nothing decides what comes next now — it is asked."""
    client, _, cipher = window_open

    # Bank first, which is what This month used to offer first. This is the
    # order that dropped somebody into the wizard with no letterhead.
    _day_one(factory, cipher)
    page = client.get("/bank")
    confirmed = client.post(
        "/bank/confirm",
        data={"csrf_token": token_from(page.text)},
        follow_redirects=False,
    )
    assert confirmed.headers["location"] == "/details"

    saved = client.post(
        "/details",
        data={"csrf_token": token_from(client.get("/details").text), **LETTERHEAD},
        follow_redirects=False,
    )
    assert saved.headers["location"] == "/additional"


def test_the_other_order_ends_up_in_the_same_place(window_open, factory) -> None:
    client, _, cipher = window_open
    _day_one(factory, cipher)

    saved = client.post(
        "/details",
        data={"csrf_token": token_from(client.get("/details").text), **LETTERHEAD},
        follow_redirects=False,
    )
    assert saved.headers["location"] == "/bank"

    page = client.get("/bank")
    confirmed = client.post(
        "/bank/confirm",
        data={"csrf_token": token_from(page.text)},
        follow_redirects=False,
    )
    assert confirmed.headers["location"] == "/additional"


def test_the_wizard_refuses_anyone_who_is_not_ready(window_open, factory) -> None:
    """It had no guard at all, so somebody could build an invoice the submit
    would then refuse."""
    client, _, cipher = window_open
    _day_one(factory, cipher)

    blocked = client.get("/additional", follow_redirects=False)
    assert blocked.status_code == 303
    assert blocked.headers["location"] == "/details"

    client.post(
        "/details",
        data={"csrf_token": token_from(client.get("/details").text), **LETTERHEAD},
        follow_redirects=False,
    )
    still_blocked = client.get("/additional", follow_redirects=False)
    assert still_blocked.headers["location"] == "/bank"

    page = client.get("/bank")
    client.post(
        "/bank/confirm",
        data={"csrf_token": token_from(page.text)},
        follow_redirects=False,
    )
    assert client.get("/additional").status_code == 200


def test_this_month_numbers_the_steps(wizard, factory) -> None:
    """Everything pending is still shown; it just reads as a sequence."""
    client, _, cipher = wizard
    _day_one(factory, cipher)

    html = client.get("/").text
    assert "Step 1 of 2" in html
    assert "Step 2 of 2" in html
    # Only the first carries a button; the rest arrive as each is finished.
    assert html.count('class="button"') == 1


def test_a_shut_window_ends_the_chain_at_this_month(wizard, factory) -> None:
    """There is nowhere to go on to, and This month is what says when it
    opens."""
    client, _, cipher = wizard
    _day_one(factory, cipher)

    client.post(
        "/details",
        data={"csrf_token": token_from(client.get("/details").text), **LETTERHEAD},
        follow_redirects=False,
    )
    page = client.get("/bank")
    confirmed = client.post(
        "/bank/confirm",
        data={"csrf_token": token_from(page.text)},
        follow_redirects=False,
    )
    assert confirmed.headers["location"] == "/"


def test_editing_your_details_later_does_not_start_an_invoice(
    window_open, factory
) -> None:
    """The chain is for the first run. Somebody who is already set up and
    changes their phone number wants to go back to where they were."""
    client, _, cipher = window_open
    seed_ready(factory, cipher)

    saved = client.post(
        "/details",
        data={"csrf_token": token_from(client.get("/details").text), **LETTERHEAD},
        follow_redirects=False,
    )
    assert saved.headers["location"] == "/me"


def test_adding_a_first_account_carries_on_rather_than_looping(
    window_open, factory
) -> None:
    """Somebody created fresh has no bank rows at all, so the bank screen
    offers "Add them" rather than a confirmation. Saving them used to come
    straight back to that screen, which by then had nothing left to do on it
    — the same dead end the chain exists to remove."""
    client, _, _cipher = window_open
    with factory() as session, session.begin():
        person = make_person(session, complete_profile=True)
        give_fee(session, person)
        give_counter(session, person, 1)

    added = client.post(
        "/bank/change",
        data={
            "csrf_token": token_from(client.get("/bank/change").text),
            "account_name": "Jane Smith",
            "bank_name": "Monzo",
            "sort_code": "23-08-01",
            "account_number": "12345678",
            "account_number_again": "12345678",
        },
        follow_redirects=False,
    )
    assert added.status_code == 303
    assert added.headers["location"] == "/additional"


def test_changing_an_account_later_stays_on_the_bank_screen(
    window_open, factory
) -> None:
    """Not a first run: they want to see what they just saved."""
    client, _, cipher = window_open
    seed_ready(factory, cipher)

    changed = client.post(
        "/bank/change",
        data={
            "csrf_token": token_from(client.get("/bank/change").text),
            "account_name": "Jane Smith",
            "bank_name": "Monzo",
            "sort_code": "23-08-01",
            "account_number": "87654321",
            "account_number_again": "87654321",
        },
        follow_redirects=False,
    )
    assert changed.headers["location"] == "/bank"


def test_a_refused_submit_says_so_in_its_status(wizard, factory) -> None:
    """It answered 200, so a submit that wrote nothing looked exactly like one
    that worked — in the logs and to anything watching them. Finding out what
    had happened took a query against the database."""
    client, _, cipher = wizard
    seed_ready(factory, cipher, kudos=None)  # no snapshot: the count is unknown

    preview = client.post(
        "/preview",
        data={"csrf_token": token_from(client.get("/additional").text)},
    )
    assert preview.status_code == 422
    assert "not available yet" in preview.text

    refused = client.post(
        "/submit",
        data={
            "csrf_token": token_from(client.get("/additional").text),
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert refused.status_code == 422

    with factory() as session:
        # And it wrote nothing, which is the point of the refusal.
        assert session.scalars(select(Invoice)).all() == []
