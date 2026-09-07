"""The theme's pure parts (app/web/theme.py).

Where it goes back to after toggling comes off a form, and a value straight
from a form into a Location header is an open redirect.
"""

from __future__ import annotations

import pytest

from app.web.theme import DAY, NIGHT, next_theme, safe_next


def test_the_toggle_alternates_and_treats_no_choice_as_day() -> None:
    assert next_theme(NIGHT) == DAY
    assert next_theme(DAY) == NIGHT
    assert next_theme("") == NIGHT


@pytest.mark.parametrize(
    "candidate",
    [
        "https://evil.example/steal",
        "//evil.example/steal",
        r"/\evil.example",
        "javascript:alert(1)",
        "http://127.0.0.1:8100/admin",
        "/admin\r\nSet-Cookie: a=b",
        "",
        "admin/people",
    ],
)
def test_it_never_redirects_off_this_app(candidate: str) -> None:
    assert safe_next(candidate) == "/"


@pytest.mark.parametrize(
    "candidate",
    ["/", "/me", "/admin/people/abc", "/admin/kudos?quarter=2026-Q2"],
)
def test_a_path_on_this_app_is_kept(candidate: str) -> None:
    assert safe_next(candidate) == candidate


def test_it_does_not_bounce_back_to_the_endpoint_itself() -> None:
    """/theme has no page of its own, so returning there is a dead end."""
    assert safe_next("/theme") == "/"
