"""Serving the stylesheet, the scripts and the fonts.

These carry nobody's data, so unlike every HTML response they can and should
be cached. Before this they were not: the security middleware puts
``Cache-Control: no-store`` on every response, which is right for a page
showing a fee and wrong for a font. The result was 186KB re-fetched on every
single page load, and the fonts arriving after the first paint every time —
the flash of Arial.

Cached for a year and marked immutable, which is only safe because the URL
carries a hash of the file's contents. Change the file, change the URL.

No build step (CLAUDE.md §3): the hash is computed once per file on first use,
at runtime.
"""

from __future__ import annotations

import hashlib
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from starlette.responses import Response
from starlette.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

STATIC_ROOT = Path("app/static")

# A year, which is what "cache forever" means in practice. Safe because the
# URL changes whenever the bytes do.
IMMUTABLE = "public, max-age=31536000, immutable"

# Fonts are referenced from inside app.css by a plain url(), and CSS cannot be
# templated without a build step. Their URLs therefore carry no hash, so
# **replacing a font means renaming the file**. They are otherwise the most
# cacheable thing here and change roughly never.
FONT_TYPES = {".woff2": "font/woff2", ".woff": "font/woff"}


@lru_cache(maxsize=64)
def fingerprint(name: str) -> str:
    """A short content hash, or an empty string if the file is not there."""
    path = STATIC_ROOT / name
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:10]
    except OSError:
        # Never fail a page render over a cache-busting query string.
        logger.warning("static file %s could not be hashed", name)
        return ""


def static_url(name: str) -> str:
    """``/static/app.css?v=<hash of app.css>``.

    Exposed to templates so a deploy cannot serve last week's stylesheet out
    of somebody's cache for a year.
    """
    digest = fingerprint(name)
    return f"/static/{name}?v={digest}" if digest else f"/static/{name}"


class CachedStaticFiles(StaticFiles):
    """``StaticFiles`` that says how long its files may be kept.

    The security middleware uses ``setdefault`` for its headers, so a
    Cache-Control set here survives rather than being overwritten by the
    ``no-store`` that HTML responses need.
    """

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = IMMUTABLE

        # Starlette guesses application/octet-stream for .woff2.
        suffix = Path(str(args[0] if args else "")).suffix.lower()
        if suffix in FONT_TYPES:
            response.headers["Content-Type"] = FONT_TYPES[suffix]
        return response
