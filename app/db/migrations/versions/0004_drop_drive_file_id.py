"""drop invoices.drive_file_id

The Drive mirror (R-JOB-06, R-STO-04, R-STO-05) was dropped on 16 September
2026: invoices are in GCS, finance receive each one by email, and a copy in a
Shared Drive would have been a second place holding everyone's full account
number. The column only ever existed to make that mirror idempotent and was
never written, so every row holds NULL and nothing is lost.

Reversible: downgrade adds the column back, empty, as it always was.

Revision ID: 0004_drop_drive_file_id
Revises: 0003_kudos_ledger
Created: 2026-09-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_drop_drive_file_id"
down_revision: str | None = "0003_kudos_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("invoices", "drive_file_id")


def downgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("drive_file_id", sa.String(length=200), nullable=True),
    )
