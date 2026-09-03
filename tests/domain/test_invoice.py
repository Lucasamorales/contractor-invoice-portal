"""R-TEST-01: totals, the kudos calculation, and the R-PDF-09 detail line."""

from __future__ import annotations

import pytest

from app.domain.invoice import (
    LineKind,
    compose,
    detail_note,
    detail_parts,
    format_gbp_compact,
    kudos_amount_pence,
)
from app.domain.money import MoneyError

RATE = 2000  # £20 per kudo (R-KUD-04), passed in, never hardcoded in app code.


def test_kudos_amount() -> None:
    assert kudos_amount_pence(3, RATE) == 6000
    assert kudos_amount_pence(0, RATE) == 0


def test_kudos_rejects_negative_count() -> None:
    with pytest.raises(MoneyError):
        kudos_amount_pence(-1, RATE)


def test_kudos_rejects_zero_rate() -> None:
    with pytest.raises(MoneyError):
        kudos_amount_pence(3, 0)


def test_fee_only_invoice() -> None:
    totals = compose(fee_pence=250000, kudos_count=0, kudos_rate_pence=RATE)
    assert totals.remuneration_pence == 250000
    assert totals.additional_pence == 0
    assert totals.total_pence == 250000
    assert [line.kind for line in totals.lines] == [LineKind.FEE]


def test_empty_additional_is_valid_and_means_zero() -> None:
    # R-WIZ-03: adding nothing is a legitimate answer.
    totals = compose(
        fee_pence=250000, kudos_count=0, kudos_rate_pence=RATE, expenses=[]
    )
    assert totals.additional_pence == 0
    assert detail_parts(totals) == ()


def test_additional_sums_kudos_and_expenses() -> None:
    totals = compose(
        fee_pence=250000,
        kudos_count=3,
        kudos_rate_pence=RATE,
        expenses=[("travel", 3450)],
    )
    assert totals.additional_pence == 6000 + 3450
    assert totals.total_pence == 250000 + 9450


def test_detail_line_matches_the_spec_example() -> None:
    # R-PDF-09, one item per line.
    totals = compose(
        fee_pence=250000,
        kudos_count=3,
        kudos_rate_pence=RATE,
        expenses=[("travel", 3450)],
    )
    assert detail_parts(totals) == ("3 kudos × £20", "travel £34.50")


def test_detail_line_puts_kudos_first() -> None:
    totals = compose(
        fee_pence=1,
        kudos_count=1,
        kudos_rate_pence=RATE,
        expenses=[("travel", 500), ("hotel", 12000)],
    )
    assert detail_parts(totals) == ("1 kudo × £20", "travel £5", "hotel £120")


def test_detail_line_is_empty_when_nothing_is_additional() -> None:
    # The template renders the line only when additional_pence > 0 (R-PDF-09).
    totals = compose(fee_pence=250000, kudos_count=0, kudos_rate_pence=RATE)
    assert detail_parts(totals) == ()


def test_other_items_appear_in_the_detail_line() -> None:
    totals = compose(
        fee_pence=1,
        kudos_count=0,
        kudos_rate_pence=RATE,
        other=[("equipment share", 7500)],
    )
    assert detail_parts(totals) == ("equipment share £75",)


def test_negative_expense_is_rejected() -> None:
    with pytest.raises(MoneyError):
        compose(
            fee_pence=1,
            kudos_count=0,
            kudos_rate_pence=RATE,
            expenses=[("refund", -100)],
        )


@pytest.mark.parametrize(
    ("pence", "expected"),
    [(2000, "£20"), (3450, "£34.50"), (100, "£1"), (123456, "£1,234.56"), (5, "£0.05")],
)
def test_compact_format(pence: int, expected: str) -> None:
    assert format_gbp_compact(pence) == expected


def test_kudos_rate_is_frozen_per_invoice() -> None:
    # R-KUD-05 / R-DATA-04: the rate travels with the snapshot, so a future rate
    # change cannot rewrite an old invoice.
    old = compose(fee_pence=0, kudos_count=3, kudos_rate_pence=2000)
    new = compose(fee_pence=0, kudos_count=3, kudos_rate_pence=2500)
    assert old.additional_pence == 6000
    assert new.additional_pence == 7500


def test_the_stored_note_is_the_same_items_newline_separated() -> None:
    """detail_note is frozen onto the invoice row, so the email and a
    re-render both read the same breakdown without recomputing it."""
    totals = compose(
        fee_pence=250000,
        kudos_count=3,
        kudos_rate_pence=RATE,
        expenses=[("travel", 3450), ("hotel", 12000)],
    )
    assert detail_note(totals).splitlines() == list(detail_parts(totals))
    assert detail_note(totals) == "3 kudos × £20\ntravel £34.50\nhotel £120"
