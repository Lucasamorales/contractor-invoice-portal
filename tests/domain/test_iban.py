"""R-TEST-01: IBAN mod-97, including known-bad inputs and the GB cross-check."""

from __future__ import annotations

import pytest

from modules.invoices.domain.iban import (
    mask,
    mod97,
    normalise_iban,
    validate_bic,
    validate_iban,
)

GB_VALID = "GB82WEST12345698765432"
LU_VALID = "LU280019400644750000"
DE_VALID = "DE89370400440532013000"


@pytest.mark.parametrize("iban", [GB_VALID, LU_VALID, DE_VALID])
def test_known_good_ibans(iban: str) -> None:
    assert mod97(iban) == 1
    assert validate_iban(iban).valid is True


def test_normalisation_strips_spaces_and_uppercases() -> None:
    assert normalise_iban("gb82 west 1234 5698 7654 32") == GB_VALID
    assert validate_iban("gb82 west 1234 5698 7654 32").valid is True


def test_transposed_digits_fail_the_checksum() -> None:
    # Swapping two digits is exactly what mod-97 exists to catch.
    bad = "GB82WEST12345698765423"
    assert mod97(bad) != 1
    check = validate_iban(bad)
    assert check.valid is False
    assert (
        "That IBAN did not pass its check. Usually one character is out."
        in check.errors
    )


def test_short_gb_iban_is_rejected() -> None:
    # R-SEED-10: a previous version of the real file held a 20-character GB
    # IBAN. It must be an error, never a shrug.
    check = validate_iban("GB82WEST123456987654")
    assert check.valid is False
    assert any("22 characters" in error for error in check.errors)


@pytest.mark.parametrize("iban", ["", "GB", "1234", "GBXX WEST 1234", "!!"])
def test_malformed_ibans_are_rejected(iban: str) -> None:
    assert validate_iban(iban).valid is False


def test_unknown_country_warns_but_does_not_invent_a_length() -> None:
    check = validate_iban("ZZ00ABCD1234567890")
    assert any("unknown IBAN country" in warning for warning in check.warnings)


def test_gb_cross_check_extracts_sort_code_and_account() -> None:
    check = validate_iban(GB_VALID, sort_code="123456", account_number="98765432")
    assert check.gb_sort_code == "123456"
    assert check.gb_account_number == "98765432"
    assert check.warnings == ()


def test_gb_cross_check_flags_a_mismatch_without_overwriting() -> None:
    # R-SEED-07: a mismatch is a flag, not a silent overwrite.
    check = validate_iban(GB_VALID, sort_code="230801", account_number="98765432")
    assert check.valid is True
    assert any("does not match" in warning for warning in check.warnings)
    assert check.gb_sort_code == "123456"


def test_cross_check_warning_never_prints_a_full_number() -> None:
    # CLAUDE.md §2.8: last four digits only, anywhere a human might read it.
    check = validate_iban(GB_VALID, account_number="11112222")
    joined = " ".join(check.warnings)
    assert "11112222" not in joined
    assert "2222" in joined


@pytest.mark.parametrize("bic", ["REVOGB21", "TRWIBEB1XXX", "MIDLGB22XXX"])
def test_valid_bics(bic: str) -> None:
    assert validate_bic(bic) is True


@pytest.mark.parametrize("bic", ["REVO", "1234GB21", "REVOGB2", "REVOGB21XX"])
def test_invalid_bics(bic: str) -> None:
    assert validate_bic(bic) is False


def test_mask_shows_last_four_only() -> None:
    assert mask("12345678") == "••••5678"
    assert mask("123") == "••••"
    assert mask("") == ""
