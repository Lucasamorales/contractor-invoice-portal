"""Presentation helpers for the invoice (R-PDF-06, R-PDF-11, R-PDF-15).

Pure string shaping. Templates hold no logic beyond calling these
(CLAUDE.md §6).
"""

from __future__ import annotations

import re
from datetime import date, datetime

from app.domain.period import LONDON, Period


def format_invoice_date(moment: datetime | date) -> str:
    """``DD/MM/YYYY`` (R-PDF-06), read in Europe/London.

    ``issued_at`` is stored in UTC. An invoice issued at 23:30 UTC on the 27th
    is still the 28th in London during summer time, and the date printed has to
    be the one the person and Robin would both write down.
    """
    if isinstance(moment, datetime):
        if moment.tzinfo is None:
            raise ValueError("issued_at must be timezone-aware")
        moment = moment.astimezone(LONDON).date()
    return f"{moment.day:02d}/{moment.month:02d}/{moment.year:04d}"


def format_sort_code(sort_code: str) -> str:
    """``NN-NN-NN`` (R-PDF-11). Stored as six bare digits (R-SEED-05)."""
    digits = re.sub(r"[^0-9]", "", sort_code)
    if len(digits) != 6:
        raise ValueError(f"sort code must be six digits, got {len(digits)}")
    return f"{digits[0:2]}-{digits[2:4]}-{digits[4:6]}"


def format_iban(iban: str) -> str:
    """Groups of four (R-PDF-11), which is how the banks print them."""
    compact = re.sub(r"\s+", "", iban).upper()
    return " ".join(compact[i : i + 4] for i in range(0, len(compact), 4))


def invoice_filename(period: Period, legal_name: str, number: int) -> str:
    """``2026-09 — Jane Smith — 34.pdf`` (R-PDF-15).

    Path separators are stripped so a name can never escape its prefix in GCS
    or Drive; nothing else about the spec's format is changed.
    """
    safe_name = re.sub(r"[\\/]+", " ", legal_name).strip()
    safe_name = re.sub(r"\s+", " ", safe_name)
    return f"{period.key} — {safe_name} — {number}.pdf"


def month_heading(period: Period) -> str:
    """``Your Company — September 2026`` (R-PDF-07).

    The month is an addition to Robin's template, agreed with him: the original
    says nothing about which period an invoice covers.
    """
    return f"Your Company — {period.label}"
