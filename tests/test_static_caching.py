"""Static assets are cacheable; pages are not (R-SEC-11).

The security middleware puts Cache-Control: no-store on every response, which
is right for a page showing somebody's fee and wrong for a font. Getting this
backwards once already cost 186KB on every page load and a flash of fallback
text every single time.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.web.static import IMMUTABLE, fingerprint, static_url


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def test_static_files_may_be_cached_for_a_year(client) -> None:
    response = client.get("/static/app.css")
    assert response.status_code == 200
    assert response.headers["cache-control"] == IMMUTABLE


def test_a_font_is_served_as_a_font(client) -> None:
    """Starlette guesses application/octet-stream for .woff2 otherwise."""
    response = client.get("/static/fonts/inter-variable.woff2")
    assert response.status_code == 200
    assert response.headers["content-type"] == "font/woff2"
    assert response.headers["cache-control"] == IMMUTABLE


def test_a_page_is_still_never_cached(client) -> None:
    """The whole reason the header exists: pages carry fees (CLAUDE.md 2.8)."""
    response = client.get("/healthz")
    assert response.headers["cache-control"] == "no-store"


def test_the_url_changes_when_the_file_does(tmp_path, monkeypatch) -> None:
    """Immutable is only safe if the URL tracks the bytes.

    Without this a deploy could serve last week's stylesheet out of somebody's
    cache for a year.
    """
    import app.web.static as static_module

    first = fingerprint("app.css")
    assert first and first in static_url("app.css")

    changed = tmp_path / "app.css"
    changed.write_text("/* different */", encoding="utf-8")
    monkeypatch.setattr(static_module, "STATIC_ROOT", tmp_path)
    fingerprint.cache_clear()
    assert fingerprint("app.css") != first


def test_a_missing_file_does_not_break_a_page(tmp_path, monkeypatch) -> None:
    """A cache-busting query string is never worth a 500."""
    import app.web.static as static_module

    monkeypatch.setattr(static_module, "STATIC_ROOT", tmp_path)
    fingerprint.cache_clear()
    assert static_url("nothing-here.css") == "/static/nothing-here.css"
    fingerprint.cache_clear()
