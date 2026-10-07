"""Turning one spreadsheet row into a proposed set of records (R-SEED-*).

Pure: the caller reads the workbook and writes the database, this decides what
the row means. Nothing here treats a value as true because it looked tidy —
every imported bank row lands ``unverified`` (R-SEED-10).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from modules.invoices.domain.banking import (
    BankingError,
    ParsedBank,
    PayoutRegion,
    collapse,
    parse_bank_blob,
    region_from_notes,
    validate_bank,
)
from modules.invoices.domain.numbering import NumberingError, seed_next_number

# The spreadsheet's own column headings, in the order they appear.
COLUMNS = (
    "NAME",
    "TEAM",
    "EMAIL",
    "REMUNERATION",
    "NOTES",
    "BANK ACCOUNTS",
    "INVOICE N°",
)


@dataclass(slots=True)
class SeedRow:
    """One person as the spreadsheet describes them, plus what is wrong with it.

    ``errors`` are things that stop the row being imported. ``warnings`` are
    things a human has to look at (R-SEED-13) but that do not block: the whole
    point of the report is that Robin sees them before go-live.
    """

    source_row: int
    display_name: str = ""
    legal_name: str | None = None
    email: str = ""
    team: str | None = None
    payout_region: PayoutRegion | None = None
    bank: ParsedBank = field(default_factory=ParsedBank)
    next_number: int | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def importable(self) -> bool:
        return not self.errors

    @property
    def needs_attention(self) -> bool:
        """Counted at the end of the report (R-SEED-13)."""
        return bool(self.errors or self.warnings or self.bank.unparsed)


def parse_invoice_number(raw: object) -> int:
    """Read the ``INVOICE N°`` cell.

    The column is formatted as a number, so openpyxl hands back ``213.0``. A
    value with a real fractional part is an error, not something to round.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise NumberingError("INVOICE N° is empty")
    if isinstance(raw, bool):
        raise NumberingError("INVOICE N° is not a number")
    if isinstance(raw, str):
        raw = raw.strip()
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise NumberingError(f"INVOICE N° is not a number: {raw!r}") from exc
    if value != int(value):
        raise NumberingError(f"INVOICE N° is not a whole number: {raw!r}")
    return int(value)


def names_agree(display_name: str, legal_name: str | None) -> bool:
    """Whether the two names are close enough not to flag (R-SEED-08).

    Deliberately strict: only an exact match after collapsing whitespace and
    case counts. "Gabriel Marlow (SIMON)" paying into "Simon Andrew
    Robinson" is legitimate, but Robin should be the one to say so.
    """
    if not legal_name:
        return False
    return collapse(display_name).casefold() == collapse(legal_name).casefold()


def build_row(raw: dict[str, object], source_row: int) -> SeedRow:
    """Interpret one spreadsheet row.

    ``REMUNERATION`` is read and discarded: every value in the file is the
    placeholder 1000.0, and fees are Robin's to type (R-SEED-02, R-ADM-03).
    """
    row = SeedRow(source_row=source_row)

    row.display_name = collapse(str(raw.get("NAME") or ""))
    if not row.display_name:
        row.errors.append("NAME is empty")

    email = collapse(str(raw.get("EMAIL") or "")).lower()
    if not email or "@" not in email:
        row.errors.append("EMAIL is missing or not an address")
    row.email = email

    team = collapse(str(raw.get("TEAM") or ""))
    row.team = team or None

    try:
        row.payout_region = region_from_notes(str(raw.get("NOTES") or ""))
    except BankingError as exc:
        row.errors.append(str(exc))

    try:
        row.next_number = seed_next_number(parse_invoice_number(raw.get("INVOICE N°")))
    except NumberingError as exc:
        row.errors.append(str(exc))

    row.bank = parse_bank_blob(str(raw.get("BANK ACCOUNTS") or ""))
    row.legal_name = row.bank.account_name

    if row.payout_region is not None:
        row.errors.extend(validate_bank(row.bank, row.payout_region))

    if row.legal_name is None:
        row.warnings.append(
            "no Name: line in the bank block, so there is no legal name for the "
            "invoice letterhead"
        )
    elif not names_agree(row.display_name, row.legal_name):
        row.warnings.append(
            f"the spreadsheet calls them {row.display_name!r} but the account is "
            f"in the name {row.legal_name!r}; confirm this is the intended payee"
        )

    for line in row.bank.unparsed:
        row.warnings.append(f"unparsed line in the bank block: {line!r}")
    row.warnings.extend(row.bank.warnings)

    return row


def build_rows(raw_rows: list[dict[str, object]]) -> list[SeedRow]:
    """Interpret every row. The header is row 1, so data starts at row 2."""
    rows = [build_row(raw, index) for index, raw in enumerate(raw_rows, start=2)]
    _flag_duplicate_emails(rows)
    return rows


def _flag_duplicate_emails(rows: list[SeedRow]) -> None:
    """Two rows for one person would silently merge on import (R-SEED-12)."""
    seen: dict[str, int] = {}
    for row in rows:
        if not row.email:
            continue
        first = seen.get(row.email)
        if first is not None:
            row.errors.append(f"duplicate email, already used by row {first}")
        else:
            seen[row.email] = row.source_row
