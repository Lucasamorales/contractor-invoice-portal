"""Flags, from accounts raising one to the person answering it (R-BANK-08).

Two people are seeded in every test that can tell them apart, because the
failure that matters is a flag reaching, or showing to, the wrong person.
"""

# The `wired` fixture is imported from the bank-change tests, and ruff reads each
# test that asks for it by name as redefining the import.
# ruff: noqa: F811

from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, BankState, Outbox
from app.services import flags
from tests.integration.conftest import give_bank, give_counter, give_fee, make_person
from tests.integration.test_bank_changes import (  # noqa: F401 - fixture
    JANE,
    NEW_ACCOUNT,
    ROBIN,
    seed,
    token_from,
    wired,
)

pytestmark = pytest.mark.integration

SAM = "sam@example.com"
BANK_REASON = "the sort code looks like a Wise one, please check"
LETTERHEAD_REASON = "your role still says Junior Designer"


def seed_sam(factory, cipher):
    with factory() as session, session.begin():
        person = make_person(session, email=SAM)
        person.display_name = "Sam Taylor"
        give_fee(session, person)
        give_bank(session, person, cipher, state=BankState.ACTIVE, verified=True)
        give_counter(session, person, 3)
        return person.id


def flag_bank(as_user, person_id, reason=BANK_REASON):
    with as_user(ROBIN, finance=True) as client:
        page = client.get(f"/admin/people/{person_id}?panel=flag")
        bank_id = re.search(r'action="/admin/bank/([0-9a-f-]+)/flag"', page.text)
        return client.post(
            f"/admin/bank/{bank_id.group(1)}/flag",
            data={"csrf_token": token_from(page.text), "reason": reason},
            follow_redirects=False,
        )


def flag_letterhead(as_user, person_id, reason=LETTERHEAD_REASON):
    with as_user(ROBIN, finance=True) as client:
        page = client.get(f"/admin/people/{person_id}?panel=flag-letterhead")
        return client.post(
            f"/admin/people/{person_id}/letterhead/flag",
            data={"csrf_token": token_from(page.text), "reason": reason},
            follow_redirects=False,
        )


def answer(as_user, email, kind, back="/me"):
    with as_user(email, finance=False) as client:
        page = client.get(back)
        return client.post(
            f"/flags/{kind}/correct",
            data={"csrf_token": token_from(page.text), "back": back},
            follow_redirects=False,
        )


def page_for(as_user, email, path, *, finance=False):
    with as_user(email, finance=finance) as client:
        return client.get(path).text


# --------------------------------------------------------------------------
# Raising one
# --------------------------------------------------------------------------


def test_a_letterhead_flag_is_emailed_to_that_person_and_nobody_else(
    wired, factory
) -> None:
    as_user, _, cipher = wired
    jane = seed(factory, cipher)
    seed_sam(factory, cipher)
    with factory() as session, session.begin():
        from app.db.models import Person

        # A contact address that is not the sign-in one: the flag must still
        # reach the address Google has verified as hers.
        session.get(Person, jane).contact_email = "jane.smith@other.test"

    assert flag_letterhead(as_user, jane).status_code == 303

    with factory() as session:
        row = session.scalars(
            select(Outbox).where(Outbox.kind == flags.OUTBOX_LETTERHEAD_FLAGGED)
        ).one()
        assert row.payload["to_person"] == [JANE, "jane.smith@other.test"]
        assert SAM not in str(row.payload)
        assert row.payload["reason"] == LETTERHEAD_REASON
        assert session.scalars(
            select(AuditLog).where(AuditLog.action == "letterhead.flag")
        ).one().entity_id == str(jane)


def test_a_bank_flag_carrying_a_full_account_number_is_refused(wired, factory) -> None:
    """CLAUDE.md §2.8: the reason is emailed as typed."""
    as_user, _, cipher = wired
    jane = seed(factory, cipher)

    for reason in ("the number 1234 5678 looks wrong", "GB82 WEST 1234 5698 7654 32"):
        response = flag_bank(as_user, jane, reason)
        assert response.status_code == 422
        assert "Leave out the full account number" in response.text

    assert (
        flag_bank(as_user, jane, "the account ending 5678 looks wrong").status_code
        == 303
    )
    with factory() as session:
        assert len(session.scalars(select(Outbox)).all()) == 1


def test_a_letterhead_flag_may_mention_a_phone_number(wired, factory) -> None:
    as_user, _, cipher = wired
    jane = seed(factory, cipher)
    assert (
        flag_letterhead(as_user, jane, "phone should be 07700 900123").status_code
        == 303
    )


# --------------------------------------------------------------------------
# Seeing it
# --------------------------------------------------------------------------


def test_a_flag_shows_to_its_owner_and_to_nobody_else(wired, factory) -> None:
    as_user, _, cipher = wired
    jane = seed(factory, cipher)
    seed_sam(factory, cipher)
    flag_bank(as_user, jane)
    flag_letterhead(as_user, jane)

    me = page_for(as_user, JANE, "/me")
    assert BANK_REASON in me and LETTERHEAD_REASON in me
    assert BANK_REASON in page_for(as_user, JANE, "/bank")
    assert LETTERHEAD_REASON in page_for(as_user, JANE, "/details")

    for path in ("/me", "/bank", "/details"):
        other = page_for(as_user, SAM, path)
        assert BANK_REASON not in other and LETTERHEAD_REASON not in other


def test_open_flags_wait_on_the_queue(wired, factory) -> None:
    as_user, _, cipher = wired
    jane = seed(factory, cipher)
    flag_bank(as_user, jane)
    flag_letterhead(as_user, jane)

    queue = page_for(as_user, ROBIN, "/admin/queue", finance=True)
    assert "Bank flagged" in queue and "Letterhead flagged" in queue
    person = page_for(as_user, ROBIN, f"/admin/people/{jane}", finance=True)
    assert person.count("waiting on Jane") == 2


# --------------------------------------------------------------------------
# Answering it
# --------------------------------------------------------------------------


def test_it_is_correct_closes_the_flag_and_tells_accounts(wired, factory) -> None:
    as_user, _, cipher = wired
    jane = seed(factory, cipher)
    flag_bank(as_user, jane)

    response = answer(as_user, JANE, flags.BANK, back="/bank")
    assert response.status_code == 303
    assert response.headers["location"] == "/bank"

    assert BANK_REASON not in page_for(as_user, JANE, "/me")
    with factory() as session:
        assert (
            session.scalars(
                select(AuditLog).where(AuditLog.action == "bank.flag_answered")
            )
            .one()
            .actor_email
            == JANE
        )
        told = session.scalars(
            select(Outbox).where(Outbox.kind == flags.OUTBOX_FLAG_ANSWERED)
        ).one()
        assert told.payload["to_finance"] == ROBIN
        assert told.payload["reason"] == BANK_REASON

    person = page_for(as_user, ROBIN, f"/admin/people/{jane}", finance=True)
    assert "they said it is correct" in person
    assert "Bank flagged" not in page_for(as_user, ROBIN, "/admin/queue", finance=True)


def test_changing_the_account_closes_a_bank_flag(wired, factory) -> None:
    as_user, _, cipher = wired
    jane = seed(factory, cipher)
    flag_bank(as_user, jane)

    with as_user(JANE, finance=False) as client:
        page = client.get("/bank/change")
        client.post(
            "/bank/change", data={"csrf_token": token_from(page.text), **NEW_ACCOUNT}
        )

    assert BANK_REASON not in page_for(as_user, JANE, "/me")
    person = page_for(as_user, ROBIN, f"/admin/people/{jane}", finance=True)
    assert "changed" in person and "waiting on Jane" not in person


def test_saving_the_letterhead_closes_its_flag(wired, factory) -> None:
    as_user, _, cipher = wired
    jane = seed(factory, cipher)
    flag_letterhead(as_user, jane)

    with as_user(JANE, finance=False) as client:
        page = client.get("/details")
        client.post(
            "/details",
            data={
                "csrf_token": token_from(page.text),
                "legal_name": "Jane Smith",
                "role": "Senior Designer",
                "address": "12 Example Street",
                "phone": "+44 7700 900000",
                "contact_email": JANE,
            },
        )

    assert LETTERHEAD_REASON not in page_for(as_user, JANE, "/details")
    with factory() as session:
        assert not session.scalars(
            select(Outbox).where(Outbox.kind == flags.OUTBOX_FLAG_ANSWERED)
        ).all(), "a change is answered by the change, not by an email"


def test_nobody_can_answer_somebody_elses_flag(wired, factory) -> None:
    as_user, _, cipher = wired
    jane = seed(factory, cipher)
    seed_sam(factory, cipher)
    flag_bank(as_user, jane)

    # Sam has no flag of his own, so his press answers nothing, and Jane's stays.
    assert answer(as_user, SAM, flags.BANK).status_code == 303
    assert BANK_REASON in page_for(as_user, JANE, "/me")
    with factory() as session:
        assert not session.scalars(
            select(AuditLog).where(AuditLog.action == "bank.flag_answered")
        ).all()


def test_only_known_kinds_and_safe_returns(wired, factory) -> None:
    as_user, _, cipher = wired
    jane = seed(factory, cipher)
    flag_bank(as_user, jane)

    assert answer(as_user, JANE, "fee").status_code == 404
    with as_user(JANE, finance=False) as client:
        page = client.get("/me")
        response = client.post(
            "/flags/bank/correct",
            data={"csrf_token": token_from(page.text), "back": "https://evil.example"},
            follow_redirects=False,
        )
    assert response.headers["location"] == "/me"


def test_a_new_flag_after_an_answer_opens_again(wired, factory) -> None:
    as_user, _, cipher = wired
    jane = seed(factory, cipher)
    flag_bank(as_user, jane)
    answer(as_user, JANE, flags.BANK)
    flag_bank(as_user, jane, "still not right, the name is different")

    assert "still not right" in page_for(as_user, JANE, "/me")
