"""Who may cash kudos (R-KUD-20), applied from one configured list.

Leadership and staff who do not invoice receive kudos and may not cash them or
win a prize. The list lives in configuration, and reaches a person's row both
ways a row can come about: the spreadsheet import, and signing in.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Person
from app.domain.seeding import COLUMNS, build_rows
from app.main import app
from app.scripts.seed import SHEET_NAME, apply_row, read_rows
from tests.integration.test_wizard import EMAIL, seed_ready, wizard  # noqa: F401

pytestmark = pytest.mark.integration

LEADER = "remy@example.com"


def test_signing_in_applies_the_list_both_ways(wizard, factory) -> None:  # noqa: F811
    client, settings, cipher = wizard
    seed_ready(factory, cipher)

    app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={"kudos_ineligible_emails": (EMAIL,)}
    )
    assert client.get("/").status_code == 200
    with factory() as session:
        person = session.scalars(select(Person).where(Person.email == EMAIL)).one()
        assert person.kudos_eligible is False

    # Taken off the list, the next sign-in makes them eligible again.
    app.dependency_overrides[get_settings] = lambda: settings
    assert client.get("/").status_code == 200
    with factory() as session:
        person = session.scalars(select(Person).where(Person.email == EMAIL)).one()
        assert person.kudos_eligible is True


def test_the_import_applies_the_list(factory, tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_NAME
    sheet.append(list(COLUMNS))
    for name, email in (
        ("Remy Leader", LEADER),
        ("Ann Clean", "ann@example.com"),
    ):
        bank = (
            f"Name: {name}\nBank: Monzo\nSort code: 23-08-01\nAccount number: 12345678"
        )
        sheet.append([name, "Leadership", email, 1000.0, "UK", bank, 3.0])
    path = tmp_path / "people.xlsx"
    workbook.save(path)

    with factory() as session, session.begin():
        for row in build_rows(read_rows(path)):
            apply_row(session, row, None, kudos_ineligible=(LEADER,))

    with factory() as session:
        eligible = {
            person.email: person.kudos_eligible
            for person in session.scalars(select(Person))
        }
    assert eligible == {LEADER: False, "ann@example.com": True}
