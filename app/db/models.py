"""SQLAlchemy models (R-DATA-01 .. R-DATA-11).

All money is ``INTEGER`` pence and all timestamps are ``TIMESTAMPTZ`` in UTC.
The invariants that matter are database constraints, not application checks: a
uniqueness rule enforced only in Python is a race condition with a comment on
it.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for every table."""


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class PersonStatus(enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    INACTIVE = "inactive"


class PayoutRegion(enum.Enum):
    UK = "UK"
    INTL = "INTL"


class BankSource(enum.Enum):
    IMPORT = "import"
    SELF = "self"
    ADMIN_PROPOSED = "admin_proposed"


class BankState(enum.Enum):
    UNVERIFIED = "unverified"
    ACTIVE = "active"
    PENDING = "pending"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class InvoiceStatus(enum.Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    VOID = "void"


class LineKind(enum.Enum):
    FEE = "fee"
    KUDOS = "kudos"
    EXPENSE = "expense"
    OTHER = "other"
    # A quarterly prize. Its own kind, not folded into KUDOS: "5 kudos x GBP 20"
    # and "Q3 1st place" are different things and Robin has to tell them apart.
    AWARD = "award"


class ExpenseKind(enum.Enum):
    EXPENSE = "expense"
    OTHER = "other"


class AwardKind(enum.Enum):
    """The quarterly cash prizes (KUDOS.md §5). Cash carries no threshold."""

    QUARTER_1ST = "quarter_1st"
    QUARTER_2ND = "quarter_2nd"
    QUARTER_3RD = "quarter_3rd"


class OutboxStatus(enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


def _enum(python_enum: type[enum.Enum], name: str) -> Enum:
    """A native Postgres enum storing the member *values*, not their names."""
    return Enum(
        python_enum,
        name=name,
        values_callable=lambda members: [member.value for member in members],
    )


_now = func.now()


class Person(Base):
    """R-DATA-01.

    ``display_name`` is how we address someone in the UI. ``legal_name`` is what
    prints on the invoice letterhead and must match the bank account holder;
    the two differ in the real data (R-SEED-08).
    """

    __tablename__ = "people"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(200))
    # The job title printed on the invoice, not the team. Nobody in the
    # spreadsheet has one; it is collected at first login (R-WIZ-02).
    role: Mapped[str | None] = mapped_column(String(200))
    team: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(50))
    contact_email: Mapped[str | None] = mapped_column(String(320))
    status: Mapped[PersonStatus] = mapped_column(
        _enum(PersonStatus, "person_status"),
        nullable=False,
        default=PersonStatus.PENDING,
    )
    # Nullable, which R-DATA-01 does not state either way. A person created
    # at first login (R-ACC-03) has no known payout route yet, and inventing
    # one would decide how somebody gets paid on no evidence.
    payout_region: Mapped[PayoutRegion | None] = mapped_column(
        _enum(PayoutRegion, "payout_region")
    )
    profile_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # R-KUD-20. Leadership may be given kudos and may appear on a leaderboard;
    # they cannot convert them to money and cannot win a prize. A flag rather
    # than a list of names, because "everything about this person is special"
    # is wrong the first time somebody else is promoted.
    kudos_eligible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_now, onupdate=_now
    )

    fees: Mapped[list[Fee]] = relationship(back_populates="person")
    bank_details: Mapped[list[BankDetail]] = relationship(back_populates="person")
    invoices: Mapped[list[Invoice]] = relationship(back_populates="person")


class Fee(Base):
    """R-DATA-02. Effective-dated: never updated, only closed and superseded.

    A March invoice must still be able to explain itself in December.
    """

    __tablename__ = "fees"
    __table_args__ = (
        CheckConstraint("fee_pence >= 0", name="ck_fees_non_negative"),
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from", name="ck_fees_range"
        ),
        Index("ix_fees_person_valid_from", "person_id", "valid_from"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    person_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("people.id", ondelete="RESTRICT"), nullable=False
    )
    fee_pence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    set_by: Mapped[str] = mapped_column(String(320), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_now
    )

    person: Mapped[Person] = relationship(back_populates="fees")


class BankDetail(Base):
    """R-DATA-03. Rows are never edited in place; a change creates a new row.

    The ``*_enc`` columns hold ciphertext (R-SEC-03): the database is a third
    party, and a breach there must not yield anything payable.
    """

    __tablename__ = "bank_details"
    __table_args__ = (
        # Exactly one active row per person, enforced by the database.
        Index(
            "uq_bank_details_one_active",
            "person_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
        Index("ix_bank_details_person", "person_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    person_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("people.id", ondelete="RESTRICT"), nullable=False
    )
    account_name: Mapped[str | None] = mapped_column(String(200))
    bank_name: Mapped[str | None] = mapped_column(String(200))
    account_number_enc: Mapped[str | None] = mapped_column(Text)
    sort_code_enc: Mapped[str | None] = mapped_column(Text)
    iban_enc: Mapped[str | None] = mapped_column(Text)
    swift_bic_enc: Mapped[str | None] = mapped_column(Text)
    # Which encryption key produced the ciphertext, so keys can be rotated.
    encryption_key_id: Mapped[str | None] = mapped_column(String(32))
    source: Mapped[BankSource] = mapped_column(
        _enum(BankSource, "bank_source"), nullable=False
    )
    state: Mapped[BankState] = mapped_column(
        _enum(BankState, "bank_state"), nullable=False, default=BankState.UNVERIFIED
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[str | None] = mapped_column(String(320))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_now
    )

    person: Mapped[Person] = relationship(back_populates="bank_details")


class KudosAlias(Base):
    """A sheet name mapped to a person, by a human (R-KUD-13).

    The sheet says ``Toni Grant`` where the payroll says ``Mitchell Grant``, and
    spellings drift between its tabs. Fuzzy matching is forbidden: a near-miss
    that resolves itself pays the wrong person. So every mapping is confirmed
    once, by somebody, and recorded here with who confirmed it.

    ``person_id`` NULL means "resolve this name to nobody, and stop reporting
    it" — someone who has left, or staff who do not invoice here (R-KUD-14).
    Ignoring is never deleting: their events stay, to explain history.
    """

    __tablename__ = "kudos_aliases"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # Accent-folded, lower-cased, quotes removed. app.domain.kudos.normalise_name
    # is the only thing that produces this, so the two cannot drift.
    normalised_name: Mapped[str] = mapped_column(
        String(200), unique=True, nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("people.id", ondelete="RESTRICT")
    )
    ignored: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    note: Mapped[str | None] = mapped_column(Text)
    confirmed_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_now
    )


class KudosEvent(Base):
    """One kudo from the sheet (R-KUD-09, R-KUD-10).

    The claim ledger. ``claimed_by_invoice_id`` is the whole point: an event is
    billed at most once, ever, and the stamp goes on in the same transaction
    that allocates the invoice number.

    Rows are never deleted. A kudo that leaves the sheet is marked
    ``withdrawn_at`` and reported (R-KUD-15) — somebody tidying the spreadsheet
    must not destroy the record of what was paid.
    """

    __tablename__ = "kudos_events"
    __table_args__ = (
        Index("ix_kudos_events_person", "person_id", "claimed_by_invoice_id"),
        Index("ix_kudos_events_awarded", "awarded_on"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    # Hash of date, giver, receiver and values. Not the row number, which any
    # insertion shifts, and not the message, so fixing a typo in the wording of
    # a kudo does not conjure a second one.
    event_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("people.id", ondelete="RESTRICT")
    )
    receiver_name: Mapped[str] = mapped_column(String(200), nullable=False)
    giver_name: Mapped[str] = mapped_column(String(200), nullable=False)
    value_text: Mapped[str | None] = mapped_column(String(400))
    awarded_on: Mapped[date] = mapped_column(Date, nullable=False)
    # False for everything before the cut-over: counted for the standings,
    # never billed, because it was settled the old way (R-KUD-12).
    claimable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    claimed_by_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="RESTRICT")
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_now
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KudosAward(Base):
    """A quarterly cash prize (R-KUD-17, R-KUD-18).

    Computed by us, awarded by a person. The policy's tiebreakers — year to
    date, consistency, kudos from other teams, longevity — are all judgements,
    so nothing here is ever created automatically.

    Claimed exactly like a kudo, and printed as its own line.
    """

    __tablename__ = "kudos_awards"
    __table_args__ = (
        # One prize of one kind per quarter per person. The backstop for a
        # double-click on the award button.
        UniqueConstraint(
            "person_id", "quarter", "kind", name="uq_kudos_awards_person_quarter_kind"
        ),
        CheckConstraint("amount_pence > 0", name="ck_kudos_awards_amount"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    person_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("people.id", ondelete="RESTRICT"), nullable=False
    )
    quarter: Mapped[str] = mapped_column(String(7), nullable=False)  # 2026-Q3
    kind: Mapped[AwardKind] = mapped_column(
        _enum(AwardKind, "award_kind"), nullable=False
    )
    amount_pence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kudos_count: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    awarded_by: Mapped[str] = mapped_column(String(320), nullable=False)
    awarded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_now
    )
    claimed_by_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="RESTRICT")
    )


class KudosSnapshot(Base):
    """R-DATA-04. The rate travels with the snapshot so a future rate change
    cannot rewrite history."""

    __tablename__ = "kudos_snapshots"
    __table_args__ = (
        CheckConstraint("kudos_count >= 0", name="ck_kudos_non_negative"),
        CheckConstraint("rate_pence > 0", name="ck_kudos_rate_positive"),
    )

    person_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("people.id", ondelete="RESTRICT"), primary_key=True
    )
    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    kudos_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rate_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_now
    )


class Invoice(Base):
    """R-DATA-06. Immutable once submitted; corrections are void and reissue."""

    __tablename__ = "invoices"
    __table_args__ = (
        # R-NUM-03: the backstop. If this ever fires it is a bug, and it must
        # surface as a 500 rather than be swallowed.
        UniqueConstraint("person_id", "number", name="uq_invoices_person_number"),
        CheckConstraint(
            "total_pence = remuneration_pence + additional_pence",
            name="ck_invoices_total",
        ),
        CheckConstraint("number >= 1", name="ck_invoices_number_positive"),
        CheckConstraint("currency = 'GBP'", name="ck_invoices_currency"),
        # R-DATA-06: one live invoice per person per period.
        Index(
            "uq_invoices_person_period_live",
            "person_id",
            "period",
            unique=True,
            postgresql_where=text("status <> 'void'"),
        ),
        Index("ix_invoices_period_status", "period", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    person_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("people.id", ondelete="RESTRICT"), nullable=False
    )
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[InvoiceStatus] = mapped_column(
        _enum(InvoiceStatus, "invoice_status"),
        nullable=False,
        default=InvoiceStatus.DRAFT,
    )
    is_late: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    remuneration_pence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    additional_pence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_pence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    detail_note: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    pdf_uri: Mapped[str | None] = mapped_column(Text)
    drive_file_id: Mapped[str | None] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False
    )
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_now
    )

    person: Mapped[Person] = relationship(back_populates="invoices")
    lines: Mapped[list[InvoiceLine]] = relationship(back_populates="invoice")
    expenses: Mapped[list[Expense]] = relationship(back_populates="invoice")


class InvoiceLine(Base):
    """R-DATA-07. The breakdown the PDF collapses into "Additional".

    Frozen at issue time: these rows are what the invoice meant, whatever the
    fee table or the kudos sheet say later.
    """

    __tablename__ = "invoice_lines"
    __table_args__ = (Index("ix_invoice_lines_invoice", "invoice_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[LineKind] = mapped_column(_enum(LineKind, "line_kind"), nullable=False)
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_pence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_pence: Mapped[int] = mapped_column(BigInteger, nullable=False)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class Expense(Base):
    """R-DATA-05."""

    __tablename__ = "expenses"
    __table_args__ = (
        CheckConstraint("amount_pence >= 0", name="ck_expenses_non_negative"),
        Index("ix_expenses_invoice", "invoice_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[ExpenseKind] = mapped_column(
        _enum(ExpenseKind, "expense_kind"), nullable=False
    )
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    amount_pence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    receipt_uri: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_now
    )

    invoice: Mapped[Invoice] = relationship(back_populates="expenses")


class Counter(Base):
    """R-DATA-08. The row locked ``FOR UPDATE`` during allocation (R-NUM-02)."""

    __tablename__ = "counters"
    __table_args__ = (CheckConstraint("next_number >= 1", name="ck_counters_positive"),)

    person_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("people.id", ondelete="RESTRICT"), primary_key=True
    )
    next_number: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_now, onupdate=_now
    )


class PrintSnapshot(Base):
    """R-DATA-09. Exactly what was printed.

    Lets an invoice be re-rendered byte-identically, and lets Robin always
    answer "which account did we pay on 25 August".
    """

    __tablename__ = "print_snapshot"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), primary_key=True
    )
    letterhead: Mapped[dict] = mapped_column(JSONB, nullable=False)
    bill_to: Mapped[dict] = mapped_column(JSONB, nullable=False)
    bank_block: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_now
    )


class Outbox(Base):
    """R-DATA-10. Mail is queued in the submit transaction and sent by a job.

    A mail-provider outage must not lose an invoice Postgres already accepted
    (R-SUB-06).
    """

    __tablename__ = "outbox"
    __table_args__ = (
        Index("ix_outbox_status_created", "status", "created_at"),
        Index("ix_outbox_due", "status", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[OutboxStatus] = mapped_column(
        _enum(OutboxStatus, "outbox_status"),
        nullable=False,
        default=OutboxStatus.PENDING,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    # When the dispatcher may next try this row. R-JOB-04 wants exponential
    # backoff, and backoff needs somewhere to say "not before". NULL means now.
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_now
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    """R-DATA-11. Written in the same transaction as the change it records.

    Bank and fee values in ``before``/``after`` are masked to last four digits:
    the audit log is not a second copy of the payroll.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_entity", "entity", "entity_id"),
        Index("ix_audit_log_at", "at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    actor_email: Mapped[str] = mapped_column(String(320), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_now
    )
