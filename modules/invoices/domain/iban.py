"""IBAN validation (R-SEED-07, R-BANK-04).

Shape, then length for the country, then the mod-97 checksum. For GB IBANs the
embedded sort code and account number are cross-checked against the separately
stated fields.

Most of the team bank with Wise, which prints the IBAN and the sort code /
account number side by side. A transposition in one of them shows up as a
mismatch here, which is why the cross-check exists at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Length of a valid IBAN per country, from the SWIFT IBAN registry. A country
# absent from this table is reported as unknown rather than assumed valid.
IBAN_LENGTHS: dict[str, int] = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16,
    "BG": 22, "BH": 22, "BR": 29, "BY": 28, "CH": 21, "CR": 22, "CY": 28,
    "CZ": 24, "DE": 22, "DK": 18, "DO": 28, "EE": 20, "EG": 29, "ES": 24,
    "FI": 18, "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23, "GL": 18,
    "GR": 27, "GT": 28, "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IQ": 23,
    "IS": 26, "IT": 27, "JO": 30, "KW": 30, "KZ": 20, "LB": 28, "LC": 32,
    "LI": 21, "LT": 20, "LU": 20, "LV": 21, "LY": 25, "MC": 27, "MD": 24,
    "ME": 22, "MK": 19, "MR": 27, "MT": 31, "MU": 30, "NL": 18, "NO": 15,
    "PK": 24, "PL": 28, "PS": 29, "PT": 25, "QA": 29, "RO": 24, "RS": 22,
    "SA": 24, "SC": 31, "SE": 24, "SI": 19, "SK": 24, "SM": 27, "ST": 25,
    "SV": 28, "TL": 23, "TN": 24, "TR": 26, "UA": 29, "VA": 22, "VG": 24,
    "XK": 20,
}  # fmt: skip

_IBAN_SHAPE = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{10,30}$")
_BIC_SHAPE = re.compile(r"^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$")


@dataclass(frozen=True, slots=True)
class IbanCheck:
    """The result of validating one IBAN. Warnings never block on their own."""

    iban: str
    valid: bool
    errors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    country: str = ""
    gb_sort_code: str | None = None
    gb_account_number: str | None = None


def normalise_iban(raw: str) -> str:
    """Uppercase and strip every space (R-SEED-05)."""
    return re.sub(r"\s+", "", raw).upper()


def mod97(iban: str) -> int:
    """The ISO 13616 checksum remainder. A valid IBAN gives 1.

    The first four characters move to the end, then every letter becomes its
    position in the alphabet plus nine (A=10 … Z=35), and the resulting decimal
    string is taken modulo 97.
    """
    rearranged = iban[4:] + iban[:4]
    digits = "".join(
        str(ord(char) - 55) if char.isalpha() else char for char in rearranged
    )
    return int(digits) % 97


def validate_iban(
    raw: str,
    *,
    sort_code: str | None = None,
    account_number: str | None = None,
) -> IbanCheck:
    """Validate an IBAN and, for GB, cross-check its embedded UK fields.

    ``sort_code`` and ``account_number`` are the separately stated values, in
    normalised form. A mismatch is a warning, not an error: it flags the row for
    a human (R-SEED-07) and never silently overwrites either value.
    """
    iban = normalise_iban(raw)
    errors: list[str] = []
    warnings: list[str] = []

    if not iban:
        return IbanCheck(iban="", valid=False, errors=("Enter your IBAN.",))

    if not _IBAN_SHAPE.match(iban):
        return IbanCheck(
            iban=iban,
            valid=False,
            errors=(
                "An IBAN starts with two letters, then two digits, then the rest. "
                "That one does not.",
            ),
        )

    country = iban[:2]
    expected = IBAN_LENGTHS.get(country)
    if expected is None:
        warnings.append(f"unknown IBAN country {country}; length not checked")
    elif len(iban) != expected:
        errors.append(
            f"A {country} IBAN is {expected} characters. That one has {len(iban)}."
        )

    if mod97(iban) != 1:
        errors.append("That IBAN did not pass its check. Usually one character is out.")

    gb_sort: str | None = None
    gb_account: str | None = None
    if country == "GB" and len(iban) == 22:
        gb_sort = iban[8:14]
        gb_account = iban[14:22]
        if not gb_sort.isdigit() or not gb_account.isdigit():
            errors.append(
                "That UK IBAN does not contain a sort code and account number. "
                "Check it against your statement."
            )
        else:
            if sort_code and sort_code != gb_sort:
                warnings.append(
                    f"The sort code {mask(sort_code)} does not match the one inside "
                    f"the IBAN, {mask(gb_sort)}."
                )
            if account_number and account_number != gb_account:
                warnings.append(
                    f"The account number {mask(account_number)} does not match the one "
                    f"inside the IBAN, {mask(gb_account)}."
                )

    return IbanCheck(
        iban=iban,
        valid=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
        country=country,
        gb_sort_code=gb_sort,
        gb_account_number=gb_account,
    )


def validate_bic(raw: str) -> bool:
    """SWIFT/BIC shape: 8 or 11 characters, first six alphabetic."""
    return bool(_BIC_SHAPE.match(re.sub(r"\s+", "", raw).upper()))


def mask(value: str) -> str:
    """Last four digits only. Never log or display more (CLAUDE.md §2.8)."""
    if not value:
        return ""
    tail = value[-4:]
    return f"••••{tail}" if len(value) > 4 else "••••"
