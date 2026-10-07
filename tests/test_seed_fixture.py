"""R-TEST-04: the seed over a fixture spreadsheet containing every messy
variant found in the real file, asserting the exact set of warnings.

The fixture is built here rather than committed, because a committed .xlsx of
bank details is exactly what R-ENV-02 and the .gitignore exist to prevent. The
values are invented; only their *shape* is copied from the real file.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from modules.invoices.domain.seeding import COLUMNS, build_rows
from modules.invoices.scripts.seed import SHEET_NAME, read_rows

# Every awkward shape the real file contains, one per row.
FIXTURE_ROWS: list[tuple[str, str, str, float, str, str, float]] = [
    (
        # Clean UK row, sort code with separators, trailing padding on the name.
        "Ann Clean",
        "Design",
        "ann@example.com",
        1000.0,
        "UK",
        "Name: Ann Clean" + " " * 70 + "\nBank: Monzo\nSort code: 23-08-01\n"
        "Account number: 12345678\nUse when sending money from the UK",
        30.0,
    ),
    (
        # Sort code written bare, and labelled "Bank branch" instead.
        "Ben Branch",
        "Build",
        "ben@example.com",
        1000.0,
        "UK",
        "Name: Ben Branch\nBank name: Starling\nBank branch: 230801\n"
        "Account no: 87654321",
        0.0,
    ),
    (
        # International: Luxembourg IBAN, no sort code, company payee.
        "Carla Overseas",
        "Design",
        "carla@example.com",
        1000.0,
        "EUR",
        "Name: Bridge Building\nBank: Wise\nIBAN: LU28 0019 4006 4475 0000\n"
        "Swift/BIC: TRWIBEB1XXX\nUse when sending money from outside the UK",
        2.0,
    ),
    (
        # Wise row stating the IBAN as well: the embedded sort code disagrees.
        "Dan Wise",
        "Build",
        "dan@example.com",
        1000.0,
        "UK",
        "Name: Dan Wise\nBank: Wise\nBranch number: 23-08-01\n"
        "Account number: 12345678\nIBAN: GB82 WEST 1234 5698 7654 32\n"
        "Swift/BIC: TRWIGB2LXXX",
        213.0,
    ),
    (
        # No Name: line at all, and no bank name.
        "Eve Nameless",
        "Design",
        "eve@example.com",
        1000.0,
        "UK",
        "Branch code: 230801\nAccount number: 11112222",
        8.0,
    ),
    (
        # A stray line that is neither a known key nor a template caption.
        "Fred Stray",
        "Build",
        "fred@example.com",
        1000.0,
        "UK",
        "Name: Fred Stray\nBank: Monzo\nSort code: 230801\n"
        "Account number: 12345678\nPaypal: fred@other.test",
        16.0,
    ),
    (
        # Display name and account holder are different people.
        "Gail Marlow (GG)",
        "Design",
        "gail@example.com",
        1000.0,
        "UK",
        "Name: Georgia Anne Robinson\nBank: Revolut\nSort code: 04-00-75\n"
        "Account number: 99887766",
        61.0,
    ),
]


@pytest.fixture(scope="module")
def fixture_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_NAME
    sheet.append(list(COLUMNS))
    for row in FIXTURE_ROWS:
        sheet.append(list(row))
    # A table of blank rows below the data, as the real sheet has.
    for _ in range(20):
        sheet.append([None] * len(COLUMNS))
    path = tmp_path_factory.mktemp("seed") / "fixture.xlsx"
    workbook.save(path)
    return path


@pytest.fixture(scope="module")
def rows(fixture_path: Path):
    return build_rows(read_rows(fixture_path))


def test_blank_rows_below_the_data_are_ignored(rows) -> None:
    assert len(rows) == len(FIXTURE_ROWS)


def test_nothing_in_the_fixture_is_blocked(rows) -> None:
    # Messy is not the same as invalid. Every one of these must import, with
    # flags, or the seed would refuse the real file too.
    blocked = {row.display_name: row.errors for row in rows if not row.importable}
    assert blocked == {}


def test_the_exact_set_of_warnings(rows) -> None:
    by_name = {row.display_name: row.warnings for row in rows}

    assert by_name["Ann Clean"] == []
    assert by_name["Ben Branch"] == []

    assert any("intended payee" in w for w in by_name["Carla Overseas"])
    assert len(by_name["Carla Overseas"]) == 1

    assert any("does not match" in w for w in by_name["Dan Wise"])

    eve = by_name["Eve Nameless"]
    assert any("no Name: line" in w for w in eve)
    assert any("no account holder name" in w for w in eve)
    assert any("no bank name" in w for w in eve)

    assert any("unparsed line" in w for w in by_name["Fred Stray"])
    assert any("Paypal" in w for w in by_name["Fred Stray"])

    assert any("intended payee" in w for w in by_name["Gail Marlow (GG)"])


def test_padding_is_stripped_from_the_account_name(rows) -> None:
    ann = next(row for row in rows if row.display_name == "Ann Clean")
    assert ann.legal_name == "Ann Clean"


def test_sort_codes_normalise_however_they_were_written(rows) -> None:
    # Written as 23-08-01, as 230801, and under three different labels.
    for name in ("Ann Clean", "Ben Branch", "Dan Wise"):
        row = next(r for r in rows if r.display_name == name)
        assert row.bank.sort_code == "230801"


def test_counters_continue_the_existing_sequence(rows) -> None:
    numbers = {row.display_name: row.next_number for row in rows}
    assert numbers["Ann Clean"] == 31
    assert numbers["Dan Wise"] == 214
    assert numbers["Ben Branch"] == 1  # never invoiced, so their first is 1


def test_remuneration_never_reaches_a_seed_row(rows) -> None:
    # R-SEED-02: the column is read and discarded.
    assert all("1000" not in repr(row) for row in rows)


def test_the_intl_row_needs_no_sort_code(rows) -> None:
    carla = next(row for row in rows if row.display_name == "Carla Overseas")
    assert carla.bank.sort_code is None
    assert carla.importable


def test_rows_needing_attention_are_counted(rows) -> None:
    # Ann and Ben are clean; the other five each carry something for a human.
    assert sum(1 for row in rows if row.needs_attention) == 5
