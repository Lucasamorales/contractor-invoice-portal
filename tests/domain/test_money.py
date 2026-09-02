"""R-TEST-01: the £1,234.56 formatter, and money as int pence only."""

from __future__ import annotations

import pytest

from app.domain.money import MoneyError, format_gbp, invoice_total, parse_gbp_to_pence


@pytest.mark.parametrize(
    ("pence", "expected"),
    [
        (0, "£0.00"),
        (1, "£0.01"),
        (50, "£0.50"),
        (100, "£1.00"),
        (3450, "£34.50"),
        (123456, "£1,234.56"),
        (100000000, "£1,000,000.00"),
        (-3450, "-£34.50"),
    ],
)
def test_format_gbp(pence: int, expected: str) -> None:
    assert format_gbp(pence) == expected


def test_format_gbp_rejects_float() -> None:
    # CLAUDE.md §2.4: no floats, ever. A float reaching here is a bug upstream.
    with pytest.raises(MoneyError):
        format_gbp(34.5)  # type: ignore[arg-type]


def test_format_gbp_rejects_bool() -> None:
    with pytest.raises(MoneyError):
        format_gbp(True)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("34.50", 3450),
        ("£34.50", 3450),
        ("  34.5 ", 3450),
        ("1,234.56", 123456),
        ("12", 1200),
        ("0", 0),
        ("-5.00", -500),
    ],
)
def test_parse_gbp_to_pence(text: str, expected: int) -> None:
    assert parse_gbp_to_pence(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "10.005", "1.2.3", "£"])
def test_parse_gbp_rejects_junk(text: str) -> None:
    with pytest.raises(MoneyError):
        parse_gbp_to_pence(text)


@pytest.mark.parametrize("text", ["0.01", "34.50", "1,234.56", "999.99"])
def test_parse_then_format_is_stable(text: str) -> None:
    # There is no rounding step anywhere, so a round trip cannot drift a penny.
    assert format_gbp(parse_gbp_to_pence(text)) == f"£{text}"


def test_invoice_total_adds() -> None:
    assert invoice_total(250000, 6450) == 256450


def test_invoice_total_rejects_negative() -> None:
    with pytest.raises(MoneyError):
        invoice_total(-1, 0)
