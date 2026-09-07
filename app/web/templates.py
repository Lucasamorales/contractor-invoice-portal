"""The Jinja environment.

Templates hold no business logic beyond formatting (CLAUDE.md §6), so the only
things registered here are the domain's own formatters.
"""

from __future__ import annotations

from fastapi.templating import Jinja2Templates

from app.domain.formatting import format_iban, format_invoice_date, format_sort_code
from app.domain.iban import mask
from app.domain.money import format_gbp
from app.web.static import static_url
from app.web.theme import theme_of
from app.web.tutorial import steps_for

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["gbp"] = format_gbp
templates.env.filters["sort_code"] = format_sort_code
templates.env.filters["iban"] = format_iban
templates.env.filters["invoice_date"] = format_invoice_date
# Last four digits only, everywhere a number is shown (CLAUDE.md §2.8).
templates.env.filters["mask"] = mask
# The tutorial's steps for the current screen. A global rather than something
# every route has to remember to put in its context.
templates.env.globals["tutorial_steps"] = steps_for
# Static URLs carry a hash of the file, so they can be cached for a year and
# a deploy still cannot serve a stale one.
templates.env.globals["static"] = static_url
# The theme, read from the cookie while the page renders. A global rather
# than something every handler has to remember, so an error page gets it too.
templates.env.globals["theme_of"] = theme_of
