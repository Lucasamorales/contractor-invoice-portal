"""Bank-detail parsing, normalisation and validation.

Covers R-SEED-03 (payout region), R-SEED-04 (the key map), R-SEED-05
(normalisation) and R-SEED-06 (validation by region). The same rules are
applied to details typed into the wizard (R-BANK-04), so the spreadsheet and a
person's own correction are held to one standard.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from app.domain.iban import mask, validate_bic, validate_iban


class PayoutRegion(Enum):
    """How Robin pays someone. Not a currency: everything is GBP (R-INV-03)."""

    UK = "UK"
    INTL = "INTL"


# NOTES in the spreadsheet is a payment route, not a currency (R-SEED-03).
_NOTES_TO_REGION = {"UK": PayoutRegion.UK, "EUR": PayoutRegion.INTL}

# Normalised blob key -> field. Every variant listed in R-SEED-04, including the
# several labels the real file uses for a sort code.
KEY_MAP: dict[str, str] = {
    "name": "account_name",
    "bank": "bank_name",
    "bank name": "bank_name",
    "sort code": "sort_code",
    "sortcode": "sort_code",
    "bank branch": "sort_code",
    "branch number": "sort_code",
    "branch code": "sort_code",
    "account number": "account_number",
    "account no": "account_number",
    "iban": "iban",
    "swift/bic": "swift_bic",
    "swift": "swift_bic",
    "bic": "swift_bic",
}

# Captions carried over from Robin's template, not data (R-SEED-04).
_IGNORED_CAPTION = "use when sending"

BANK_FIELDS = (
    "account_name",
    "bank_name",
    "sort_code",
    "account_number",
    "iban",
    "swift_bic",
)


class BankingError(ValueError):
    """A value could not be interpreted as a bank field."""


@dataclass(slots=True)
class ParsedBank:
    """One person's bank block, as parsed from the free-text blob.

    Unparsed lines are never dropped: R-SEED-04 requires them to be reported
    and the row flagged.
    """

    account_name: str | None = None
    bank_name: str | None = None
    sort_code: str | None = None
    account_number: str | None = None
    iban: str | None = None
    swift_bic: str | None = None
    unparsed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, str | None]:
        return {name: getattr(self, name) for name in BANK_FIELDS}

    def masked(self) -> dict[str, str | None]:
        """The safe-to-print form: account number and IBAN to last four."""
        out = self.as_dict()
        for sensitive in ("account_number", "iban"):
            value = out[sensitive]
            if value:
                out[sensitive] = mask(value)
        return out


def region_from_notes(notes: str | None) -> PayoutRegion:
    """Map the spreadsheet NOTES column to a payout region (R-SEED-03)."""
    key = (notes or "").strip().upper()
    region = _NOTES_TO_REGION.get(key)
    if region is None:
        raise BankingError(f"unrecognised NOTES value for payout region: {notes!r}")
    return region


def collapse(value: str) -> str:
    """Strip surrounding whitespace and collapse internal runs (R-SEED-05).

    The real file has a name padded with roughly seventy trailing spaces.
    """
    return re.sub(r"\s+", " ", value).strip()


def normalise_key(raw: str) -> str:
    """Lower-case, whitespace-collapsed key for the R-SEED-04 lookup."""
    return collapse(raw).lower()


def normalise_sort_code(raw: str) -> str:
    """Six digits, no separators. The file holds both 230801 and 23-08-01."""
    digits = re.sub(r"[^0-9]", "", raw)
    if len(digits) != 6:
        raise BankingError(f"sort code must be six digits, got {len(digits)}")
    return digits


def normalise_account_number(raw: str) -> str:
    """Digits only. UK account numbers are eight digits (R-SEED-06)."""
    return re.sub(r"[^0-9]", "", raw)


def normalise_upper_nospace(raw: str) -> str:
    """Uppercase with all whitespace removed. Used for IBAN and BIC."""
    return re.sub(r"\s+", "", raw).upper()


def parse_bank_blob(blob: str) -> ParsedBank:
    """Parse the free-text BANK ACCOUNTS cell into fields (R-SEED-04/05).

    One "Key: Value" per line. Unknown lines that are not template captions are
    collected in ``unparsed`` so the seed report can flag the row.
    """
    parsed = ParsedBank()
    for raw_line in (blob or "").splitlines():
        line = collapse(raw_line)
        if not line:
            continue
        if _IGNORED_CAPTION in line.lower():
            continue
        key_part, separator, value_part = line.partition(":")
        if not separator:
            parsed.unparsed.append(line)
            continue
        field_name = KEY_MAP.get(normalise_key(key_part))
        value = collapse(value_part)
        if field_name is None:
            parsed.unparsed.append(line)
            continue
        if not value:
            parsed.warnings.append(f"{field_name} was present but empty")
            continue
        _assign(parsed, field_name, value)
    return parsed


def _assign(parsed: ParsedBank, field_name: str, value: str) -> None:
    """Normalise one value onto the record, recording a warning if it is odd."""
    try:
        if field_name == "sort_code":
            value = normalise_sort_code(value)
        elif field_name == "account_number":
            value = normalise_account_number(value)
        elif field_name in ("iban", "swift_bic"):
            value = normalise_upper_nospace(value)
    except BankingError as exc:
        parsed.warnings.append(str(exc))
        parsed.unparsed.append(f"{field_name}: {value}")
        return

    existing = getattr(parsed, field_name)
    if existing is not None and existing != value:
        # Two lines claimed the same field with different values. Keep the first
        # and flag it; picking a winner silently is how the wrong account is paid.
        parsed.warnings.append(
            f"{field_name} given twice with different values; kept the first"
        )
        return
    setattr(parsed, field_name, value)


def validate_bank(parsed: ParsedBank, region: PayoutRegion) -> list[str]:
    """Region-specific completeness and correctness checks (R-SEED-06/07).

    Returns the list of blocking errors. Softer observations go onto
    ``parsed.warnings``: a missing sort code is an error for a UK payee and
    perfectly normal for an international one.
    """
    errors: list[str] = []

    if not parsed.account_name:
        parsed.warnings.append("no account holder name given")
    if not parsed.bank_name:
        parsed.warnings.append("no bank name given")

    if region is PayoutRegion.UK:
        if not parsed.sort_code:
            errors.append("UK payee requires a sort code")
        elif len(parsed.sort_code) != 6:
            errors.append("sort code must be six digits")
        if not parsed.account_number:
            errors.append("UK payee requires an account number")
        elif len(parsed.account_number) != 8:
            errors.append(
                "UK account number must be eight digits, got "
                f"{len(parsed.account_number)}"
            )
    else:
        if not parsed.iban:
            errors.append("international payee requires an IBAN")
        if not parsed.swift_bic:
            errors.append("international payee requires a SWIFT/BIC")

    if parsed.iban:
        check = validate_iban(
            parsed.iban,
            sort_code=parsed.sort_code,
            account_number=parsed.account_number,
        )
        errors.extend(check.errors)
        parsed.warnings.extend(check.warnings)

    if parsed.swift_bic and not validate_bic(parsed.swift_bic):
        errors.append("swift/bic is not a valid 8 or 11 character BIC")

    return errors


def parse_bank_fields(
    *,
    account_name: str = "",
    bank_name: str = "",
    sort_code: str = "",
    account_number: str = "",
    iban: str = "",
    swift_bic: str = "",
) -> ParsedBank:
    """Normalise individually typed fields (R-BANK-04).

    The wizard collects one field per input rather than a pasted blob, but the
    normalisation has to be identical to the seed's: the same sort code typed
    into a form and imported from a spreadsheet must end up as the same six
    digits, or a person confirming their details would appear to be changing
    them.
    """
    parsed = ParsedBank()
    for field_name, raw in (
        ("account_name", account_name),
        ("bank_name", bank_name),
        ("sort_code", sort_code),
        ("account_number", account_number),
        ("iban", iban),
        ("swift_bic", swift_bic),
    ):
        value = collapse(raw or "")
        if value:
            _assign(parsed, field_name, value)
    return parsed


def same_account(left: ParsedBank, right: ParsedBank) -> bool:
    """Whether two records point at the same account.

    Used to tell a real change from a re-typed confirmation: only the fields
    that identify where money lands count, not the display names around them.
    """
    keys = ("sort_code", "account_number", "iban", "swift_bic")
    return all(getattr(left, key) == getattr(right, key) for key in keys)
