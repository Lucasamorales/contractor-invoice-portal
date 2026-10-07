"""What the leave rules refuse, in one place."""

from __future__ import annotations


class LeaveError(ValueError):
    """A leave rule was given something it cannot make sense of."""


class TransitionRefused(LeaveError):
    """A request cannot move from where it is to where it was asked to go."""
