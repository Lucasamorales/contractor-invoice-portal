"""The kudos claim ledger, end to end (R-KUD-07 .. R-KUD-23).

The question every one of these answers is the one Leo asked: can a kudo be
billed twice, and can one go missing. Everything else about this feature is
presentation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from core.config import Settings
from core.services.crypto import Cipher
from core.services.storage import LocalObjectStore
from modules.invoices.db.models import (
    Invoice,
    InvoiceStatus,
    KudosAlias,
    KudosAward,
    KudosEvent,
    KudosSnapshot,
    LineKind,
    Person,
)
from modules.invoices.domain.kudos import Quarter, normalise_name
from modules.invoices.services import kudos_admin, kudos_sync
from modules.invoices.services.invoicing import submit
from tests.integration.conftest import (
    IN_WINDOW,
    give_award,
    give_bank,
    give_counter,
    give_fee,
    make_person,
)

pytestmark = pytest.mark.integration

HEADER = ["Date", "Giver", "Value", "Receiver", "Space", "Message"]
JUL_1 = 46204  # 1 July 2026 as a Sheets serial
SEP_1 = 46266
JUN_30 = 46203


def settings_for(**kwargs) -> Settings:
    return Settings(
        _env_file=None,
        environment="staging",
        kudos_sheet_id="sheet-1",
        kudos_claimable_from=date(2026, 7, 1),
        **kwargs,
    )


def seeded(factory: sessionmaker[Session], cipher: Cipher, **kwargs) -> uuid.UUID:
    with factory() as session, session.begin():
        person = make_person(session, email=kwargs.get("email", "jane@example.com"))
        if kwargs.get("leadership"):
            person.kudos_eligible = False
        give_fee(session, person)
        give_bank(session, person, cipher)
        give_counter(session, person, kwargs.get("next_number", 8))
        return person.id


def sync_rows(session: Session, rows: list[list[object]], settings: Settings):
    return kudos_sync.store(
        session,
        kudos_sync.parse(session, rows),
        settings=settings,
        now=IN_WINDOW,
    )


# --------------------------------------------------------------------------
# Ingest
# --------------------------------------------------------------------------


def test_syncing_twice_does_not_double_the_kudos(factory, cipher) -> None:
    """Idempotence is the whole reason for the event key (R-KUD-09)."""
    person_id = seeded(factory, cipher)
    settings = settings_for()
    rows = [
        HEADER,
        [JUL_1, "Riley Garner", "Own it", "Jane Smith", None, "one"],
        [SEP_1, "Toni Grant", "Go beyond", "Jane Smith", None, "two"],
    ]

    with factory() as session, session.begin():
        alias(session, "Jane Smith", person_id)
        first = sync_rows(session, rows, settings)
    with factory() as session, session.begin():
        second = sync_rows(session, rows, settings)

    assert (first.events_new, first.events_seen) == (2, 0)
    assert (second.events_new, second.events_seen) == (0, 2)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(KudosEvent)) == 2


def test_history_before_the_cut_over_is_not_billable(factory, cipher) -> None:
    """R-KUD-12. Q2 was settled the old way; billing it again pays twice."""
    person_id = seeded(factory, cipher)
    with factory() as session, session.begin():
        alias(session, "Jane Smith", person_id)
        sync_rows(
            session,
            [
                HEADER,
                [JUN_30, "Riley", "Own it", "Jane Smith", None, "Q2"],
                [JUL_1, "Riley", "Own it", "Jane Smith", None, "Q3"],
            ],
            settings_for(),
        )

    with factory() as session:
        claimable = session.scalars(
            select(KudosEvent).where(KudosEvent.claimable.is_(True))
        ).all()
        assert [e.awarded_on for e in claimable] == [date(2026, 7, 1)]


def test_a_row_that_leaves_the_sheet_is_marked_not_deleted(factory, cipher) -> None:
    """R-KUD-15. Deleting it would destroy the record of what was paid."""
    person_id = seeded(factory, cipher)
    settings = settings_for()
    row = [JUL_1, "Riley", "Own it", "Jane Smith", None, "one"]

    with factory() as session, session.begin():
        alias(session, "Jane Smith", person_id)
        sync_rows(session, [HEADER, row], settings)
    with factory() as session, session.begin():
        report = sync_rows(session, [HEADER], settings)

    assert report.withdrawn == 1
    with factory() as session:
        event = session.scalars(select(KudosEvent)).one()
        assert event.withdrawn_at is not None


def test_a_billed_kudo_leaving_the_sheet_is_reported_loudly(
    factory, cipher, caplog
) -> None:
    """R-KUD-23. An issued invoice is immutable, so this needs a person."""
    person_id = seeded(factory, cipher)
    settings = settings_for()
    with factory() as session, session.begin():
        alias(session, "Jane Smith", person_id)
        sync_rows(
            session,
            [HEADER, [JUL_1, "Riley", "Own it", "Jane Smith", None, "one"]],
            settings,
        )
        event = session.scalars(select(KudosEvent)).one()
        invoice_id = _fake_invoice(session, person_id)
        event.claimed_by_invoice_id = invoice_id

    with factory() as session, session.begin(), caplog.at_level("ERROR"):
        report = sync_rows(session, [HEADER], settings)

    assert report.changed_after_claiming
    assert "has left the sheet" in caplog.text


def test_an_unmapped_name_is_reported_and_holds_its_kudos(factory, cipher) -> None:
    seeded(factory, cipher)
    with factory() as session, session.begin():
        report = sync_rows(
            session,
            [HEADER, [JUL_1, "Riley", "Own it", "Someone Else", None, ""]],
            settings_for(),
        )
    assert report.unmatched == ("Someone Else",)
    with factory() as session:
        from modules.invoices.db import repo

        assert repo.unresolved_kudos_names(session) == [("Someone Else", 1)]


def test_mapping_a_name_resolves_what_already_arrived(factory, cipher) -> None:
    person_id = seeded(factory, cipher)
    with factory() as session, session.begin():
        sync_rows(
            session,
            [HEADER, [JUL_1, "Riley", "Own it", "Jane Smith", None, ""]],
            settings_for(),
        )
    with factory() as session, session.begin():
        person = session.get(Person, person_id)
        kudos_admin.map_name(
            session,
            sheet_name="Jane Smith",
            person=person,
            actor_email="robin@example.com",
        )
    with factory() as session:
        assert session.scalars(select(KudosEvent)).one().person_id == person_id


def test_ignoring_a_name_silences_it_without_deleting_anything(factory, cipher) -> None:
    """R-KUD-14. A leaver must not sit on the queue forever."""
    seeded(factory, cipher)
    with factory() as session, session.begin():
        sync_rows(
            session,
            [HEADER, [JUL_1, "Riley", "Own it", "Victor Harlow", None, ""]],
            settings_for(),
        )
    with factory() as session, session.begin():
        kudos_admin.map_name(
            session,
            sheet_name="Victor Harlow",
            person=None,
            actor_email="robin@example.com",
            note="left the agency",
        )
    with factory() as session:
        from modules.invoices.db import repo

        assert repo.unresolved_kudos_names(session) == []
        assert session.scalar(select(func.count()).select_from(KudosEvent)) == 1


# --------------------------------------------------------------------------
# Claiming — the part with money on it
# --------------------------------------------------------------------------


def test_a_kudo_is_billed_once_and_never_again(factory, cipher, tmp_path) -> None:
    """The question this whole feature exists to answer (R-KUD-10)."""
    person_id = seeded(factory, cipher)
    settings = settings_for(csrf_secret="x", bank_encryption_key=cipher_key(cipher))
    with factory() as session, session.begin():
        alias(session, "Jane Smith", person_id)
        sync_rows(session, [HEADER, *([_row("Jane Smith")] * 3)], settings)
        give_kudos_snapshot(session, person_id)

    invoice_id = _submit(factory, person_id, settings, cipher, tmp_path, key="first")

    with factory() as session:
        events = session.scalars(select(KudosEvent)).all()
        assert all(e.claimed_by_invoice_id == invoice_id for e in events)
        invoice = session.get(Invoice, invoice_id)
        assert invoice.additional_pence == 6000  # 3 x £20

    # A second period, nothing new in the sheet: nothing left to bill.
    with factory() as session, session.begin():
        session.get(Invoice, invoice_id).status = InvoiceStatus.VOID
        give_kudos_snapshot(session, person_id, period="2026-12")

    from modules.invoices.domain.period import Period

    second = _submit(
        factory,
        person_id,
        settings,
        cipher,
        tmp_path,
        key="second",
        period=Period(2026, 12),
        now=datetime(2026, 12, 24, 10, 0, tzinfo=UTC),
    )
    with factory() as session:
        assert session.get(Invoice, second).additional_pence == 0


def test_kudos_only_appear_on_a_quarter_end_invoice(factory, cipher, tmp_path) -> None:
    """KUDOS.md §4: paid quarterly, so October bills none of them."""
    from modules.invoices.domain.period import Period

    person_id = seeded(factory, cipher)
    settings = settings_for(csrf_secret="x")
    with factory() as session, session.begin():
        alias(session, "Jane Smith", person_id)
        sync_rows(session, [HEADER, _row("Jane Smith")], settings)
        give_kudos_snapshot(session, person_id, period="2026-10")

    invoice_id = _submit(
        factory,
        person_id,
        settings,
        cipher,
        tmp_path,
        key="october",
        period=Period(2026, 10),
        now=datetime(2026, 10, 24, 10, 0, tzinfo=UTC),
    )
    with factory() as session:
        assert session.get(Invoice, invoice_id).additional_pence == 0
        assert session.scalars(select(KudosEvent)).one().claimed_by_invoice_id is None


def test_leadership_receives_kudos_and_cannot_cash_them(
    factory, cipher, tmp_path
) -> None:
    """R-KUD-20. Remy Keller invoices here; his kudos are not money."""
    person_id = seeded(factory, cipher, leadership=True)
    settings = settings_for(csrf_secret="x")
    with factory() as session, session.begin():
        alias(session, "Jane Smith", person_id)
        sync_rows(session, [HEADER, *([_row("Jane Smith")] * 2)], settings)
        give_kudos_snapshot(session, person_id)

    invoice_id = _submit(factory, person_id, settings, cipher, tmp_path, key="lead")
    with factory() as session:
        assert session.get(Invoice, invoice_id).additional_pence == 0
        # The kudos are still on the record, just not billable.
        assert session.scalar(select(func.count()).select_from(KudosEvent)) == 2


def test_an_award_is_billed_as_its_own_line(factory, cipher, tmp_path) -> None:
    """R-KUD-18. "3 kudos × £20" and "Q3 1st place" must be distinguishable."""
    person_id = seeded(factory, cipher)
    settings = settings_for(csrf_secret="x")
    with factory() as session, session.begin():
        person = session.get(Person, person_id)
        give_award(session, person)
        give_kudos_snapshot(session, person_id)

    invoice_id = _submit(factory, person_id, settings, cipher, tmp_path, key="award")

    with factory() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.additional_pence == 10000
        lines = session.scalars(
            select(
                __import__(
                    "modules.invoices.db.models", fromlist=["InvoiceLine"]
                ).InvoiceLine
            )
        ).all()
        award_lines = [line for line in lines if line.kind is LineKind.AWARD]
        assert len(award_lines) == 1
        assert "1st place" in award_lines[0].label
        assert session.scalars(select(KudosAward)).one().claimed_by_invoice_id == (
            invoice_id
        )


# --------------------------------------------------------------------------
# Standings and awarding
# --------------------------------------------------------------------------


def test_a_tie_is_shown_and_never_broken(factory, cipher) -> None:
    """The policy's tiebreakers are judgements, so the code refuses to pick."""
    settings = settings_for()
    ids = []
    for name in ("a", "b", "c"):
        ids.append(seeded(factory, cipher, email=f"{name}@example.com"))

    with factory() as session, session.begin():
        for person_id, name in zip(ids, ("A One", "B Two", "C Three"), strict=True):
            alias(session, name, person_id)
        rows = [HEADER]
        rows += [_row("A One")] * 4
        rows += [_row("B Two")] * 2
        rows += [_row("C Three")] * 2
        sync_rows(session, rows, settings)

    with factory() as session:
        view = kudos_admin.quarter_view(session, Quarter(2026, 3), settings=settings)
        assert view.has_tie
        assert [(r.place, r.kudos_count) for r in view.rows] == [
            (1, 4),
            (2, 2),
            (2, 2),
        ]
        assert view.rows[0].cash_pence == 10000


def test_leadership_is_not_in_the_standings(factory, cipher) -> None:
    settings = settings_for()
    boss = seeded(factory, cipher, email="boss@example.com", leadership=True)
    with factory() as session, session.begin():
        alias(session, "The Boss", boss)
        sync_rows(session, [HEADER, *([_row("The Boss")] * 9)], settings)

    with factory() as session:
        view = kudos_admin.quarter_view(session, Quarter(2026, 3), settings=settings)
        assert view.rows == ()


def test_the_same_person_cannot_be_awarded_twice(factory, cipher) -> None:
    settings = settings_for()
    person_id = seeded(factory, cipher)
    with factory() as session, session.begin():
        person = session.get(Person, person_id)
        kudos_admin.award(
            session,
            person=person,
            quarter=Quarter(2026, 3),
            place=1,
            kudos_count=4,
            settings=settings,
            actor_email="robin@example.com",
        )
    with factory() as session, session.begin():
        person = session.get(Person, person_id)
        with pytest.raises(kudos_admin.KudosAdminError):
            kudos_admin.award(
                session,
                person=person,
                quarter=Quarter(2026, 3),
                place=2,
                kudos_count=4,
                settings=settings,
                actor_email="robin@example.com",
            )


def test_leadership_cannot_be_awarded_a_prize(factory, cipher) -> None:
    settings = settings_for()
    person_id = seeded(factory, cipher, leadership=True)
    with factory() as session, session.begin():
        person = session.get(Person, person_id)
        with pytest.raises(kudos_admin.KudosAdminError):
            kudos_admin.award(
                session,
                person=person,
                quarter=Quarter(2026, 3),
                place=1,
                kudos_count=9,
                settings=settings,
                actor_email="robin@example.com",
            )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _row(receiver: str, serial: int = JUL_1) -> list[object]:
    # Distinct messages are irrelevant to the key; the ordinal separates them.
    return [serial, "Riley Garner", "Own it", receiver, None, ""]


def alias(session: Session, name: str, person_id: uuid.UUID | None) -> None:
    session.add(
        KudosAlias(
            normalised_name=normalise_name(name),
            display_name=name,
            person_id=person_id,
            ignored=person_id is None,
            confirmed_by="test",
        )
    )
    session.flush()


def give_kudos_snapshot(
    session: Session, person_id: uuid.UUID, *, period: str = "2026-09"
) -> None:
    """Evidence the sheet was read (R-KUD-06). The ledger holds the count."""
    if session.get(KudosSnapshot, (person_id, period)) is None:
        session.add(
            KudosSnapshot(
                person_id=person_id, period=period, kudos_count=0, rate_pence=2000
            )
        )
        session.flush()


def cipher_key(cipher: Cipher) -> str:
    del cipher
    return ""


def _fake_invoice(session: Session, person_id: uuid.UUID) -> uuid.UUID:
    invoice = Invoice(
        person_id=person_id,
        period="2026-09",
        number=99,
        status=InvoiceStatus.SUBMITTED,
        remuneration_pence=1,
        additional_pence=0,
        total_pence=1,
        idempotency_key=str(uuid.uuid4()),
    )
    session.add(invoice)
    session.flush()
    return invoice.id


def _submit(
    factory,
    person_id,
    settings,
    cipher,
    tmp_path,
    *,
    key: str,
    period=None,
    now=IN_WINDOW,
):
    from modules.invoices.domain.period import Period

    return submit(
        factory,
        person_id=person_id,
        period=period or Period(2026, 9),
        idempotency_key=key,
        now=now,
        settings=settings,
        cipher=cipher,
        store=LocalObjectStore(tmp_path / f"objects-{key}"),
        render=lambda context: b"%PDF-1.7 test",
    )


def test_joining_after_the_sync_is_not_the_same_as_an_unreadable_sheet(
    factory, settings, cipher
) -> None:
    """R-KUD-06 distinguishes "no kudos" from "we could not read them". It
    must not also catch "you were created after this morning's sync".

    The snapshot is stored per person because the count is, but what it
    records — that the sheet was read — is true for the whole period. Asking
    per person told a new joiner their kudos were unavailable while their
    events sat there, attached and claimable, and parked them on the queue.
    """
    from modules.invoices.db import repo
    from modules.invoices.domain.period import Period
    from modules.invoices.services.invoicing import readiness

    period = Period(2026, 9)
    now = datetime(2026, 9, 8, 12, tzinfo=UTC)

    with factory() as session, session.begin():
        synced = make_person(session, email="synced@example.com")
        joiner = make_person(session, email="joiner@example.com")
        session.flush()
        # The sync ran and wrote a row for everybody it knew about.
        session.add(
            KudosSnapshot(
                person_id=synced.id,
                period=period.key,
                kudos_count=0,
                rate_pence=settings.kudos_rate_pence,
            )
        )
        # The joiner has kudos of their own, matched by alias — ledger rows
        # and no snapshot, which is exactly the state a sync cannot produce
        # for somebody who did not exist when it ran.
        for index in range(2):
            session.add(
                KudosEvent(
                    event_key=f"joiner-{index}",
                    person_id=joiner.id,
                    receiver_name=joiner.display_name,
                    giver_name="Riley Garner",
                    value_text="Own it",
                    awarded_on=date(2026, 9, 1),
                    claimable=True,
                )
            )
        joiner_id = joiner.id

    with factory() as session:
        assert repo.kudos_sheet_was_read(session, period.key) is True
        assert repo.people_missing_kudos_snapshot(session, period.key) == []

        joiner = session.get(Person, joiner_id)
        state = readiness(
            session, person=joiner, period=period, now=now, settings=settings
        )
        # Stated, not refused, and at the configured rate.
        assert state.kudos_count == 2
        assert state.kudos_rate_pence == settings.kudos_rate_pence


def test_a_sheet_that_was_never_read_still_refuses_to_guess(
    factory, settings, cipher
) -> None:
    """The protection it exists for is unchanged."""
    from modules.invoices.db import repo
    from modules.invoices.domain.period import Period
    from modules.invoices.services.invoicing import readiness

    period = Period(2026, 9)
    now = datetime(2026, 9, 8, 12, tzinfo=UTC)

    with factory() as session, session.begin():
        person = make_person(session)
        session.flush()
        session.add(
            KudosEvent(
                event_key="never-read",
                person_id=person.id,
                receiver_name=person.display_name,
                giver_name="Riley Garner",
                value_text="Own it",
                awarded_on=date(2026, 9, 1),
                claimable=True,
            )
        )
        person_id = person.id

    with factory() as session:
        assert repo.kudos_sheet_was_read(session, period.key) is False
        assert repo.people_missing_kudos_snapshot(session, period.key) != []

        state = readiness(
            session,
            person=session.get(Person, person_id),
            period=period,
            now=now,
            settings=settings,
        )
        assert state.kudos_count is None
