"""Invoice numbering rules (R-NUM-01 .. R-NUM-06).

Numbers are per person and continue the sequence each contractor was already
using before this system existed, so their own records stay contiguous. Two
people holding the same number is expected and correct.

Allocation itself is a database concern (R-NUM-02 locks the counter row); this
module holds only the rules that can be stated without a database.
"""

from __future__ import annotations


class NumberingError(ValueError):
    """An invoice number or counter value violated a rule."""


def seed_next_number(last_issued: int | None) -> int:
    """The counter value to seed from the spreadsheet's INVOICE N° (R-SEED-09).

    The column holds the *last* number a person issued, so the next one is that
    plus one. Gabriel's ``0`` means he has never invoiced, and his first invoice
    is ``1``.
    """
    if last_issued is None:
        return 1
    if not isinstance(last_issued, int) or isinstance(last_issued, bool):
        raise NumberingError("last issued number must be an int")
    if last_issued < 0:
        raise NumberingError(f"last issued number may not be negative: {last_issued}")
    return last_issued + 1


def format_number(number: int) -> str:
    """Print plain, with no zero padding (R-NUM-04): ``8``, ``34``, ``213``."""
    if not isinstance(number, int) or isinstance(number, bool):
        raise NumberingError("invoice number must be an int")
    if number < 1:
        raise NumberingError(f"invoice number must be positive: {number}")
    return str(number)


def advance(current_next: int) -> int:
    """The counter value after allocating ``current_next``.

    A voided invoice does not release its number (R-NUM-05): the counter only
    ever moves forward, so a reissue takes a fresh one and the gap stays.
    """
    if current_next < 1:
        raise NumberingError(f"counter must be positive: {current_next}")
    return current_next + 1
