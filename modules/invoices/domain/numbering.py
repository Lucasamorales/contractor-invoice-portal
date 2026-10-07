"""Invoice numbering rules (R-NUM-01 .. R-NUM-06).

Numbers are per person and continue the sequence each contractor was already
using before this system existed, so their own records stay contiguous. Two
people holding the same number is expected and correct.

Allocation itself is a database concern (R-NUM-02 locks the counter row); this
module holds only the rules that can be stated without a database.
"""

from __future__ import annotations

from collections.abc import Iterable


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
    """The counter value after allocating ``current_next``."""
    if current_next < 1:
        raise NumberingError(f"counter must be positive: {current_next}")
    return current_next + 1


def next_number(floor: int, taken: Iterable[int]) -> int:
    """The number the next invoice takes (R-NUM-05).

    **The lowest number no live invoice holds, never below where their
    sequence started.** ``taken`` is the numbers their live invoices hold — a
    voided invoice is not live, so its number comes back.

    This is the whole of the reissue rule. Void invoice 10 and the next one is
    10 again, which is what somebody correcting a mistake means by "send it
    again": one invoice 10 exists, the earlier attempt is cancelled, and their
    own books run unbroken. A permanent gap has to be explained to an
    accountant every time — and the sequence belongs to the contractor,
    continuing the one they kept before this system existed (R-NUM-01).

    ``floor`` is what stops it walking backwards into history: the numbers
    before it were issued out of a Word template and are not in this database.
    """
    if floor < 1:
        raise NumberingError(f"floor must be positive: {floor}")
    held = {n for n in taken if n >= floor}
    number = floor
    while number in held:
        number += 1
    return number


def released_floor(floor: int, voided: int, freed: Iterable[int]) -> int:
    """Where the counter sits after an invoice is voided (R-NUM-05).

    Down to the voided number — and further, if an earlier void left a number
    free that nothing live has taken since. Voiding 35 when 34 is already void
    gives back both, in order, so somebody with two cancelled invoices does not
    fill the second gap and keep the first.
    """
    if floor < 1:
        raise NumberingError(f"floor must be positive: {floor}")
    if voided < 1:
        raise NumberingError(f"voided number must be positive: {voided}")
    return min([floor, voided, *(n for n in freed if n >= 1)])
