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


def test_every_download_link_opts_out_of_boosting() -> None:
    """hx-boost turns a link into an AJAX GET whose body is swapped into the
    page. That is right for a page and wrong for a file: htmx does not look at
    the content type, so clicking an invoice printed the PDF's own bytes into
    the document instead of letting the browser act on Content-Disposition.

    Anything serving bytes rather than a page has to opt out, and there is no
    way to see that from the server, so it is asserted on the markup.
    """
    import re
    from pathlib import Path

    downloads = re.compile(r"<a[^>]*href=\"[^\"]*(?:/pdf|\.csv)[^\"]*\"[^>]*>", re.S)

    missing: list[str] = []
    for template in sorted(Path("app/templates").rglob("*.html")):
        for tag in downloads.findall(template.read_text(encoding="utf-8")):
            if 'hx-boost="false"' not in tag:
                missing.append(f"{template}: {tag[:70]}")
    assert not missing, "these download links would be boosted: " + "; ".join(missing)


def test_every_colour_lives_in_a_token_block() -> None:
    """Night mode is a second set of tokens, so a rule that hardcodes a colour
    is a rule that only works in one of them.

    The failure is silent and easy to reintroduce — a literal looks perfectly
    fine until somebody switches theme — so it is asserted rather than trusted
    to review.
    """
    import re
    from pathlib import Path

    css = Path("app/static/app.css").read_text(encoding="utf-8")
    # Everything after the last token block is ordinary rules.
    end = css.rindex("@media print {")
    end = css.index("}\n}", end) + 3
    literals = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)", css[end:])
    assert not literals, f"colours outside the token blocks: {sorted(set(literals))}"


def test_the_two_night_blocks_agree() -> None:
    """The night values appear twice — once for somebody who chose, once for
    somebody whose machine is dark and who has not. There is no build step, so
    nothing but this stops them drifting apart."""
    import re
    from pathlib import Path

    css = Path("app/static/app.css").read_text(encoding="utf-8")

    def tokens(block: str) -> dict[str, str]:
        return {
            name: value.strip()
            for name, value in re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", block)
        }

    chosen = re.search(r':root\[data-theme="night"\] \{(.*?)\n\}', css, re.S)
    system = re.search(r":root:not\(\[data-theme\]\) \{(.*?)\n  \}", css, re.S)
    assert chosen and system
    picked, followed = tokens(chosen.group(1)), tokens(system.group(1))
    # The system block may omit tokens it does not need to restate, but any it
    # does set must agree.
    disagree = {
        name: (value, picked.get(name))
        for name, value in followed.items()
        if picked.get(name) != value
    }
    assert not disagree, f"night blocks drifted: {disagree}"
    assert set(followed) == set(picked), (
        f"only in one block: {set(followed) ^ set(picked)}"
    )


def test_the_tab_icon_is_present_and_hashed() -> None:
    """A missing favicon is a 404 on every first page load, and a favicon with
    no hash in its URL is one that cannot be changed for a year."""
    from pathlib import Path

    base = Path("app/templates/base.html").read_text(encoding="utf-8")
    for name in ("favicon.ico", "favicon-32.png", "apple-touch-icon.png"):
        assert (Path("app/static") / name).exists(), f"{name} is missing"
        assert f"static('{name}')" in base, f"{name} is not linked, or not hashed"


def test_scripts_in_the_body_bind_their_global_listeners_once() -> None:
    """hx-boost swaps the body, so htmx re-executes every script inside it on
    every navigation. A listener on document or window is not replaced with
    the body, so each navigation added another copy: after four, one press of
    the theme toggle fired four handlers, flipped four times and landed back
    where it started — a button that appeared to do nothing, on whichever
    tabs happened to land on an even count.

    Anything bound per element is fine, because the elements are new. This is
    only about the ones that outlive the swap.
    """
    import re
    from pathlib import Path

    for name in (
        "theme.js",
        "tutorial.js",
        "preview.js",
        "forms.js",
        "kudos.js",
        "receipts.js",
    ):
        source = Path("app/static") / name
        text = source.read_text(encoding="utf-8")
        globals_bound = re.findall(
            r"(?:document|window)\.addEventListener\(\s*\"(\w+)\"", text
        )
        # DOMContentLoaded is the entry point and is harmless twice: it has
        # already fired by the time a swapped script runs, so it never binds.
        lasting = [event for event in globals_bound if event != "DOMContentLoaded"]
        if lasting:
            assert "__portal" in text, (
                f"{name} binds {lasting} on document or window with no guard, "
                "so a boosted navigation will bind them again"
            )


def test_the_toggle_does_not_depend_on_a_request_finishing() -> None:
    """It used to: the page changed on the press and a POST wrote the cookie
    afterwards, so pressing the toggle and then leaving the page cancelled the
    write and the next screen came back the way it was.

    The script writes the cookie itself now. The POST is only the fallback —
    for a browser that refuses the write, or an httponly cookie left over from
    before — and keepalive is what lets that one survive leaving the page.
    """
    from pathlib import Path

    source = Path("app/static/theme.js").read_text(encoding="utf-8")
    assert "document.cookie =" in source, "the press must not need a request"
    assert "keepalive: true" in source, "the fallback must survive a navigation"
    # Painted first, so the page never waits on anything.
    assert source.index("paint(root, button, next)") < source.index("document.cookie =")


def test_the_repeated_account_number_is_checked_as_it_is_typed() -> None:
    """The field says it is typed twice to catch a transposition (R-BANK-03),
    so it has to say when the two differ while there is still a cursor in it —
    not after a round trip.

    The server still decides: banking.parse_bank_fields compares them again,
    and nothing here is trusted.
    """
    from pathlib import Path

    forms = Path("app/static/forms.js").read_text(encoding="utf-8")
    assert "data-match" in forms
    # setCustomValidity is what makes the browser refuse the submit and move
    # focus, the same as it already does for a malformed email address.
    assert "setCustomValidity" in forms

    for name in ("bank_change.html", "admin/person.html"):
        markup = (Path("app/templates") / name).read_text(encoding="utf-8")
        assert 'name="account_number_again"' in markup, name
        pair = markup[markup.index('name="account_number_again"') :][:400]
        assert 'data-match="account_number"' in pair, (
            f"{name}: the repeated field is not checked against the first"
        )


def test_no_screen_loads_its_script_from_the_head() -> None:
    """Moving between screens swaps the body and keeps the head, so a script a
    screen asks for in its head is never fetched unless somebody reloads. The
    receipt upload, the Add another button and the running total all sat dead
    that way on every normal route into the expenses screen.

    htmx itself is the one script that belongs up there: it has to be present
    before anything is swapped.
    """
    import re
    from pathlib import Path

    templates = Path("app/templates")
    for path in templates.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert "block scripts" not in text, (
            f"{path} uses a head script block; put the script at the end of "
            "its content block"
        )

    base = (templates / "base.html").read_text(encoding="utf-8")
    head = base.split("</head>", 1)[0]
    in_head = re.findall(r"static\('([^']+\.js)'\)", head)
    assert in_head == ["htmx.min.js"], f"scripts in the head: {in_head}"
