"""Invoice composition: lines, totals and the printed detail line.

Pure arithmetic over pence. The caller supplies the fee, the kudos snapshot and
the expenses; nothing here reads a database or a sheet.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.domain.money import MoneyError, format_gbp, invoice_total


class LineKind(Enum):
    """The ``invoice_lines.kind`` values (R-DATA-07)."""

    FEE = "fee"
    KUDOS = "kudos"
    EXPENSE = "expense"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class InvoiceLine:
    """One frozen breakdown line. Written at issue time and never changed."""

    kind: LineKind
    label: str
    qty: int
    unit_pence: int
    amount_pence: int


@dataclass(frozen=True, slots=True)
class InvoiceTotals:
    """What prints on the invoice, plus the breakdown behind it."""

    remuneration_pence: int
    additional_pence: int
    total_pence: int
    lines: tuple[InvoiceLine, ...]


def kudos_amount_pence(kudos_count: int, rate_pence: int) -> int:
    """Value of a kudos balance (R-KUD-04).

    The rate is passed in, never hardcoded: it is config, and it is frozen onto
    the snapshot so a future change cannot rewrite an old invoice (R-DATA-04).
    """
    if kudos_count < 0:
        raise MoneyError("kudos count may not be negative")
    if rate_pence <= 0:
        raise MoneyError("kudos rate must be positive")
    return kudos_count * rate_pence


def compose(
    *,
    fee_pence: int,
    kudos_count: int,
    kudos_rate_pence: int,
    expenses: list[tuple[str, int]] | None = None,
    other: list[tuple[str, int]] | None = None,
) -> InvoiceTotals:
    """Build the frozen breakdown and the two printed figures.

    ``Remuneration`` is the fee alone. ``Additional`` is everything else added
    together: kudos, expenses and any other item the person added (glossary).
    An empty ``Additional`` is valid and means zero (R-WIZ-03).
    """
    lines: list[InvoiceLine] = [
        InvoiceLine(LineKind.FEE, "Remuneration", 1, fee_pence, fee_pence)
    ]

    kudos_pence = 0
    if kudos_count > 0:
        kudos_pence = kudos_amount_pence(kudos_count, kudos_rate_pence)
        lines.append(
            InvoiceLine(
                LineKind.KUDOS, "Kudos", kudos_count, kudos_rate_pence, kudos_pence
            )
        )

    additional_pence = kudos_pence
    for kind, items in ((LineKind.EXPENSE, expenses), (LineKind.OTHER, other)):
        for label, amount_pence in items or []:
            if amount_pence < 0:
                raise MoneyError(f"{kind.value} amount may not be negative: {label}")
            lines.append(InvoiceLine(kind, label, 1, amount_pence, amount_pence))
            additional_pence += amount_pence

    return InvoiceTotals(
        remuneration_pence=fee_pence,
        additional_pence=additional_pence,
        total_pence=invoice_total(fee_pence, additional_pence),
        lines=tuple(lines),
    )


def format_gbp_compact(pence: int) -> str:
    """``£20`` for whole pounds, ``£34.50`` otherwise.

    Used only for the R-PDF-09 detail line, whose worked example in the spec is
    ``3 kudos × £20 · travel £34.50`` — mixing both forms in one line. Every
    other figure on the invoice uses ``format_gbp`` and always shows two
    decimals (R-PDF-12).
    """
    return format_gbp(pence).removesuffix(".00")


def detail_line(totals: InvoiceTotals) -> str:
    """The italic line printed beneath ``Additional`` (R-PDF-09).

    Empty when there is nothing additional, which is the signal to the template
    not to render the line at all.
    """
    if totals.additional_pence <= 0:
        return ""

    parts: list[str] = []
    for line in totals.lines:
        if line.kind is LineKind.KUDOS:
            noun = "kudo" if line.qty == 1 else "kudos"
            parts.append(f"{line.qty} {noun} × {format_gbp_compact(line.unit_pence)}")
        elif line.kind in (LineKind.EXPENSE, LineKind.OTHER):
            parts.append(f"{line.label} {format_gbp_compact(line.amount_pence)}")
    return " · ".join(parts)
