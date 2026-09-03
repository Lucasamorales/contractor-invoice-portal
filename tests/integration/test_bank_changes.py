"""The bank-details lifecycle (R-BANK-01 .. R-BANK-08).

The tests that matter here are the ones asserting what an admin *cannot* do.
CLAUDE.md §2.2: only the owner can make a bank change payable, and that is the
control which means a taken-over admin account cannot redirect the payroll.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth import Identity, current_identity
from app.config import Settings, get_settings
from app.db.models import AuditLog, BankDetail, BankSource, BankState, Outbox
from app.db.session import get_session, get_sessionmaker
from app.main import app
from app.services import bank_changes
from app.services.crypto import Cipher, field_aad, generate_key
from app.services.storage import LocalObjectStore
from app.web.member import get_cipher, get_renderer, get_store
from tests.integration.conftest import give_bank, give_counter, give_fee, make_person

pytestmark = pytest.mark.integration

JANE = "jane@example.com"
ROBIN = "robin@example.com"

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)

NEW_ACCOUNT = {
    "account_name": "Jane Smith",
    "bank_name": "Starling",
    "sort_code": "60-83-71",
    "account_number": "87654321",
    "account_number_again": "87654321",
}


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="staging",
        bank_encryption_key=generate_key(),
        csrf_secret="test-csrf-secret",
        kudos_rate_pence=2000,
        finance_inbox=ROBIN,
        finance_emails=(ROBIN,),
    )


@pytest.fixture
def wired(engine: Engine, factory: sessionmaker[Session], tmp_path):
    """A client factory that can log in as either the owner or Robin."""
    settings = _settings()
    cipher = Cipher.from_settings(settings)

    def db() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    def as_user(email: str, *, finance: bool):
        app.dependency_overrides[current_identity] = lambda: Identity(
            email=email, subject=email, is_finance=finance
        )
        app.dependency_overrides[get_session] = db
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_cipher] = lambda: cipher
        app.dependency_overrides[get_store] = lambda: LocalObjectStore(tmp_path / "o")
        app.dependency_overrides[get_sessionmaker] = lambda: factory
        app.dependency_overrides[get_renderer] = lambda: lambda c: b"%PDF"
        return TestClient(app)

    yield as_user, settings, cipher
    app.dependency_overrides.clear()


def token_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if match is None:
        match = re.search(r"X-CSRF-Token&#34;: &#34;([^&]+)&#34;", html)
    assert match, "no csrf token rendered"
    return match.group(1)


def seed(factory, cipher, *, verified: bool = True):
    with factory() as session, session.begin():
        person = make_person(session, email=JANE)
        give_fee(session, person)
        give_bank(
            session,
            person,
            cipher,
            state=BankState.ACTIVE if verified else BankState.UNVERIFIED,
            verified=verified,
        )
        give_counter(session, person, 8)
        return person.id


def plaintext(session, cipher, detail: BankDetail, field: str) -> str:
    stored = getattr(detail, f"{field}_enc")
    return cipher.decrypt(stored, aad=field_aad(str(detail.person_id), field))


# --------------------------------------------------------------------------
# The owner changes their own details (R-BANK-03, R-BANK-05)
# --------------------------------------------------------------------------


def test_a_change_creates_a_new_row_and_closes_the_old_one(wired, factory) -> None:
    as_user, _, cipher = wired
    person_id = seed(factory, cipher)

    with as_user(JANE, finance=False) as client:
        page = client.get("/bank/change")
        response = client.post(
            "/bank/change",
            data={"csrf_token": token_from(page.text), **NEW_ACCOUNT},
            follow_redirects=False,
        )
    assert response.status_code == 303

    with factory() as session:
        rows = session.scalars(
            select(BankDetail)
            .where(BankDetail.person_id == person_id)
            .order_by(BankDetail.created_at)
        ).all()
        assert len(rows) == 2, "a change adds a row, it never edits one"
        old, new = rows
        assert old.state is BankState.SUPERSEDED
        assert old.valid_to is not None
        assert plaintext(session, cipher, old, "account_number") == "12345678"

        assert new.state is BankState.ACTIVE
        assert new.source is BankSource.SELF
        assert new.verified_by == JANE
        assert new.verified_at is not None
        assert plaintext(session, cipher, new, "account_number") == "87654321"
        assert plaintext(session, cipher, new, "sort_code") == "608371"


def test_only_one_row_is_ever_active(wired, factory) -> None:
    """The database index enforces it; this proves the change path respects it."""
    as_user, _, cipher = wired
    person_id = seed(factory, cipher)

    with as_user(JANE, finance=False) as client:
        for account in ("87654321", "11223344"):
            page = client.get("/bank/change")
            client.post(
                "/bank/change",
                data={
                    "csrf_token": token_from(page.text),
                    **NEW_ACCOUNT,
                    "account_number": account,
                    "account_number_again": account,
                },
                follow_redirects=False,
            )

    with factory() as session:
        active = session.scalars(
            select(BankDetail).where(
                BankDetail.person_id == person_id,
                BankDetail.state == BankState.ACTIVE,
            )
        ).all()
        assert len(active) == 1
        assert plaintext(session, cipher, active[0], "account_number") == "11223344"


def test_the_account_number_must_be_typed_twice_and_match(wired, factory) -> None:
    """R-BANK-03. Double entry catches what no checksum can."""
    as_user, _, cipher = wired
    seed(factory, cipher)

    with as_user(JANE, finance=False) as client:
        page = client.get("/bank/change")
        response = client.post(
            "/bank/change",
            data={
                "csrf_token": token_from(page.text),
                **NEW_ACCOUNT,
                # A transposition. Both are valid account numbers.
                "account_number_again": "87645321",
            },
        )
    assert response.status_code == 422
    assert "do not match" in response.text

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(BankDetail)) == 1


def test_an_invalid_change_is_refused_with_the_reason(wired, factory) -> None:
    as_user, _, cipher = wired
    seed(factory, cipher)

    with as_user(JANE, finance=False) as client:
        page = client.get("/bank/change")
        response = client.post(
            "/bank/change",
            data={
                "csrf_token": token_from(page.text),
                "account_name": "Jane Smith",
                "bank_name": "Starling",
                "sort_code": "608371",
                "account_number": "1234567",  # seven digits
                "account_number_again": "1234567",
            },
        )
    assert response.status_code == 422
    assert "eight digits" in response.text


def test_the_form_never_echoes_the_account_number_back(wired, factory) -> None:
    as_user, _, cipher = wired
    seed(factory, cipher)

    with as_user(JANE, finance=False) as client:
        page = client.get("/bank/change")
        response = client.post(
            "/bank/change",
            data={
                "csrf_token": token_from(page.text),
                **NEW_ACCOUNT,
                "account_number_again": "99999999",
            },
        )
    assert response.status_code == 422
    assert "87654321" not in response.text


def test_a_change_emails_both_the_person_and_perry_with_last_four_only(
    wired, factory
) -> None:
    """R-BANK-05, and CLAUDE.md §2.8."""
    as_user, _, cipher = wired
    seed(factory, cipher)

    with as_user(JANE, finance=False) as client:
        page = client.get("/bank/change")
        client.post(
            "/bank/change",
            data={"csrf_token": token_from(page.text), **NEW_ACCOUNT},
            follow_redirects=False,
        )

    with factory() as session:
        row = session.scalars(
            select(Outbox).where(Outbox.kind == bank_changes.OUTBOX_BANK_CHANGED)
        ).one()
        payload = row.payload
        assert payload["to_person"] == JANE
        assert payload["to_finance"] == ROBIN
        assert payload["old_account"] == "••••5678"
        assert payload["new_account"] == "••••4321"
        assert "12345678" not in str(payload)
        assert "87654321" not in str(payload)


def test_re_entering_the_same_account_is_refused(wired, factory) -> None:
    as_user, _, cipher = wired
    seed(factory, cipher)

    with as_user(JANE, finance=False) as client:
        page = client.get("/bank/change")
        response = client.post(
            "/bank/change",
            data={
                "csrf_token": token_from(page.text),
                "account_name": "Jane Smith",
                "bank_name": "Monzo",
                "sort_code": "230801",
                "account_number": "12345678",
                "account_number_again": "12345678",
            },
        )
    assert response.status_code == 422
    assert "already have" in response.text


def test_a_change_is_audited(wired, factory) -> None:
    as_user, _, cipher = wired
    seed(factory, cipher)

    with as_user(JANE, finance=False) as client:
        page = client.get("/bank/change")
        client.post(
            "/bank/change",
            data={"csrf_token": token_from(page.text), **NEW_ACCOUNT},
            follow_redirects=False,
        )

    with factory() as session:
        entry = session.scalars(
            select(AuditLog).where(AuditLog.action == "bank.change")
        ).one()
        assert entry.actor_email == JANE
        assert entry.before["account"] == "••••5678"
        assert entry.after["account"] == "••••4321"


# --------------------------------------------------------------------------
# An admin proposes; only the owner confirms (R-BANK-07, CLAUDE.md §2.2)
# --------------------------------------------------------------------------


def test_a_proposal_lands_pending_and_does_not_change_what_is_paid(
    wired, factory
) -> None:
    as_user, _, cipher = wired
    person_id = seed(factory, cipher)

    with as_user(ROBIN, finance=True) as client:
        page = client.get(f"/admin/people/{person_id}")
        response = client.post(
            f"/admin/people/{person_id}/bank/propose",
            data={"csrf_token": token_from(page.text), **NEW_ACCOUNT},
            follow_redirects=False,
        )
    assert response.status_code == 303

    with factory() as session:
        proposed = session.scalars(
            select(BankDetail).where(BankDetail.state == BankState.PENDING)
        ).one()
        assert proposed.source is BankSource.ADMIN_PROPOSED
        assert proposed.verified_at is None

        # The payable row is untouched.
        active = session.scalars(
            select(BankDetail).where(BankDetail.state == BankState.ACTIVE)
        ).one()
        assert plaintext(session, cipher, active, "account_number") == "12345678"


def test_an_admin_cannot_make_a_row_payable_by_any_route(wired, factory) -> None:
    """CLAUDE.md §2.2, tested against every write route the admin has.

    The member confirm route exists, but it acts on the caller's own person,
    so Robin calling it would only ever confirm Robin's own details.
    """
    as_user, _, cipher = wired
    person_id = seed(factory, cipher)

    with as_user(ROBIN, finance=True) as client:
        page = client.get(f"/admin/people/{person_id}")
        client.post(
            f"/admin/people/{person_id}/bank/propose",
            data={"csrf_token": token_from(page.text), **NEW_ACCOUNT},
            follow_redirects=False,
        )
        with factory() as session:
            pending_id = session.scalars(
                select(BankDetail.id).where(BankDetail.state == BankState.PENDING)
            ).one()

        # Robin confirming: the route resolves the person from *his* identity,
        # so it cannot touch Jane's row.
        response = client.post(
            "/bank/confirm",
            data={"csrf_token": token_from(page.text), "detail_id": str(pending_id)},
        )
        assert response.status_code == 400

    with factory() as session:
        still_pending = session.get(BankDetail, pending_id)
        assert still_pending.state is BankState.PENDING
        active = session.scalars(
            select(BankDetail).where(BankDetail.state == BankState.ACTIVE)
        ).one()
        assert plaintext(session, cipher, active, "account_number") == "12345678"


def test_the_owner_confirming_a_proposal_makes_it_payable(wired, factory) -> None:
    as_user, _, cipher = wired
    person_id = seed(factory, cipher)

    with as_user(ROBIN, finance=True) as client:
        page = client.get(f"/admin/people/{person_id}")
        client.post(
            f"/admin/people/{person_id}/bank/propose",
            data={"csrf_token": token_from(page.text), **NEW_ACCOUNT},
            follow_redirects=False,
        )

    with as_user(JANE, finance=False) as client:
        bank_page = client.get("/bank")
        assert "Robin has suggested a change" in bank_page.text
        assert "••••4321" in bank_page.text

        with factory() as session:
            pending_id = session.scalars(
                select(BankDetail.id).where(BankDetail.state == BankState.PENDING)
            ).one()

        response = client.post(
            "/bank/confirm",
            data={
                "csrf_token": token_from(bank_page.text),
                "detail_id": str(pending_id),
            },
            follow_redirects=False,
        )
    assert response.status_code == 303

    with factory() as session:
        active = session.scalars(
            select(BankDetail).where(BankDetail.state == BankState.ACTIVE)
        ).one()
        assert active.id == pending_id
        assert active.verified_by == JANE
        assert plaintext(session, cipher, active, "account_number") == "87654321"
        # The row it replaced is closed, not deleted.
        superseded = session.scalars(
            select(BankDetail).where(BankDetail.state == BankState.SUPERSEDED)
        ).one()
        assert plaintext(session, cipher, superseded, "account_number") == "12345678"


def test_only_one_proposal_at_a_time(wired, factory) -> None:
    as_user, _, cipher = wired
    person_id = seed(factory, cipher)

    with as_user(ROBIN, finance=True) as client:
        page = client.get(f"/admin/people/{person_id}")
        client.post(
            f"/admin/people/{person_id}/bank/propose",
            data={"csrf_token": token_from(page.text), **NEW_ACCOUNT},
            follow_redirects=False,
        )
        response = client.post(
            f"/admin/people/{person_id}/bank/propose",
            data={"csrf_token": token_from(page.text), **NEW_ACCOUNT},
        )
    assert response.status_code == 422
    assert "already a proposed change" in response.text


def test_proposing_emails_the_person_and_is_audited(wired, factory) -> None:
    as_user, _, cipher = wired
    person_id = seed(factory, cipher)

    with as_user(ROBIN, finance=True) as client:
        page = client.get(f"/admin/people/{person_id}")
        client.post(
            f"/admin/people/{person_id}/bank/propose",
            data={"csrf_token": token_from(page.text), **NEW_ACCOUNT},
            follow_redirects=False,
        )

    with factory() as session:
        row = session.scalars(
            select(Outbox).where(Outbox.kind == bank_changes.OUTBOX_BANK_PROPOSED)
        ).one()
        assert row.payload["to_person"] == JANE
        assert row.payload["proposed_account"] == "••••4321"
        assert "87654321" not in str(row.payload)

        entry = session.scalars(
            select(AuditLog).where(AuditLog.action == "bank.propose")
        ).one()
        assert entry.actor_email == ROBIN


# --------------------------------------------------------------------------
# Flagging back (R-BANK-08)
# --------------------------------------------------------------------------


def test_flagging_emails_the_person_and_changes_nothing(wired, factory) -> None:
    as_user, _, cipher = wired
    person_id = seed(factory, cipher)

    with as_user(ROBIN, finance=True) as client:
        page = client.get(f"/admin/people/{person_id}")
        bank_id = re.search(r'action="/admin/bank/([0-9a-f-]+)/flag"', page.text).group(
            1
        )
        response = client.post(
            f"/admin/bank/{bank_id}/flag",
            data={
                "csrf_token": token_from(page.text),
                "reason": "the sort code looks like a Wise one, please check",
            },
            follow_redirects=False,
        )
    assert response.status_code == 303

    with factory() as session:
        row = session.scalars(
            select(Outbox).where(Outbox.kind == bank_changes.OUTBOX_BANK_FLAGGED)
        ).one()
        assert row.payload["to_person"] == JANE
        assert "Wise" in row.payload["reason"]

        # Nothing about what is payable moved.
        active = session.scalars(
            select(BankDetail).where(BankDetail.state == BankState.ACTIVE)
        ).one()
        assert plaintext(session, cipher, active, "account_number") == "12345678"

        assert session.scalars(
            select(AuditLog).where(AuditLog.action == "bank.flag")
        ).one()


def test_flagging_without_a_reason_is_refused(wired, factory) -> None:
    as_user, _, cipher = wired
    person_id = seed(factory, cipher)

    with as_user(ROBIN, finance=True) as client:
        page = client.get(f"/admin/people/{person_id}")
        bank_id = re.search(r'action="/admin/bank/([0-9a-f-]+)/flag"', page.text).group(
            1
        )
        response = client.post(
            f"/admin/bank/{bank_id}/flag",
            data={"csrf_token": token_from(page.text), "reason": "  "},
        )
    assert response.status_code == 422
    assert "Say what needs fixing" in response.text


# --------------------------------------------------------------------------
# Invoicing follows the change (R-BANK-02)
# --------------------------------------------------------------------------


def test_a_changed_account_is_immediately_the_one_that_would_be_invoiced(
    wired, factory
) -> None:
    as_user, _, cipher = wired
    person_id = seed(factory, cipher)

    with as_user(JANE, finance=False) as client:
        page = client.get("/bank/change")
        client.post(
            "/bank/change",
            data={"csrf_token": token_from(page.text), **NEW_ACCOUNT},
            follow_redirects=False,
        )

    from app.db import repo

    with factory() as session:
        active = repo.active_bank_detail(session, person_id)
        assert plaintext(session, cipher, active, "account_number") == "87654321"
        assert active.verified_at is not None  # R-BANK-02: payable
