"""The two decisions a person has to make about kudos.

Who a name in the sheet belongs to (R-KUD-13), and who wins a quarter
(R-KUD-17). Both are here rather than in ``admin.py`` because both are
judgements the code deliberately refuses to make on its own.

Nothing in this module awards anything by itself. It computes a leaderboard and
shows exactly where a person is required — which, for the very first quarter
this touches, is a three-way tie for second place.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import Settings
from modules.invoices.db import repo
from modules.invoices.db.models import (
    AwardKind,
    KudosAlias,
    KudosAward,
    KudosEvent,
    Person,
    PersonStatus,
)
from modules.invoices.domain.kudos import Quarter, normalise_name, podium


class KudosAdminError(ValueError):
    """A refused admin action, with a message fit to show a person."""


PLACES = {
    1: AwardKind.QUARTER_1ST,
    2: AwardKind.QUARTER_2ND,
    3: AwardKind.QUARTER_3RD,
}
PLACE_NAMES = {1: "1st", 2: "2nd", 3: "3rd"}


def cash_for(place: int, settings: Settings) -> int:
    """The prize for a place (KUDOS.md §5).

    Config, not literals: these came out of a policy document and policies get
    revised. The cash carries **no kudos threshold** — the 5+ and 3+ in the
    policy gate the perks, which are out of scope.
    """
    return {
        1: settings.award_first_pence,
        2: settings.award_second_pence,
        3: settings.award_third_pence,
    }.get(place, 0)


@dataclass(frozen=True, slots=True)
class StandingRow:
    person: Person
    kudos_count: int
    place: int | None
    tied_with: int
    cash_pence: int
    awarded: KudosAward | None

    @property
    def needs_a_decision(self) -> bool:
        """On the podium, tied, and not yet awarded."""
        return self.place is not None and self.tied_with > 1 and self.awarded is None


@dataclass(frozen=True, slots=True)
class QuarterView:
    quarter: Quarter
    rows: tuple[StandingRow, ...]
    unbilled_kudos: int

    @property
    def has_tie(self) -> bool:
        return any(row.needs_a_decision for row in self.rows)

    @property
    def awarded_pence(self) -> int:
        return sum(row.awarded.amount_pence for row in self.rows if row.awarded)


def quarter_view(
    session: Session, quarter: Quarter, *, settings: Settings
) -> QuarterView:
    """The quarter's standings, and where a human decision is required.

    Leadership and anyone deactivated are out (R-KUD-20, R-KUD-21): neither can
    be paid a prize, so neither should appear to be winning one.

    Ties are shown as ties. The policy's tiebreakers — year to date,
    consistency, kudos from other teams, longevity — are all judgements.
    """
    people = {
        person.id: person
        for person in repo.all_people(session)
        if person.kudos_eligible and person.status is not PersonStatus.INACTIVE
    }
    events = repo.kudos_events_in(
        session, since=date(quarter.year, quarter.months[0], 1)
    )

    counts: Counter[uuid.UUID] = Counter()
    for event in events:
        if event.person_id in people and quarter.contains(event.awarded_on):
            counts[event.person_id] += 1

    awarded = {
        award.person_id: award
        for award in repo.awards_for_quarter(session, quarter.key)
    }

    table = sorted(
        counts.items(), key=lambda pair: (-pair[1], people[pair[0]].display_name)
    )
    place_of: dict[str, tuple[int, int]] = {}
    for place, ids, _count in podium(tuple((str(pid), n) for pid, n in table)):
        for pid in ids:
            place_of[pid] = (place, len(ids))

    rows = tuple(
        StandingRow(
            person=people[person_id],
            kudos_count=count,
            place=place_of.get(str(person_id), (None, 0))[0],
            tied_with=place_of.get(str(person_id), (None, 0))[1],
            cash_pence=cash_for(place_of.get(str(person_id), (0, 0))[0] or 0, settings),
            awarded=awarded.get(person_id),
        )
        for person_id, count in table
    )

    unbilled = sum(
        1
        for event in events
        if event.claimable
        and event.claimed_by_invoice_id is None
        and event.person_id is not None
        and quarter.contains(event.awarded_on)
    )
    return QuarterView(quarter=quarter, rows=rows, unbilled_kudos=unbilled)


def award(
    session: Session,
    *,
    person: Person,
    quarter: Quarter,
    place: int,
    kudos_count: int,
    settings: Settings,
    actor_email: str,
    note: str = "",
) -> KudosAward:
    """Record a quarterly prize (R-KUD-17).

    Never called by a scheduler and never derived from the standings alone.
    The unique constraint on (person, quarter, kind) is the backstop for a
    double-click; this is the readable refusal in front of it.
    """
    if place not in PLACES:
        raise KudosAdminError("Only first, second and third place carry a prize.")
    if not person.kudos_eligible:
        raise KudosAdminError(
            f"{person.display_name} is leadership and cannot take a kudos prize."
        )

    already = [
        row
        for row in repo.awards_for_quarter(session, quarter.key)
        if row.person_id == person.id
    ]
    if already:
        raise KudosAdminError(
            f"{person.display_name} already has a {quarter.key} prize."
        )

    row = KudosAward(
        person_id=person.id,
        quarter=quarter.key,
        kind=PLACES[place],
        amount_pence=cash_for(place, settings),
        kudos_count=kudos_count,
        note=note.strip() or None,
        awarded_by=actor_email,
    )
    session.add(row)
    repo.record_audit(
        session,
        actor_email=actor_email,
        action="kudos.award",
        entity="kudos_awards",
        entity_id=f"{quarter.key}:{person.id}",
        after={
            "place": PLACE_NAMES[place],
            "amount_pence": cash_for(place, settings),
            "kudos": kudos_count,
        },
    )
    return row


def map_name(
    session: Session,
    *,
    sheet_name: str,
    person: Person | None,
    actor_email: str,
    note: str = "",
) -> KudosAlias:
    """Confirm who a sheet name is, or that it is nobody (R-KUD-13).

    ``person=None`` means ignore it: someone who has left, or staff who do not
    invoice here. Ignoring is not deleting — the events stay, so the history
    still explains itself, and the queue stops reporting a name nobody will
    ever map.
    """
    key = normalise_name(sheet_name)
    if not key:
        raise KudosAdminError("That is not a name.")

    if session.scalar(select(KudosAlias).where(KudosAlias.normalised_name == key)):
        raise KudosAdminError(f"{sheet_name} is already mapped.")

    alias = KudosAlias(
        normalised_name=key,
        display_name=sheet_name.strip(),
        person_id=person.id if person else None,
        ignored=person is None,
        note=note.strip() or None,
        confirmed_by=actor_email,
    )
    session.add(alias)

    if person is not None:
        # Resolve what has already arrived under that name. Only unclaimed
        # events: moving a billed kudo to somebody else would silently change
        # money an immutable invoice has already stated (CLAUDE.md §2.3).
        for event in session.scalars(
            select(KudosEvent).where(
                KudosEvent.person_id.is_(None),
                KudosEvent.claimed_by_invoice_id.is_(None),
            )
        ).all():
            if normalise_name(event.receiver_name) == key:
                event.person_id = person.id

    repo.record_audit(
        session,
        actor_email=actor_email,
        action="kudos.map_name",
        entity="kudos_aliases",
        entity_id=key,
        after={"person": person.email if person else None, "ignored": person is None},
    )
    return alias
