"""Kudos rules (R-KUD-07 .. R-KUD-23). See KUDOS.md for the reasoning.

Pure. No sheet, no database, no clock. Rows in, events and problems out, so
the part that decides what people get paid is testable without a network.

Two rules carry all the money and both are counter-intuitive enough to state
plainly:

**One row is one kudo.** The ``Value`` column may list several company values
("Own it, Go beyond, Be curious, Think big") and that is still one kudo at one
rate. Verified against the live workbook: the sheet's own derived grid equals a
count of log rows across 24 receivers and 9 months, with no exceptions.

**A name is resolved from an explicit table or not at all.** Fuzzy matching is
forbidden (R-KUD-13). The sheet holds ``Toni Grant`` where the payroll holds
``Mitchell Grant``, two different Remy-somethings, a nickname in quotes and
spellings that drift between tabs. A near-miss that resolves itself pays the
wrong person, which is the failure this whole system exists to prevent.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

# Column order in the sheet's `Raw Data` tab. Positional because that is what
# the values API returns, and because the announcer that feeds this sheet reads
# it the same way — if the columns move, both break together and loudly.
COL_DATE = 0
COL_GIVER = 1
COL_VALUE = 2
COL_RECEIVER = 3

# Sheets counts days from 1899-12-30. Reading the log with
# valueRenderOption=UNFORMATTED_VALUE gives this number rather than a rendered
# string, which matters: "09/01/2026" is the 9th of January to us and the 1st
# of September to an American locale, and the sheet holds both months.
SHEETS_EPOCH = date(1899, 12, 30)

QUARTER_MONTHS = {1: (1, 2, 3), 2: (4, 5, 6), 3: (7, 8, 9), 4: (10, 11, 12)}


class KudosError(ValueError):
    """A row could not be read as a kudo."""


@dataclass(frozen=True, slots=True)
class Quarter:
    """A billing quarter. Kudos are paid quarterly (KUDOS.md §4)."""

    year: int
    number: int

    def __post_init__(self) -> None:
        if not 1 <= self.number <= 4:
            raise KudosError(f"quarter out of range: {self.number}")

    @classmethod
    def containing(cls, day: date) -> Quarter:
        return cls(day.year, (day.month - 1) // 3 + 1)

    @property
    def key(self) -> str:
        return f"{self.year}-Q{self.number}"

    @property
    def months(self) -> tuple[int, ...]:
        return QUARTER_MONTHS[self.number]

    @property
    def last_month(self) -> int:
        return self.months[-1]

    def contains(self, day: date) -> bool:
        return day.year == self.year and day.month in self.months

    @property
    def previous(self) -> Quarter:
        """The quarter before this one, crossing the year boundary."""
        if self.number == 1:
            return Quarter(self.year - 1, 4)
        return Quarter(self.year, self.number - 1)

    @property
    def next(self) -> Quarter:
        """The quarter after this one, crossing the year boundary."""
        if self.number == 4:
            return Quarter(self.year + 1, 1)
        return Quarter(self.year, self.number + 1)

    def __str__(self) -> str:
        return self.key


def is_quarter_end(year: int, month: int) -> bool:
    """Does this invoice period carry kudos?

    March, June, September and December. In the other two months of a quarter
    the wizard says when kudos will next appear rather than showing a zero,
    which reads like a fault (KUDOS.md §4).
    """
    del year
    return month in (3, 6, 9, 12)


@dataclass(frozen=True, slots=True)
class KudosEvent:
    """One row of the log, resolved as far as it can be without a database."""

    event_key: str
    awarded_on: date
    giver_name: str
    receiver_name: str
    value_text: str
    email: str | None
    ignored: bool

    @property
    def resolved(self) -> bool:
        return self.email is not None


@dataclass(frozen=True, slots=True)
class RowProblem:
    """A row that could not be read. Reported, never guessed past."""

    row_number: int
    detail: str


@dataclass(frozen=True, slots=True)
class ParseResult:
    events: tuple[KudosEvent, ...]
    problems: tuple[RowProblem, ...]

    @property
    def unmatched_names(self) -> tuple[str, ...]:
        """Receivers with no alias and no place on the ignore list.

        These are what reach an admin screen (R-KUD-14). Names on the ignore
        list — people who have left, staff who do not invoice here — resolve to
        nobody on purpose and must not report, or the screen stays permanently
        red and stops being read.
        """
        seen = {
            e.receiver_name for e in self.events if not e.resolved and not e.ignored
        }
        return tuple(sorted(seen))


def normalise_name(text: str) -> str:
    """A name reduced to what can be compared: accents, case and quotes gone.

    Only ever used to look a name up in the alias table, never to decide that
    two different names are the same person.
    """
    stripped = unicodedata.normalize("NFKD", str(text or ""))
    # Folding to ASCII removes the accents and drops typographic quotes
    # outright, so only the straight ones are left to turn into separators.
    stripped = stripped.encode("ascii", "ignore").decode("ascii")
    for ch in ('"', "'"):
        stripped = stripped.replace(ch, " ")
    return " ".join(stripped.lower().split())


def serial_to_date(value: object) -> date:
    """A Sheets serial number as a date. Whole days; the time is discarded.

    Twelve rows in the live log carry a time as well as a date. Nothing in the
    rules is finer-grained than a day, so the fraction is dropped rather than
    carried around to be mishandled at a quarter boundary.
    """
    # datetime is a subclass of date, so it must be narrowed first or a
    # datetime falls through as itself and every later comparison against a
    # date raises. Twelve rows in the live log are datetimes.
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        days = int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise KudosError(f"not a date: {value!r}") from exc
    if days <= 0:
        raise KudosError(f"not a date: {value!r}")
    return SHEETS_EPOCH + timedelta(days=days)


def event_key(
    awarded_on: date, giver: str, receiver: str, value_text: str, ordinal: int
) -> str:
    """A stable identity for one kudo (R-KUD-09).

    Built from the date, the giver, the receiver and the values — deliberately
    **not** the row number, which any insertion above shifts, and **not** the
    message, so that fixing a typo in the text of a kudo does not conjure a
    second one.

    ``ordinal`` separates genuine repeats: the same person giving the same
    person the same kudo twice on one day. There are none in the live log
    today, so this is the case that would otherwise be discovered by
    underpaying somebody.
    """
    parts = (
        awarded_on.isoformat(),
        normalise_name(giver),
        normalise_name(receiver),
        normalise_name(value_text),
        str(ordinal),
    )
    return hashlib.sha256("".join(parts).encode()).hexdigest()[:32]


def parse_rows(
    rows: Sequence[Sequence[object]],
    *,
    aliases: Mapping[str, str],
    ignored: Iterable[str] = (),
    has_header: bool = True,
) -> ParseResult:
    """Read the log into events.

    ``aliases`` and ``ignored`` are keyed by normalised name and supplied by
    the caller, so this stays free of the database (CLAUDE.md §5).

    A row that cannot be read becomes a problem and is skipped. It never
    becomes a zero, and it never becomes a guess.
    """
    ignore_set = {normalise_name(name) for name in ignored}
    events: list[KudosEvent] = []
    problems: list[RowProblem] = []
    seen: Counter[tuple[str, str, str, str]] = Counter()

    for index, row in enumerate(rows):
        row_number = index + 1
        if has_header and index == 0:
            continue

        cells = list(row) + [""] * (COL_RECEIVER + 1 - len(row))
        raw_date, giver, value_text, receiver = (
            cells[COL_DATE],
            str(cells[COL_GIVER] or "").strip(),
            str(cells[COL_VALUE] or "").strip(),
            str(cells[COL_RECEIVER] or "").strip(),
        )

        # A wholly blank row is the spreadsheet's padding, not a problem.
        if not any((raw_date, giver, receiver)):
            continue

        if not receiver:
            problems.append(RowProblem(row_number, "no receiver"))
            continue
        if not giver:
            problems.append(RowProblem(row_number, f"no giver for {receiver}"))
            continue
        try:
            awarded_on = serial_to_date(raw_date)
        except KudosError as exc:
            problems.append(RowProblem(row_number, str(exc)))
            continue

        key_parts = (
            awarded_on.isoformat(),
            normalise_name(giver),
            normalise_name(receiver),
            normalise_name(value_text),
        )
        seen[key_parts] += 1
        normalised = normalise_name(receiver)

        events.append(
            KudosEvent(
                event_key=event_key(
                    awarded_on, giver, receiver, value_text, seen[key_parts]
                ),
                awarded_on=awarded_on,
                giver_name=giver,
                receiver_name=receiver,
                value_text=value_text,
                email=aliases.get(normalised),
                ignored=normalised in ignore_set,
            )
        )

    return ParseResult(events=tuple(events), problems=tuple(problems))


def billable(
    events: Iterable[KudosEvent], *, claimable_from: date
) -> tuple[KudosEvent, ...]:
    """Events that may ever appear on an invoice (R-KUD-12).

    Everything before the cut-over is history: ingested and counted for the
    standings, never billed, because it was settled the old way.
    """
    return tuple(
        event
        for event in events
        if event.resolved and not event.ignored and event.awarded_on >= claimable_from
    )


def standings(
    events: Iterable[KudosEvent],
    quarter: Quarter,
    *,
    eligible: Iterable[str],
) -> tuple[tuple[str, int], ...]:
    """The quarter's leaderboard, highest first, then by email for stability.

    ``eligible`` is who may win: contractors, minus leadership and minus anyone
    who has left (R-KUD-20, R-KUD-21). Leadership can be given kudos and can be
    shown on a leaderboard; they cannot be paid for them and cannot take a
    prize. Someone who has left cannot be paid a prize either, so they are not
    in the standings that decide one.

    The cut-over does **not** apply here. A quarter's standings are about the
    whole quarter, including the part that was settled the old way.
    """
    allowed = set(eligible)
    counts: Counter[str] = Counter()
    for event in events:
        if event.email in allowed and quarter.contains(event.awarded_on):
            counts[event.email] += 1  # type: ignore[index]
    return tuple(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])))


def podium(
    table: Sequence[tuple[str, int]],
) -> tuple[tuple[int, tuple[str, ...], int], ...]:
    """The standings grouped into places, ties kept together.

    Returns ``(place, emails, count)``. A tie is returned as a tie and never
    broken here: the policy's tiebreakers are year-to-date total, consistency,
    kudos from other teams and longevity, all of which are judgements a person
    makes. This function's job is to show management exactly where a judgement
    is required.

    Only the first three places are returned, because only three are paid.
    """
    grouped: list[tuple[int, tuple[str, ...], int]] = []
    place = 1
    for count, emails in _by_count(table):
        if place > 3:
            break
        grouped.append((place, emails, count))
        place += len(emails)
    return tuple(grouped)


def _by_count(
    table: Sequence[tuple[str, int]],
) -> Iterable[tuple[int, tuple[str, ...]]]:
    buckets: dict[int, list[str]] = {}
    for email, count in table:
        buckets.setdefault(count, []).append(email)
    for count in sorted(buckets, reverse=True):
        yield count, tuple(sorted(buckets[count]))
