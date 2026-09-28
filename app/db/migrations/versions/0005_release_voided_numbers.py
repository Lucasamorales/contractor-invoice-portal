"""A voided invoice gives its number back (R-NUM-05).

``uq_invoices_person_number`` held one number per person for ever, void or
not, so a reissue could never carry the number of the invoice it replaced —
the insert failed on the constraint. The number was therefore permanently
spent, and every correction left a hole in somebody's own sequence that had to
be explained to an accountant.

Replaced with the same uniqueness over **live** invoices only, which is the
pattern already used next to it by ``uq_invoices_person_period_live``. A void
row keeps its number for the record; nothing live may share one.

Reversible. Going back rejects the change if any person now holds the same
number on more than one live invoice — which cannot happen, because the index
being dropped is what forbids it — but the check is left in so a downgrade
fails loudly rather than leaving a constraint that cannot be created.

Revision ID: 0005_release_voided_numbers
Revises: 0004_drop_drive_file_id
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005_release_voided_numbers"
down_revision: str | None = "0004_drop_drive_file_id"
branch_labels: str | None = None
depends_on: str | None = None

LIVE_INDEX = "uq_invoices_person_number_live"
OLD_CONSTRAINT = "uq_invoices_person_number"


def upgrade() -> None:
    op.drop_constraint(OLD_CONSTRAINT, "invoices", type_="unique")
    op.create_index(
        LIVE_INDEX,
        "invoices",
        ["person_id", "number"],
        unique=True,
        postgresql_where=sa.text("status <> 'void'"),
    )


def downgrade() -> None:
    """Postgres refuses this by itself if a number has already been reused.

    Recreating the constraint over a table where one person holds the same
    number twice fails with a unique violation naming the pair, which is a
    better message than anything written here — and this migration must not
    read the database to find out, because it also has to run offline.
    """
    op.drop_index(LIVE_INDEX, table_name="invoices")
    op.create_unique_constraint(OLD_CONSTRAINT, "invoices", ["person_id", "number"])
