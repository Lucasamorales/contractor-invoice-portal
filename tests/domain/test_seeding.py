"""Row interpretation for the seed (R-SEED-02, 03, 08, 09, 12)."""

from __future__ import annotations

import pytest

from app.domain.banking import PayoutRegion
from app.domain.numbering import NumberingError
from app.domain.seeding import build_row, build_rows, names_agree, parse_invoice_number

UK_BLOB = "\n".join(
    [
        "Name: Jane Smith",
        "Bank: Monzo",
        "Sort code: 23-08-01",
        "Account number: 12345678",
        "Use when sending money from the UK",
    ]
)


def raw(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "NAME": "Jane Smith",
        "TEAM": "Design",
        "EMAIL": "jane@example.com",
        "REMUNERATION": 1000.0,
        "NOTES": "UK",
        "BANK ACCOUNTS": UK_BLOB,
        "INVOICE N°": 7.0,
    }
    row.update(overrides)
    return row


def test_a_clean_uk_row_imports() -> None:
    row = build_row(raw(), 2)
    assert row.errors == []
    assert row.importable
    assert row.payout_region is PayoutRegion.UK
    assert row.next_number == 8
    assert row.legal_name == "Jane Smith"
    assert row.warnings == []


def test_remuneration_is_never_imported() -> None:
    # R-SEED-02: every value in the file is the placeholder 1000.0 and fees are
    # Robin's to type. SeedRow has nowhere to put one.
    row = build_row(raw(REMUNERATION=999999.0), 2)
    assert not hasattr(row, "fee_pence")
    assert "999999" not in repr(row)


def test_eur_maps_to_intl_not_to_a_currency() -> None:
    row = build_row(
        raw(
            NOTES="EUR",
            **{
                "BANK ACCOUNTS": (
                    "Name: Bridge Building\nBank: Wise\n"
                    "IBAN: LU28 0019 4006 4475 0000\nSwift/BIC: TRWIBEB1XXX"
                )
            },
        ),
        2,
    )
    assert row.payout_region is PayoutRegion.INTL
    assert row.errors == []


def test_intl_row_with_no_sort_code_is_not_an_error() -> None:
    # R-SEED-06: Jamie is the only INTL row and has no sort code.
    row = build_row(
        raw(
            NOTES="EUR",
            **{
                "BANK ACCOUNTS": (
                    "Name: Bridge Building\nBank: Wise\n"
                    "IBAN: LU28 0019 4006 4475 0000\nSwift/BIC: TRWIBEB1XXX"
                )
            },
        ),
        2,
    )
    assert row.errors == []
    assert row.bank.sort_code is None


def test_a_company_payee_is_flagged_for_confirmation() -> None:
    # R-SEED-08 / R-ADM-06: "Bridge Building" is a company, and Robin has to
    # confirm it is the intended payee before the first automated payment.
    row = build_row(
        raw(
            NAME="Jamie",
            NOTES="EUR",
            **{
                "BANK ACCOUNTS": (
                    "Name: Bridge Building\nBank: Wise\n"
                    "IBAN: LU28 0019 4006 4475 0000\nSwift/BIC: TRWIBEB1XXX"
                )
            },
        ),
        2,
    )
    assert any("intended payee" in warning for warning in row.warnings)
    assert row.importable  # flagged, not blocked


def test_a_name_mismatch_is_flagged_but_still_imports() -> None:
    row = build_row(
        raw(
            NAME="Gabriel Marlow (SIMON)",
            **{"BANK ACCOUNTS": UK_BLOB.replace("Jane Smith", "Simon Andrew Robinson")},
        ),
        2,
    )
    assert any("intended payee" in warning for warning in row.warnings)
    assert row.importable


def test_a_missing_name_line_is_flagged() -> None:
    blob = "Bank: Monzo\nSort code: 230801\nAccount number: 12345678"
    row = build_row(raw(**{"BANK ACCOUNTS": blob}), 2)
    assert row.legal_name is None
    assert any("no Name: line" in warning for warning in row.warnings)


def test_names_agree_ignores_case_and_padding() -> None:
    assert names_agree("Jane Smith", "  jane   smith ") is True
    assert names_agree("Jane Smith", "Bridge Building") is False
    assert names_agree("Jane Smith", None) is False


@pytest.mark.parametrize(
    ("cell", "expected"),
    [(0.0, 0), (213.0, 213), (8, 8), ("34", 34), (" 61 ", 61)],
)
def test_parse_invoice_number(cell: object, expected: int) -> None:
    assert parse_invoice_number(cell) == expected


@pytest.mark.parametrize("cell", [None, "", "abc", 12.5, True])
def test_bad_invoice_number_is_an_error(cell: object) -> None:
    with pytest.raises(NumberingError):
        parse_invoice_number(cell)


def test_zero_becomes_first_invoice_number_one() -> None:
    # R-SEED-09: Gabriel has never invoiced, so his first is 1.
    row = build_row(raw(**{"INVOICE N°": 0.0}), 2)
    assert row.next_number == 1


def test_unparsed_bank_line_is_reported_and_the_row_flagged() -> None:
    row = build_row(raw(**{"BANK ACCOUNTS": UK_BLOB + "\nFavourite colour: blue"}), 2)
    assert any("unparsed line" in warning for warning in row.warnings)
    assert row.needs_attention


def test_a_bad_iban_blocks_the_row() -> None:
    row = build_row(
        raw(**{"BANK ACCOUNTS": UK_BLOB + "\nIBAN: GB82WEST123456987654"}), 2
    )
    assert not row.importable
    assert any("22 characters" in error for error in row.errors)


def test_a_wise_transposition_is_flagged_not_overwritten() -> None:
    # R-SEED-07: the GB IBAN carries its own sort code and account number, and
    # a mismatch with the stated fields is a flag for a human.
    blob = (
        "Name: Jane Smith\nBank: Wise\nSort code: 23-08-01\n"
        "Account number: 12345678\nIBAN: GB82 WEST 1234 5698 7654 32"
    )
    row = build_row(raw(**{"BANK ACCOUNTS": blob}), 2)
    assert row.bank.sort_code == "230801"  # not overwritten by the IBAN's 123456
    assert any("does not match" in warning for warning in row.warnings)


def test_missing_email_blocks_the_row() -> None:
    assert not build_row(raw(EMAIL=""), 2).importable


def test_duplicate_emails_are_blocked_not_merged() -> None:
    # R-SEED-12 matches on email, so two rows for one person would silently
    # collapse into one.
    rows = build_rows([raw(), raw(NAME="Jane S.")])
    assert rows[0].importable
    assert not rows[1].importable
    assert any("duplicate email" in error for error in rows[1].errors)


def test_rows_are_numbered_from_the_spreadsheet_row_not_the_index() -> None:
    rows = build_rows([raw(), raw(EMAIL="other@example.com")])
    assert [row.source_row for row in rows] == [2, 3]


def test_unknown_notes_blocks_the_row() -> None:
    row = build_row(raw(NOTES="GBP"), 2)
    assert not row.importable
