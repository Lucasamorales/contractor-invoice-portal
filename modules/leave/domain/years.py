"""The leave year (R-LEAVE-01).

The company's holiday year runs January to December (policy §4.1), so a date's
leave year is simply its calendar year. It is a function rather than
``date.year`` written inline because Remy's brief lists leave-year boundaries
as a known hard part: if the rule ever stops being the calendar year, this is
the one place that changes.
"""

from __future__ import annotations

from calendar import isleap
from datetime import date


def leave_year_of(day: date) -> int:
    """The leave year a date belongs to."""
    return day.year


def leave_year_bounds(year: int) -> tuple[date, date]:
    """First and last day of a leave year, both inclusive."""
    return date(year, 1, 1), date(year, 12, 31)


def days_in_leave_year(year: int) -> int:
    """365, or 366 in a leap year.

    The denominator of the leaver formula in the brief ("days in leave year"),
    so a leaver in 2028 accrues a 366th of their allowance per day, not a 365th.
    """
    return 366 if isleap(year) else 365
