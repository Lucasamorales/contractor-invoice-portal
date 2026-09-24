"""Correct one person's invoice counter (R-NUM-01, R-NUM-05, R-SEED-09).

Why this exists. The counter is seeded from the spreadsheet's ``INVOICE N°``
column, which is the last number that person issued before this system existed
(R-SEED-09). That column is a human-maintained snapshot and can be out of date:
Alexandra's said 34 when she had in fact reached 62, so the portal offered her
35.

The only other way to fix it was to correct the spreadsheet and re-run the
import, which takes the higher number correctly but also sets
``profile_confirmed_at`` to NULL for **everybody** in the file and sends the
whole company back through the details step mid-cycle. This changes one row,
for one person, with a reason, and writes the audit row in the same
transaction.

    uv run python -m app.scripts.set_counter --email someone@example.com \\
        --last-issued 62 --actor accounts@example.com \\
        --reason "the spreadsheet's INVOICE N was out of date"

Dry run by default. It prints what it would change and writes nothing until
``--commit``.

Two refusals, both of them R-NUM-05. A counter only ever moves **forward**, so a
correction that would wind it back is refused; and a number this person has
already used — issued, drafted or voided — can never be handed out again, so a
correction that would collide with one is refused too. Neither is overridable
by a flag: winding back is how two invoices end up sharing a number, and that
is the one thing the numbering rules exist to prevent.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import repo
from app.db.models import Counter, Invoice, Person
from app.db.session import get_sessionmaker
from app.domain.numbering import seed_next_number

ACTION = "counter.correct"
ENTITY = "counter"


class CorrectionRefused(RuntimeError):
    """The correction would break a numbering rule, so nothing was written."""


@dataclass(frozen=True, slots=True)
class Correction:
    """What the correction did, or would do."""

    person: Person
    before: int | None
    after: int
    highest_used: int | None

    @property
    def created(self) -> bool:
        """True when this person had no counter row yet."""
        return self.before is None


def correct(
    session: Session,
    *,
    email: str,
    last_issued: int,
    actor: str,
    reason: str,
) -> Correction:
    """Move one person's counter to follow ``last_issued``.

    The caller passes the last number the person actually issued, in the same
    terms as the spreadsheet column that got it wrong, so there is one less
    off-by-one to make by hand.
    """
    person = repo.get_person_by_email(session, email)
    if person is None:
        raise CorrectionRefused(f"no person with email {email}")

    after = seed_next_number(last_issued)

    # Every number this person has ever been given, whatever became of it. A
    # voided invoice keeps its number (R-NUM-05) and a draft is holding one,
    # so both count.
    highest_used = session.scalar(
        select(func.max(Invoice.number)).where(Invoice.person_id == person.id)
    )
    if highest_used is not None and after <= highest_used:
        raise CorrectionRefused(
            f"{person.email} has already used invoice number {highest_used}; "
            f"setting the next one to {after} would reissue a number that is "
            f"already spent (R-NUM-05)"
        )

    counter = repo.lock_counter(session, person.id)
    before = counter.next_number if counter is not None else None
    if before is not None and after <= before:
        raise CorrectionRefused(
            f"{person.email}'s next number is already {before}; a counter only "
            f"ever moves forward (R-NUM-05), so it cannot be set to {after}"
        )

    if counter is None:
        session.add(Counter(person_id=person.id, next_number=after))
    else:
        counter.next_number = after

    repo.record_audit(
        session,
        actor_email=actor,
        action=ACTION,
        entity=ENTITY,
        entity_id=str(person.id),
        before={"next_number": before},
        after={"next_number": after, "last_issued": last_issued, "reason": reason},
    )
    return Correction(
        person=person, before=before, after=after, highest_used=highest_used
    )


def describe(correction: Correction) -> str:
    """One block a human can check before it is written."""
    person = correction.person
    was = "none" if correction.before is None else str(correction.before)
    used = "none" if correction.highest_used is None else str(correction.highest_used)
    return "\n".join(
        (
            f"  person        {person.display_name} <{person.email}>",
            f"  next number   {was} -> {correction.after}",
            f"  highest used  {used}",
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="set_counter",
        description="Correct one person's invoice counter.",
    )
    parser.add_argument("--email", required=True, help="whose counter to correct")
    parser.add_argument(
        "--last-issued",
        required=True,
        type=int,
        help="the last invoice number this person actually issued",
    )
    parser.add_argument(
        "--actor", required=True, help="who authorised this, for the audit row"
    )
    parser.add_argument(
        "--reason", required=True, help="why, in a sentence, for the audit row"
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="write to the database; without this nothing is written",
    )
    args = parser.parse_args(argv)

    if "@" not in args.actor:
        raise SystemExit("--actor must be an email address: it is the audit trail")
    if not args.reason.strip():
        raise SystemExit("--reason may not be empty")

    from app.config import get_settings

    print(f"environment: {get_settings().environment.value}")

    factory = get_sessionmaker()
    with factory() as session, session.begin():
        try:
            correction = correct(
                session,
                email=args.email,
                last_issued=args.last_issued,
                actor=args.actor,
                reason=args.reason,
            )
        except CorrectionRefused as refused:
            print(f"Refused: {refused}")
            session.rollback()
            return 2

        print(describe(correction))
        if not args.commit:
            # Everything above ran against the real row, so the report is
            # accurate; rolling back is what makes it a dry run.
            session.rollback()
            print("\nNothing was written. Re-run with --commit to apply.")
            return 0

    print("\nWritten, with an audit row.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
