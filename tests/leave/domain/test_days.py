"""Counting leave in half-days, and splitting it at the year end (R-LEAVE-02 .. 05).

Dates are fixed and named by weekday. 12 October 2026 is a Monday, which the
first assertion checks, so a wrong calendar fails loudly instead of quietly
shifting every expectation below it.
"""

from __future__ import annotations

from datetime import date

import pytest

from modules.leave.domain.days import (
    MON_FRI,
    Part,
    Span,
    count_half_days,
    format_days,
    half_days_by_date,
    split_by_leave_year,
)
from modules.leave.domain.errors import LeaveError

MON, TUE, WED, THU, FRI, SAT, SUN, NEXT_MON = (date(2026, 10, d) for d in range(12, 20))


def test_the_calendar_these_tests_assume() -> None:
    assert [d.weekday() for d in (MON, FRI, SAT, SUN, NEXT_MON)] == [0, 4, 5, 6, 0]


# --------------------------------------------------------------------------
# What a span costs
# --------------------------------------------------------------------------


def test_a_working_week_is_five_whole_days() -> None:
    """Two half-days a day, never a fraction of one."""
    assert count_half_days(Span.whole_days(MON, FRI)) == 10


def test_a_weekend_inside_a_request_costs_nothing() -> None:
    assert count_half_days(Span.whole_days(FRI, NEXT_MON)) == 4


def test_a_public_holiday_inside_a_request_is_not_charged_again() -> None:
    """The entitlement already includes bank holidays (policy §3.2, §4.2), so
    booking leave over one must not take it from the person twice."""
    assert count_half_days(Span.whole_days(MON, FRI), holidays={WED}) == 8


def test_a_holiday_on_a_non_working_day_changes_nothing() -> None:
    assert count_half_days(Span.whole_days(MON, FRI), holidays={SAT}) == 10


def test_starting_in_the_afternoon_gives_the_first_morning_back() -> None:
    span = Span(MON, Part.PM, FRI, Part.PM)
    assert count_half_days(span) == 9


def test_finishing_at_lunchtime_gives_the_last_afternoon_back() -> None:
    span = Span(MON, Part.AM, FRI, Part.AM)
    assert count_half_days(span) == 9


def test_a_single_morning_or_afternoon_is_one_half_day() -> None:
    assert count_half_days(Span.half_day(TUE, Part.AM)) == 1
    assert count_half_days(Span.half_day(TUE, Part.PM)) == 1


def test_half_a_day_on_a_weekend_is_nothing() -> None:
    assert count_half_days(Span.half_day(SAT, Part.AM)) == 0


def test_a_part_time_pattern_is_charged_on_the_days_worked_only() -> None:
    """Monday to Wednesday: a whole working week is three days, not five."""
    pattern = frozenset({0, 1, 2})
    assert count_half_days(Span.whole_days(MON, FRI), pattern=pattern) == 6


def test_the_default_pattern_is_monday_to_friday() -> None:
    assert frozenset({0, 1, 2, 3, 4}) == MON_FRI


def test_each_date_is_charged_separately() -> None:
    """Per date, because a request over the year end belongs to two years."""
    span = Span(MON, Part.PM, WED, Part.AM)
    assert half_days_by_date(span) == {MON: 1, TUE: 2, WED: 1}


def test_days_that_cost_nothing_are_left_out_of_the_breakdown() -> None:
    assert half_days_by_date(Span.whole_days(FRI, NEXT_MON)) == {FRI: 2, NEXT_MON: 2}


# --------------------------------------------------------------------------
# What a span may be
# --------------------------------------------------------------------------


def test_a_request_cannot_end_before_it_starts() -> None:
    with pytest.raises(LeaveError, match="cannot end before it starts"):
        Span.whole_days(FRI, MON)


def test_the_afternoon_cannot_come_before_the_morning_of_the_same_day() -> None:
    with pytest.raises(LeaveError):
        Span(TUE, Part.PM, TUE, Part.AM)


def test_a_single_slot_is_a_valid_span() -> None:
    assert list(Span.half_day(TUE, Part.PM).slots()) == [(TUE, Part.PM)]


# --------------------------------------------------------------------------
# The year end (R-LEAVE-05)
# --------------------------------------------------------------------------

DEC_28 = date(2026, 12, 28)  # Monday
DEC_31 = date(2026, 12, 31)  # Thursday
JAN_1 = date(2027, 1, 1)  # Friday
JAN_4 = date(2027, 1, 4)  # Monday


def test_a_request_inside_one_year_is_not_split() -> None:
    span = Span.whole_days(MON, FRI)
    assert split_by_leave_year(span) == [span]


def test_a_request_over_the_year_end_becomes_one_piece_per_year() -> None:
    pieces = split_by_leave_year(Span.whole_days(DEC_28, JAN_4))
    assert pieces == [
        Span(DEC_28, Part.AM, DEC_31, Part.PM),
        Span(JAN_1, Part.AM, JAN_4, Part.PM),
    ]


def test_the_outer_half_days_are_kept_when_splitting() -> None:
    """Starting on an afternoon and ending at lunchtime survive the split."""
    pieces = split_by_leave_year(Span(DEC_28, Part.PM, JAN_4, Part.AM))
    assert pieces[0].start_part is Part.PM
    assert pieces[-1].end_part is Part.AM


def test_the_pieces_cost_exactly_what_the_whole_request_does() -> None:
    """Splitting must never gain or lose a day, holidays included."""
    whole = Span.whole_days(DEC_28, JAN_4)
    holidays = {JAN_1}
    pieces = split_by_leave_year(whole)
    assert sum(
        count_half_days(p, holidays=holidays) for p in pieces
    ) == count_half_days(whole, holidays=holidays)
    assert [count_half_days(p, holidays=holidays) for p in pieces] == [8, 2]


def test_a_request_over_three_years_gets_a_whole_year_in_the_middle() -> None:
    span = Span.whole_days(date(2026, 12, 30), date(2028, 1, 2))
    pieces = split_by_leave_year(span)
    assert [(p.start, p.end) for p in pieces] == [
        (date(2026, 12, 30), date(2026, 12, 31)),
        (date(2027, 1, 1), date(2027, 12, 31)),
        (date(2028, 1, 1), date(2028, 1, 2)),
    ]


# --------------------------------------------------------------------------
# Saying it
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("half_days", "said"),
    [(0, "0"), (1, "0.5"), (2, "1"), (5, "2.5"), (56, "28"), (-3, "-1.5")],
)
def test_half_days_are_said_the_way_people_say_them(half_days: int, said: str) -> None:
    assert format_days(half_days) == said
