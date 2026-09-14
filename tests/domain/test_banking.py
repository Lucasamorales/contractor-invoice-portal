"""R-TEST-01: the bank key-normalisation map with every variant from R-SEED-04,
plus sort-code and account-number normalisation and region validation."""

from __future__ import annotations

import pytest

from app.domain.banking import (
    KEY_MAP,
    BankingError,
    PayoutRegion,
    collapse,
    normalise_account_number,
    normalise_sort_code,
    parse_bank_blob,
    region_from_notes,
    validate_bank,
)


@pytest.mark.parametrize(
    ("label", "expected_field"),
    [
        ("Name", "account_name"),
        ("Bank", "bank_name"),
        ("Bank name", "bank_name"),
        ("BANK NAME", "bank_name"),
        ("Sort code", "sort_code"),
        ("sortcode", "sort_code"),
        ("Bank branch", "sort_code"),
        ("Branch number", "sort_code"),
        ("Branch code", "sort_code"),
        ("Account number", "account_number"),
        ("Account No", "account_number"),
        ("IBAN", "iban"),
        ("Swift/BIC", "swift_bic"),
        ("swift", "swift_bic"),
        ("bic", "swift_bic"),
        ("  Sort   Code  ", "sort_code"),  # whitespace collapsed (R-SEED-04)
    ],
)
def test_every_key_variant_maps(label: str, expected_field: str) -> None:
    parsed = parse_bank_blob(f"{label}: 230801")
    assert getattr(parsed, expected_field) is not None
    assert parsed.unparsed == []


def test_key_map_has_no_unreachable_entries() -> None:
    # Every key in the map must survive normalisation, or it can never match.
    for key in KEY_MAP:
        assert collapse(key).lower() == key


def test_captions_from_the_template_are_ignored_not_flagged() -> None:
    blob = "\n".join(
        [
            "Name: Jane Smith",
            "Bank: Monzo",
            "Sort code: 23-08-01",
            "Account number: 12345678",
            "Use when sending money from the UK",
            "Use when sending money from outside the UK",
        ]
    )
    parsed = parse_bank_blob(blob)
    assert parsed.unparsed == []
    assert parsed.sort_code == "230801"


def test_unknown_line_is_reported_never_dropped() -> None:
    # R-SEED-04: neither a known key nor an ignored caption -> flagged.
    parsed = parse_bank_blob("Name: Jane Smith\nFavourite colour: blue\nsomething else")
    assert parsed.unparsed == ["Favourite colour: blue", "something else"]


def test_trailing_whitespace_padding_is_stripped() -> None:
    # The real file has a name padded with roughly seventy trailing spaces.
    parsed = parse_bank_blob("Name: Jane Smith" + " " * 70)
    assert parsed.account_name == "Jane Smith"


def test_collapse_handles_internal_runs() -> None:
    assert collapse("  Jane   \t Smith  ") == "Jane Smith"


@pytest.mark.parametrize("raw", ["230801", "23-08-01", "23 08 01", " 23/08/01 "])
def test_sort_code_normalises_to_six_bare_digits(raw: str) -> None:
    assert normalise_sort_code(raw) == "230801"


@pytest.mark.parametrize("raw", ["2308", "2308011", "", "abc"])
def test_sort_code_rejects_wrong_length(raw: str) -> None:
    with pytest.raises(BankingError):
        normalise_sort_code(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("12345678", "12345678"), ("1234 5678", "12345678"), ("12-34-56-78", "12345678")],
)
def test_account_number_normalisation(raw: str, expected: str) -> None:
    assert normalise_account_number(raw) == expected


def test_bad_sort_code_is_warned_and_flagged_not_silently_kept() -> None:
    parsed = parse_bank_blob("Sort code: 2308")
    assert parsed.sort_code is None
    assert parsed.warnings
    assert parsed.unparsed


def test_conflicting_duplicate_keys_keep_the_first_and_warn() -> None:
    parsed = parse_bank_blob("Sort code: 230801\nBank branch: 405678")
    assert parsed.sort_code == "230801"
    assert any("twice" in warning for warning in parsed.warnings)


def test_identical_duplicate_keys_do_not_warn() -> None:
    parsed = parse_bank_blob("Sort code: 230801\nBank branch: 23-08-01")
    assert parsed.sort_code == "230801"
    assert not any("twice" in warning for warning in parsed.warnings)


@pytest.mark.parametrize(
    ("notes", "expected"),
    [("UK", PayoutRegion.UK), ("uk", PayoutRegion.UK), ("EUR", PayoutRegion.INTL)],
)
def test_notes_maps_to_payout_region(notes: str, expected: PayoutRegion) -> None:
    # R-SEED-03: NOTES is a payment route, not a currency.
    assert region_from_notes(notes) is expected


@pytest.mark.parametrize("notes", ["", None, "GBP", "Europe"])
def test_unrecognised_notes_is_an_error_not_a_default(notes: str | None) -> None:
    with pytest.raises(BankingError):
        region_from_notes(notes)


def test_uk_row_requires_sort_code_and_account_number() -> None:
    parsed = parse_bank_blob("Name: Jane Smith\nBank: Monzo")
    errors = validate_bank(parsed, PayoutRegion.UK)
    assert any("sort code" in error for error in errors)
    assert any("account number" in error for error in errors)


def test_uk_row_rejects_a_seven_digit_account_number() -> None:
    parsed = parse_bank_blob(
        "Name: Jane Smith\nBank: Monzo\nSort code: 230801\nAccount number: 1234567"
    )
    errors = validate_bank(parsed, PayoutRegion.UK)
    assert any("eight digits" in error for error in errors)


def test_complete_uk_row_validates() -> None:
    parsed = parse_bank_blob(
        "Name: Jane Smith\nBank: Monzo\nSort code: 23-08-01\nAccount number: 12345678"
    )
    assert validate_bank(parsed, PayoutRegion.UK) == []


def test_intl_row_without_a_sort_code_is_valid() -> None:
    # R-SEED-06: Jamie is INTL with a Luxembourg IBAN and no sort code.
    parsed = parse_bank_blob(
        "Name: Bridge Building\nBank: Wise\n"
        "IBAN: LU28 0019 4006 4475 0000\nSwift/BIC: TRWIBEB1XXX"
    )
    assert parsed.sort_code is None
    assert validate_bank(parsed, PayoutRegion.INTL) == []


def test_intl_row_requires_iban_and_bic() -> None:
    parsed = parse_bank_blob("Name: Bridge Building\nBank: Wise")
    errors = validate_bank(parsed, PayoutRegion.INTL)
    assert any("IBAN" in error for error in errors)
    assert any("Swift/BIC" in error for error in errors)


def test_iban_is_uppercased_and_despaced() -> None:
    parsed = parse_bank_blob("IBAN: lu28 0019 4006 4475 0000")
    assert parsed.iban == "LU280019400644750000"


def test_missing_account_name_warns_but_does_not_block() -> None:
    parsed = parse_bank_blob("Bank: Monzo\nSort code: 230801\nAccount number: 12345678")
    errors = validate_bank(parsed, PayoutRegion.UK)
    assert errors == []
    assert any("account holder name" in warning for warning in parsed.warnings)


def test_masked_output_never_carries_a_full_number() -> None:
    parsed = parse_bank_blob(
        "Name: Jane Smith\nSort code: 230801\nAccount number: 12345678\n"
        "IBAN: GB82 WEST 1234 5698 7654 32"
    )
    masked = parsed.masked()
    assert masked["account_number"] == "••••5678"
    assert masked["iban"] == "••••5432"
    # The sort code is not a payment instruction on its own and prints in full
    # on every invoice, so it is not masked here.
    assert masked["sort_code"] == "230801"
