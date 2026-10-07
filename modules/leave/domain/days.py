"""Counting leave in half-days (R-LEAVE-02 .. R-LEAVE-05).

**Units.** Leave is counted in integer half-days: a whole day is 2, a morning or
an afternoon is 1. The brief makes half-day requests part of v1, so the unit is
not a guess, and integers keep it out of the float trap for the same reason
money is integer pence everywhere else in this system. 28 days of allowance is
56; "2.5 days" is 5. Nothing here ever holds a fraction of a half-day.

**A request is a span of half-day slots.** It runs from a date and a part of the
day to a date and a part of the day, so "Monday afternoon to Wednesday
lunchtime" is expressible and so is a single morning. Whole days are simply
morning-to-afternoon.

**What is charged.** Only half-days that fall on a day the person works and that
is not a public holiday. A weekend inside a request costs nothing, and neither
does a bank holiday: the entitlement already includes them (policy §3.2, §4.2),
so charging one again as booked leave would take it from the person twice.
Who works which days, and which days are public holidays, are *inputs*. The
policy does not say where either comes from (BACKLOG L-02, L-16), and a rule
that quietly assumed an answer would be wrong for somebody.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator
from dataclasses import dataclass
from datetime import date
from enum import IntEnum

from modules.leave.domain.errors import LeaveError


class Part(IntEnum):
    """Which half of a day. The value is the slot offset within the day."""

    AM = 0
    PM = 1


# Weekdays as ``date.weekday()`` numbers: Monday is 0. A full-time pattern.
MON_FRI: frozenset[int] = frozenset({0, 1, 2, 3, 4})

WHOLE_DAY = 2


def _slot(day: date, part: Part) -> int:
    """A number that orders half-days: two per calendar day, forever."""
    return day.toordinal() * 2 + int(part)


@dataclass(frozen=True, slots=True)
class Span:
    """A run of half-days, both ends inclusive.

    ``Span(Mon, AM, Wed, PM)`` is three whole days. ``Span(Mon, PM, Wed, AM)``
    is Monday afternoon, all of Tuesday and Wednesday morning.
    """

    start: date
    start_part: Part
    end: date
    end_part: Part

    def __post_init__(self) -> None:
        if self.first_slot > self.last_slot:
            raise LeaveError(
                "a request cannot end before it starts: "
                f"{self.start} {self.start_part.name} to "
                f"{self.end} {self.end_part.name}"
            )

    @classmethod
    def whole_days(cls, start: date, end: date) -> Span:
        return cls(start, Part.AM, end, Part.PM)

    @classmethod
    def half_day(cls, day: date, part: Part) -> Span:
        return cls(day, part, day, part)

    @property
    def first_slot(self) -> int:
        return _slot(self.start, self.start_part)

    @property
    def last_slot(self) -> int:
        return _slot(self.end, self.end_part)

    def slots(self) -> Iterator[tuple[date, Part]]:
        """Every half-day in the span, in order."""
        for slot in range(self.first_slot, self.last_slot + 1):
            yield date.fromordinal(slot // 2), Part(slot % 2)


def half_days_by_date(
    span: Span,
    *,
    pattern: frozenset[int] = MON_FRI,
    holidays: Collection[date] = (),
) -> dict[date, int]:
    """What each date of the span costs, in half-days.

    Days that cost nothing are left out, so the result is exactly the dates a
    ledger needs an entry for. It is per date rather than a single total
    because a request that crosses the year end has to be charged to two leave
    years, and the date is what says which.
    """
    closed = frozenset(holidays)
    charged: dict[date, int] = {}
    for day, _part in span.slots():
        if day.weekday() not in pattern or day in closed:
            continue
        charged[day] = charged.get(day, 0) + 1
    return charged


def count_half_days(
    span: Span,
    *,
    pattern: frozenset[int] = MON_FRI,
    holidays: Collection[date] = (),
) -> int:
    """Half-days of leave a span uses up."""
    return sum(half_days_by_date(span, pattern=pattern, holidays=holidays).values())


def split_by_leave_year(span: Span) -> list[Span]:
    """One span per leave year it touches (R-LEAVE-05).

    The brief names requests that run over the year end as a known hard part.
    The allowance is per leave year (policy §4.8), so a week from 28 December
    to 4 January spends part of one year's allowance and part of the next. The
    first piece ends on the afternoon of 31 December and the next begins on the
    morning of 1 January; the original start and end parts are kept at the two
    outer ends.
    """
    if span.start.year == span.end.year:
        return [span]

    pieces: list[Span] = []
    start, start_part = span.start, span.start_part
    for year in range(span.start.year, span.end.year):
        pieces.append(Span(start, start_part, date(year, 12, 31), Part.PM))
        start, start_part = date(year + 1, 1, 1), Part.AM
    pieces.append(Span(start, start_part, span.end, span.end_part))
    return pieces


def format_days(half_days: int) -> str:
    """Half-days as people say them: ``5`` becomes ``"2.5"``, ``4`` ``"2"``."""
    sign = "-" if half_days < 0 else ""
    whole, half = divmod(abs(half_days), 2)
    return f"{sign}{whole}.5" if half else f"{sign}{whole}"
