"""The birthday day off (R-LEAVE-18, policy §4.14).

A weekend or restricted birthday moves to the nearest Friday, before or after.
October 2026: Fri 9, Mon 12, Tue 13, Wed 14, Thu 15, Fri 16, Sat 17, Sun 18,
Fri 23.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from modules.leave.domain.birthday import birthday_day_off
from modules.leave.domain.errors import LeaveError

FRI_9, MON, TUE, WED, THU, FRI_16, SAT, SUN = (
    date(2026, 10, d) for d in (9, 12, 13, 14, 15, 16, 17, 18)
)
FRI_23 = date(2026, 10, 23)


def test_a_birthday_on_an_ordinary_weekday_is_the_day_itself() -> None:
    assert birthday_day_off(TUE) == TUE


@pytest.mark.parametrize(("birthday", "taken"), [(SAT, FRI_16), (SUN, FRI_16)])
def test_a_weekend_birthday_moves_to_the_nearest_friday(
    birthday: date, taken: date
) -> None:
    assert birthday_day_off(birthday) == taken


def test_a_restricted_monday_goes_back_to_the_friday_before() -> None:
    """Three days back beats four days forward."""
    assert birthday_day_off(MON, restricted={MON}) == FRI_9


@pytest.mark.parametrize("restricted_day", [TUE, WED, THU])
def test_a_restricted_midweek_day_goes_forward_to_the_friday_after(
    restricted_day: date,
) -> None:
    assert birthday_day_off(restricted_day, restricted={restricted_day}) == FRI_16


def test_a_restricted_friday_takes_the_earlier_of_the_two_equally_near_fridays() -> (
    None
):
    """The policy is silent here (BACKLOG L-06): seven days either way, and the
    earlier one is the assumption."""
    assert birthday_day_off(FRI_16, restricted={FRI_16}) == FRI_9


def test_when_the_nearest_friday_is_restricted_the_next_nearest_is_used() -> None:
    """Saturday's nearest Friday is closed, so the next is Friday the 23rd (six
    days on), not Friday the 9th (eight days back)."""
    assert birthday_day_off(SAT, restricted={FRI_16}) == FRI_23


def test_a_restricted_date_that_is_not_the_birthday_changes_nothing() -> None:
    assert birthday_day_off(TUE, restricted={WED, THU}) == TUE


def test_no_free_friday_within_a_year_is_an_error_not_a_loop() -> None:
    everything = {SAT + timedelta(days=n) for n in range(-400, 401)}
    with pytest.raises(LeaveError, match="no free Friday"):
        birthday_day_off(SAT, restricted=everything)


def test_two_fridays_are_never_equally_near_a_weekend_or_weekday_birthday() -> None:
    """The claim in the docstring, checked on every day of 2026: for anything
    that is not a Friday, the nearest Friday is unique, so there is nothing to
    break a tie over."""
    day = date(2026, 1, 1)
    while day.year == 2026:
        if day.weekday() != 4:
            before = (day.weekday() - 4) % 7
            after = (4 - day.weekday()) % 7
            assert before != after
        day += timedelta(days=1)


def test_a_weekend_birthday_is_never_more_than_two_days_from_the_friday() -> None:
    day = date(2026, 1, 1)
    while day.year == 2026:
        if day.weekday() >= 5:
            assert abs((birthday_day_off(day) - day).days) <= 2
        day += timedelta(days=1)
