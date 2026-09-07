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


def test_every_form_opts_out_of_boosting() -> None:
    """hx-boost is on the body so navigation does not flash white.

    It boosts forms as well as links, and a boosted POST swaps a response into
    the page instead of navigating. The submit path allocates an invoice
    number and must stay an ordinary form post, so every form says so — and
    one already slipped through because its attributes were in a different
    order, which is why this asserts rather than trusting a search-and-replace.
    """
    import re
    from pathlib import Path

    unboosted = re.compile(r"<form[^>]*hx-boost=\"false\"", re.S)
    forms = re.compile(r"<form[^>]*>", re.S)

    missing: list[str] = []
    for template in sorted(Path("app/templates").rglob("*.html")):
        for tag in forms.findall(template.read_text(encoding="utf-8")):
            if 'method="post"' in tag and not unboosted.match(tag):
                missing.append(f"{template}: {tag[:70]}")
    assert not missing, "these POST forms would be boosted: " + "; ".join(missing)


def test_boosting_does_not_override_the_swap() -> None:
    """The body must not tell htmx how to swap a boosted page in.

    hx-boost already replaces the body's contents and takes the new title.
    Adding hx-target="body" hx-swap="outerHTML" asked htmx to replace <body>
    with a fragment parsed from a <body> tag, and the HTML fragment parser
    throws that tag away — so the swap emptied the body and every boosted
    navigation landed on a blank white page. A reload fixed it, because the
    server render was never at fault, which is exactly how it was reported.
    """
    import re
    from pathlib import Path

    base = Path("app/templates/base.html").read_text(encoding="utf-8")
    tag = re.search(r"<body[^>]*>", base, re.S)
    assert tag is not None
    body = tag.group(0)
    assert 'hx-boost="true"' in body
    for attribute in ("hx-select", "hx-target", "hx-swap"):
        assert attribute not in body, f"{attribute} on <body> breaks boosted navigation"


def test_the_footer_is_the_same_width_as_the_page() -> None:
    """base.html gives main and the footer the same width class.

    They used to be set separately — main defaults to 860px, the footer was
    pinned at 1000px — so on most screens the footer sat 70px to the left of
    every heading above it. Both now read one block, and every width class
    main can take needs a matching rule in the stylesheet or the footer goes
    back to hanging off the edge.
    """
    import re
    from pathlib import Path

    base = Path("app/templates/base.html").read_text(encoding="utf-8")
    assert '<main class="{{ page_width | trim }}">' in base
    assert '<footer class="foot {{ page_width | trim }}">' in base

    css = Path("app/static/app.css").read_text(encoding="utf-8")
    widths = set(re.findall(r"^main\.([a-z-]+) \{", css, re.M))
    feet = set(re.findall(r"^\.foot\.([a-z-]+) \{", css, re.M))
    assert widths <= feet, f"no .foot rule for: {sorted(widths - feet)}"
