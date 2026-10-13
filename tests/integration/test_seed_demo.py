"""Invented people for staging (app/scripts/seed_demo.py, R-ENV-02).

Staging must never hold real data, so this script is how its screens get
filled. What matters is that it cannot run where real people are, that running
it twice changes nothing, and that the bank details it writes go through the
same encryption as real ones.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.db.models import BankDetail, BankState, Counter, Fee, KudosSnapshot, Person
from app.scripts import seed_demo
from app.services.crypto import Cipher, CryptoError, field_aad, generate_key
from tests.integration.conftest import make_person

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
EMAILS = {spec["email"] for spec in seed_demo.PEOPLE}


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def test_every_demo_person_is_on_the_reserved_demo_addresses() -> None:
    """The safety check recognises its own rows by this prefix and domain."""
    assert EMAILS, "the script has people to create"
    for email in EMAILS:
        assert email.startswith("demo.")
        assert email.endswith(seed_demo.DEMO_DOMAIN)


def test_build_creates_each_person_with_a_counter_and_this_months_kudos(
    factory, cipher
) -> None:
    with factory() as session, session.begin():
        assert seed_demo.build(session, cipher, now=NOW) == len(seed_demo.PEOPLE)

    with factory() as session:
        people = session.scalars(select(Person)).all()
        assert {p.email for p in people} == EMAILS
        assert all(p.profile_confirmed_at is not None for p in people)
        assert _count(session, Counter) == len(people)
        periods = set(session.scalars(select(KudosSnapshot.period)))
        assert periods == {"2026-09"}
        with_fee = sum(1 for spec in seed_demo.PEOPLE if spec["fee"] is not None)
        assert _count(session, Fee) == with_fee


def test_bank_details_are_stored_encrypted_and_decrypt_to_the_invented_values(
    factory, cipher
) -> None:
    with factory() as session, session.begin():
        seed_demo.build(session, cipher, now=NOW)

    with factory() as session:
        for spec in seed_demo.PEOPLE:
            person = session.scalar(select(Person).where(Person.email == spec["email"]))
            detail = session.scalar(
                select(BankDetail).where(BankDetail.person_id == person.id)
            )
            for name in ("sort_code", "account_number", "iban", "swift_bic"):
                stored = getattr(detail, f"{name}_enc")
                if not spec.get(name):
                    assert stored is None
                    continue
                assert spec[name] not in stored
                plain = cipher.decrypt(stored, aad=field_aad(str(person.id), name))
                assert plain == spec[name]


def test_only_a_verified_account_is_marked_verified(factory, cipher) -> None:
    with factory() as session, session.begin():
        seed_demo.build(session, cipher, now=NOW)

    with factory() as session:
        details = session.scalars(select(BankDetail)).all()
        assert {d.state for d in details} >= {BankState.ACTIVE, BankState.UNVERIFIED}
        for detail in details:
            if detail.state is BankState.ACTIVE:
                assert detail.verified_at == NOW
                assert detail.verified_by
            else:
                assert detail.verified_at is None
                assert detail.verified_by is None


def test_building_twice_creates_nothing_new(factory, cipher) -> None:
    with factory() as session, session.begin():
        seed_demo.build(session, cipher, now=NOW)
    with factory() as session, session.begin():
        assert seed_demo.build(session, cipher, now=NOW) == 0

    with factory() as session:
        assert _count(session, Person) == len(seed_demo.PEOPLE)
        assert _count(session, BankDetail) == len(seed_demo.PEOPLE)
        assert _count(session, Counter) == len(seed_demo.PEOPLE)


def test_production_is_refused(factory) -> None:
    production = Settings(_env_file=None, environment="production")
    with factory() as session, pytest.raises(seed_demo.DemoRefused, match="production"):
        seed_demo.refuse_if_unsafe(production, session)


def test_a_database_holding_anybody_else_is_refused(factory) -> None:
    """In case the URL points somewhere other than the staging it was meant for."""
    staging = Settings(_env_file=None, environment="staging")
    with factory() as session, session.begin():
        make_person(session, email="jane@example.com")
    with factory() as session, pytest.raises(seed_demo.DemoRefused, match="1 people"):
        seed_demo.refuse_if_unsafe(staging, session)


def test_a_database_holding_only_demo_people_is_accepted(factory, cipher) -> None:
    staging = Settings(_env_file=None, environment="staging")
    with factory() as session, session.begin():
        seed_demo.build(session, cipher, now=NOW)
    with factory() as session:
        seed_demo.refuse_if_unsafe(staging, session)


def _run(monkeypatch, factory, environment: str, *argv: str) -> int:
    settings = Settings(
        _env_file=None, environment=environment, bank_encryption_key=generate_key()
    )
    monkeypatch.setattr(seed_demo, "get_settings", lambda: settings)
    monkeypatch.setattr(seed_demo, "get_sessionmaker", lambda: factory)
    return seed_demo.main(list(argv))


def test_without_commit_it_lists_the_people_and_writes_nothing(
    monkeypatch, factory, capsys
) -> None:
    assert _run(monkeypatch, factory, "staging") == 0
    out = capsys.readouterr().out
    assert (
        f"Would create up to {len(seed_demo.PEOPLE)} invented people in staging" in out
    )
    for spec in seed_demo.PEOPLE:
        assert spec["display_name"] in out
    with factory() as session:
        assert _count(session, Person) == 0


def test_commit_creates_them_and_a_second_run_reports_them_already_there(
    monkeypatch, factory, capsys
) -> None:
    total = len(seed_demo.PEOPLE)
    assert _run(monkeypatch, factory, "staging", "--commit") == 0
    assert f"staging: {total} created, 0 already there." in capsys.readouterr().out
    assert _run(monkeypatch, factory, "staging", "--commit") == 0
    assert f"staging: 0 created, {total} already there." in capsys.readouterr().out


def test_the_command_refuses_production_and_writes_nothing(
    monkeypatch, factory, capsys
) -> None:
    assert _run(monkeypatch, factory, "production", "--commit") == 2
    assert "Refused:" in capsys.readouterr().out
    with factory() as session:
        assert _count(session, Person) == 0


def test_the_cipher_used_is_the_one_from_settings(monkeypatch, factory) -> None:
    """A row written with one key must not be readable with another."""
    assert _run(monkeypatch, factory, "staging", "--commit") == 0
    other = Cipher.from_settings(
        Settings(_env_file=None, bank_encryption_key=generate_key())
    )
    with factory() as session:
        person = session.scalar(select(Person))
        detail = session.scalar(
            select(BankDetail).where(BankDetail.person_id == person.id)
        )
        name = next(
            n
            for n in ("sort_code", "account_number", "iban", "swift_bic")
            if getattr(detail, f"{n}_enc") is not None
        )
        with pytest.raises(CryptoError):
            other.decrypt(
                getattr(detail, f"{name}_enc"), aad=field_aad(str(person.id), name)
            )
