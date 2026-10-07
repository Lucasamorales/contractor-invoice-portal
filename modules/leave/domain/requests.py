"""A leave request's life, and when two of them clash (R-LEAVE-06 .. R-LEAVE-09).

Pure state and arithmetic. Who may *press* a button is a permission question for
the layer above; the two rules that live here are the ones that must hold
whichever route reaches them.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from modules.leave.domain.days import Span
from modules.leave.domain.errors import TransitionRefused


class Status(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


# Requests that still claim the days. A rejected or cancelled one claims nothing,
# so it neither blocks a new request nor appears on anybody's balance.
LIVE: frozenset[Status] = frozenset({Status.PENDING, Status.APPROVED})


def overlaps(a: Span, b: Span) -> bool:
    """Do two spans share a half-day?

    Half-day granular, so a morning and the afternoon of the same day do not
    clash. Somebody taking Tuesday morning and, separately, Tuesday afternoon is
    not making an overlapping request, and the brief's "overlapping requests"
    edge case must not reject it.
    """
    return a.first_slot <= b.last_slot and b.first_slot <= a.last_slot


def find_overlaps(candidate: Span, live: Mapping[str, Span]) -> tuple[str, ...]:
    """The references of the live requests a new one would collide with.

    ``live`` is one person's pending and approved requests, keyed by whatever
    identifies them. Returns them in a stable order so the message a person is
    shown does not change between refreshes.
    """
    return tuple(sorted(ref for ref, span in live.items() if overlaps(candidate, span)))


def _same_person(a: str, b: str) -> bool:
    return a.strip().lower() == b.strip().lower()


def _require(status: Status, allowed: frozenset[Status], action: str) -> None:
    if status not in allowed:
        raise TransitionRefused(f"a {status.value} request cannot be {action}")


def approve(status: Status, *, requester: str, approver: str) -> Status:
    """Pending to approved.

    **Nobody approves their own leave.** Policy §4.5 has the line manager approve,
    which already implies somebody else; the rule is held here so that no route,
    admin screen or script can be the one place that forgets. It is the same
    shape as the bank rule elsewhere in this system, where only the owner can
    make a change payable. Who approves a manager's own leave is open
    (BACKLOG L-10); until that is answered the safe side is to refuse.
    """
    _require(status, frozenset({Status.PENDING}), "approved")
    if _same_person(requester, approver):
        raise TransitionRefused("nobody can approve their own leave")
    return Status.APPROVED


def reject(status: Status, *, requester: str, approver: str) -> Status:
    """Pending to rejected, by somebody other than the person asking."""
    _require(status, frozenset({Status.PENDING}), "rejected")
    if _same_person(requester, approver):
        raise TransitionRefused("nobody can reject their own leave")
    return Status.REJECTED


def cancel(status: Status) -> Status:
    """Pending or approved to cancelled.

    Cancelling approved leave is one of the brief's named edge cases. Here it is
    only a change of state; the days come back because the ledger records a
    matching entry (ledger.cancellation_entries), not because anything is
    edited. Whether leave that has already been taken may be cancelled, and by
    whom, is a permission and audit question (BACKLOG L-37).
    """
    _require(status, LIVE, "cancelled")
    return Status.CANCELLED
