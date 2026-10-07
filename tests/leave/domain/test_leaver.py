"""The leaver calculation, from Remy's brief (R-LEAVE-15 .. R-LEAVE-17).

    accrued = (days worked this leave year ÷ days in the leave year) × allowance
    owed    = accrued - days taken

"Wrong numbers mean lost trust", so every boundary has its own test: the first
day of the year, the last, a leap year, somebody who started and left in the
same year, and the answer that is negative. Amounts are in half-days.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from fractions import Fraction

import pytest

from modules.leave.domain.days import Span
from modules.leave.domain.errors import LeaveError
from modules.leave.domain.leaver import (
    accrued,
    calendar_days_worked,
    leaver_calculation,
    to_days,
)
from modules.leave.domain.ledger import Entry, Kind, balance, booking_entries

ALLOWANCE = 56  # 28 days


# --------------------------------------------------------------------------
# The formula
# --------------------------------------------------------------------------


def test_the_brief_formula_for_somebody_leaving_at_the_end_of_june() -> None:
    """Jan 1 to Jun 30 2026 is 181 of 365 days, so 28 days accrue 13.88 of them."""
    result = leaver_calculation(
        allowance=ALLOWANCE, year=2026, last_day=date(2026, 6, 30), taken=0
    )
    assert (result.worked, result.in_year) == (181, 365)
    assert result.accrued == Fraction(ALLOWANCE * 181, 365)
    assert to_days(result.accrued) == Decimal("13.88")


def test_owed_is_what_has_accrued_less_what_has_been_taken() -> None:
    """Ten days (20 half-days) taken: 3.88 days still owed to them."""
    result = leaver_calculation(
        allowance=ALLOWANCE, year=2026, last_day=date(2026, 6, 30), taken=20
    )
    assert result.owed == result.accrued - 20
    assert to_days(result.owed) == Decimal("3.88")


def test_somebody_who_stays_all_year_accrues_the_whole_allowance() -> None:
    result = leaver_calculation(
        allowance=ALLOWANCE, year=2026, last_day=date(2026, 12, 31), taken=0
    )
    assert result.accrued == ALLOWANCE


def test_somebody_who_leaves_on_the_first_of_january_accrues_one_day_of_the_year() -> (
    None
):
    result = leaver_calculation(
        allowance=ALLOWANCE, year=2026, last_day=date(2026, 1, 1), taken=0
    )
    assert result.worked == 1
    assert result.accrued == Fraction(ALLOWANCE, 365)


def test_a_leap_year_divides_by_366() -> None:
    """Jan 1 to Jun 30 2028 is 182 days of 366."""
    result = leaver_calculation(
        allowance=ALLOWANCE, year=2028, last_day=date(2028, 6, 30), taken=0
    )
    assert (result.worked, result.in_year) == (182, 366)
    assert result.accrued == Fraction(ALLOWANCE * 182, 366)


def test_somebody_who_starts_in_july_accrues_the_second_half() -> None:
    result = leaver_calculation(
        allowance=ALLOWANCE,
        year=2026,
        first_day=date(2026, 7, 1),
        last_day=date(2026, 12, 31),
        taken=0,
    )
    assert result.worked == 184
    assert result.accrued == Fraction(ALLOWANCE * 184, 365)


def test_somebody_who_both_started_and_left_in_the_year() -> None:
    result = leaver_calculation(
        allowance=ALLOWANCE,
        year=2026,
        first_day=date(2026, 3, 1),
        last_day=date(2026, 8, 31),
        taken=10,
    )
    assert result.worked == 184
    assert result.owed == Fraction(ALLOWANCE * 184, 365) - 10


# --------------------------------------------------------------------------
# Negative is a real answer (policy §4.13)
# --------------------------------------------------------------------------


def test_taking_more_than_has_accrued_is_reported_as_a_shortfall() -> None:
    """Policy §4.13 reserves the right to deduct for leave taken in excess. The
    calculation reports it; whether anything is deducted is accounts' decision."""
    result = leaver_calculation(
        allowance=ALLOWANCE, year=2026, last_day=date(2026, 1, 31), taken=40
    )
    assert result.owed < 0
    assert to_days(result.owed) < 0


# --------------------------------------------------------------------------
# Boundaries
# --------------------------------------------------------------------------


def test_the_first_day_of_the_year_counts_as_worked() -> None:
    assert calendar_days_worked(2026, last_day=date(2026, 1, 1)) == 1


def test_a_first_day_before_the_year_began_does_not_add_days() -> None:
    assert calendar_days_worked(2026, first_day=date(2025, 6, 1)) == 365


def test_a_last_day_after_the_year_ended_does_not_add_days() -> None:
    assert calendar_days_worked(2026, last_day=date(2027, 3, 1)) == 365


def test_somebody_who_left_before_the_year_began_worked_none_of_it() -> None:
    assert calendar_days_worked(2026, last_day=date(2025, 12, 31)) == 0


def test_somebody_who_starts_after_the_year_ends_worked_none_of_it() -> None:
    assert calendar_days_worked(2026, first_day=date(2027, 1, 1)) == 0


def test_nobody_leaving_before_the_year_began_has_anything_accrued() -> None:
    result = leaver_calculation(
        allowance=ALLOWANCE, year=2026, last_day=date(2025, 12, 31), taken=0
    )
    assert result.accrued == 0


# --------------------------------------------------------------------------
# Exact, and indifferent to the unit
# --------------------------------------------------------------------------


def test_the_result_is_exact_and_never_a_float() -> None:
    value = accrued(1, 1, 3)
    assert isinstance(value, Fraction)
    assert value == Fraction(1, 3)


def test_days_and_weeks_give_the_same_answer_when_the_year_divides_evenly() -> None:
    """The formula only needs both quantities in one unit. Policy §4.2 says
    weeks and the brief says days; this is why that choice can wait."""
    assert accrued(ALLOWANCE, 26, 52) == accrued(ALLOWANCE, 182, 364)


def test_display_rounding_is_for_display_only() -> None:
    assert to_days(Fraction(1), places=2) == Decimal("0.50")
    assert to_days(Fraction(5), places=0) == Decimal("3")  # 2.5 days, half up


@pytest.mark.parametrize(
    ("allowance", "worked", "in_year"),
    [(-1, 10, 365), (56, 10, 0), (56, 366, 365), (56, -1, 365)],
)
def test_impossible_inputs_are_refused(
    allowance: int, worked: int, in_year: int
) -> None:
    with pytest.raises(LeaveError):
        accrued(allowance, worked, in_year)


# --------------------------------------------------------------------------
# With the ledger: what a leaver has actually taken
# --------------------------------------------------------------------------


def test_what_was_taken_up_to_the_last_day_comes_straight_from_the_ledger() -> None:
    """Leave booked after the last day is not taken and must not be counted."""
    last_day = date(2026, 6, 30)
    entries = [
        Entry(Kind.GRANT, date(2026, 1, 1), ALLOWANCE),
        *booking_entries(
            "in-june", Span.whole_days(date(2026, 6, 22), date(2026, 6, 26))
        ),
        *booking_entries(
            "in-august", Span.whole_days(date(2026, 8, 3), date(2026, 8, 7))
        ),
    ]
    taken = balance(entries, year=2026, today=last_day).used
    assert taken == 10

    result = leaver_calculation(
        allowance=ALLOWANCE, year=2026, last_day=last_day, taken=taken
    )
    assert result.owed == Fraction(ALLOWANCE * 181, 365) - 10
