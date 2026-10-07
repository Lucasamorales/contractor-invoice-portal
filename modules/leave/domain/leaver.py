"""What somebody who joins or leaves part-way through the year is owed
(R-LEAVE-15 .. R-LEAVE-17).

The formula is the one in Remy's brief, and it has to be as quick and reliable as
Leave Dates is today:

    accrued = (time worked this leave year ÷ time in the leave year) × annual allowance
    owed    = accrued - leave taken

**Exact, never rounded.** The result is a ``Fraction``. The policy does not say
how a leaver's entitlement is rounded, and the two documents disagree on the
unit it is measured in (BACKLOG L-05), so rounding here would be guessing
at money. Whoever applies a rule applies it at the edge, to a number that is
still exactly right.

**Unit-agnostic.** ``worked`` and ``in_year`` only have to be in the same unit.
Policy §4.2 says weeks worked; the brief says days. ``calendar_days_worked``
below is the brief's version, and swapping the unit changes that one helper, not
the formula.

**Negative is a real answer.** A leaver who has taken more than they accrued
owes days (policy §4.13 reserves the right to deduct). The calculation reports
the shortfall; whether anything is deducted is accounts' decision, not
arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from modules.leave.domain.errors import LeaveError
from modules.leave.domain.years import days_in_leave_year, leave_year_bounds


def calendar_days_worked(
    year: int, *, first_day: date | None = None, last_day: date | None = None
) -> int:
    """Calendar days in the leave year between the first and last day worked.

    Both ends are inclusive and both are clamped to the year, so a starter's
    first day and a leaver's last day are each counted as worked, a person who
    was there all year gets the whole year, and somebody who left before the
    year began gets nothing.
    """
    year_start, year_end = leave_year_bounds(year)
    begin = max(first_day, year_start) if first_day else year_start
    finish = min(last_day, year_end) if last_day else year_end
    return max(0, (finish - begin).days + 1)


def accrued(allowance: int, worked: int, in_year: int) -> Fraction:
    """``allowance × worked ÷ in_year``, exactly.

    ``allowance`` is in half-days, and the result is too. ``worked`` and
    ``in_year`` are any one unit, as long as they are the same one.
    """
    if allowance < 0:
        raise LeaveError("an allowance cannot be negative")
    if in_year <= 0:
        raise LeaveError("the leave year must have at least one unit in it")
    if not 0 <= worked <= in_year:
        raise LeaveError(f"cannot have worked {worked} of {in_year}")
    return Fraction(allowance * worked, in_year)


@dataclass(frozen=True, slots=True)
class LeaverResult:
    """The working, not just the answer, so it can be shown and checked."""

    allowance: int
    worked: int
    in_year: int
    accrued: Fraction
    taken: int
    owed: Fraction


def leaver_calculation(
    *,
    allowance: int,
    year: int,
    last_day: date,
    taken: int,
    first_day: date | None = None,
) -> LeaverResult:
    """The brief's formula for somebody whose last day is ``last_day``.

    ``taken`` is the leave used up to and including ``last_day``, in half-days:
    ``ledger.balance(entries, year=year, today=last_day).used`` is exactly that.
    Anything booked after the last day is not taken and does not count; it should
    be cancelled, which gives the days back by the ledger, before this is read.
    Pass ``first_day`` for somebody who also started during the year.
    """
    worked = calendar_days_worked(year, first_day=first_day, last_day=last_day)
    in_year = days_in_leave_year(year)
    earned = accrued(allowance, worked, in_year)
    return LeaverResult(
        allowance=allowance,
        worked=worked,
        in_year=in_year,
        accrued=earned,
        taken=taken,
        owed=earned - taken,
    )


def to_days(half_days: Fraction | int, places: int = 2) -> Decimal:
    """Half-days as days to ``places`` decimals. **For display only.**

    This rounds, so it must never feed a payment or a balance; it exists so a
    screen can say "13.88 days" without anybody dividing by two.
    """
    quantum = Decimal(1).scaleb(-places)
    exact = Decimal(half_days.numerator) / Decimal(half_days.denominator) / 2
    return exact.quantize(quantum, rounding=ROUND_HALF_UP)
