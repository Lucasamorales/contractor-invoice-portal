"""Read models and writes for the admin portal (R-ADM-01 .. R-ADM-09).

Composition only: every query lives in ``app/db/repo.py`` (CLAUDE.md §5).

Nothing here is reachable without the finance check in ``require_finance``
(R-ACC-04), and nothing here can make a bank row payable — an admin may read,
review, flag and propose, never confirm (R-BANK-07, CLAUDE.md §2.2).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import Settings
from app.db import repo
from app.db.models import (
    BankDetail,
    Fee,
    Invoice,
    PayoutRegion,
    Person,
    PersonStatus,
)
from app.domain.formatting import format_sort_code
from app.domain.iban import mask
from app.domain.kudos import is_quarter_end
from app.domain.money import format_gbp
from app.domain.period import Period
from app.services import mailer
from app.services.crypto import Cipher, field_aad


@dataclass(frozen=True, slots=True)
class PersonRow:
    """One line of the people list (R-ADM-01)."""

    person: Person
    fee_pence: int | None
    invoiced_this_period: Invoice | None

    @property
    def fee_is_set(self) -> bool:
        return self.fee_pence is not None


def people_overview(session: Session, period: Period, on: date) -> list[PersonRow]:
    """Everyone, with whether a fee is set and whether they have invoiced.

    Fees come back as figures because the finance group is the only audience,
    but the template masks them by default: a screenshare should not leak the
    payroll (R-ADM-01).
    """
    invoices = {
        invoice.person_id: invoice
        for invoice in repo.invoices_for_period(session, period.key)
    }
    rows: list[PersonRow] = []
    for person in repo.all_people(session):
        fee = repo.fee_for(session, person.id, on=on)
        rows.append(
            PersonRow(
                person=person,
                fee_pence=fee.fee_pence if fee else None,
                invoiced_this_period=invoices.get(person.id),
            )
        )
    return rows


@dataclass(frozen=True, slots=True)
class Flag:
    """Something on a period row that Robin should look at (R-ADM-04)."""

    kind: str
    detail: str


@dataclass(frozen=True, slots=True)
class PeriodRow:
    """One person in a period: their invoice if they sent one, and any open flags."""

    person: Person
    invoice: Invoice | None
    flags: tuple[Flag, ...] = ()


@dataclass(frozen=True, slots=True)
class PeriodView:
    """Who has and has not invoiced for a period.

    Only active people are expected; an inactive person who did invoice
    still appears as received.
    """

    period: Period
    rows: list[PeriodRow]

    @property
    def expected(self) -> int:
        return sum(1 for row in self.rows if row.person.status is PersonStatus.ACTIVE)

    @property
    def received(self) -> int:
        return sum(1 for row in self.rows if row.invoice is not None)

    @property
    def missing(self) -> list[PeriodRow]:
        return [
            row
            for row in self.rows
            if row.invoice is None and row.person.status is PersonStatus.ACTIVE
        ]

    @property
    def flagged(self) -> list[PeriodRow]:
        return [row for row in self.rows if row.flags]


def period_view(session: Session, period: Period, *, settings: Settings) -> PeriodView:
    """Expected against received, and everything worth a second look."""
    invoices = {
        invoice.person_id: invoice
        for invoice in repo.invoices_for_period(session, period.key)
    }
    expenses_by_invoice: dict = {}
    for expense in repo.expenses_for_invoices(
        session, [i.id for i in invoices.values()]
    ):
        expenses_by_invoice.setdefault(expense.invoice_id, []).append(expense)

    window_closes = datetime.combine(period.closes_on(), datetime.min.time()).replace(
        tzinfo=UTC
    )
    flag_from = window_closes - timedelta(hours=settings.bank_change_flag_hours)

    rows: list[PeriodRow] = []
    for person in repo.all_people(session):
        invoice = invoices.get(person.id)
        flags: list[Flag] = []

        if invoice is not None and invoice.is_late:
            flags.append(Flag("late", "submitted after the 28th"))

        # A fee that changed inside the period it is being billed for.
        for fee in repo.fee_history(session, person.id):
            if fee.valid_from.strftime("%Y-%m") == period.key:
                flags.append(
                    Flag(
                        "fee_changed",
                        f"fee changed on {fee.valid_from:%d %b} — {fee.note}",
                    )
                )
                break

        # R-BANK-06: a change close to the window closing flags, never blocks.
        for detail in repo.bank_history(session, person.id):
            if flag_from <= detail.created_at <= window_closes:
                flags.append(
                    Flag(
                        "bank_changed",
                        f"bank row added {detail.created_at:%d %b}, inside "
                        f"{settings.bank_change_flag_hours}h of the window closing",
                    )
                )
                break

        if invoice is not None:
            for expense in expenses_by_invoice.get(invoice.id, []):
                if expense.amount_pence > settings.expense_flag_threshold_pence:
                    flags.append(
                        Flag(
                            "large_expense",
                            f"{expense.description} {format_gbp(expense.amount_pence)}",
                        )
                    )

        rows.append(PeriodRow(person=person, invoice=invoice, flags=tuple(flags)))

    return PeriodView(period=period, rows=rows)


@dataclass(frozen=True, slots=True)
class PayoutLine:
    """One line of a payment run (R-ADM-05)."""

    payee: str
    legal_name: str
    amount_pence: int
    invoice_number: int
    account_name: str
    bank_name: str
    sort_code: str | None
    account_number: str | None
    iban: str | None
    swift_bic: str | None


@dataclass(frozen=True, slots=True)
class PaymentRun:
    """Split into the two batches Robin actually pays separately."""

    period: Period
    uk: list[PayoutLine] = field(default_factory=list)
    intl: list[PayoutLine] = field(default_factory=list)

    @property
    def uk_total_pence(self) -> int:
        return sum(line.amount_pence for line in self.uk)

    @property
    def intl_total_pence(self) -> int:
        return sum(line.amount_pence for line in self.intl)


def payment_run(session: Session, period: Period, *, cipher: Cipher) -> PaymentRun:
    """The period's submitted invoices, split UK and international.

    Read from each invoice's print snapshot, not from live bank rows: the
    account that was printed is the account that was invoiced, even if the
    person has changed their details since.
    """
    from app.db.models import PrintSnapshot
    from app.services.invoicing import decrypt_bank_block

    run = PaymentRun(period=period)
    for invoice in repo.invoices_for_period(session, period.key):
        person = session.get(Person, invoice.person_id)
        snapshot = session.get(PrintSnapshot, invoice.id)
        if person is None or snapshot is None:
            continue
        bank = decrypt_bank_block(
            snapshot.bank_block, person_id=invoice.person_id, cipher=cipher
        )
        line = PayoutLine(
            payee=person.display_name,
            legal_name=str(snapshot.letterhead.get("legal_name") or ""),
            amount_pence=invoice.total_pence,
            invoice_number=invoice.number,
            account_name=bank.account_name,
            bank_name=bank.bank_name,
            sort_code=bank.sort_code,
            account_number=bank.account_number,
            iban=bank.iban,
            swift_bic=bank.swift_bic,
        )
        if person.payout_region is PayoutRegion.INTL:
            run.intl.append(line)
        else:
            run.uk.append(line)
    return run


def payment_run_csv(run: PaymentRun) -> str:
    """The export (R-ADM-05). Every export is audited by the caller.

    Full account numbers are present: this is the file Robin pays from, and
    the same numbers are printed on every invoice he already holds (R-ACC-06).
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "batch",
            "payee",
            "legal name",
            "invoice number",
            "amount",
            "account name",
            "bank",
            "sort code",
            "account number",
            "iban",
            "swift/bic",
        ]
    )
    for batch, lines in (("UK", run.uk), ("INTL", run.intl)):
        for line in lines:
            writer.writerow(
                [
                    batch,
                    line.payee,
                    line.legal_name,
                    line.invoice_number,
                    f"{line.amount_pence / 100:.2f}",
                    line.account_name,
                    line.bank_name,
                    format_sort_code(line.sort_code) if line.sort_code else "",
                    line.account_number or "",
                    line.iban or "",
                    line.swift_bic or "",
                ]
            )
    return buffer.getvalue()


@dataclass(frozen=True, slots=True)
class CheckRow:
    """One line of the go-live grid (R-ADM-06)."""

    person: Person
    fee_set: bool
    profile_complete: bool
    bank_confirmed: bool
    counter_seeded: bool
    notes: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        """Whether the person can submit an invoice with nothing left for an admin to
        set.
        """
        return (
            self.fee_set
            and self.profile_complete
            and self.bank_confirmed
            and self.counter_seeded
        )


def data_check(session: Session, *, on: date) -> list[CheckRow]:
    """Four ticks per person, plus what the seed flagged for a human.

    This is the screen that says whether the first real cycle will work.
    """
    from app.services.invoicing import profile_is_complete

    people = repo.all_people(session)
    counters = repo.counters_for(session, [person.id for person in people])

    rows: list[CheckRow] = []
    for person in people:
        bank = repo.active_bank_detail(session, person.id)
        history = repo.bank_history(session, person.id)
        notes: list[str] = []

        if history and not bank:
            notes.append("bank details on file but not confirmed by them")
        if not history:
            notes.append("no bank details at all")
        names_differ = (
            person.legal_name
            and person.display_name
            and person.legal_name.strip().casefold()
            != person.display_name.strip().casefold()
        )
        if names_differ:
            notes.append(
                f"pays into the name {person.legal_name!r} — confirm this is "
                f"the intended payee"
            )
        if not person.role:
            notes.append("no role for the invoice letterhead")

        rows.append(
            CheckRow(
                person=person,
                fee_set=repo.fee_for(session, person.id, on=on) is not None,
                profile_complete=profile_is_complete(person),
                bank_confirmed=bank is not None and bank.verified_at is not None,
                counter_seeded=person.id in counters,
                notes=tuple(notes),
            )
        )
    return rows


@dataclass(frozen=True, slots=True)
class QueueView:
    """Anything waiting on somebody (R-ADM-07)."""

    awaiting_fee: list[Person]
    pending_bank: list[BankDetail]
    unconfirmed_bank: list[BankDetail]


def queue(session: Session, *, on: date) -> QueueView:
    """What is waiting on an admin today: fees to set and bank rows to review."""
    awaiting = [
        person
        for person in repo.all_people(session)
        if repo.fee_for(session, person.id, on=on) is None
        and person.status is not PersonStatus.INACTIVE
    ]
    return QueueView(
        awaiting_fee=awaiting,
        pending_bank=repo.pending_bank_rows(session),
        unconfirmed_bank=repo.unverified_bank_rows(session),
    )


class AdminError(ValueError):
    """An admin write was refused."""


def set_fee(
    session: Session,
    *,
    person: Person,
    fee_pence: int,
    valid_from: date,
    note: str,
    actor_email: str,
) -> Fee:
    """Set a fee, effective-dated (R-ADM-03, R-DATA-02).

    The open row is closed rather than updated: a March invoice must still be
    able to explain itself in December. The note is required, and both the
    change and its reason are audited in the same transaction.
    """
    if fee_pence < 0:
        raise AdminError("a fee cannot be negative")
    if not note.strip():
        raise AdminError("a note is required, so the change explains itself later")

    open_row = repo.current_fee(session, person.id)
    before: dict | None = None
    if open_row is not None:
        if open_row.valid_from >= valid_from:
            raise AdminError(
                f"the current fee starts on {open_row.valid_from:%d %b %Y}; "
                f"a new one must start after that"
            )
        before = {
            "fee": format_gbp(open_row.fee_pence),
            "valid_from": open_row.valid_from.isoformat(),
        }
        open_row.valid_to = valid_from - timedelta(days=1)

    fee = Fee(
        person_id=person.id,
        fee_pence=fee_pence,
        valid_from=valid_from,
        set_by=actor_email,
        note=note.strip(),
    )
    session.add(fee)

    # R-ADM-03: setting a fee for a pending person activates them.
    if person.status is PersonStatus.PENDING:
        person.status = PersonStatus.ACTIVE

    repo.record_audit(
        session,
        actor_email=actor_email,
        action="fee.set",
        entity="people",
        entity_id=str(person.id),
        before=before,
        after={
            "fee": format_gbp(fee_pence),
            "valid_from": valid_from.isoformat(),
            "note": note.strip(),
        },
    )
    return fee


def deactivate(
    session: Session, *, person: Person, actor_email: str, reason: str
) -> None:
    """R-ADM-09. No reminders, cannot invoice, history preserved.

    Nothing is ever deleted.
    """
    before = {"status": person.status.value}
    person.status = PersonStatus.INACTIVE
    repo.record_audit(
        session,
        actor_email=actor_email,
        action="person.deactivate",
        entity="people",
        entity_id=str(person.id),
        before=before,
        after={"status": "inactive", "reason": reason},
    )


def masked_bank_summary(session: Session, person: Person, cipher: Cipher) -> list[dict]:
    """Bank history for the person page, masked to last four (R-ADM-02).

    Robin may reveal a full number where he needs it (R-ACC-06), and that
    reveal is audited. The default view is masked.
    """
    from app.services.invoicing import ENCRYPTED_FIELDS

    out: list[dict] = []
    for detail in repo.bank_history(session, person.id):
        values: dict[str, str] = {}
        for name in ENCRYPTED_FIELDS:
            stored = getattr(detail, f"{name}_enc")
            values[name] = (
                mask(cipher.decrypt(stored, aad=field_aad(str(person.id), name)))
                if stored
                else ""
            )
        out.append(
            {
                "id": str(detail.id),
                "created_at": detail.created_at,
                "state": detail.state.value,
                "source": detail.source.value,
                "account_name": detail.account_name or "",
                "bank_name": detail.bank_name or "",
                "verified_at": detail.verified_at,
                **values,
            }
        )
    return out


# --------------------------------------------------------------------------
# Nothing fails silently
# --------------------------------------------------------------------------

# A draft is normal for the few seconds a render takes. Beyond this it is
# stuck, not in progress.
STUCK_DRAFT_MINUTES = 60


@dataclass(frozen=True, slots=True)
class Attention:
    """Things that are broken and would otherwise go unnoticed.

    The invoice table looking healthy is not the same as an invoice having
    arrived. A stuck draft holds a number and was never sent; a failed outbox
    row means Postgres accepted an invoice the provider never delivered.
    Neither shows up anywhere else, and the period view would count both as
    received.
    """

    stuck_drafts: list[Invoice]
    failed_mail: list
    unconfirmed_bank: list[BankDetail]
    # Kudos whose receiver nobody has mapped to a person (R-KUD-14). Money
    # sitting in the sheet with nowhere to go, which nothing else reports.
    unmapped_kudos: list[tuple[str, int]] = field(default_factory=list)
    # People whose kudos count could not be stated this quarter (R-KUD-06).
    # Their screen says accounts have been told, and this is what makes that
    # true rather than a sentence that sounds reassuring.
    kudos_unreadable: list[Person] = field(default_factory=list)
    # Still retrying, but for long enough that somebody should know. Not a
    # failure: nothing has given up, and nothing will.
    stale_mail: list = field(default_factory=list)

    @property
    def total(self) -> int:
        """How many things need a human, across every kind this screen tracks."""
        return (
            len(self.stuck_drafts)
            + len(self.failed_mail)
            + len(self.unconfirmed_bank)
            + len(self.unmapped_kudos)
            + len(self.kudos_unreadable)
            + len(self.stale_mail)
        )

    @property
    def is_clear(self) -> bool:
        return self.total == 0


def needs_attention(
    session: Session, *, now: datetime, stuck_after_minutes: int = STUCK_DRAFT_MINUTES
) -> Attention:
    """Everything quietly broken, in one place."""
    period = Period.containing(now)
    unreadable = (
        repo.people_missing_kudos_snapshot(session, period.key)
        if is_quarter_end(period.year, period.month)
        else []
    )
    return Attention(
        stuck_drafts=repo.stuck_drafts(
            session, older_than=now - timedelta(minutes=stuck_after_minutes)
        ),
        failed_mail=repo.failed_outbox(session),
        unconfirmed_bank=repo.unverified_bank_rows(session),
        unmapped_kudos=repo.unresolved_kudos_names(session),
        kudos_unreadable=unreadable,
        stale_mail=repo.unsent_outbox(
            session, queued_before=now - timedelta(hours=mailer.STALE_AFTER_HOURS)
        ),
    )
