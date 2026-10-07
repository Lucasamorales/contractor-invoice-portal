"""Presentation helpers for the invoice (R-PDF-06, R-PDF-11, R-PDF-15).

Pure string shaping. Templates hold no logic beyond calling these
(CLAUDE.md §6).
"""

from __future__ import annotations

import re
from datetime import date, datetime

from modules.invoices.domain.money import format_gbp
from modules.invoices.domain.period import LONDON, Period


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


def format_read_time(moment: datetime, *, now: datetime) -> str:
    """How long ago something happened, in Europe/London (R-KUD-24).

    ``today at 07:00``, ``yesterday at 07:00``, ``22 Sep at 09:14``. Both
    arguments are read in London, because the reader is in London or Argentina
    and neither of them thinks in UTC: a sync at 06:05 UTC is "today at 07:00"
    to both of them during summer time.
    """
    if moment.tzinfo is None or now.tzinfo is None:
        raise ValueError("both times must be timezone-aware")
    local = moment.astimezone(LONDON)
    today = now.astimezone(LONDON).date()
    clock = f"{local.hour:02d}:{local.minute:02d}"
    days = (today - local.date()).days
    if days == 0:
        return f"today at {clock}"
    if days == 1:
        return f"yesterday at {clock}"
    return f"{local.day} {local.strftime('%b')} at {clock}"


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


def invoice_filename(period: Period, legal_name: str, total_pence: int) -> str:
    """``September 2026 — Jane Smith — £2594.50.pdf`` (R-PDF-15).

    The month in words and the amount, because this is the name somebody sees
    in a downloads folder or an email attachment — where "2026-09" and an
    invoice number answer neither of the questions being asked, which are which
    month and how much.

    The name is the one on the invoice itself (R-PDF-02), so the file and the
    document agree, and so it matches the payee on the payment run.

    No thousands separator: a comma in a filename is legal everywhere and
    unwelcome in some of them.

    Path separators are stripped so a name can never escape its prefix in GCS.
    """
    safe_name = re.sub(r"[\\/]+", " ", legal_name).strip()
    safe_name = re.sub(r"\s+", " ", safe_name)
    amount = format_gbp(total_pence).replace(",", "")
    return f"{period.label} — {safe_name} — {amount}.pdf"


def month_heading(period: Period) -> str:
    """``September 2026`` (R-PDF-07).

    The period line is an addition to Robin's template, which says nothing
    about which month an invoice covers. It used to read "Your Company — September
    2026"; the "Your Company" was redundant next to "Your ref: Your Company" three
    lines above, so it is just the month now.
    """
    return period.label


def name_from_email(email: str) -> str:
    """A first guess at somebody's name, from the address IAP verified.

    A person who was never in the seed spreadsheet is created on their first
    login, and the only thing known about them at that moment is their
    address. Taking the local part verbatim greeted them as "leo" and put
    "leo" in the payee column of the payment run, so this at least
    capitalises it and splits the usual separators:
    ``remy.castellanos@`` becomes ``Remy Castellanos``.

    A guess, not a fact — it is a display name, never the legal name that
    prints on the invoice, and finance can correct it.
    """
    # Everything from a "+" is a subaddress, not part of who they are.
    local = email.partition("@")[0].partition("+")[0]
    words = [word for word in re.split(r"[._\-]+", local) if word]
    if not words:
        return local
    # Only lift a word that is entirely lower case. "McDonald" and "OConnor"
    # are somebody's own capitalisation and title() would ruin them.
    return " ".join(word.capitalize() if word.islower() else word for word in words)
