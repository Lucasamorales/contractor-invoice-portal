"""initial schema

Creates every table in SPEC.md section 2 (R-DATA-01 .. R-DATA-11).

The invariants live here as database constraints rather than as application
checks: UNIQUE (person_id, number) is the backstop for R-NUM-03, the partial
unique index on (person_id, period) is R-DATA-06's one-live-invoice-per-period
rule, and the CHECK on total_pence is the arithmetic the app must agree with.

Revision ID: 0001_initial
Revises:
Created: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("actor_email", sa.String(length=320), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("entity", sa.String(length=60), nullable=False),
        sa.Column("entity_id", sa.String(length=100), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_log_at", "audit_log", ["at"], unique=False)
    op.create_index(
        "ix_audit_log_entity", "audit_log", ["entity", "entity_id"], unique=False
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=60), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "sent", "failed", name="outbox_status"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_outbox_status_created", "outbox", ["status", "created_at"], unique=False
    )
    op.create_table(
        "people",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("legal_name", sa.String(length=200), nullable=True),
        sa.Column("role", sa.String(length=200), nullable=True),
        sa.Column("team", sa.String(length=200), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("contact_email", sa.String(length=320), nullable=True),
        sa.Column(
            "status",
            sa.Enum("pending", "active", "inactive", name="person_status"),
            nullable=False,
        ),
        sa.Column(
            "payout_region", sa.Enum("UK", "INTL", name="payout_region"), nullable=True
        ),
        sa.Column("profile_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "bank_details",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("account_name", sa.String(length=200), nullable=True),
        sa.Column("bank_name", sa.String(length=200), nullable=True),
        sa.Column("account_number_enc", sa.Text(), nullable=True),
        sa.Column("sort_code_enc", sa.Text(), nullable=True),
        sa.Column("iban_enc", sa.Text(), nullable=True),
        sa.Column("swift_bic_enc", sa.Text(), nullable=True),
        sa.Column("encryption_key_id", sa.String(length=32), nullable=True),
        sa.Column(
            "source",
            sa.Enum("import", "self", "admin_proposed", name="bank_source"),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.Enum(
                "unverified",
                "active",
                "pending",
                "rejected",
                "superseded",
                name="bank_state",
            ),
            nullable=False,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by", sa.String(length=320), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_bank_details_person", "bank_details", ["person_id"], unique=False
    )
    op.create_index(
        "uq_bank_details_one_active",
        "bank_details",
        ["person_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )
    op.create_table(
        "counters",
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("next_number", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("next_number >= 1", name="ck_counters_positive"),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("person_id"),
    )
    op.create_table(
        "fees",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("fee_pence", sa.BigInteger(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("set_by", sa.String(length=320), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("fee_pence >= 0", name="ck_fees_non_negative"),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from", name="ck_fees_range"
        ),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_fees_person_valid_from", "fees", ["person_id", "valid_from"], unique=False
    )
    op.create_table(
        "invoices",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("draft", "submitted", "void", name="invoice_status"),
            nullable=False,
        ),
        sa.Column("is_late", sa.Boolean(), nullable=False),
        sa.Column("remuneration_pence", sa.BigInteger(), nullable=False),
        sa.Column("additional_pence", sa.BigInteger(), nullable=False),
        sa.Column("total_pence", sa.BigInteger(), nullable=False),
        sa.Column("detail_note", sa.Text(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("pdf_uri", sa.Text(), nullable=True),
        sa.Column("drive_file_id", sa.String(length=200), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("currency = 'GBP'", name="ck_invoices_currency"),
        sa.CheckConstraint("number >= 1", name="ck_invoices_number_positive"),
        sa.CheckConstraint(
            "total_pence = remuneration_pence + additional_pence",
            name="ck_invoices_total",
        ),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["replaced_by_id"], ["invoices.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("person_id", "number", name="uq_invoices_person_number"),
    )
    op.create_index(
        "ix_invoices_period_status", "invoices", ["period", "status"], unique=False
    )
    op.create_index(
        "uq_invoices_person_period_live",
        "invoices",
        ["person_id", "period"],
        unique=True,
        postgresql_where=sa.text("status <> 'void'"),
    )
    op.create_table(
        "kudos_snapshots",
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("kudos_count", sa.Integer(), nullable=False),
        sa.Column("rate_pence", sa.Integer(), nullable=False),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("kudos_count >= 0", name="ck_kudos_non_negative"),
        sa.CheckConstraint("rate_pence > 0", name="ck_kudos_rate_positive"),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("person_id", "period"),
    )
    op.create_table(
        "expenses",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column(
            "kind", sa.Enum("expense", "other", name="expense_kind"), nullable=False
        ),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column("amount_pence", sa.BigInteger(), nullable=False),
        sa.Column("receipt_uri", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount_pence >= 0", name="ck_expenses_non_negative"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_expenses_invoice", "expenses", ["invoice_id"], unique=False)
    op.create_table(
        "invoice_lines",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("fee", "kudos", "expense", "other", name="line_kind"),
            nullable=False,
        ),
        sa.Column("label", sa.String(length=300), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("unit_pence", sa.BigInteger(), nullable=False),
        sa.Column("amount_pence", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_invoice_lines_invoice", "invoice_lines", ["invoice_id"], unique=False
    )
    op.create_table(
        "print_snapshot",
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column(
            "letterhead", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("bill_to", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "bank_block", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("invoice_id"),
    )


def downgrade() -> None:
    op.drop_table("print_snapshot")
    op.drop_index("ix_invoice_lines_invoice", table_name="invoice_lines")
    op.drop_table("invoice_lines")
    op.drop_index("ix_expenses_invoice", table_name="expenses")
    op.drop_table("expenses")
    op.drop_table("kudos_snapshots")
    op.drop_index("ix_invoices_period_status", table_name="invoices")
    op.drop_index(
        "uq_invoices_person_period_live",
        table_name="invoices",
        postgresql_where=sa.text("status <> 'void'"),
    )
    op.drop_table("invoices")
    op.drop_index("ix_fees_person_valid_from", table_name="fees")
    op.drop_table("fees")
    op.drop_table("counters")
    op.drop_index("ix_bank_details_person", table_name="bank_details")
    op.drop_index(
        "uq_bank_details_one_active",
        table_name="bank_details",
        postgresql_where=sa.text("state = 'active'"),
    )
    op.drop_table("bank_details")
    op.drop_table("people")
    op.drop_index("ix_outbox_status_created", table_name="outbox")
    op.drop_table("outbox")
    op.drop_index("ix_audit_log_at", table_name="audit_log")
    op.drop_index("ix_audit_log_entity", table_name="audit_log")
    op.drop_table("audit_log")
    # A dropped table does not take its enum type with it, so the migration
    # would not be reversible without this (R-DATA-12).
    op.execute("DROP TYPE IF EXISTS bank_source")
    op.execute("DROP TYPE IF EXISTS bank_state")
    op.execute("DROP TYPE IF EXISTS expense_kind")
    op.execute("DROP TYPE IF EXISTS invoice_status")
    op.execute("DROP TYPE IF EXISTS line_kind")
    op.execute("DROP TYPE IF EXISTS outbox_status")
    op.execute("DROP TYPE IF EXISTS payout_region")
    op.execute("DROP TYPE IF EXISTS person_status")
