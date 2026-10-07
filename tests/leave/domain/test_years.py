"""The leave year (R-LEAVE-01, policy §4.1)."""

from __future__ import annotations

from datetime import date

import pytest

from modules.leave.domain.years import (
    days_in_leave_year,
    leave_year_bounds,
    leave_year_of,
)


def test_the_leave_year_is_the_calendar_year() -> None:
    """Policy §4.1: the holiday year runs January to December."""
    assert leave_year_of(date(2026, 1, 1)) == 2026
    assert leave_year_of(date(2026, 12, 31)) == 2026
    assert leave_year_of(date(2027, 1, 1)) == 2027


def test_a_leave_year_runs_first_of_january_to_thirty_first_of_december() -> None:
    assert leave_year_bounds(2026) == (date(2026, 1, 1), date(2026, 12, 31))


@pytest.mark.parametrize(
    ("year", "days"),
    [(2026, 365), (2027, 365), (2028, 366), (2100, 365), (2000, 366)],
)
def test_a_leap_year_has_one_more_day(year: int, days: int) -> None:
    """The denominator of the leaver formula. 2100 is not a leap year and 2000
    was, which is the rule people get wrong."""
    assert days_in_leave_year(year) == days
