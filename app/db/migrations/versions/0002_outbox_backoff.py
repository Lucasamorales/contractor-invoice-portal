"""outbox retry scheduling

Adds ``outbox.next_attempt_at``. R-JOB-04 requires the dispatcher to retry with
exponential backoff, and backoff needs a column that says "not before this".
NULL means the row is due now, so existing rows are unaffected.

Revision ID: 0002_outbox_backoff
Revises: 0001_initial
Created: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_outbox_backoff"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbox",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The dispatcher claims rows by status and due time on every run.
    op.create_index(
        "ix_outbox_due", "outbox", ["status", "next_attempt_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_due", table_name="outbox")
    op.drop_column("outbox", "next_attempt_at")
