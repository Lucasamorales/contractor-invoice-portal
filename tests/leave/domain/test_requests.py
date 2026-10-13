"""A request's life, and when two of them clash (R-LEAVE-06 .. R-LEAVE-09)."""

from __future__ import annotations

from datetime import date

import pytest

from modules.leave.domain.days import Part, Span
from modules.leave.domain.errors import TransitionRefused
from modules.leave.domain.requests import (
    LIVE,
    Status,
    approve,
    cancel,
    find_overlaps,
    overlaps,
    reject,
)

MON, TUE, WED, THU, FRI = (date(2026, 10, d) for d in range(12, 17))
NEXT_MON = date(2026, 10, 19)

JANE = "jane@example.com"
BOSS = "boss@example.com"


# --------------------------------------------------------------------------
# Overlap: the brief's "overlapping requests" edge case
# --------------------------------------------------------------------------


def test_requests_that_share_a_day_overlap() -> None:
    assert overlaps(Span.whole_days(MON, WED), Span.whole_days(TUE, THU))


def test_a_request_inside_another_overlaps_it() -> None:
    assert overlaps(Span.whole_days(MON, FRI), Span.half_day(WED, Part.AM))


def test_separate_weeks_do_not_overlap() -> None:
    assert not overlaps(Span.whole_days(MON, FRI), Span.whole_days(NEXT_MON, NEXT_MON))


def test_a_morning_and_the_afternoon_of_the_same_day_do_not_overlap() -> None:
    """Somebody taking Tuesday morning and, separately, Tuesday afternoon is not
    making a clashing request."""
    assert not overlaps(Span.half_day(TUE, Part.AM), Span.half_day(TUE, Part.PM))


def test_one_request_ending_at_lunchtime_and_the_next_starting_after_it() -> None:
    first = Span(MON, Part.AM, WED, Part.AM)
    second = Span(WED, Part.PM, FRI, Part.PM)
    assert not overlaps(first, second)


def test_the_same_half_day_twice_is_an_overlap() -> None:
    assert overlaps(Span.half_day(TUE, Part.AM), Span.half_day(TUE, Part.AM))


def test_overlaps_are_found_in_a_stable_order() -> None:
    live = {
        "b": Span.whole_days(MON, TUE),
        "a": Span.whole_days(TUE, WED),
        "c": Span.whole_days(NEXT_MON, NEXT_MON),
    }
    assert find_overlaps(Span.whole_days(TUE, TUE), live) == ("a", "b")


def test_nothing_overlaps_when_there_is_nothing_live() -> None:
    assert find_overlaps(Span.whole_days(MON, FRI), {}) == ()


def test_only_pending_and_approved_requests_claim_days() -> None:
    assert frozenset({Status.PENDING, Status.APPROVED}) == LIVE


# --------------------------------------------------------------------------
# The lifecycle
# --------------------------------------------------------------------------


def test_a_manager_approves_a_pending_request() -> None:
    assert approve(Status.PENDING, requester=JANE, approver=BOSS) is Status.APPROVED


def test_a_manager_rejects_a_pending_request() -> None:
    assert reject(Status.PENDING, requester=JANE, approver=BOSS) is Status.REJECTED


@pytest.mark.parametrize("status", [Status.APPROVED, Status.REJECTED, Status.CANCELLED])
def test_only_a_pending_request_can_be_approved(status: Status) -> None:
    with pytest.raises(TransitionRefused):
        approve(status, requester=JANE, approver=BOSS)


@pytest.mark.parametrize("status", [Status.APPROVED, Status.REJECTED, Status.CANCELLED])
def test_only_a_pending_request_can_be_rejected(status: Status) -> None:
    with pytest.raises(TransitionRefused):
        reject(status, requester=JANE, approver=BOSS)


@pytest.mark.parametrize("status", [Status.PENDING, Status.APPROVED])
def test_a_live_request_can_be_cancelled(status: Status) -> None:
    """Including an approved one: the brief names cancelling approved leave."""
    assert cancel(status) is Status.CANCELLED


@pytest.mark.parametrize("status", [Status.REJECTED, Status.CANCELLED])
def test_a_request_that_is_already_over_cannot_be_cancelled(status: Status) -> None:
    with pytest.raises(TransitionRefused):
        cancel(status)


# --------------------------------------------------------------------------
# Nobody approves their own leave
# --------------------------------------------------------------------------


def test_nobody_can_approve_their_own_leave() -> None:
    with pytest.raises(TransitionRefused, match="own leave"):
        approve(Status.PENDING, requester=JANE, approver=JANE)


@pytest.mark.parametrize("spelling", ["JANE@example.com", "  jane@example.com "])
def test_the_same_person_in_a_different_spelling_is_still_the_same_person(
    spelling: str,
) -> None:
    """Email is the identity here, and nobody should get past the rule by
    changing its case."""
    with pytest.raises(TransitionRefused):
        approve(Status.PENDING, requester=JANE, approver=spelling)


def test_nobody_can_reject_their_own_leave_either() -> None:
    with pytest.raises(TransitionRefused):
        reject(Status.PENDING, requester=JANE, approver=JANE)
