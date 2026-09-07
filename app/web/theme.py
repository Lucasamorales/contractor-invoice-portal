"""Day or night, decided on the server.

The usual way to do this is an inline ``<script>`` in ``<head>`` that reads
``localStorage`` and sets an attribute before the first paint. That is not
available here: the CSP is ``default-src 'self'`` with no ``unsafe-inline``
precisely so that nothing third-party sits in the request path of a page
showing somebody's fee, and an inline script is blocked by the same rule. A
theme applied by script after load would then flash the light page on every
navigation, and this app navigates for everything.

So the preference is a cookie, read while the page is rendered and written on
to ``<html>``. No flash on any route, boosted or not; it works with scripting
disabled; and it costs no extra request.

It is a cookie and not a column on purpose. A UI preference is not a piece of
personal data about somebody, so this needs no migration and adds nothing to
``people`` (CLAUDE.md §8). Keep it that way.
"""

from __future__ import annotations

from fastapi import Request

COOKIE = "theme"
NIGHT = "night"
DAY = "day"
THEMES = frozenset({DAY, NIGHT})

# A year. The preference is not worth asking about twice.
MAX_AGE = 31_536_000


def theme_of(request: Request) -> str:
    """The chosen theme, or an empty string if nobody has chosen.

    Empty means no ``data-theme`` attribute is written, which leaves
    ``prefers-color-scheme`` to follow the machine. The moment the toggle is
    pressed a cookie exists and the choice wins over the operating system.
    """
    value = request.cookies.get(COOKIE, "")
    return value if value in THEMES else ""


def next_theme(current: str) -> str:
    """What the toggle switches to. No cookie is treated as day."""
    return DAY if current == NIGHT else NIGHT


def safe_next(candidate: str) -> str:
    """Where to go back to after toggling.

    A value straight from the form into a ``Location`` header is an open
    redirect, so this accepts only a path on this app: one leading slash, and
    nothing that could be read as a host or a new scheme.
    """
    if not candidate.startswith("/"):
        return "/"
    if candidate.startswith("//") or candidate.startswith("/\\"):
        return "/"
    if (
        any(character in candidate for character in ("\r", "\n", "\t"))
        or ":" in (candidate.split("?", 1)[0])
    ):
        return "/"
    # Never bounce back to the endpoint that has no page of its own.
    if candidate.startswith("/theme"):
        return "/"
    return candidate
