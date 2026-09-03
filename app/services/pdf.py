"""Rendering the invoice (R-PDF-01, R-PDF-13, R-SUB-04).

One template produces both the on-screen preview and the PDF, so what a person
approves is what Robin receives. WeasyPrint turns the same HTML and CSS into
A4; there is no headless browser anywhere in this path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.domain.formatting import invoice_filename, month_heading
from app.domain.invoice import InvoiceTotals, detail_parts
from app.domain.period import Period
from app.web.templates import templates

TEMPLATE = "invoice.html"
STYLESHEET = Path("app/static/invoice.css")


@dataclass(frozen=True, slots=True)
class Letterhead:
    """R-PDF-02. Frozen into ``print_snapshot`` at issue time."""

    legal_name: str
    role: str
    address_lines: tuple[str, ...]
    phone: str
    contact_email: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "legal_name": self.legal_name,
            "role": self.role,
            "address_lines": list(self.address_lines),
            "phone": self.phone,
            "contact_email": self.contact_email,
        }


@dataclass(frozen=True, slots=True)
class BankBlock:
    """R-PDF-11. The decrypted values, held only for the length of a render.

    Robin needs the full numbers: he pays these invoices and they are printed on
    every PDF he receives (R-ACC-06). They are never logged (R-SEC-05).
    """

    account_name: str
    bank_name: str
    sort_code: str | None = None
    account_number: str | None = None
    iban: str | None = None
    swift_bic: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "account_name": self.account_name,
            "bank_name": self.bank_name,
            "sort_code": self.sort_code,
            "account_number": self.account_number,
            "iban": self.iban,
            "swift_bic": self.swift_bic,
        }


def build_context(
    *,
    number: int,
    period: Period,
    issued_at: datetime,
    letterhead: Letterhead,
    bill_to_lines: tuple[str, ...],
    bank: BankBlock,
    payout_region: str,
    totals: InvoiceTotals,
) -> dict[str, Any]:
    """Everything the template needs, and nothing it could query for itself."""
    return {
        "number": number,
        "issued_at": issued_at,
        "month_heading": month_heading(period),
        "letterhead": letterhead.as_dict(),
        "bill_to": {"lines": list(bill_to_lines)},
        "bank": bank.as_dict(),
        "payout_region": payout_region,
        "remuneration_pence": totals.remuneration_pence,
        "additional_pence": totals.additional_pence,
        "total_pence": totals.total_pence,
        "detail_parts": list(detail_parts(totals)),
    }


def render_html(context: dict[str, Any], *, inline_css: bool = False) -> str:
    """Render the invoice to HTML.

    ``inline_css`` embeds the stylesheet, which is what the PDF path needs:
    WeasyPrint would otherwise have to resolve a URL, and the renderer should
    not be making requests.
    """
    template = templates.get_template(TEMPLATE)
    html = template.render(**context)
    if inline_css:
        css = STYLESHEET.read_text(encoding="utf-8")
        html = html.replace(
            '<link rel="stylesheet" href="/static/invoice.css">',
            f"<style>{css}</style>",
        )
    return html


def render_pdf(context: dict[str, Any]) -> bytes:
    """Render to PDF bytes (R-SUB-04).

    Imported lazily so the rest of the application — the seed, the domain
    tests, the wizard — does not require WeasyPrint's native libraries to be
    present just to import a module.
    """
    from weasyprint import HTML

    html = render_html(context, inline_css=True)
    return HTML(string=html, base_url=".").write_pdf()


def pdf_filename(period: Period, legal_name: str, number: int) -> str:
    """R-PDF-15."""
    return invoice_filename(period, legal_name, number)


def gcs_object_name(period: Period, legal_name: str, number: int) -> str:
    """R-STO-02: ``invoices/{period}/{filename}.pdf``."""
    return f"invoices/{period.key}/{pdf_filename(period, legal_name, number)}"
