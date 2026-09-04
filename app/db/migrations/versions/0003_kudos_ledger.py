"""kudos claim ledger, aliases and quarterly awards

R-KUD-07 .. R-KUD-23, and KUDOS.md for the reasoning.

Three tables and one column:

``kudos_events``   one row per kudo in the sheet, with the stamp that says
                   which invoice billed it. This is what makes "already
                   claimed" a fact rather than a convention.
``kudos_aliases``  a sheet name mapped to a person by a human, or explicitly
                   mapped to nobody.
``kudos_awards``   quarterly cash prizes, computed by us and awarded by a
                   person.
``people.kudos_eligible``  leadership receives kudos and cannot cash them.

Reversible: downgrade drops all four and the two enum types. Nothing else is
touched, so it runs both ways on a database that already holds invoices.

Revision ID: 0003_kudos_ledger
Revises: 0002_outbox_backoff
Created: 2026-09-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM, UUID

revision: str = "0003_kudos_ledger"
down_revision: str | None = "0002_outbox_backoff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# create_type=False: the type is created once, explicitly, in upgrade().
# Left to itself SQLAlchemy emits CREATE TYPE again from inside create_table
# and the migration dies on "type already exists" — which only shows up on the
# second run, or on a downgrade followed by an upgrade.
AWARD_KIND = ENUM(
    "quarter_1st",
    "quarter_2nd",
    "quarter_3rd",
    name="award_kind",
    create_type=False,
)


def upgrade() -> None:
    # Existing people are eligible. Leadership is switched off deliberately,
    # by the seed or by hand — defaulting anyone to "cannot be paid" would be
    # the wrong way round to be wrong.
    op.add_column(
        "people",
        sa.Column(
            "kudos_eligible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )

    # 'award' joins fee/kudos/expense/other. A quarterly prize prints as its
    # own line: "5 kudos x GBP 20" and "Q3 1st place" are different things.
    op.execute("ALTER TYPE line_kind ADD VALUE IF NOT EXISTS 'award'")

    AWARD_KIND.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "kudos_aliases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("normalised_name", sa.String(200), nullable=False, unique=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column(
            "person_id",
            UUID(as_uuid=True),
            sa.ForeignKey("people.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "ignored", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("confirmed_by", sa.String(320), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "kudos_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("event_key", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "person_id",
            UUID(as_uuid=True),
            sa.ForeignKey("people.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("receiver_name", sa.String(200), nullable=False),
        sa.Column("giver_name", sa.String(200), nullable=False),
        sa.Column("value_text", sa.String(400), nullable=True),
        sa.Column("awarded_on", sa.Date(), nullable=False),
        sa.Column(
            "claimable", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "claimed_by_invoice_id",
            UUID(as_uuid=True),
            sa.ForeignKey("invoices.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The question asked on every wizard load: what does this person have that
    # nobody has billed yet.
    op.create_index(
        "ix_kudos_events_person",
        "kudos_events",
        ["person_id", "claimed_by_invoice_id"],
    )
    op.create_index("ix_kudos_events_awarded", "kudos_events", ["awarded_on"])

    op.create_table(
        "kudos_awards",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "person_id",
            UUID(as_uuid=True),
            sa.ForeignKey("people.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quarter", sa.String(7), nullable=False),
        sa.Column("kind", AWARD_KIND, nullable=False),
        sa.Column("amount_pence", sa.BigInteger(), nullable=False),
        sa.Column("kudos_count", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("awarded_by", sa.String(320), nullable=False),
        sa.Column(
            "awarded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "claimed_by_invoice_id",
            UUID(as_uuid=True),
            sa.ForeignKey("invoices.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "person_id", "quarter", "kind", name="uq_kudos_awards_person_quarter_kind"
        ),
        sa.CheckConstraint("amount_pence > 0", name="ck_kudos_awards_amount"),
    )


def downgrade() -> None:
    op.drop_table("kudos_awards")
    op.drop_index("ix_kudos_events_awarded", table_name="kudos_events")
    op.drop_index("ix_kudos_events_person", table_name="kudos_events")
    op.drop_table("kudos_events")
    op.drop_table("kudos_aliases")
    op.drop_column("people", "kudos_eligible")
    AWARD_KIND.drop(op.get_bind(), checkfirst=True)
    # 'award' stays in line_kind. Postgres cannot remove a value from an enum,
    # and an unused value is harmless; pretending otherwise would make this
    # migration fail on the way down, which is worse than a spare label.
