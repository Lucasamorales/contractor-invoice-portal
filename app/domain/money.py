"""Money rules.

Money is always an ``int`` number of pence and the currency is always GBP
(CLAUDE.md §2.4). Floats are never used: a float cannot represent 0.1 exactly,
and an invoice that is a penny out is an invoice Robin has to chase.
"""

from __future__ import annotations

CURRENCY = "GBP"


class MoneyError(ValueError):
    """A money value violated an invariant."""


def format_gbp(pence: int) -> str:
    """Render pence as ``£1,234.56`` (R-PDF-12).

    This is the single formatting helper referred to by CLAUDE.md §6. Formatting
    happens only at the template boundary; everything upstream stays in pence.

    Negative amounts render as ``-£1.00`` rather than ``£-1.00`` so a credit
    reads correctly.
    """
    if not isinstance(pence, int) or isinstance(pence, bool):
        raise MoneyError(f"money must be int pence, got {type(pence).__name__}")
    sign = "-" if pence < 0 else ""
    whole, part = divmod(abs(pence), 100)
    return f"{sign}£{whole:,}.{part:02d}"


def parse_gbp_to_pence(text: str) -> int:
    """Parse a user-typed amount such as ``34.50``, ``£34.50`` or ``1,234`` .

    Rejects anything with more than two decimal places rather than rounding it:
    if someone types ``10.005`` we do not get to decide which penny they meant.
    """
    cleaned = text.strip().replace("£", "").replace(",", "").replace(" ", "")
    if not cleaned:
        raise MoneyError("amount is empty")
    negative = cleaned.startswith("-")
    if negative:
        cleaned = cleaned[1:]
    if "." in cleaned:
        whole, _, frac = cleaned.partition(".")
        if len(frac) > 2:
            raise MoneyError(f"more than two decimal places: {text!r}")
        frac = frac.ljust(2, "0")
    else:
        whole, frac = cleaned, "00"
    if not whole.isdigit() or not frac.isdigit():
        raise MoneyError(f"not a valid amount: {text!r}")
    pence = int(whole) * 100 + int(frac)
    return -pence if negative else pence


def invoice_total(remuneration_pence: int, additional_pence: int) -> int:
    """Total of an invoice (R-DATA-06 ``CHECK`` constraint, R-PDF-08).

    Kept here so the application computes the total the same way the database
    constraint verifies it.
    """
    for value in (remuneration_pence, additional_pence):
        if not isinstance(value, int) or isinstance(value, bool):
            raise MoneyError("money must be int pence")
    if remuneration_pence < 0 or additional_pence < 0:
        raise MoneyError("invoice components may not be negative")
    return remuneration_pence + additional_pence
