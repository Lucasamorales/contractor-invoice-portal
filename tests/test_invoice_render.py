"""The invoice template (R-PDF-01 .. R-PDF-15).

Renders the HTML that WeasyPrint turns into the PDF, so these assertions are
about the same output Robin receives. The pixel comparison in R-PDF-13 needs
WeasyPrint's native libraries and lives in tests/integration.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.invoice import compose
from app.domain.period import Period
from app.services.pdf import BankBlock, Letterhead, build_context, render_html

ISSUED = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
PERIOD = Period.parse("2026-09")
RATE = 2000

BILL_TO = (
    "Charlie Castellanos",
    "Your Company Ltd",
    "Example House",
    "1 Example Street",
    "Example Town EX1 2AB",
)

LETTERHEAD = Letterhead(
    legal_name="Jane Smith",
    role="Creative Director",
    address_lines=("12 Example Street", "London N1 1AA"),
    phone="+44 7700 900000",
    contact_email="jane@other.test",
)

UK_BANK = BankBlock(
    account_name="Jane Smith",
    bank_name="Monzo",
    sort_code="230801",
    account_number="12345678",
)

INTL_BANK = BankBlock(
    account_name="Bridge Building",
    bank_name="Wise",
    iban="LU280019400644750000",
    swift_bic="TRWIBEB1XXX",
)


def render(
    *,
    bank: BankBlock = UK_BANK,
    region: str = "UK",
    number: int = 34,
    kudos: int = 0,
    expenses: list[tuple[str, int]] | None = None,
) -> str:
    totals = compose(
        fee_pence=250000,
        kudos_count=kudos,
        kudos_rate_pence=RATE,
        expenses=expenses,
    )
    return render_html(
        build_context(
            number=number,
            period=PERIOD,
            issued_at=ISSUED,
            letterhead=LETTERHEAD,
            bill_to_lines=BILL_TO,
            bank=bank,
            payout_region=region,
            totals=totals,
        )
    )


@pytest.fixture(scope="module")
def uk_html() -> str:
    return render(kudos=3, expenses=[("travel", 3450)])


@pytest.fixture(scope="module")
def intl_html() -> str:
    return render(bank=INTL_BANK, region="INTL", number=2)


def test_letterhead_carries_all_five_printed_fields(uk_html: str) -> None:
    # R-PDF-02, and why R-WIZ-02 makes all five required.
    for value in (
        "Jane Smith",
        "Creative Director",
        "12 Example Street",
        "London N1 1AA",
        "+44 7700 900000",
        "jane@other.test",
    ):
        assert value in uk_html


def test_bill_to_block_is_the_fixed_address(uk_html: str) -> None:
    for line in BILL_TO:
        assert line in uk_html


def test_your_ref_is_constant(uk_html: str) -> None:
    assert "Your ref: Your Company" in uk_html


def test_invoice_number_prints_plain_and_unpadded(uk_html: str) -> None:
    # R-PDF-05 with R-NUM-04.
    assert "Invoice No: 34" in uk_html
    assert "Invoice No: 034" not in uk_html


def test_invoice_date_is_day_month_year(uk_html: str) -> None:
    assert "Invoice Date: 24/09/2026" in uk_html


def test_role_line_is_uppercase_and_the_month_follows(uk_html: str) -> None:
    # R-PDF-07. The month line is the one addition to Robin's template.
    assert "ROLE: CREATIVE DIRECTOR" in uk_html
    assert "September 2026" in uk_html
    assert "Your Company — September" not in uk_html


def test_figures_print_with_thousands_and_two_decimals(uk_html: str) -> None:
    # R-PDF-12. Fee 2500.00, kudos 3 x 20 plus travel 34.50 = 94.50.
    assert "£2,500.00" in uk_html
    assert "£94.50" in uk_html
    assert "£2,594.50" in uk_html


def test_detail_line_appears_when_there_is_something_additional(
    uk_html: str,
) -> None:
    # R-PDF-09, in the spec's own worked format.
    assert "3 kudos × £20" in uk_html
    assert "travel £34.50" in uk_html
    assert " · " not in uk_html  # one item per line now


def test_detail_line_is_absent_when_additional_is_zero() -> None:
    html = render()
    assert "detail-line" not in html
    assert "£0.00" in html  # Additional still prints, it is simply zero


def test_payment_terms_are_constant(uk_html: str) -> None:
    assert "Please pay within 14 days of date of invoice" in uk_html


def test_uk_block_prints_sort_code_grouped_and_the_uk_caption(uk_html: str) -> None:
    # R-PDF-11.
    assert "Sort code: 23-08-01" in uk_html
    assert "Account number: 12345678" in uk_html
    assert "Use when sending money from the UK" in uk_html


def test_uk_block_omits_the_outside_caption_when_there_is_no_iban(
    uk_html: str,
) -> None:
    assert "Use when sending money from outside the UK" not in uk_html
    assert "IBAN:" not in uk_html


def test_uk_block_prints_both_captions_when_an_iban_is_present() -> None:
    html = render(
        bank=BankBlock(
            account_name="Jane Smith",
            bank_name="Wise",
            sort_code="230801",
            account_number="98765432",
            iban="GB82WEST12345698765432",
            swift_bic="TRWIGB2LXXX",
        )
    )
    assert "Use when sending money from the UK" in html
    assert "Use when sending money from outside the UK" in html
    assert "GB82 WEST 1234 5698 7654 32" in html  # grouped in fours


def test_intl_block_prints_only_the_outside_caption(intl_html: str) -> None:
    assert "LU28 0019 4006 4475 0000" in intl_html
    assert "Swift/BIC: TRWIBEB1XXX" in intl_html
    assert "Use when sending money from outside the UK" in intl_html
    assert "Use when sending money from the UK</div>" not in intl_html
    assert "Sort code:" not in intl_html


def test_intl_block_does_not_demand_a_sort_code(intl_html: str) -> None:
    # Jamie has none, and R-SEED-06 says that is valid.
    assert "None" not in intl_html


def test_no_vat_or_tax_line_anywhere(uk_html: str, intl_html: str) -> None:
    # R-PDF-14: everyone invoices as a contractor.
    for html in (uk_html, intl_html):
        assert "VAT" not in html
        assert "Tax" not in html


def test_the_page_is_a4(uk_html: str) -> None:
    from app.services.pdf import STYLESHEET

    assert "size: A4 portrait" in STYLESHEET.read_text(encoding="utf-8")


def test_inlining_the_stylesheet_removes_the_external_link() -> None:
    # The PDF renderer must not need to fetch anything.
    totals = compose(fee_pence=1, kudos_count=0, kudos_rate_pence=RATE)
    html = render_html(
        build_context(
            number=1,
            period=PERIOD,
            issued_at=ISSUED,
            letterhead=LETTERHEAD,
            bill_to_lines=BILL_TO,
            bank=UK_BANK,
            payout_region="UK",
            totals=totals,
        ),
        inline_css=True,
    )
    assert "<link rel=" not in html
    assert "size: A4 portrait" in html


def test_the_preview_style_never_reaches_the_printed_document() -> None:
    """The screen hides the frame's scrollbars; the PDF must not know.

    invoice.css is the print document (R-PDF-*). Anything added to suit a
    screen goes on the preview copy only, and the two paths are never both
    asked for at once.
    """
    from app.services.pdf import PREVIEW_STYLE

    printed = render()
    assert "scrollbar-width" not in printed
    assert PREVIEW_STYLE not in printed

    inlined = render_html(
        build_context(
            number=34,
            period=PERIOD,
            issued_at=ISSUED,
            letterhead=LETTERHEAD,
            bill_to_lines=BILL_TO,
            bank=UK_BANK,
            payout_region="UK",
            totals=compose(fee_pence=250000, kudos_count=0, kudos_rate_pence=RATE),
        ),
        inline_css=True,
    )
    assert "scrollbar-width" not in inlined


def test_the_preview_hides_the_frames_scrollbars() -> None:
    """Two grey bars across a piece of paper that is meant to look like paper.

    Hidden, not clipped: overflow:hidden would silently lose the bottom of a
    long invoice.
    """
    previewed = render_html(
        build_context(
            number=34,
            period=PERIOD,
            issued_at=ISSUED,
            letterhead=LETTERHEAD,
            bill_to_lines=BILL_TO,
            bank=UK_BANK,
            payout_region="UK",
            totals=compose(fee_pence=250000, kudos_count=0, kudos_rate_pence=RATE),
        ),
        for_preview=True,
    )
    assert "scrollbar-width:none" in previewed
    assert "overflow:hidden" not in previewed
    assert previewed.count("</head>") == 1
