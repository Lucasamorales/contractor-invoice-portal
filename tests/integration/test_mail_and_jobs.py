"""The outbox dispatcher and the scheduled jobs (R-SUB-06, R-JOB-01 .. R-JOB-06).

Resend itself is not reachable from a test and its credentials do not exist
yet, so the client is a stand-in. Everything around it — what gets queued, what
gets retried, what gives up, and who is allowed to trigger any of it — is real.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import MailMode, Settings, get_settings, mail_mode
from app.db.models import Outbox, OutboxStatus, PersonStatus
from app.db.session import get_session
from app.domain.period import Period
from app.main import app
from app.services import bank_changes, jobs, mailer
from app.services.crypto import Cipher, generate_key
from app.services.invoicing import ExpenseInput, submit
from app.services.storage import LocalObjectStore
from app.web.member import get_store
from app.web.tasks import verify_scheduler
from tests.integration.conftest import (
    give_bank,
    give_counter,
    give_fee,
    give_kudos,
    make_person,
)

pytestmark = pytest.mark.integration

ROBIN = "robin@example.com"
IN_WINDOW = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
PERIOD = Period.parse("2026-09")


class Recorder:
    """A mail client that records, and can be told to fail."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.sent: list[mailer.Message] = []
        self.fail = fail

    def send(self, message: mailer.Message) -> None:
        if self.fail is not None:
            raise self.fail
        self.sent.append(message)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="staging",
        bank_encryption_key=generate_key(),
        csrf_secret="s",
        kudos_rate_pence=2000,
        finance_inbox=ROBIN,
        finance_emails=(ROBIN,),
    )


@pytest.fixture
def cipher(settings: Settings) -> Cipher:
    return Cipher.from_settings(settings)


def seed_and_submit(
    factory, cipher, settings, store, *, email="jane@example.com"
):
    with factory() as session, session.begin():
        person = make_person(session, email=email)
        give_fee(session, person)
        give_bank(session, person, cipher)
        give_counter(session, person, 8)
        give_kudos(session, person, PERIOD.key, 3)
        person_id = person.id

    return submit(
        factory,
        person_id=person_id,
        period=PERIOD,
        idempotency_key=f"key-{email}",
        now=IN_WINDOW,
        settings=settings,
        cipher=cipher,
        store=store,
        expenses=[ExpenseInput("travel", 3450)],
        render=lambda context: b"%PDF-1.7 fake",
    )


# --------------------------------------------------------------------------
# The dispatcher (R-SUB-06, R-JOB-04)
# --------------------------------------------------------------------------


def test_a_queued_invoice_is_sent_with_the_pdf_attached(
    factory, settings, cipher, tmp_path
) -> None:
    """R-SUB-07: the PDF attached, the breakdown in the body."""
    store = LocalObjectStore(tmp_path / "objects")
    seed_and_submit(factory, cipher, settings, store)
    client = Recorder()

    with factory() as session, session.begin():
        report = mailer.dispatch(session, client=client, settings=settings, store=store)

    assert report.sent == 1
    message = client.sent[0]
    assert message.subject == "Invoice 8 — Jane Smith — September 2026"
    # R-SUB-08: Robin and the person.
    assert ROBIN in message.to
    assert "jane@example.com" in message.to
    # The body carries the breakdown regardless of the PDF (R-SUB-07).
    assert "£2,500.00" in message.text
    assert "3 kudos × £20" in message.text
    assert "travel £34.50" in message.text
    assert "£2,594.50" in message.text
    assert len(message.attachments) == 1
    assert message.attachments[0].content_type == "application/pdf"
    assert message.attachments[0].content == b"%PDF-1.7 fake"

    with factory() as session:
        row = session.scalars(select(Outbox)).one()
        assert row.status is OutboxStatus.SENT
        assert row.sent_at is not None
        assert row.attempts == 1


def test_no_fee_appears_in_a_subject_line(factory, settings, cipher, tmp_path) -> None:
    """R-SEC-05 / R-SUB-07."""
    store = LocalObjectStore(tmp_path / "objects")
    seed_and_submit(factory, cipher, settings, store)
    client = Recorder()
    with factory() as session, session.begin():
        mailer.dispatch(session, client=client, settings=settings, store=store)
    assert "2,500" not in client.sent[0].subject
    assert "£" not in client.sent[0].subject


def test_a_failure_is_retried_with_backoff_not_dropped(
    factory, settings, cipher, tmp_path
) -> None:
    """R-JOB-04. Postgres accepted the invoice; the email must not be lost."""
    store = LocalObjectStore(tmp_path / "objects")
    seed_and_submit(factory, cipher, settings, store)
    now = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)

    with factory() as session, session.begin():
        report = mailer.dispatch(
            session,
            client=Recorder(fail=mailer.MailError("Resend is down")),
            settings=settings,
            store=store,
            now=now,
        )
    assert report.retried == 1
    assert report.sent == 0

    with factory() as session:
        row = session.scalars(select(Outbox)).one()
        assert row.status is OutboxStatus.PENDING
        assert row.attempts == 1
        assert row.next_attempt_at == now + timedelta(seconds=60)
        assert "Resend is down" in row.last_error


def test_a_row_in_backoff_is_left_alone_until_it_is_due(
    factory, settings, cipher, tmp_path
) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    seed_and_submit(factory, cipher, settings, store)
    now = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)

    with factory() as session, session.begin():
        mailer.dispatch(
            session,
            client=Recorder(fail=mailer.MailError("down")),
            settings=settings,
            store=store,
            now=now,
        )

    # Half a minute later: not due.
    client = Recorder()
    with factory() as session, session.begin():
        report = mailer.dispatch(
            session,
            client=client,
            settings=settings,
            store=store,
            now=now + timedelta(seconds=30),
        )
    assert report.considered == 0
    assert client.sent == []

    # Two minutes later: due, and it goes.
    with factory() as session, session.begin():
        report = mailer.dispatch(
            session,
            client=client,
            settings=settings,
            store=store,
            now=now + timedelta(minutes=2),
        )
    assert report.sent == 1


def test_it_gives_up_after_five_attempts(factory, settings, cipher, tmp_path) -> None:
    """R-JOB-04: after five failures the row is marked failed and alerts."""
    store = LocalObjectStore(tmp_path / "objects")
    seed_and_submit(factory, cipher, settings, store)
    moment = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)

    for attempt in range(5):
        with factory() as session, session.begin():
            mailer.dispatch(
                session,
                client=Recorder(fail=mailer.MailError("still down")),
                settings=settings,
                store=store,
                now=moment + timedelta(hours=attempt),
            )

    with factory() as session:
        row = session.scalars(select(Outbox)).one()
        assert row.status is OutboxStatus.FAILED
        assert row.attempts == 5


def test_a_permanent_failure_is_not_retried_five_times(
    factory, settings, cipher, tmp_path
) -> None:
    """A rejected address will be rejected identically in twenty minutes."""
    store = LocalObjectStore(tmp_path / "objects")
    seed_and_submit(factory, cipher, settings, store)

    with factory() as session, session.begin():
        report = mailer.dispatch(
            session,
            client=Recorder(fail=mailer.MailError("bad address", permanent=True)),
            settings=settings,
            store=store,
        )
    assert report.failed == 1
    with factory() as session:
        assert session.scalars(select(Outbox)).one().attempts == 1


def test_one_bad_message_does_not_hold_up_the_others(
    factory, settings, cipher, tmp_path
) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    seed_and_submit(factory, cipher, settings, store)
    with factory() as session, session.begin():
        # A row nothing knows how to render.
        session.add(Outbox(kind="not_a_real_kind", payload={}))

    client = Recorder()
    with factory() as session, session.begin():
        report = mailer.dispatch(session, client=client, settings=settings, store=store)

    assert report.sent == 1, "the good one still went"
    assert report.failed == 1
    assert len(client.sent) == 1


def test_failed_mail_can_be_put_back_in_the_queue(
    factory, settings, cipher, tmp_path
) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    seed_and_submit(factory, cipher, settings, store)
    with factory() as session, session.begin():
        mailer.dispatch(
            session,
            client=Recorder(fail=mailer.MailError("nope", permanent=True)),
            settings=settings,
            store=store,
        )

    with factory() as session, session.begin():
        assert jobs.retry_failed(session).queued == 1

    client = Recorder()
    with factory() as session, session.begin():
        assert (
            mailer.dispatch(session, client=client, settings=settings, store=store).sent
            == 1
        )


def test_a_bank_change_email_carries_only_last_four(
    factory, settings, cipher, tmp_path
) -> None:
    """R-BANK-05 rendered, not just queued."""
    store = LocalObjectStore(tmp_path / "objects")
    with factory() as session, session.begin():
        person = make_person(session)
        give_bank(session, person, cipher)
        session.add(
            Outbox(
                kind=bank_changes.OUTBOX_BANK_CHANGED,
                payload={
                    "person_name": "Jane Smith",
                    "to_person": person.email,
                    "to_finance": ROBIN,
                    "old_account": "••••5678",
                    "new_account": "••••4321",
                    "subject": "Bank details changed — Jane Smith",
                },
            )
        )

    client = Recorder()
    with factory() as session, session.begin():
        mailer.dispatch(session, client=client, settings=settings, store=store)

    text = client.sent[0].text
    assert "••••5678" in text
    assert "••••4321" in text
    assert "12345678" not in text


# --------------------------------------------------------------------------
# Staging cannot send real mail (R-ENV-03)
# --------------------------------------------------------------------------


def test_staging_gets_the_sandbox_client_and_sends_nothing() -> None:
    staging = Settings(_env_file=None, environment="staging", resend_api_key="live-key")
    assert mail_mode(staging) is MailMode.SANDBOX
    client = mailer.build_client(staging)
    assert isinstance(client, mailer.SandboxClient)

    client.send(mailer.Message(to=("someone@other.test",), subject="x", text="y"))
    assert len(client.sent) == 1  # recorded, not delivered


def test_production_without_a_token_refuses_rather_than_pretending() -> None:
    production = Settings(_env_file=None, environment="production")
    with pytest.raises(mailer.MailError):
        mailer.build_client(production)


# --------------------------------------------------------------------------
# The reminder and summary jobs (R-JOB-02, R-JOB-03)
# --------------------------------------------------------------------------


def make_people(factory, cipher, count: int = 3) -> list:
    ids = []
    with factory() as session, session.begin():
        for index in range(count):
            person = make_person(session, email=f"p{index}@example.com")
            give_fee(session, person)
            give_bank(session, person, cipher)
            give_counter(session, person, 1)
            give_kudos(session, person, PERIOD.key, 0)
            ids.append(person.id)
    return ids


def test_the_24th_tells_everyone_the_window_is_open(factory, settings, cipher) -> None:
    make_people(factory, cipher, 3)
    with factory() as session, session.begin():
        report = jobs.remind(session, settings=settings, now=IN_WINDOW)
    assert report.queued == 3
    with factory() as session:
        kinds = session.scalars(select(Outbox.kind)).all()
        assert kinds == ["window_open"] * 3


def test_the_27th_only_nudges_those_who_have_not_submitted(
    factory, settings, cipher, tmp_path
) -> None:
    """Reminding somebody who has already done it is how people learn to
    ignore the emails."""
    store = LocalObjectStore(tmp_path / "objects")
    make_people(factory, cipher, 3)
    seed_and_submit(
        factory, cipher, settings, store, email="done@example.com"
    )

    with factory() as session, session.begin():
        report = jobs.remind(
            session,
            settings=settings,
            now=datetime(2026, 9, 27, 9, 0, tzinfo=UTC),
        )

    assert report.queued == 3  # the three who have not
    assert report.skipped == 1  # the one who has
    with factory() as session:
        nudges = session.scalars(select(Outbox).where(Outbox.kind == "reminder")).all()
        assert len(nudges) == 3
        assert all("done@" not in row.payload["to_person"] for row in nudges)


def test_reminders_are_idempotent(factory, settings, cipher) -> None:
    """R-JOB-*: Scheduler retries, and a double fire must not double-send."""
    make_people(factory, cipher, 3)
    for _ in range(3):
        with factory() as session, session.begin():
            jobs.remind(session, settings=settings, now=IN_WINDOW)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Outbox)) == 3


def test_nothing_is_sent_on_other_days(factory, settings, cipher) -> None:
    make_people(factory, cipher, 2)
    with factory() as session, session.begin():
        report = jobs.remind(
            session, settings=settings, now=datetime(2026, 9, 12, 9, tzinfo=UTC)
        )
    assert report.queued == 0
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Outbox)) == 0


def test_an_inactive_person_is_not_reminded(factory, settings, cipher) -> None:
    """R-ADM-09: deactivated means no reminders."""
    ids = make_people(factory, cipher, 2)
    with factory() as session, session.begin():
        from app.db.models import Person

        session.get(Person, ids[0]).status = PersonStatus.INACTIVE

    with factory() as session, session.begin():
        report = jobs.remind(session, settings=settings, now=IN_WINDOW)
    assert report.queued == 1


def test_the_summary_reconciles_expected_against_received(
    factory, settings, cipher, tmp_path
) -> None:
    """R-JOB-03."""
    store = LocalObjectStore(tmp_path / "objects")
    make_people(factory, cipher, 2)
    seed_and_submit(factory, cipher, settings, store, email="in@example.com")

    with factory() as session, session.begin():
        report = jobs.summary(
            session, settings=settings, now=datetime(2026, 9, 29, 9, tzinfo=UTC)
        )
    assert report.queued == 1
    assert report.detail == "1 of 3 received"

    client = Recorder()
    with factory() as session, session.begin():
        mailer.dispatch(session, client=client, settings=settings, store=store)

    text = next(m for m in client.sent if "Invoices for" in m.subject).text
    assert "1 of 3 invoices received" in text
    assert "Not in:" in text


def test_the_summary_is_idempotent(factory, settings, cipher) -> None:
    make_people(factory, cipher, 2)
    for _ in range(3):
        with factory() as session, session.begin():
            jobs.summary(
                session, settings=settings, now=datetime(2026, 9, 29, 9, tzinfo=UTC)
            )
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Outbox)) == 1


# --------------------------------------------------------------------------
# Who may trigger a job (R-JOB-*)
# --------------------------------------------------------------------------


def test_task_endpoints_refuse_an_anonymous_caller(
    engine: Engine, factory: sessionmaker[Session]
) -> None:
    """These sit outside IAP, so the OIDC check is the only thing there is."""
    configured = Settings(
        _env_file=None,
        environment="staging",
        bank_encryption_key=generate_key(),
        csrf_secret="s",
        scheduler_audience="https://portal.example/",
        scheduler_service_account="scheduler@your-org.iam.gserviceaccount.com",
    )

    def db() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = db
    app.dependency_overrides[get_settings] = lambda: configured
    try:
        with TestClient(app) as client:
            for path in (
                "/tasks/outbox",
                "/tasks/remind",
                "/tasks/summary",
                "/tasks/sync-kudos",
                "/tasks/backup",
                "/tasks/drive-mirror",
            ):
                assert client.post(path).status_code == 401, path
                assert (
                    client.post(path, headers={"Authorization": "Bearer nonsense"})
                ).status_code == 401, path
    finally:
        app.dependency_overrides.clear()


def test_task_endpoints_refuse_when_not_configured(
    engine: Engine, factory: sessionmaker[Session]
) -> None:
    """An unauthenticated endpoint that runs the payroll reminders is worse
    than one that is switched off."""
    unconfigured = Settings(
        _env_file=None, environment="staging", bank_encryption_key=generate_key()
    )

    def db() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = db
    app.dependency_overrides[get_settings] = lambda: unconfigured
    try:
        with TestClient(app) as client:
            response = client.post(
                "/tasks/remind", headers={"Authorization": "Bearer whatever"}
            )
            assert response.status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_a_verified_scheduler_can_run_the_jobs(
    engine: Engine, factory: sessionmaker[Session], settings: Settings, cipher, tmp_path
) -> None:
    """With the token check satisfied, the endpoints do their work."""
    make_people(factory, cipher, 2)

    def db() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = db
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_store] = lambda: LocalObjectStore(tmp_path / "o")
    app.dependency_overrides[verify_scheduler] = lambda: "scheduler@other.test"
    try:
        with TestClient(app) as client:
            body = client.post("/tasks/outbox").json()
            assert body["sent"] == 0  # nothing queued in this test

            # The jobs that are not configured yet say so rather than
            # reporting success. They report it in their own terms: the kudos
            # sync counts events, the mirror counts files queued.
            body = client.post("/tasks/sync-kudos").json()
            assert body["events_new"] == 0
            assert "no kudos sheet configured" in body["detail"]

            body = client.post("/tasks/drive-mirror").json()
            assert body["queued"] == 0
            assert "no " in body["detail"]

            # The backup refuses outright with nowhere durable to write. A
            # backup job that quietly writes to a container filesystem is worse
            # than none, because it looks like it is working (R-JOB-05).
            assert client.post("/tasks/backup").status_code == 503
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# Resend, the redirect, and the size ceiling
# --------------------------------------------------------------------------


def test_staging_does_not_send_unless_everything_goes_to_one_address() -> None:
    """R-ENV-03, kept rather than weakened.

    Staging may send only when every message is redirected to a single
    address, so it cannot reach a real contractor by construction.
    """
    plain = Settings(_env_file=None, environment="staging", resend_api_key="re_x")
    assert isinstance(mailer.build_client(plain), mailer.SandboxClient)

    redirected = Settings(
        _env_file=None,
        environment="staging",
        resend_api_key="re_x",
        mail_from="portal@other.test",
        mail_redirect_to="leo@example.com",
    )
    assert isinstance(mailer.build_client(redirected), mailer.RedirectingClient)


def test_production_refuses_to_start_with_a_redirect_set() -> None:
    """Every invoice quietly landing in one inbox is worse than not starting."""
    from app.config import ConfigError, assert_production_ready

    settings = Settings(
        _env_file=None,
        environment="production",
        iap_audience="aud",
        database_url="postgresql+psycopg://u:p@h/d?sslmode=verify-full",
        bank_encryption_key="key",
        csrf_secret="csrf",
        gcs_bucket="bucket",
        finance_emails="robin@example.com",
        mail_redirect_to="leo@example.com",
    )
    with pytest.raises(ConfigError, match="mail_redirect_to"):
        assert_production_ready(settings)


def test_a_redirected_email_says_who_it_was_for() -> None:
    """Otherwise somebody reads it and believes Robin got it."""
    inner = Recorder()
    client = mailer.RedirectingClient(inner, "leo@example.com")
    client.send(
        mailer.Message(
            to=("robin@example.com", "jane@example.com"),
            subject="Invoice 8",
            text="the body",
            attachments=(mailer.Attachment("a.pdf", b"%PDF", "application/pdf"),),
        )
    )

    sent = inner.sent[0]
    assert sent.to == ("leo@example.com",)
    assert "robin@example.com" in sent.text
    assert "jane@example.com" in sent.text
    assert sent.text.endswith("the body")
    assert sent.subject.startswith("[redirected]")
    # The attachments are the point of the test send.
    assert sent.attachments[0].name == "a.pdf"


def test_the_encoded_size_accounts_for_base64() -> None:
    """Budgeting on raw bytes would put us a third over the limit."""
    one_meg = mailer.Attachment("x", b"x" * 1_000_000, "application/octet-stream")
    assert mailer._encoded_size([one_meg]) == pytest.approx(1_333_334, abs=2)
