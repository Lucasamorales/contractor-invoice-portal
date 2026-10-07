"""R-TEST-01: period-window logic, including the boundary days and lateness."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from modules.invoices.domain.period import (
    LONDON,
    Period,
    PeriodError,
    WindowState,
    can_submit,
    closed_message,
    is_late,
    window_state,
)


def london(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, tzinfo=LONDON)


def test_period_parse_and_key() -> None:
    period = Period.parse("2026-09")
    assert (period.year, period.month) == (2026, 9)
    assert period.key == "2026-09"
    assert period.label == "September 2026"
    assert period.month_name == "September"


@pytest.mark.parametrize("text", ["2026-13", "2026-00", "26-09", "2026-9", "", "sept"])
def test_period_parse_rejects_junk(text: str) -> None:
    with pytest.raises(PeriodError):
        Period.parse(text)


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (1, WindowState.BEFORE),
        (21, WindowState.BEFORE),
        (22, WindowState.OPEN),  # opens on the 22nd (R-PER-02)
        (24, WindowState.OPEN),
        (26, WindowState.OPEN),
        (28, WindowState.OPEN),  # closes on the 28th, inclusive
        (29, WindowState.LATE),
        (30, WindowState.LATE),
    ],
)
def test_window_boundaries(day: int, expected: WindowState) -> None:
    assert window_state(london(2026, 9, day)) is expected


@pytest.mark.parametrize("day", [22, 24, 28, 29, 30])
def test_nobody_is_locked_out_once_the_window_has_opened(day: int) -> None:
    # R-PER-04: after the 28th the portal still accepts the submission.
    assert can_submit(london(2026, 9, day)) is True


@pytest.mark.parametrize("day", [1, 21])
def test_submit_disabled_before_the_window(day: int) -> None:
    assert can_submit(london(2026, 9, day)) is False


def test_lateness_flag() -> None:
    assert is_late(london(2026, 9, 28)) is False
    assert is_late(london(2026, 9, 29)) is True


def test_closed_message_names_the_month() -> None:
    assert closed_message(london(2026, 9, 3)) == (
        "The window for September opens on the 22nd."
    )


def test_period_is_evaluated_in_london_not_utc() -> None:
    # 23:30 UTC on 30 June is already 1 July in London under summer time, so the
    # period must roll over with London, not with UTC (R-PER-01).
    moment = datetime(2026, 6, 30, 23, 30, tzinfo=UTC)
    assert Period.containing(moment).key == "2026-07"


def test_window_state_is_evaluated_in_london() -> None:
    # 23:30 UTC on the 21st is 00:30 on the 22nd in London: the window is open.
    moment = datetime(2026, 9, 21, 23, 30, tzinfo=UTC)
    assert window_state(moment) is WindowState.OPEN


def test_naive_datetime_is_rejected() -> None:
    # Guessing a timestamp's timezone is how an invoice lands in the wrong month.
    with pytest.raises(PeriodError):
        window_state(datetime(2026, 9, 24, 12, 0))


def test_window_dates() -> None:
    period = Period.parse("2026-09")
    assert period.opens_on().day == 22
    assert period.closes_on().day == 28


@pytest.mark.parametrize(
    ("day", "written"),
    [(1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (12, "12th"),
     (13, "13th"), (21, "21st"), (22, "22nd"), (23, "23rd"), (28, "28th"),
     (31, "31st")],
)  # fmt: skip
def test_a_window_day_is_written_the_way_people_write_it(
    day: int, written: str
) -> None:
    from modules.invoices.domain.period import ordinal

    assert ordinal(day) == written
