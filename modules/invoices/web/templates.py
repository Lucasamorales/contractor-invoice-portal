"""The Jinja environment.

Templates hold no business logic beyond formatting (CLAUDE.md §6), so the only
things registered here are the domain's own formatters.
"""

from __future__ import annotations

from fastapi.templating import Jinja2Templates

from modules.invoices.domain.formatting import (
    format_iban,
    format_invoice_date,
    format_sort_code,
)
from modules.invoices.domain.iban import mask
from modules.invoices.domain.money import format_gbp
from modules.invoices.domain.period import WINDOW_OPENS_DAY, ordinal
from modules.invoices.web.static import static_url
from modules.invoices.web.theme import theme_of
from modules.invoices.web.tutorial import steps_for

templates = Jinja2Templates(directory="modules/invoices/templates")
templates.env.filters["gbp"] = format_gbp
templates.env.filters["sort_code"] = format_sort_code
templates.env.filters["iban"] = format_iban
templates.env.filters["invoice_date"] = format_invoice_date
# Last four digits only, everywhere a number is shown (CLAUDE.md §2.8).
templates.env.filters["mask"] = mask
# The tutorial's steps for the current screen. A global rather than something
# every route has to remember to put in its context.
templates.env.globals["tutorial_steps"] = steps_for
# The day the window opens, as copy names it ("22nd"), so no template
# repeats the number.
templates.env.globals["window_opens_on"] = ordinal(WINDOW_OPENS_DAY)
# Static URLs carry a hash of the file, so they can be cached for a year and
# a deploy still cannot serve a stale one.
templates.env.globals["static"] = static_url
# The theme, read from the cookie while the page renders. A global rather
# than something every handler has to remember, so an error page gets it too.
templates.env.globals["theme_of"] = theme_of
