"""Moving somebody between payout regions (R-SEED-03, R-BANK-02).

The real case: somebody paid into an EUR account moved to a UK one, and the
bank screen had no sort-code field to offer her because the region decides both
the fields and the validation.

The property that matters most here is the one in the middle: between the
region moving and the new account being confirmed, she must be *blocked* from
invoicing rather than allowed to submit something that cannot be rendered.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import (
    AuditLog,
    BankState,
    Invoice,
    InvoiceStatus,
    PayoutRegion,
    Person,
)
from app.domain.period import Period
from app.scripts.set_payout_region import (
    BANK_ACTION,
    REGION_ACTION,
    RegionChangeRefused,
    change_region,
)
from app.services.crypto import Cipher
from app.services.invoicing import SubmissionBlocked, prepare
from tests.integration.conftest import (
    IN_WINDOW,
    give_bank,
    give_counter,
    give_fee,
    make_person,
)

pytestmark = pytest.mark.integration

JAMIE = "jamie@example.com"
ACCOUNTS = "accounts@example.com"
REASON = "moving from an EUR account to a UK one"


def settings_for() -> Settings:
    return Settings(_env_file=None, environment="staging", finance_emails=(ACCOUNTS,))


def seeded(factory: sessionmaker[Session], cipher: Cipher) -> uuid.UUID:
    with factory() as session, session.begin():
        person = make_person(session, email=JAMIE, region=PayoutRegion.INTL)
        give_fee(session, person)
        give_bank(session, person, cipher, state=BankState.ACTIVE, verified=True)
        give_counter(session, person, 10)
        return person.id


def test_the_region_moves_and_the_old_account_is_retired(factory, cipher):
    person_id = seeded(factory, cipher)

    with factory() as session, session.begin():
        change = change_region(
            session, email=JAMIE, region=PayoutRegion.UK, actor=ACCOUNTS, reason=REASON
        )
        assert change.before is PayoutRegion.INTL
        assert change.retired is not None

    with factory() as session:
        person = session.get(Person, person_id)
        assert person.payout_region is PayoutRegion.UK
        assert [row.state for row in person.bank_details] == [BankState.SUPERSEDED]
        assert person.bank_details[0].valid_to is not None


def test_she_cannot_invoice_until_the_new_account_is_confirmed(factory, cipher):
    """The whole reason the two halves happen together.

    With the region moved and the old international row still active, nothing
    would stop her submitting — and the PDF would be rendered against UK fields
    she does not have, after the invoice number had already been allocated.
    """
    person_id = seeded(factory, cipher)

    with factory() as session, session.begin():
        change_region(
            session, email=JAMIE, region=PayoutRegion.UK, actor=ACCOUNTS, reason=REASON
        )

    with factory() as session:
        person = session.get(Person, person_id)
        with pytest.raises(SubmissionBlocked) as blocked:
            prepare(
                session,
                person=person,
                period=Period.containing(IN_WINDOW),
                now=IN_WINDOW,
                settings=settings_for(),
            )
    assert blocked.value.reason == "bank_unconfirmed"


def test_both_halves_are_audited(factory, cipher):
    person_id = seeded(factory, cipher)

    with factory() as session, session.begin():
        change_region(
            session, email=JAMIE, region=PayoutRegion.UK, actor=ACCOUNTS, reason=REASON
        )

    with factory() as session:
        entries = {row.action: row for row in session.scalars(select(AuditLog)).all()}
        region = entries[REGION_ACTION]
        assert region.actor_email == ACCOUNTS
        assert region.entity_id == str(person_id)
        assert region.before == {"payout_region": "INTL"}
        assert region.after["payout_region"] == "UK"
        assert region.after["reason"] == REASON

        retired = entries[BANK_ACTION]
        assert retired.before == {"state": "active"}
        assert retired.after["state"] == "superseded"


def test_an_unfinished_invoice_stops_it(factory, cipher):
    """A draft holds a number and will be rendered later, against whatever the
    region says then."""
    person_id = seeded(factory, cipher)
    with factory() as session, session.begin():
        session.add(
            Invoice(
                person_id=person_id,
                period="2026-09",
                number=10,
                status=InvoiceStatus.DRAFT,
                remuneration_pence=250000,
                additional_pence=0,
                total_pence=250000,
                idempotency_key="draft-key",
            )
        )

    with (
        factory() as session,
        session.begin(),
        pytest.raises(RegionChangeRefused, match="unfinished invoice"),
    ):
        change_region(
            session, email=JAMIE, region=PayoutRegion.UK, actor=ACCOUNTS, reason=REASON
        )

    with factory() as session:
        assert session.get(Person, person_id).payout_region is PayoutRegion.INTL


def test_moving_somebody_who_is_already_there_is_refused(factory, cipher):
    seeded(factory, cipher)
    with (
        factory() as session,
        session.begin(),
        pytest.raises(RegionChangeRefused, match="already INTL"),
    ):
        change_region(
            session,
            email=JAMIE,
            region=PayoutRegion.INTL,
            actor=ACCOUNTS,
            reason=REASON,
        )


def test_an_unknown_person_is_refused(factory):
    with (
        factory() as session,
        session.begin(),
        pytest.raises(RegionChangeRefused, match="no person"),
    ):
        change_region(
            session,
            email="nobody@example.com",
            region=PayoutRegion.UK,
            actor=ACCOUNTS,
            reason=REASON,
        )
