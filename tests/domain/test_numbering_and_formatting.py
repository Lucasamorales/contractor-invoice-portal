"""R-TEST-01: numbering rules and the printed formats."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from modules.invoices.domain.formatting import (
    format_iban,
    format_invoice_date,
    format_read_time,
    format_sort_code,
    invoice_filename,
    month_heading,
    name_from_email,
)
from modules.invoices.domain.numbering import (
    NumberingError,
    advance,
    format_number,
    next_number,
    released_floor,
    seed_next_number,
)
from modules.invoices.domain.period import Period


@pytest.mark.parametrize(
    ("last_issued", "expected"),
    [
        (0, 1),  # Gabriel has never invoiced; his first is 1 (R-SEED-09).
        (7, 8),
        (213, 214),
        (None, 1),
    ],
)
def test_seed_next_number(last_issued: int | None, expected: int) -> None:
    assert seed_next_number(last_issued) == expected


def test_seed_rejects_a_negative_last_number() -> None:
    with pytest.raises(NumberingError):
        seed_next_number(-1)


@pytest.mark.parametrize(("number", "expected"), [(8, "8"), (34, "34"), (213, "213")])
def test_numbers_print_plain_with_no_padding(number: int, expected: str) -> None:
    # R-NUM-04: no zero padding, ever.
    assert format_number(number) == expected


def test_zero_is_not_a_valid_invoice_number() -> None:
    with pytest.raises(NumberingError):
        format_number(0)


def test_counter_only_moves_forward() -> None:
    # R-NUM-05: a voided invoice does not release its number.
    assert advance(8) == 9


def test_invoice_date_format() -> None:
    assert format_invoice_date(date(2026, 9, 24)) == "24/09/2026"
    assert format_invoice_date(date(2026, 1, 5)) == "05/01/2026"


def test_invoice_date_is_read_in_london() -> None:
    # 23:30 UTC on the 27th is already the 28th in London under summer time.
    assert (
        format_invoice_date(datetime(2026, 9, 27, 23, 30, tzinfo=UTC)) == "28/09/2026"
    )


def test_naive_issued_at_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        format_invoice_date(datetime(2026, 9, 24, 12, 0))


def test_sort_code_prints_grouped() -> None:
    assert format_sort_code("230801") == "23-08-01"


def test_sort_code_rejects_wrong_length() -> None:
    with pytest.raises(ValueError, match="six digits"):
        format_sort_code("2308")


def test_iban_prints_in_groups_of_four() -> None:
    assert format_iban("GB82WEST12345698765432") == "GB82 WEST 1234 5698 7654 32"


def test_filename_format() -> None:
    """R-PDF-15: the month in words, the name on the invoice, the amount.

    It is the name somebody reads in a downloads folder, where "2026-09" and
    an invoice number answer neither question being asked.
    """
    assert (
        invoice_filename(Period.parse("2026-09"), "Jane Smith", 259450)
        == "September 2026 — Jane Smith — £2594.50.pdf"
    )


def test_a_filename_carries_the_year_so_two_septembers_differ() -> None:
    september = Period.parse("2026-09")
    next_september = Period.parse("2027-09")
    assert invoice_filename(september, "Jane Smith", 1000) != invoice_filename(
        next_september, "Jane Smith", 1000
    )


def test_a_filename_has_no_thousands_separator() -> None:
    """Legal in a filename everywhere, unwelcome in some of them."""
    assert "," not in invoice_filename(Period.parse("2026-09"), "Jane", 1234567)
    assert invoice_filename(Period.parse("2026-09"), "Jane", 1234567).endswith(
        "£12345.67.pdf"
    )


def test_filename_strips_path_separators() -> None:
    # Without separators the remaining dots cannot escape the GCS key prefix.
    name = invoice_filename(Period.parse("2026-09"), "Jane/../Smith", 100)
    assert "/" not in name
    assert "\\" not in name
    assert name == "September 2026 — Jane .. Smith — £1.00.pdf"


def test_month_heading_is_just_the_month() -> None:
    # "Your Company" was redundant three lines under "Your ref: Your Company".
    assert month_heading(Period.parse("2026-09")) == "September 2026"


def test_a_name_is_guessed_from_the_address_for_a_new_person() -> None:
    """Somebody who was never in the spreadsheet is created on first login,
    and their address is the only thing known about them at that moment."""
    assert name_from_email("leo@example.com") == "Leo"
    assert name_from_email("remy.castellanos@x.com") == "Remy Castellanos"
    assert name_from_email("mary_jamie@x.com") == "Mary Jamie"


def test_a_guessed_name_leaves_somebody_elses_capitals_alone() -> None:
    """title() would turn McDonald into Mcdonald. Only lift a word that is
    entirely lower case; anything else was already somebody's choice."""
    assert name_from_email("McDonald@x.com") == "McDonald"
    assert name_from_email("aMBrose.OConnor@x.com") == "aMBrose OConnor"


def test_a_subaddress_is_not_part_of_a_name() -> None:
    assert name_from_email("julian.robin+invoices@x.com") == "Julian Robin"


def test_a_read_time_is_said_the_way_somebody_would_say_it() -> None:
    """R-KUD-24. The kudos screen shows when the sheet was last read, and the
    reader is in London or Argentina — neither of them thinks in UTC."""
    now = datetime(2026, 9, 25, 15, 0, tzinfo=UTC)
    assert (
        format_read_time(datetime(2026, 9, 25, 6, 0, tzinfo=UTC), now=now)
        == "today at 07:00"  # BST, which is what both readers would write
    )
    assert (
        format_read_time(datetime(2026, 9, 24, 6, 0, tzinfo=UTC), now=now)
        == "yesterday at 07:00"
    )
    assert (
        format_read_time(datetime(2026, 9, 22, 8, 14, tzinfo=UTC), now=now)
        == "22 Sep at 09:14"
    )


def test_a_read_time_refuses_a_time_with_no_zone() -> None:
    """A naive datetime here would silently be read as UTC and print an hour
    out for half the year."""
    with pytest.raises(ValueError, match="timezone-aware"):
        format_read_time(
            datetime(2026, 9, 25, 6, 5), now=datetime(2026, 9, 25, 15, 0, tzinfo=UTC)
        )


# R-NUM-05: a cancelled invoice gives its number back
# --------------------------------------------------------------------------


def test_the_next_number_is_the_lowest_one_nothing_live_holds() -> None:
    """Void 10 and the replacement is 10. That is the whole rule."""
    assert next_number(10, taken=set()) == 10
    assert next_number(10, taken={10}) == 11
    assert next_number(34, taken={35}) == 34


def test_it_steps_over_every_number_a_live_invoice_holds() -> None:
    """Filling an old gap must not then collide with what came after it."""
    assert next_number(34, taken={34, 35, 36}) == 37


def test_the_floor_is_where_their_sequence_started() -> None:
    """Numbers below it were issued out of a Word template years ago and are
    not in this database, so nothing may reach back into them."""
    assert next_number(63, taken={1, 2, 62}) == 63
    with pytest.raises(NumberingError):
        next_number(0, taken=set())


def test_voiding_lowers_the_floor_to_the_number_it_released() -> None:
    assert released_floor(11, voided=10, freed={10}) == 10


def test_voiding_reaches_past_a_gap_left_earlier() -> None:
    """Eze: 34 cancelled, 35 sent, then 35 cancelled too. The next invoice is
    34, not 35, or he fills the second hole and keeps the first."""
    assert released_floor(36, voided=35, freed={34, 35}) == 34


def test_voiding_never_raises_the_floor() -> None:
    """Cancelling something old cannot push the sequence forward."""
    assert released_floor(20, voided=30, freed={30}) == 20
