"""The migration must match the models, and it must run both ways (R-DATA-12).

Alembic's offline mode emits the DDL without touching a database, so this runs
in CI with no Postgres. It is not a substitute for applying the migration for
real, which tests/integration does.
"""

from __future__ import annotations

import contextlib
import io
import re

import pytest
from alembic import command
from alembic.config import Config

from modules.invoices.db.models import Base


def offline_sql(direction: str) -> str:
    config = Config("alembic.ini")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        if direction == "up":
            command.upgrade(config, "head", sql=True)
        else:
            command.downgrade(config, "head:base", sql=True)
    return buffer.getvalue()


@pytest.fixture(scope="module")
def upgrade_sql() -> str:
    return offline_sql("up")


@pytest.fixture(scope="module")
def downgrade_sql() -> str:
    return offline_sql("down")


def created_tables(sql: str) -> dict[str, set[str]]:
    """Map each table to its columns, as the whole migration chain leaves it.

    Follows ``ALTER TABLE ... ADD/DROP COLUMN`` as well as ``CREATE TABLE``:
    the schema is built by a sequence of migrations, and only the end state has
    to match the models.
    """
    tables: dict[str, set[str]] = {}
    for match in re.finditer(r"CREATE TABLE (\w+) \((.*?)\n\)", sql, flags=re.DOTALL):
        name, body = match.group(1), match.group(2)
        columns: set[str] = set()
        for line in body.splitlines():
            stripped = line.strip().rstrip(",")
            if not stripped or stripped.startswith(
                ("PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CONSTRAINT", "CHECK")
            ):
                continue
            columns.add(stripped.split()[0])
        tables[name] = columns

    for name, column in re.findall(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", sql):
        tables.setdefault(name, set()).add(column)
    for name, column in re.findall(r"ALTER TABLE (\w+) DROP COLUMN (\w+)", sql):
        tables.get(name, set()).discard(column)

    return tables


def test_migration_creates_every_model_table(upgrade_sql: str) -> None:
    created = created_tables(upgrade_sql)
    created.pop("alembic_version", None)
    assert set(created) == set(Base.metadata.tables)


def test_migration_creates_every_model_column(upgrade_sql: str) -> None:
    created = created_tables(upgrade_sql)
    mismatches: list[str] = []
    for name, table in Base.metadata.tables.items():
        expected = {column.name for column in table.columns}
        if created.get(name) != expected:
            missing = expected - created.get(name, set())
            extra = created.get(name, set()) - expected
            mismatches.append(
                f"{name}: missing={sorted(missing)} extra={sorted(extra)}"
            )
    assert mismatches == []


def test_the_constraints_that_matter_are_in_the_database(upgrade_sql: str) -> None:
    # These are the invariants the application is allowed to rely on.
    assert "uq_invoices_person_number" in upgrade_sql  # R-NUM-03
    assert "ck_invoices_total" in upgrade_sql  # R-DATA-06 arithmetic
    assert "uq_invoices_person_period_live" in upgrade_sql  # one live per period
    assert "uq_bank_details_one_active" in upgrade_sql  # R-DATA-03
    assert "WHERE status <> 'void'" in upgrade_sql
    assert "WHERE state = 'active'" in upgrade_sql


def test_downgrade_removes_everything_including_the_enum_types(
    downgrade_sql: str,
) -> None:
    # A dropped table does not take its enum type with it, so a downgrade that
    # leaves the types behind cannot be re-applied.
    for table in Base.metadata.tables:
        assert f"DROP TABLE {table}" in downgrade_sql
    for enum_name in (
        "bank_source",
        "bank_state",
        "expense_kind",
        "invoice_status",
        "line_kind",
        "outbox_status",
        "payout_region",
        "person_status",
    ):
        assert f"DROP TYPE IF EXISTS {enum_name}" in downgrade_sql


def test_money_columns_are_integers_never_numeric(upgrade_sql: str) -> None:
    # CLAUDE.md §2.4: money is integer pence. A NUMERIC or DOUBLE column here
    # would be the first step towards a float.
    assert "NUMERIC" not in upgrade_sql.upper()
    assert "DOUBLE PRECISION" not in upgrade_sql.upper()
    for raw in upgrade_sql.splitlines():
        line = raw.strip()
        if line.startswith(("CONSTRAINT", "CHECK")):
            continue  # a constraint may mention a column without declaring it
        if line.split(" ")[0].endswith("_pence"):
            assert "BIGINT" in line or "INTEGER" in line, line
