"""Period and submission-window rules (R-PER-01 .. R-PER-06).

A period is one calendar month, ``YYYY-MM``, evaluated in Europe/London
(CLAUDE.md §6). Timestamps are stored in UTC; the window is a question about
what day it is where the people are, so it is answered in local time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")

# Moved from the 24th to the 22nd on 15 September 2026. Copy that names the
# opening day reads it from here through ordinal().
WINDOW_OPENS_DAY = 22
WINDOW_CLOSES_DAY = 28

_PERIOD_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")

_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip


class PeriodError(ValueError):
    """A period string was not a valid ``YYYY-MM``."""


class WindowState(Enum):
    """Where "now" sits relative to this month's submission window.

    ``BEFORE`` disables the submit action (R-PER-06). ``LATE`` does not:
    nobody is ever locked out (R-PER-04), the invoice is simply flagged.
    """

    BEFORE = "before"
    OPEN = "open"
    LATE = "late"


@dataclass(frozen=True, slots=True)
class Period:
    """One calendar month, ``YYYY-MM``."""

    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise PeriodError(f"month out of range: {self.month}")

    @classmethod
    def parse(cls, text: str) -> Period:
        """Parse ``2026-09``. Anything else is an error, never a guess."""
        match = _PERIOD_RE.match(text.strip())
        if not match:
            raise PeriodError(f"not a YYYY-MM period: {text!r}")
        return cls(int(match.group(1)), int(match.group(2)))

    @classmethod
    def containing(cls, moment: datetime) -> Period:
        """The period a moment falls in, evaluated in Europe/London (R-PER-01)."""
        return cls.from_date(london_date(moment))

    @classmethod
    def from_date(cls, day: date) -> Period:
        return cls(day.year, day.month)

    @property
    def key(self) -> str:
        """The storage form, ``2026-09``."""
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def month_name(self) -> str:
        return _MONTH_NAMES[self.month - 1]

    @property
    def label(self) -> str:
        """Human form used on the invoice and in emails: ``September 2026``."""
        return f"{self.month_name} {self.year}"

    def opens_on(self) -> date:
        return date(self.year, self.month, WINDOW_OPENS_DAY)

    def closes_on(self) -> date:
        return date(self.year, self.month, WINDOW_CLOSES_DAY)

    def __str__(self) -> str:
        return self.key


def london_date(moment: datetime) -> date:
    """The calendar date in Europe/London for a moment in time.

    A naive datetime is rejected rather than assumed to be UTC: guessing the
    timezone of a timestamp is how an invoice ends up in the wrong month.
    """
    if moment.tzinfo is None:
        raise PeriodError("moment must be timezone-aware")
    return moment.astimezone(LONDON).date()


def window_state(moment: datetime) -> WindowState:
    """Where ``moment`` sits relative to its own month's window (R-PER-02)."""
    day = london_date(moment).day
    if day < WINDOW_OPENS_DAY:
        return WindowState.BEFORE
    if day <= WINDOW_CLOSES_DAY:
        return WindowState.OPEN
    return WindowState.LATE


def can_submit(moment: datetime) -> bool:
    """True when the submit action is enabled.

    Open and late both submit; only the days before the window opens are
    disabled
    (R-PER-04, R-PER-06).
    """
    return window_state(moment) is not WindowState.BEFORE


def is_late(moment: datetime) -> bool:
    """Whether a submission at ``moment`` is flagged late (R-PER-04)."""
    return window_state(moment) is WindowState.LATE


def closed_message(moment: datetime) -> str:
    """The exact copy shown before the window opens (R-PER-06)."""
    month = Period.containing(moment).month_name
    return f"The window for {month} opens on the {ordinal(WINDOW_OPENS_DAY)}."


def ordinal(day: int) -> str:
    """A day of the month as copy writes it: 22 is "22nd".

    Wording that names a window day follows the constant instead of
    repeating the number, so moving the window is a one-line change.
    """
    if 11 <= day % 100 <= 13:
        return f"{day}th"
    return f"{day}" + {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
