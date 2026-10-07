"""The refresh button on the kudos screen (R-KUD-24).

The sheet is read every morning by the scheduler (R-JOB-01). This is the same
read, on demand, because a kudo given during the day is otherwise invisible
until tomorrow — and the six days a month when that matters are exactly the days
people are invoicing.

What these tests hold: it is finance-only, it cannot run twice at once, a failed
read says what to fix rather than pretending to have worked, and the scheduled
job still runs.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.testclient import TestClient

from app.main import app
from core.auth import Identity, current_identity
from core.config import Settings, get_settings
from core.db.session import get_session, get_sessionmaker
from core.services.crypto import Cipher, generate_key
from core.services.storage import LocalObjectStore
from modules.invoices.db.models import AuditLog, KudosEvent, KudosSnapshot
from modules.invoices.services import kudos_sync
from modules.invoices.web.member import get_cipher, get_store
from modules.invoices.web.ratelimit import kudos_refresh_limiter
from tests.integration.conftest import give_counter, give_fee, make_person

pytestmark = pytest.mark.integration

JANE = "jane@example.com"
ROBIN = "accounts@example.com"
HEADER = ["Date", "Giver", "Value", "Receiver", "Space", "Message"]
SEP_1 = 46266  # 1 September 2026 as a Sheets serial


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="staging",
        bank_encryption_key=generate_key(),
        csrf_secret="test-csrf-secret",
        kudos_sheet_id="sheet-1",
        kudos_claimable_from=date(2026, 7, 1),
        finance_inbox=ROBIN,
        finance_emails=(ROBIN,),
    )


@pytest.fixture
def wired(engine: Engine, factory: sessionmaker[Session], tmp_path):
    settings = _settings()
    cipher = Cipher.from_settings(settings)
    kudos_refresh_limiter.reset()

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
        return TestClient(app)

    yield as_user, settings
    app.dependency_overrides.clear()
    kudos_refresh_limiter.reset()


@pytest.fixture
def jane(factory: sessionmaker[Session]):
    with factory() as session, session.begin():
        person = make_person(session, email=JANE)
        person.display_name = "Jane Smith"
        give_fee(session, person)
        give_counter(session, person, 8)
        return person.id


def rows(receiver: str = "Jane Smith", count: int = 1) -> list[list[object]]:
    return [HEADER] + [
        [SEP_1 + n, "Remy", "Own it", receiver, "Kudos", "well done"]
        for n in range(count)
    ]


def sheet_returns(monkeypatch, value):
    """Replace the HTTP fetch. The rest of the sync is the real thing."""

    def fake(settings, **kwargs):
        del settings, kwargs
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(kudos_sync, "fetch_rows", fake)


def token_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "no csrf token rendered"
    return match.group(1)


def press_refresh(client, *, token: str | None = None, quarter: str = "2026-Q3"):
    if token is None:
        page = client.get("/admin/kudos")
        token = token_from(page.text)
    return client.post(
        "/admin/kudos/refresh",
        data={"csrf_token": token, "quarter": quarter},
        follow_redirects=False,
    )


def alias(factory, name: str, person_id):
    from modules.invoices.services import kudos_admin

    with factory() as session, session.begin():
        from modules.invoices.db.models import Person

        kudos_admin.map_name(
            session,
            sheet_name=name,
            person=session.get(Person, person_id),
            actor_email=ROBIN,
        )


# --------------------------------------------------------------------------
# It reads the sheet now
# --------------------------------------------------------------------------


def test_accounts_can_read_the_sheet_on_demand(wired, factory, jane, monkeypatch):
    as_user, _ = wired
    alias(factory, "Jane Smith", jane)
    sheet_returns(monkeypatch, rows(count=2))

    with as_user(ROBIN, finance=True) as client:
        response = press_refresh(client)

    assert response.status_code == 303
    assert "refreshed=2" in response.headers["location"]
    with factory() as session:
        stored = session.scalars(select(KudosEvent)).all()
        assert len(stored) == 2
        assert all(event.person_id == jane for event in stored)
        assert all(event.claimable for event in stored)


def test_it_is_the_same_read_as_the_scheduler(wired, factory, jane, monkeypatch):
    """Pressing it twice must not double anybody's kudos (R-KUD-10)."""
    as_user, _ = wired
    alias(factory, "Jane Smith", jane)
    sheet_returns(monkeypatch, rows(count=1))

    with as_user(ROBIN, finance=True) as client:
        first = press_refresh(client)
        second = press_refresh(client)

    assert (first.status_code, second.status_code) == (303, 303)
    assert "refreshed=1" in first.headers["location"]
    assert "refreshed=0" in second.headers["location"]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(KudosEvent)) == 1


def test_it_records_who_pressed_it(wired, factory, jane, monkeypatch):
    as_user, _ = wired
    alias(factory, "Jane Smith", jane)
    sheet_returns(monkeypatch, rows(count=1))

    with as_user(ROBIN, finance=True) as client:
        press_refresh(client)

    with factory() as session:
        entry = session.scalar(select(AuditLog).where(AuditLog.action == "kudos.sync"))
        assert entry is not None
        assert entry.actor_email == ROBIN
        assert entry.after["events_new"] == 1


def test_the_screen_says_when_the_sheet_was_last_read(
    wired, factory, jane, monkeypatch
):
    as_user, _ = wired
    sheet_returns(monkeypatch, rows(count=1))

    with as_user(ROBIN, finance=True) as client:
        assert "has not been read this month yet" in client.get("/admin/kudos").text
        press_refresh(client)
        page = client.get("/admin/kudos")

    assert "Sheet last read today at" in page.text


# --------------------------------------------------------------------------
# Who may press it
# --------------------------------------------------------------------------


def test_a_contractor_cannot_press_it(wired, factory, jane, monkeypatch):
    as_user, _ = wired
    sheet_returns(monkeypatch, rows(count=1))

    with as_user(JANE, finance=False) as client:
        response = client.post(
            "/admin/kudos/refresh",
            data={"csrf_token": "anything", "quarter": "2026-Q3"},
            follow_redirects=False,
        )

    assert response.status_code == 403
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(KudosEvent)) == 0


def test_a_stale_form_is_refused(wired, factory, jane, monkeypatch):
    as_user, _ = wired
    sheet_returns(monkeypatch, rows(count=1))

    with as_user(ROBIN, finance=True) as client:
        response = press_refresh(client, token="not-a-token")

    assert response.status_code == 403
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(KudosEvent)) == 0


# --------------------------------------------------------------------------
# When it cannot work
# --------------------------------------------------------------------------


def test_an_unreadable_sheet_says_what_to_fix(wired, factory, jane, monkeypatch):
    as_user, _ = wired
    sheet_returns(
        monkeypatch,
        kudos_sync.KudosSyncError(
            "the sheet refused us: share it read-only with the service account"
        ),
    )

    with as_user(ROBIN, finance=True) as client:
        response = press_refresh(client)

    assert response.status_code == 422
    assert "share it read-only" in response.text
    with factory() as session:
        # Not even a snapshot: a failed read must not look like a read
        # (R-KUD-06).
        assert session.scalar(select(func.count()).select_from(KudosSnapshot)) == 0


def test_a_refresh_already_running_is_not_an_error(
    wired, factory, jane, monkeypatch, engine
):
    """The 07:00 job and the button can land on the same second."""
    as_user, _ = wired
    sheet_returns(monkeypatch, rows(count=1))

    # Hold the lock on another connection, exactly as a running sync would.
    with engine.connect() as held:
        held.execute(select(func.pg_advisory_lock(kudos_sync.SYNC_LOCK_KEY)))
        try:
            with as_user(ROBIN, finance=True) as client:
                response = press_refresh(client)
        finally:
            held.execute(select(func.pg_advisory_unlock(kudos_sync.SYNC_LOCK_KEY)))

    assert response.status_code == 409
    assert "already running" in response.text
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(KudosEvent)) == 0


def test_the_sync_itself_refuses_to_run_twice(factory, engine):
    """What the scheduler sees, so it can report a skip instead of a failure."""
    settings = _settings()
    with engine.connect() as held:
        held.execute(select(func.pg_advisory_lock(kudos_sync.SYNC_LOCK_KEY)))
        try:
            with (
                factory() as session,
                session.begin(),
                pytest.raises(kudos_sync.KudosSyncBusy),
            ):
                kudos_sync.sync(
                    session, settings=settings, fetch=lambda _settings: rows()
                )
        finally:
            held.execute(select(func.pg_advisory_unlock(kudos_sync.SYNC_LOCK_KEY)))


def test_an_environment_with_no_sheet_says_so(factory, engine, tmp_path):
    """Staging has none by design (R-ENV-02), and the button says why."""
    settings = _settings().model_copy(update={"kudos_sheet_id": ""})

    def db() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[current_identity] = lambda: Identity(
        email=ROBIN, subject=ROBIN, is_finance=True
    )
    app.dependency_overrides[get_session] = db
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_sessionmaker] = lambda: factory
    try:
        with TestClient(app) as client:
            page = client.get("/admin/kudos")
            assert "disabled" in page.text
            response = press_refresh(client, token=token_from(page.text))
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "No kudos sheet is configured" in response.text
