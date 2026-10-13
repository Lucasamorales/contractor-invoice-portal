"""Kudos rules (R-KUD-07 .. R-KUD-23).

Every awkward case here is one taken from the live workbook rather than
invented: the four-value "Multi Kill", the quoted nickname, the accents, the
surname that appears twice, and the leadership member who invoices but cannot
cash. Those are what will actually arrive.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from modules.invoices.domain.kudos import (
    KudosError,
    Quarter,
    billable,
    event_key,
    is_quarter_end,
    normalise_name,
    parse_rows,
    podium,
    serial_to_date,
    standings,
)

HEADER = ["Date", "Giver", "Value", "Receiver", "Space", "Message"]

# 1 July 2026 and 1 September 2026 as Sheets serial numbers.
JUL_1 = 46204
SEP_1 = 46266

ALIASES = {
    "ezra coleman": "ezra.coleman@example.com",
    "marta aldridge": "marta@example.com",
    "toni grant": "toni@example.com",
    "remy kit keller": "remy.keller@example.com",
    "remy carter bell": "gone@example.com",
}


def test_a_serial_number_becomes_the_right_day() -> None:
    # The reason we ask the API for serials at all: "09/01/2026" is the 9th of
    # January here and the 1st of September in another locale, and the log
    # contains both months.
    assert serial_to_date(46204) == date(2026, 7, 1)
    assert serial_to_date(46266) == date(2026, 9, 1)
    # Twelve rows in the live log carry a time. The day is all that matters.
    assert serial_to_date(46266.51) == date(2026, 9, 1)


def test_a_datetime_becomes_a_date_not_itself() -> None:
    """datetime subclasses date, so it must be narrowed before the isinstance.

    Left as itself it compares fine until the first ``>=`` against a real date,
    which is the cut-over check, which is money.
    """
    assert serial_to_date(datetime(2026, 9, 1, 12, 18)) == date(2026, 9, 1)
    assert not isinstance(serial_to_date(datetime(2026, 9, 1, 12, 18)), datetime)


def test_something_that_is_not_a_date_is_refused_not_guessed() -> None:
    for bad in ("", "last tuesday", None, 0, -3):
        with pytest.raises(KudosError):
            serial_to_date(bad)


def test_names_normalise_without_deciding_two_people_are_one() -> None:
    assert normalise_name('Remy "Kit" Keller') == "remy kit keller"
    assert normalise_name("  Marta  Jamie Vance Gray ") == "marta jamie vance gray"
    # The 2025 tab spells her differently from the 2026 tab.
    assert normalise_name("Jamie Blake Irwin") == normalise_name(
        "Jamie Blake irwin"
    )
    # But two different people stay two different people.
    assert normalise_name("Remy Castellanos") != normalise_name("Charlie Castellanos")


def test_a_multi_value_kudo_is_one_kudo() -> None:
    """R-KUD-08, and the single most expensive thing to get wrong.

    The live log has five of these. Counting the values instead of the row
    would pay £80 for one kudo.
    """
    result = parse_rows(
        [
            HEADER,
            [
                JUL_1,
                "Carmen Dawson",
                "Own it, Go beyond, Be curious, Think big",
                "Ezra Coleman",
                None,
                "a multi kill",
            ],
        ],
        aliases=ALIASES,
    )
    assert len(result.events) == 1
    assert result.events[0].value_text.count(",") == 3


def test_the_key_ignores_the_message_but_not_the_kudo() -> None:
    """R-KUD-09. Editing the wording of a kudo must not create a second one."""
    args = (date(2026, 7, 1), "Riley Garner", "Marta Aldridge", "Own it")
    assert event_key(*args, 1) == event_key(*args, 1)
    # A different day, giver, receiver or value is a different kudo.
    assert event_key(date(2026, 7, 2), *args[1:], 1) != event_key(*args, 1)
    assert event_key(args[0], "Toni Grant", *args[2:], 1) != event_key(*args, 1)
    # And a genuine repeat on the same day is not collapsed into one.
    assert event_key(*args, 2) != event_key(*args, 1)


def test_the_same_kudo_twice_in_one_day_is_two_kudos() -> None:
    row = [JUL_1, "Riley Garner", "Own it", "Marta Aldridge", None, "twice"]
    result = parse_rows([HEADER, list(row), list(row)], aliases=ALIASES)
    assert len({e.event_key for e in result.events}) == 2


def test_reordering_the_sheet_does_not_change_any_key() -> None:
    """The reason the key is not a row number."""
    a = [JUL_1, "Riley Garner", "Own it", "Marta Aldridge", None, "one"]
    b = [SEP_1, "Toni Grant", "Go beyond", "Ezra Coleman", None, "two"]
    first = parse_rows([HEADER, a, b], aliases=ALIASES)
    second = parse_rows([HEADER, b, a], aliases=ALIASES)
    assert {e.event_key for e in first.events} == {e.event_key for e in second.events}


def test_an_unknown_name_is_reported_and_never_guessed() -> None:
    """R-KUD-03, R-KUD-13. 'Mitchell' is not 'Toni' unless a human says so."""
    result = parse_rows(
        [
            HEADER,
            [JUL_1, "Riley Garner", "Own it", "Victor Harlow", None, ""],
            [JUL_1, "Riley Garner", "Own it", "Toni Grant", None, ""],
        ],
        aliases=ALIASES,
    )
    assert result.unmatched_names == ("Victor Harlow",)
    assert [e.email for e in result.events] == [None, ALIASES["toni grant"]]


def test_an_ignored_name_resolves_to_nobody_and_stays_quiet() -> None:
    """R-KUD-14. Leavers and staff must not sit on the queue forever."""
    result = parse_rows(
        [HEADER, [JUL_1, "Riley Garner", "Own it", "Victor Harlow", None, ""]],
        aliases=ALIASES,
        ignored=["Victor Harlow"],
    )
    assert result.unmatched_names == ()
    assert result.events[0].ignored is True
    # Ignoring is not deleting: the event is still there to explain history.
    assert len(result.events) == 1


def test_broken_rows_are_reported_and_the_rest_still_read() -> None:
    result = parse_rows(
        [
            HEADER,
            [None, None, None, None],
            ["nonsense", "Riley Garner", "Own it", "Marta Aldridge"],
            [JUL_1, "", "Own it", "Marta Aldridge"],
            [JUL_1, "Riley Garner", "Own it", ""],
            [JUL_1, "Riley Garner", "Own it", "Marta Aldridge"],
        ],
        aliases=ALIASES,
    )
    assert len(result.events) == 1
    assert [p.row_number for p in result.problems] == [3, 4, 5]
    assert "not a date" in result.problems[0].detail


def test_a_short_row_does_not_crash() -> None:
    """Sheets omits trailing empty cells, so rows arrive ragged."""
    result = parse_rows(
        [HEADER, [JUL_1, "Riley Garner", "Own it", "Marta Aldridge"]],
        aliases=ALIASES,
    )
    assert len(result.events) == 1


def test_nothing_before_the_cut_over_is_billable() -> None:
    """R-KUD-12. Q2 was settled the old way; billing it again pays twice."""
    result = parse_rows(
        [
            HEADER,
            [JUL_1 - 1, "Riley", "Own it", "Marta Aldridge", None, "June"],
            [JUL_1, "Riley", "Own it", "Marta Aldridge", None, "July"],
        ],
        aliases=ALIASES,
    )
    kept = billable(result.events, claimable_from=date(2026, 7, 1))
    assert [e.awarded_on for e in kept] == [date(2026, 7, 1)]


def test_an_unresolved_or_ignored_event_is_never_billable() -> None:
    result = parse_rows(
        [
            HEADER,
            [JUL_1, "Riley", "Own it", "Someone Unknown", None, ""],
            [JUL_1, "Riley", "Own it", "Remy Carter Bell", None, ""],
        ],
        aliases=ALIASES,
        ignored=["Remy Carter Bell"],
    )
    assert billable(result.events, claimable_from=date(2026, 1, 1)) == ()


def test_quarters() -> None:
    assert Quarter.containing(date(2026, 9, 1)).key == "2026-Q3"
    assert Quarter.containing(date(2026, 10, 1)).key == "2026-Q4"
    assert Quarter(2026, 3).months == (7, 8, 9)
    assert Quarter(2026, 3).contains(date(2026, 7, 1))
    assert not Quarter(2026, 3).contains(date(2026, 6, 30))
    with pytest.raises(KudosError):
        Quarter(2026, 5)


def test_only_quarter_end_months_carry_kudos() -> None:
    """KUDOS.md §4: paid quarterly, on the March/June/September/December invoice."""
    assert [m for m in range(1, 13) if is_quarter_end(2026, m)] == [3, 6, 9, 12]


def test_standings_ignore_the_cut_over_but_not_eligibility() -> None:
    """A quarter's leaderboard covers the whole quarter (R-KUD-21, R-KUD-20)."""
    rows = [HEADER]
    rows += [[JUL_1, "Riley", "Own it", "Ezra Coleman", None, ""]] * 4
    rows += [[JUL_1, "Riley", "Own it", "Marta Aldridge", None, ""]] * 2
    # Leadership: counted in the log, never in the standings.
    rows += [[JUL_1, "Riley", "Own it", 'Remy "Kit" Keller', None, ""]] * 9
    # Someone who has left cannot win a prize they cannot be paid.
    rows += [[JUL_1, "Riley", "Own it", "Remy Carter Bell", None, ""]] * 7

    result = parse_rows(rows, aliases=ALIASES)
    table = standings(
        result.events,
        Quarter(2026, 3),
        eligible={
            ALIASES["ezra coleman"],
            ALIASES["marta aldridge"],
        },
    )
    assert table == (
        (ALIASES["ezra coleman"], 4),
        (ALIASES["marta aldridge"], 2),
    )


def test_standings_are_scoped_to_the_quarter() -> None:
    result = parse_rows(
        [
            HEADER,
            [JUL_1 - 1, "Riley", "Own it", "Marta Aldridge", None, "Q2"],
            [JUL_1, "Riley", "Own it", "Marta Aldridge", None, "Q3"],
        ],
        aliases=ALIASES,
    )
    eligible = {ALIASES["marta aldridge"]}
    assert standings(result.events, Quarter(2026, 3), eligible=eligible) == (
        (ALIASES["marta aldridge"], 1),
    )
    assert standings(result.events, Quarter(2026, 2), eligible=eligible) == (
        (ALIASES["marta aldridge"], 1),
    )


def test_the_podium_shows_a_tie_rather_than_breaking_it() -> None:
    """The policy's tiebreakers are all human judgement, so we never guess."""
    table = (("a@x", 4), ("b@x", 2), ("c@x", 2), ("d@x", 2), ("e@x", 1))
    assert podium(table) == (
        (1, ("a@x",), 4),
        (2, ("b@x", "c@x", "d@x"), 2),
    )


def test_the_podium_stops_at_three_because_only_three_are_paid() -> None:
    table = (("a@x", 5), ("b@x", 4), ("c@x", 3), ("d@x", 2), ("e@x", 1))
    assert [place for place, _, _ in podium(table)] == [1, 2, 3]


def test_an_empty_quarter_has_no_podium() -> None:
    assert podium(()) == ()


def test_a_quarter_knows_its_neighbours_across_the_year_boundary() -> None:
    """Stepping between quarters, so the screen need not tell anyone to edit
    a URL to see last quarter."""
    assert Quarter(2026, 2).previous == Quarter(2026, 1)
    assert Quarter(2026, 2).next == Quarter(2026, 3)
    assert Quarter(2026, 1).previous == Quarter(2025, 4)
    assert Quarter(2026, 4).next == Quarter(2027, 1)
