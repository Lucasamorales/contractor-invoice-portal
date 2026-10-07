"""The rendered PDF (R-PDF-13, R-TEST-03).

Renders the real template through WeasyPrint for a UK person and an
international one, and asserts the things Robin would notice: one page, A4, the
right figures, and the right bank block.

Skipped where WeasyPrint's native Pango and Cairo libraries are absent — a
Windows machine without the GTK runtime, typically. They are present in the
container and in CI, so this runs where it matters. See docs/setup.md §2.2.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from modules.invoices.domain.invoice import compose
from modules.invoices.domain.period import Period
from modules.invoices.services.pdf import BankBlock, Letterhead, build_context

pdfplumber = pytest.importorskip("pdfplumber", reason="pdfplumber not installed")


def _render(context: dict) -> bytes:
    try:
        from modules.invoices.services.pdf import render_pdf
    except OSError as exc:  # pragma: no cover - platform dependent
        pytest.skip(f"WeasyPrint's native libraries are missing: {exc}")
    try:
        return render_pdf(context)
    except OSError as exc:  # pragma: no cover - platform dependent
        pytest.skip(f"WeasyPrint's native libraries are missing: {exc}")


BILL_TO = (
    "Charlie Castellanos",
    "Your Company Ltd",
    "Example House",
    "1 Example Street",
    "Example Town EX1 2AB",
)

A4_POINTS = (595.3, 841.9)


def uk_context() -> dict:
    return build_context(
        number=213,
        period=Period.parse("2026-09"),
        issued_at=datetime(2026, 9, 24, 10, tzinfo=UTC),
        letterhead=Letterhead(
            legal_name="Jane Smith",
            role="Creative Director",
            address_lines=("12 Example Street", "London N1 1AA"),
            phone="+44 7700 900000",
            contact_email="jane@other.test",
        ),
        bill_to_lines=BILL_TO,
        bank=BankBlock(
            account_name="Jane Smith",
            bank_name="Monzo",
            sort_code="230801",
            account_number="12345678",
        ),
        payout_region="UK",
        totals=compose(
            fee_pence=250000,
            kudos_count=3,
            kudos_rate_pence=2000,
            expenses=[("travel", 3450), ("hotel", 12000)],
        ),
    )


def intl_context() -> dict:
    return build_context(
        number=2,
        period=Period.parse("2026-09"),
        issued_at=datetime(2026, 9, 24, 10, tzinfo=UTC),
        letterhead=Letterhead(
            legal_name="Bridge Building",
            role="Motion Designer",
            address_lines=("4 Rue Exemple", "L-1234 Luxembourg"),
            phone="+352 000 000",
            contact_email="jamie@other.test",
        ),
        bill_to_lines=BILL_TO,
        bank=BankBlock(
            account_name="Bridge Building",
            bank_name="Wise",
            iban="LU280019400644750000",
            swift_bic="TRWIBEB1XXX",
        ),
        payout_region="INTL",
        totals=compose(fee_pence=180000, kudos_count=0, kudos_rate_pence=2000),
    )


@pytest.fixture(scope="module")
def uk_pdf() -> bytes:
    return _render(uk_context())


@pytest.fixture(scope="module")
def intl_pdf() -> bytes:
    return _render(intl_context())


def pages(pdf: bytes) -> list:
    import io

    with pdfplumber.open(io.BytesIO(pdf)) as document:
        return [
            (page.width, page.height, page.extract_text()) for page in document.pages
        ]


@pytest.mark.parametrize("which", ["uk", "intl"])
def test_the_invoice_fits_on_one_a4_page(which, uk_pdf, intl_pdf) -> None:
    """R-PDF-13. The most likely way to break this is a long address."""
    rendered = pages(uk_pdf if which == "uk" else intl_pdf)
    assert len(rendered) == 1, f"{which} spilled onto {len(rendered)} pages"
    width, height, _ = rendered[0]
    assert (round(width, 1), round(height, 1)) == A4_POINTS


def test_a_long_address_still_fits_on_one_page() -> None:
    """The realistic way R-PDF-13 gets broken later."""
    context = uk_context()
    context["letterhead"]["address_lines"] = [
        "Flat 12, Example Mansions",
        "185 Example Square",
        "Example Borough",
        "London N1 2AB",
        "United Kingdom",
    ]
    assert len(pages(_render(context))) == 1


def test_the_uk_pdf_says_what_it_should(uk_pdf) -> None:
    text = pages(uk_pdf)[0][2]
    assert "Invoice No: 213" in text  # plain and unpadded (R-NUM-04)
    assert "Invoice Date: 24/09/2026" in text  # R-PDF-06
    assert "ROLE: CREATIVE DIRECTOR" in text  # R-PDF-07
    assert "September 2026" in text
    assert "Your Company — September" not in text
    assert "Remuneration: £2,500.00" in text  # R-PDF-08, R-PDF-12
    assert "Additional: £214.50" in text
    for part in ("3 kudos × £20", "travel £34.50", "hotel £120"):
        assert part in text  # R-PDF-09
    assert "Total Invoice: £2,714.50" in text
    assert "Please pay within 14 days of the invoice date." in text  # R-PDF-10
    assert "VAT" not in text  # R-PDF-14


def test_the_uk_bank_block_prints_the_uk_caption_only(uk_pdf) -> None:
    """R-PDF-11."""
    text = pages(uk_pdf)[0][2]
    assert "Sort code: 23-08-01" in text  # grouped
    assert "Account number: 12345678" in text
    assert "Use when sending money from the UK" in text
    assert "outside the UK" not in text
    assert "IBAN" not in text


def test_the_international_bank_block_prints_the_other_caption_only(
    intl_pdf,
) -> None:
    """R-PDF-11, and Jamie has no sort code, which is valid (R-SEED-06)."""
    text = pages(intl_pdf)[0][2]
    assert "IBAN: LU28 0019 4006 4475 0000" in text  # groups of four
    assert "Swift/BIC: TRWIBEB1XXX" in text
    assert "Use when sending money from outside the UK" in text
    assert "Sort code" not in text
    assert "Account number" not in text


def test_no_detail_line_when_there_is_nothing_additional(intl_pdf) -> None:
    """R-PDF-09 renders only when additional_pence > 0."""
    text = pages(intl_pdf)[0][2]
    assert "Additional: £0.00" in text
    assert "kudos ×" not in text


def test_the_pdf_is_a_real_pdf(uk_pdf) -> None:
    assert uk_pdf.startswith(b"%PDF-")
    assert b"%%EOF" in uk_pdf[-1024:]
