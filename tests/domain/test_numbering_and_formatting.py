"""R-TEST-01: numbering rules and the printed formats."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.domain.formatting import (
    format_iban,
    format_invoice_date,
    format_sort_code,
    invoice_filename,
    month_heading,
)
from app.domain.numbering import (
    NumberingError,
    advance,
    format_number,
    seed_next_number,
)
from app.domain.period import Period


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
    # R-PDF-15: {period} — {legal_name} — {number}.pdf
    assert (
        invoice_filename(Period.parse("2026-09"), "Jane Smith", 34)
        == "2026-09 — Jane Smith — 34.pdf"
    )


def test_filename_strips_path_separators() -> None:
    # Without separators the remaining dots cannot escape the GCS key prefix.
    name = invoice_filename(Period.parse("2026-09"), "Jane/../Smith", 1)
    assert "/" not in name
    assert "\\" not in name
    assert name == "2026-09 — Jane .. Smith — 1.pdf"


def test_month_heading_is_just_the_month() -> None:
    # "Your Company" was redundant three lines under "Your ref: Your Company".
    assert month_heading(Period.parse("2026-09")) == "September 2026"
