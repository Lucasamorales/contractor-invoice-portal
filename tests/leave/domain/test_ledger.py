"""The balance as a ledger nobody edits (R-LEAVE-10 .. R-LEAVE-14).

The brief's employee screen is "allowance, used, booked, remaining", and the
brief's edge cases include cancelling approved leave and requests over the year
end. Each is a test here. The shape of the system is that the days come back
because a cancellation entry exists, so most of these tests assert on entries
rather than on a function that "refunds".
"""

from __future__ import annotations

from datetime import date

import pytest

from modules.leave.domain.days import Span
from modules.leave.domain.errors import LeaveError
from modules.leave.domain.ledger import (
    Entry,
    Kind,
    balance,
    booking_entries,
    cancellation_entries,
)

MON, TUE, WED, THU, FRI = (date(2026, 10, d) for d in range(12, 17))
ALLOWANCE = 56  # 28 days, in half-days
GRANT = Entry(Kind.GRANT, date(2026, 1, 1), ALLOWANCE)


def week_booking() -> list[Entry]:
    return booking_entries("r1", Span.whole_days(MON, FRI))


# --------------------------------------------------------------------------
# Allowance, used, booked, remaining
# --------------------------------------------------------------------------


def test_nothing_booked_leaves_the_whole_allowance() -> None:
    result = balance([GRANT], year=2026, today=date(2026, 6, 1))
    assert (result.allowance, result.used, result.booked, result.remaining) == (
        ALLOWANCE,
        0,
        0,
        ALLOWANCE,
    )


def test_a_booking_is_one_entry_per_charged_date() -> None:
    entries = week_booking()
    assert [e.on for e in entries] == [MON, TUE, WED, THU, FRI]
    assert {e.half_days for e in entries} == {-2}
    assert {e.ref for e in entries} == {"r1"}


def test_leave_before_today_is_used_and_leave_after_it_is_booked() -> None:
    result = balance([GRANT, *week_booking()], year=2026, today=WED)
    assert result.used == 6  # Monday, Tuesday and Wednesday
    assert result.booked == 4  # Thursday and Friday
    assert result.remaining == ALLOWANCE - 10


def test_today_counts_as_used_because_the_person_is_already_off() -> None:
    before = balance([GRANT, *week_booking()], year=2026, today=TUE)
    on_the_day = balance([GRANT, *week_booking()], year=2026, today=WED)
    assert on_the_day.used - before.used == 2


@pytest.mark.parametrize("today", [date(2026, 3, 1), MON, WED, FRI, date(2026, 12, 31)])
def test_remaining_never_depends_on_which_day_it_is_read(today: date) -> None:
    """The split between used and booked moves days between two columns. The
    remaining balance is the plain sum of the entries, whenever you look."""
    entries = [GRANT, *week_booking()]
    result = balance(entries, year=2026, today=today)
    assert result.remaining == sum(e.half_days for e in entries)


def test_the_balance_reads_the_same_next_year_because_today_is_a_parameter() -> None:
    """The brief says wrong numbers mean lost trust: a number must be
    reproducible for any date, which it cannot be if it reads the clock."""
    entries = [GRANT, *week_booking()]
    assert balance(entries, year=2026, today=WED) == balance(
        entries, year=2026, today=WED
    )


# --------------------------------------------------------------------------
# Cancelling gives the days back, by construction
# --------------------------------------------------------------------------


def test_cancelling_a_booking_appends_the_days_back() -> None:
    bookings = week_booking()
    reversal = cancellation_entries(bookings)
    assert [e.kind for e in reversal] == [Kind.CANCELLATION] * 5
    assert [e.half_days for e in reversal] == [2] * 5
    assert [e.on for e in reversal] == [e.on for e in bookings]


def test_a_cancelled_booking_leaves_the_whole_allowance_again() -> None:
    bookings = week_booking()
    entries = [GRANT, *bookings, *cancellation_entries(bookings)]
    result = balance(entries, year=2026, today=date(2026, 10, 20))
    assert (result.used, result.booked, result.remaining) == (0, 0, ALLOWANCE)


def test_the_original_booking_is_never_edited_or_removed() -> None:
    """The record of what was approved survives the cancellation."""
    bookings = week_booking()
    entries = [GRANT, *bookings, *cancellation_entries(bookings)]
    assert sum(1 for e in entries if e.kind is Kind.BOOKING) == 5


def test_cancelling_the_rest_of_a_week_does_not_refund_the_days_already_taken() -> None:
    """Took Monday and Tuesday, cancels from Wednesday on."""
    bookings = week_booking()
    entries = [GRANT, *bookings, *cancellation_entries(bookings, from_date=WED)]
    result = balance(entries, year=2026, today=FRI)
    assert result.used == 4
    assert result.booked == 0
    assert result.remaining == ALLOWANCE - 4


def test_only_a_booking_can_be_cancelled() -> None:
    with pytest.raises(LeaveError, match="only a booking"):
        cancellation_entries([GRANT])


# --------------------------------------------------------------------------
# Leave years
# --------------------------------------------------------------------------

DEC_28 = date(2026, 12, 28)
JAN_4 = date(2027, 1, 4)


def test_a_request_over_the_year_end_is_charged_to_both_years() -> None:
    entries = [
        Entry(Kind.GRANT, date(2026, 1, 1), ALLOWANCE),
        Entry(Kind.GRANT, date(2027, 1, 1), ALLOWANCE),
        *booking_entries("r2", Span.whole_days(DEC_28, JAN_4)),
    ]
    today = date(2026, 12, 1)
    in_2026 = balance(entries, year=2026, today=today)
    in_2027 = balance(entries, year=2027, today=today)
    assert in_2026.booked == 8  # Monday to Thursday
    assert in_2027.booked == 4  # Friday 1 January and Monday 4 January
    assert in_2026.remaining == ALLOWANCE - 8
    assert in_2027.remaining == ALLOWANCE - 4


def test_another_years_entries_do_not_leak_into_this_one() -> None:
    entries = [Entry(Kind.GRANT, date(2025, 1, 1), 999), GRANT]
    assert balance(entries, year=2026, today=date(2026, 6, 1)).allowance == ALLOWANCE


def test_there_is_no_carry_over_unless_somebody_adds_it() -> None:
    """Policy §4.8: leave is used within the year it relates to. Days left at
    the end of 2025 do nothing for 2026."""
    entries = [Entry(Kind.GRANT, date(2025, 1, 1), ALLOWANCE), GRANT]
    assert balance(entries, year=2026, today=date(2026, 6, 1)).remaining == ALLOWANCE


def test_a_carry_over_exception_is_an_adjustment_somebody_added_on_purpose() -> None:
    """Policy §4.9: consent in special circumstances, long-term sickness or
    family leave. It shows up as its own entry, so it can be audited."""
    entries = [GRANT, Entry(Kind.ADJUSTMENT, date(2026, 1, 1), 10, "carried over")]
    assert balance(entries, year=2026, today=date(2026, 6, 1)).allowance == 66


def test_a_correction_can_take_days_away() -> None:
    entries = [GRANT, Entry(Kind.ADJUSTMENT, date(2026, 1, 1), -4, "entered twice")]
    assert balance(entries, year=2026, today=date(2026, 6, 1)).allowance == 52


def test_the_ledger_does_not_decide_whether_a_balance_may_go_negative() -> None:
    """Whether to refuse, warn or allow it is a policy decision (BACKLOG L-38).
    The arithmetic just reports it."""
    entries = [Entry(Kind.GRANT, date(2026, 1, 1), 4), *week_booking()]
    assert balance(entries, year=2026, today=FRI).remaining == -6


# --------------------------------------------------------------------------
# An entry that cannot exist
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind", [Kind.GRANT, Kind.ADJUSTMENT, Kind.BOOKING, Kind.CANCELLATION]
)
def test_an_entry_of_nothing_is_refused(kind: Kind) -> None:
    with pytest.raises(LeaveError, match="records nothing"):
        Entry(kind, MON, 0)


def test_signs_are_fixed_by_the_kind() -> None:
    with pytest.raises(LeaveError):
        Entry(Kind.GRANT, MON, -2)
    with pytest.raises(LeaveError):
        Entry(Kind.CANCELLATION, MON, -2)
    with pytest.raises(LeaveError):
        Entry(Kind.BOOKING, MON, 2)
