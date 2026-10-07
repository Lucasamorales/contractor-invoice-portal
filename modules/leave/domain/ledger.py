"""The balance, as a ledger nobody edits (R-LEAVE-10 .. R-LEAVE-14).

A balance is not a number somebody types over. It is the sum of entries, each of
which is only ever added. Booking leave appends negative entries; cancelling it
appends the same days back as positive ones. Nothing is updated or deleted, so
the balance cannot drift from what happened and "why is my number wrong" has an
answer in the entries.

The invoice side of this system learned that the hard way: voided invoices once
kept their kudos and their numbers because releasing them was a step somebody
had to remember. Here the release is the cancellation entry, and a balance that
ignores it is not computing a balance.

All amounts are half-days (see days.py). Signs are fixed by the kind so a
malformed entry cannot exist.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date
from enum import Enum

from modules.leave.domain.days import MON_FRI, Span, half_days_by_date
from modules.leave.domain.errors import LeaveError
from modules.leave.domain.years import leave_year_of


class Kind(Enum):
    GRANT = "grant"  # the year's allowance, or part of it. Positive.
    ADJUSTMENT = "adjustment"  # a carry-over exception or a correction. Either sign.
    BOOKING = "booking"  # approved leave on a date. Negative.
    CANCELLATION = "cancellation"  # the days from a booking coming back. Positive.


@dataclass(frozen=True, slots=True)
class Entry:
    """One line of the ledger.

    ``on`` is the day it takes effect: the leave day for a booking or a
    cancellation, the day an allowance or adjustment starts to count for a
    grant. Its leave year decides which year's balance the entry belongs to,
    which is how a request over the year end is charged to two years without
    any special case.

    ``ref`` links a cancellation to the request it reverses.
    """

    kind: Kind
    on: date
    half_days: int
    ref: str = ""

    def __post_init__(self) -> None:
        if self.half_days == 0:
            raise LeaveError("a ledger entry of nothing records nothing")
        if self.kind in (Kind.GRANT, Kind.CANCELLATION) and self.half_days < 0:
            raise LeaveError(f"a {self.kind.value} adds days, so it cannot be negative")
        if self.kind is Kind.BOOKING and self.half_days > 0:
            raise LeaveError("a booking spends days, so it cannot be positive")


@dataclass(frozen=True, slots=True)
class Balance:
    """What the brief's employee screen says: allowance, used, booked, remaining."""

    year: int
    allowance: int
    used: int
    booked: int

    @property
    def remaining(self) -> int:
        return self.allowance - self.used - self.booked


def balance(entries: Iterable[Entry], *, year: int, today: date) -> Balance:
    """One person's balance for one leave year, as at ``today``.

    ``today`` is a parameter and never read from the clock: the answer to "what
    was my balance on the 3rd" has to be the same next month, and a test that
    reads the real date fails on the one day of the year it was not written for.

    **Used** is leave on or before ``today``; **booked** is leave after it.
    Today counts as used, because somebody on leave today is already off. The
    split only moves days between those two columns; ``remaining`` is the same
    either way, and equals the plain sum of the year's entries.

    Entries from other years are ignored, which is where the leave-year
    boundary is enforced. Carry-over is not applied here: there is none by
    default (policy §4.8), and an exception is an ADJUSTMENT somebody added on
    purpose (policy §4.9).
    """
    allowance = used = booked = 0
    for entry in entries:
        if leave_year_of(entry.on) != year:
            continue
        if entry.kind in (Kind.GRANT, Kind.ADJUSTMENT):
            allowance += entry.half_days
        elif entry.on <= today:
            used -= entry.half_days
        else:
            booked -= entry.half_days
    return Balance(year=year, allowance=allowance, used=used, booked=booked)


def booking_entries(
    ref: str,
    span: Span,
    *,
    pattern: frozenset[int] = MON_FRI,
    holidays: Collection[date] = (),
) -> list[Entry]:
    """The ledger entries that approving a request creates: one per charged date."""
    return [
        Entry(Kind.BOOKING, day, -cost, ref)
        for day, cost in sorted(
            half_days_by_date(span, pattern=pattern, holidays=holidays).items()
        )
    ]


def cancellation_entries(
    bookings: Iterable[Entry], *, from_date: date | None = None
) -> list[Entry]:
    """The entries that give a booking's days back.

    With ``from_date`` only the days on or after it are returned, which is how
    somebody who took Monday and Tuesday and cancels the rest of the week gets
    Wednesday to Friday back without being refunded the days they took. Left
    out, the whole booking is reversed. Whether past days may be refunded at all
    is a rule for the layer above (BACKLOG L-37), not an arithmetic question.
    """
    reversals: list[Entry] = []
    for booking in bookings:
        if booking.kind is not Kind.BOOKING:
            raise LeaveError("only a booking can be cancelled")
        if from_date is not None and booking.on < from_date:
            continue
        reversals.append(
            Entry(Kind.CANCELLATION, booking.on, -booking.half_days, booking.ref)
        )
    return reversals
