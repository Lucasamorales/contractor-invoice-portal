"""Fixtures for the integration suite (R-TEST-02).

Runs against a throwaway Postgres, never against staging. The URL is read from
``PORTAL_TEST_DATABASE_URL`` and defaults to the container in docs/setup.md §2.1;
if nothing is listening, the whole suite skips rather than failing CI.

The schema is built with ``create_all`` rather than by running Alembic. Pointing
a migration runner at a URL during a test is one configuration mistake away from
migrating something real, and the migration is already verified against the
models in tests/test_migration.py.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from core.config import Settings
from core.services.crypto import Cipher, field_aad, generate_key
from core.services.storage import LocalObjectStore
from modules.invoices.db.models import (
    AwardKind,
    BankDetail,
    BankSource,
    BankState,
    Base,
    Counter,
    Fee,
    KudosAward,
    KudosEvent,
    KudosSnapshot,
    PayoutRegion,
    Person,
    PersonStatus,
)

DEFAULT_TEST_URL = "postgresql+psycopg://postgres:test@localhost:5433/portaltest"

# Not the real window, deliberately: 24 September 2026 is inside it, so the
# tests exercise an on-time submission unless they say otherwise.
IN_WINDOW = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
LATE = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)


def _test_url() -> str:
    return os.environ.get("PORTAL_TEST_DATABASE_URL", DEFAULT_TEST_URL)


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    url = _test_url()
    if "localhost" not in url and "127.0.0.1" not in url:
        # A cheap guard against ever pointing this at something that matters.
        pytest.fail(f"integration tests must use a local database, got {url}")
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    except OperationalError:
        pytest.skip(f"no test database at {url}; see docs/setup.md §2.1")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    yield sessionmaker(bind=engine, expire_on_commit=False, future=True)
    # Truncate rather than drop: faster, and it resets identity cleanly.
    with engine.begin() as conn:
        tables = ", ".join(f'"{name}"' for name in Base.metadata.tables)
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session")
def cipher() -> Cipher:
    return Cipher.from_settings(
        Settings(_env_file=None, bank_encryption_key=generate_key())
    )


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="staging",
        kudos_rate_pence=2000,
        payment_terms_days=14,
        finance_inbox="robin@example.com",
    )


@pytest.fixture
def store(tmp_path: Path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "objects")


@pytest.fixture
def fake_pdf() -> object:
    """A renderer that does not need WeasyPrint's native libraries.

    The real template is covered by tests/test_invoice_render.py; what matters
    here is the transaction structure around it.
    """

    def render(context: dict) -> bytes:
        return f"%PDF-1.7 invoice {context['number']}".encode()

    return render


def make_person(
    session: Session,
    *,
    email: str = "jane@example.com",
    complete_profile: bool = True,
    region: PayoutRegion = PayoutRegion.UK,
) -> Person:
    person = Person(
        email=email,
        display_name="Jane Smith",
        status=PersonStatus.ACTIVE,
        payout_region=region,
    )
    if complete_profile:
        person.legal_name = "Jane Smith"
        person.role = "Creative Director"
        person.address = "12 Example Street\nLondon N1 1AA"
        person.phone = "+44 7700 900000"
        person.contact_email = email
        person.profile_confirmed_at = datetime.now(UTC)
    session.add(person)
    session.flush()
    return person


def give_fee(session: Session, person: Person, pence: int = 250000) -> Fee:
    fee = Fee(
        person_id=person.id,
        fee_pence=pence,
        valid_from=date(2026, 1, 1),
        set_by="robin@example.com",
        note="agreed rate",
    )
    session.add(fee)
    session.flush()
    return fee


def give_bank(
    session: Session,
    person: Person,
    cipher: Cipher,
    *,
    state: BankState = BankState.ACTIVE,
    verified: bool = True,
) -> BankDetail:
    detail = BankDetail(
        person_id=person.id,
        account_name="Jane Smith",
        bank_name="Monzo",
        source=BankSource.SELF,
        state=state,
        verified_at=datetime.now(UTC) if verified else None,
        verified_by=person.email if verified else None,
        encryption_key_id=cipher.key_id,
        account_number_enc=cipher.encrypt(
            "12345678", aad=field_aad(str(person.id), "account_number")
        ),
        sort_code_enc=cipher.encrypt(
            "230801", aad=field_aad(str(person.id), "sort_code")
        ),
    )
    session.add(detail)
    session.flush()
    return detail


def give_counter(session: Session, person: Person, next_number: int = 8) -> Counter:
    counter = Counter(person_id=person.id, next_number=next_number)
    session.add(counter)
    session.flush()
    return counter


def give_kudos(
    session: Session, person: Person, period: str, count: int, rate: int = 2000
) -> KudosSnapshot:
    """A snapshot and the ledger rows behind it.

    Both, because they answer different questions: the ledger is what gets
    billed, and the snapshot is the evidence that the sheet was readable at all
    (R-KUD-06). A helper that wrote only one of them would let a test pass
    against a state the sync can never produce.
    """
    snapshot = KudosSnapshot(
        person_id=person.id, period=period, kudos_count=count, rate_pence=rate
    )
    session.add(snapshot)
    year, _, month = period.partition("-")
    for index in range(count):
        session.add(
            KudosEvent(
                event_key=f"test-{person.id}-{period}-{index}",
                person_id=person.id,
                receiver_name=person.display_name,
                giver_name="Riley Garner",
                value_text="Own it",
                awarded_on=date(int(year), int(month), 1),
                claimable=True,
            )
        )
    session.flush()
    return snapshot


def give_award(
    session: Session,
    person: Person,
    *,
    quarter: str = "2026-Q3",
    kind: AwardKind = AwardKind.QUARTER_1ST,
    amount_pence: int = 10000,
    kudos_count: int = 4,
) -> KudosAward:
    award = KudosAward(
        person_id=person.id,
        quarter=quarter,
        kind=kind,
        amount_pence=amount_pence,
        kudos_count=kudos_count,
        awarded_by="robin@example.com",
    )
    session.add(award)
    session.flush()
    return award


def ready_person(
    factory: sessionmaker[Session],
    cipher: Cipher,
    *,
    email: str = "jane@example.com",
    next_number: int = 8,
    kudos: int | None = 3,
    period: str = "2026-09",
) -> uuid.UUID:
    """A person who can actually submit: profile, fee, verified bank, counter."""
    with factory() as session, session.begin():
        person = make_person(session, email=email)
        give_fee(session, person)
        give_bank(session, person, cipher)
        give_counter(session, person, next_number)
        if kudos is not None:
            give_kudos(session, person, period, kudos)
        return person.id


@pytest.fixture(autouse=True)
def _reset_rate_limits() -> Iterator[None]:
    """The limiters are process-global by design (R-SEC-08), so tests would
    otherwise spend each other's budget."""
    from modules.invoices.web.ratelimit import bank_limiter, submit_limiter

    bank_limiter.reset()
    submit_limiter.reset()
    yield
    bank_limiter.reset()
    submit_limiter.reset()
