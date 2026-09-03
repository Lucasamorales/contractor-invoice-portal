"""Invented people for staging, so the screens have something in them.

    uv run python -m app.scripts.seed_demo --commit

**Every name, fee and account number here is made up.** R-ENV-02 is absolute:
production data never reaches staging. The moment a real account number exists
in staging, staging is production with weaker access rules. So this generates
data that shares the *structure* of the real thing and none of its content.

It refuses to run against production, and refuses again if the database it is
pointed at already has people who did not come from here.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Environment, Settings, get_settings
from app.db.models import (
    BankDetail,
    BankSource,
    BankState,
    Counter,
    Fee,
    KudosSnapshot,
    PayoutRegion,
    Person,
    PersonStatus,
)
from app.db.session import get_sessionmaker
from app.domain.period import Period
from app.services.crypto import Cipher, field_aad

# The marker that says a row came from here and is safe to remove.
DEMO_DOMAIN = "@example.com"
DEMO_TAG = "demo"


class DemoRefused(RuntimeError):
    """Refusing to touch something that might be real."""


# Invented. Shaped like the real spreadsheet — a mix of teams, one
# international payee, one person with no fee yet, one who pays into a company
# — so every admin screen has something interesting on it.
PEOPLE = [
    {
        "email": f"demo.amy{DEMO_DOMAIN}",
        "display_name": "Amy Demo",
        "legal_name": "Amy Demo",
        "role": "Creative Director",
        "team": "Design",
        "region": PayoutRegion.UK,
        "fee": 285000,
        "sort_code": "040075",
        "account_number": "51234567",
        "kudos": 3,
        "next_number": 34,
    },
    {
        "email": f"demo.ben{DEMO_DOMAIN}",
        "display_name": "Ben Demo",
        "legal_name": "Benjamin Demo",
        "role": "Motion Designer",
        "team": "Design",
        "region": PayoutRegion.UK,
        "fee": 240000,
        "sort_code": "230801",
        "account_number": "62345678",
        "kudos": 0,
        "next_number": 8,
    },
    {
        "email": f"demo.carla{DEMO_DOMAIN}",
        "display_name": "Carla Demo",
        "legal_name": "Demo Holdings",  # pays into a company, like Jamie
        "role": "Producer",
        "team": "Production",
        "region": PayoutRegion.INTL,
        "fee": 310000,
        "iban": "LU280019400644750000",
        "swift_bic": "TRWIBEB1XXX",
        "kudos": 5,
        "next_number": 2,
    },
    {
        "email": f"demo.dan{DEMO_DOMAIN}",
        "display_name": "Dan Demo",
        "legal_name": "Daniel Demo",
        "role": "Developer",
        "team": "Build",
        "region": PayoutRegion.UK,
        "fee": 265000,
        "sort_code": "608371",
        "account_number": "73456789",
        "kudos": 1,
        "next_number": 213,
    },
    {
        "email": f"demo.eve{DEMO_DOMAIN}",
        "display_name": "Eve Demo",
        "legal_name": "Eve Demo",
        "role": "Copywriter",
        "team": "Design",
        "region": PayoutRegion.UK,
        # No fee: shows up in the queue and on the data check as not ready.
        "fee": None,
        "sort_code": "090128",
        "account_number": "84567890",
        "kudos": 2,
        "next_number": 1,
    },
    {
        "email": f"demo.femi{DEMO_DOMAIN}",
        "display_name": "Femi Demo",
        "legal_name": "Femi Demo",
        "role": "Strategist",
        "team": "Production",
        "region": PayoutRegion.UK,
        "fee": 250000,
        "sort_code": "560003",
        "account_number": "95678901",
        "kudos": 0,
        "next_number": 16,
        # Never confirmed their details: cannot invoice, shows on the queue.
        "bank_state": BankState.UNVERIFIED,
    },
]


def refuse_if_unsafe(settings: Settings, session: Session) -> None:
    """Two checks, both of which have to pass before anything is written."""
    if settings.environment is Environment.PRODUCTION:
        raise DemoRefused(
            "This writes invented people. It must never run against production "
            "(R-ENV-02)."
        )

    real = session.scalar(
        select(func.count()).select_from(Person).where(~Person.email.like("demo.%"))
    )
    if real:
        raise DemoRefused(
            f"That database already has {real} people who did not come from "
            "this script. Refusing, in case it is not the one you meant."
        )


def build(session: Session, cipher: Cipher, *, now: datetime) -> int:
    """Create the invented people. Idempotent on email."""
    period = Period.containing(now)
    created = 0

    for spec in PEOPLE:
        person = session.scalar(select(Person).where(Person.email == spec["email"]))
        if person is None:
            person = Person(email=spec["email"])
            session.add(person)
            created += 1

        person.display_name = spec["display_name"]
        person.legal_name = spec["legal_name"]
        person.role = spec["role"]
        person.team = spec["team"]
        person.address = "1 Invented Street\nLondon N1 1AA"
        person.phone = "+44 7700 900000"
        person.contact_email = spec["email"]
        person.payout_region = spec["region"]
        person.status = PersonStatus.ACTIVE
        person.profile_confirmed_at = now
        session.flush()

        if spec["fee"] is not None and not session.scalar(
            select(Fee).where(Fee.person_id == person.id)
        ):
            session.add(
                Fee(
                    person_id=person.id,
                    fee_pence=spec["fee"],
                    valid_from=date(now.year, 1, 1),
                    set_by=f"demo.setup{DEMO_DOMAIN}",
                    note="invented figure for staging",
                )
            )

        if session.get(Counter, person.id) is None:
            session.add(Counter(person_id=person.id, next_number=spec["next_number"]))

        if session.get(KudosSnapshot, (person.id, period.key)) is None:
            session.add(
                KudosSnapshot(
                    person_id=person.id,
                    period=period.key,
                    kudos_count=spec["kudos"],
                    rate_pence=2000,
                )
            )

        if not session.scalar(
            select(BankDetail).where(BankDetail.person_id == person.id)
        ):
            state = spec.get("bank_state", BankState.ACTIVE)
            detail = BankDetail(
                person_id=person.id,
                account_name=spec["legal_name"],
                bank_name="Demo Bank",
                source=BankSource.IMPORT,
                state=state,
                verified_at=now if state is BankState.ACTIVE else None,
                verified_by=spec["email"] if state is BankState.ACTIVE else None,
                valid_from=now if state is BankState.ACTIVE else None,
                encryption_key_id=cipher.key_id,
            )
            for name in ("sort_code", "account_number", "iban", "swift_bic"):
                value = spec.get(name)
                if value:
                    setattr(
                        detail,
                        f"{name}_enc",
                        cipher.encrypt(value, aad=field_aad(str(person.id), name)),
                    )
            session.add(detail)

    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="seed_demo",
        description="Invented people for staging. Never production.",
    )
    parser.add_argument(
        "--commit", action="store_true", help="write; without this, nothing happens"
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    cipher = Cipher.from_settings(settings)
    now = datetime.now(UTC)

    factory = get_sessionmaker()
    with factory() as session:
        try:
            refuse_if_unsafe(settings, session)
        except DemoRefused as exc:
            print(f"Refused: {exc}")
            return 2
        # The check above read from the database, which opened a transaction.
        # Close it so the write below can own its own.
        session.rollback()

        if not args.commit:
            print(
                f"Would create up to {len(PEOPLE)} invented people in "
                f"{settings.environment.value}. Re-run with --commit."
            )
            for spec in PEOPLE:
                fee = f"£{spec['fee'] / 100:,.2f}" if spec["fee"] else "no fee"
                print(f"  {spec['display_name']:<14} {spec['team']:<12} {fee}")
            return 0

        with session.begin():
            created = build(session, cipher, now=now)

        print(
            f"{settings.environment.value}: {created} created, "
            f"{len(PEOPLE) - created} already there."
        )
        print("Every name, fee and account number is invented (R-ENV-02).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
