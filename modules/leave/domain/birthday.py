"""The birthday day off (R-LEAVE-18).

Policy §4.14: everybody has one paid day off for their birthday each holiday
year. If it falls on a weekend or a restricted date (Team Week, bank holidays,
closure days) it is taken on the nearest Friday, before or after.

This only answers *which day*. Whether the day sits inside the allowance or on
top of it, and where the birthday is stored, are open (BACKLOG L-06), so the
birthday arrives as a date and the restricted dates as an input.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date, timedelta

from modules.leave.domain.errors import LeaveError

FRIDAY = 4
SATURDAY = 5

# A year in either direction is far more than any real set of closure dates
# could cover, and stops a bad input from looping for ever.
_SEARCH_DAYS = 366


def birthday_day_off(birthday: date, *, restricted: Collection[date] = ()) -> date:
    """The day the birthday leave is taken, given this year's birthday.

    A weekday that is not restricted is simply the birthday. Anything else moves
    to the nearest Friday. Two Fridays are never equally near a non-Friday, since
    they are seven days apart, so for a weekend or weekday birthday there is
    always a single answer.

    The edge is a Friday that is itself restricted (a bank holiday, say): the
    nearest Fridays are then both seven days away. The earlier one is taken, and
    if that is restricted too the search widens. The policy is silent on this,
    so it is an assumption and is recorded as one (BACKLOG L-06).
    """
    closed = frozenset(restricted)
    if birthday.weekday() < SATURDAY and birthday not in closed:
        return birthday

    for distance in range(_SEARCH_DAYS + 1):
        for candidate in (
            birthday - timedelta(days=distance),
            birthday + timedelta(days=distance),
        ):
            if candidate.weekday() == FRIDAY and candidate not in closed:
                return candidate
    raise LeaveError(f"no free Friday within a year of {birthday}")
