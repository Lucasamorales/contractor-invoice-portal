"""The staging kudos fixture (R-ENV-02).

Its value is entirely in the awkward cases it contains, and those are easy to
edit away by accident. These tests hold the fixture to what it is for: if
somebody tidies the data and the tie disappears, the award button stops being
exercised and nobody finds out until a real quarter ends.

No database. The rows are parsed with the same function the real sync uses.
"""

from __future__ import annotations

from datetime import date

import pytest

from modules.invoices.config import Environment, Settings
from modules.invoices.domain.kudos import Quarter, parse_rows, podium, standings
from modules.invoices.scripts import seed_demo_kudos as demo

CLAIMABLE_FROM = date(2026, 7, 1)


@pytest.fixture
def parsed():
    aliases = {
        demo.normalise_name(name): email
        for name, email in demo.ALIASES.items()
        if email
    }
    return parse_rows(demo.sheet_rows(Settings(_env_file=None)), aliases=aliases)


def test_the_fixture_is_shaped_like_the_sheet(parsed) -> None:
    """Six columns, serial dates, a header row — what the values API returns."""
    rows = demo.sheet_rows(Settings(_env_file=None))
    assert rows[0] == demo.HEADER
    assert all(len(row) == 6 for row in rows)
    assert all(isinstance(row[0], int) for row in rows[1:])
    assert parsed.problems == ()
    assert len(parsed.events) == len(demo.KUDOS)


def test_the_serial_conversion_round_trips() -> None:
    for day in (date(2026, 6, 3), date(2026, 7, 1), date(2026, 9, 4)):
        assert demo.as_serial(day) == (day - date(1899, 12, 30)).days


def test_it_contains_a_multi_value_kudo(parsed) -> None:
    """R-KUD-08. The case that would pay £80 for one kudo if miscounted."""
    multi = [e for e in parsed.events if "," in e.value_text]
    assert len(multi) == 1
    assert multi[0].value_text.count(",") == 3


def test_it_contains_history_that_is_not_billable(parsed) -> None:
    """R-KUD-12. Without this the cut-over is never exercised in staging."""
    before = [e for e in parsed.events if e.awarded_on < CLAIMABLE_FROM]
    assert before, "the fixture must span the cut-over"
    assert all(e.awarded_on.month == 6 for e in before)


def test_it_leaves_names_for_a_human_to_map(parsed) -> None:
    """R-KUD-14. The admin queue needs something real on it."""
    assert set(parsed.unmatched_names) == {"Priya Nobody", "Departed Demo"}


def test_it_has_a_leadership_member_who_earns_and_cannot_cash(parsed) -> None:
    """R-KUD-20."""
    assert demo.LEADERSHIP
    email = demo.LEADERSHIP[0]
    theirs = [e for e in parsed.events if e.email == email]
    assert theirs, "leadership must actually receive kudos, or nothing is tested"


def test_q3_has_a_clear_first_place_and_a_tie(parsed) -> None:
    """The shape of the real Q3, which is the point of the fixture.

    A single winner tests the ordinary path; the tie is what makes the award
    button more than a formality, because the policy's tiebreakers are
    judgements no code should make.
    """
    eligible = {
        email
        for email in demo.ALIASES.values()
        if email and email not in demo.LEADERSHIP
    }
    table = standings(parsed.events, Quarter(2026, 3), eligible=eligible)
    places = podium(table)

    assert places[0][1] == ("demo.amy@example.com",)
    assert places[0][2] == 4
    assert len(places[1][1]) == 3, "second place must be a tie"


def test_it_refuses_to_run_in_production() -> None:
    """R-ENV-02 is absolute, and this writes invented rows."""
    with pytest.raises(demo.DemoRefused):
        demo.refuse_production(
            Settings(_env_file=None, environment=Environment.PRODUCTION)
        )
