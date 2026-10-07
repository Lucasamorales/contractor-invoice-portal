"""Flags: accounts asking a person to check something of theirs (R-BANK-08).

Two things can be flagged: a person's bank details, and their letterhead (the
five lines printed at the top of their invoice). A flag is a request, never an
edit. It changes nothing about what is payable or what prints.

A flag is stored as an audit row and nothing else, and so is its answer, so no
table records a flag's state. Its status is read back from what happened after
it:

- **changed** — the person replaced the flagged bank account, or saved their
  letterhead;
- **correct** — the person pressed "It is correct", or confirmed the flagged
  bank account itself;
- **open** — neither has happened yet.

Whichever came first after the flag decides it. A new flag starts again.

Every one of those is read from an audit row, never from a timestamp the
application wrote. Audit rows are stamped by the database, so the flag and what
ended it are always measured on the same clock; comparing the database's clock
with the application's would let a few seconds of drift between two machines
reopen a flag somebody had just answered.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from modules.invoices.db import repo
from modules.invoices.db.models import AuditLog, Person

BANK = "bank"
LETTERHEAD = "letterhead"

FLAG_ACTIONS = {BANK: "bank.flag", LETTERHEAD: "letterhead.flag"}
ANSWER_ACTIONS = {BANK: "bank.flag_answered", LETTERHEAD: "letterhead.flag_answered"}
# Written by save_details whenever somebody saves their letterhead.
LETTERHEAD_UPDATE = "letterhead.update"

OUTBOX_LETTERHEAD_FLAGGED = "letterhead_flagged"
OUTBOX_FLAG_ANSWERED = "flag_answered"

OPEN = "open"
CHANGED = "changed"
CORRECT = "correct"

# What the thing is called in a sentence about it.
WHAT = {BANK: "bank details", LETTERHEAD: "letterhead"}

# Eight digits in a row, once spaces and dashes are taken out: a UK account
# number, or the digits of an IBAN. CLAUDE.md §2.8 keeps a full account number
# out of every email, and the reason is emailed exactly as typed.
_ACCOUNT_NUMBER = re.compile(r"\d{8,}")


class FlagError(ValueError):
    """A flag or an answer that cannot be recorded, with a message to show."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class Flag:
    kind: str
    person_id: uuid.UUID
    target_id: str  # the bank row for a bank flag, the person for a letterhead one
    reason: str
    raised_at: datetime
    raised_by: str
    status: str
    closed_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        return self.status == OPEN


def check_reason(reason: str, *, kind: str) -> str:
    """The reason as it will be stored and emailed, or a refusal."""
    text = reason.strip()
    if not text:
        raise FlagError(
            "Say what needs fixing, otherwise the email gives them nothing to act on."
        )
    # Only for bank flags: a letterhead flag may well be about a phone number,
    # which is theirs to see and is no secret.
    if kind == BANK and _ACCOUNT_NUMBER.search(re.sub(r"[\s-]", "", text)):
        raise FlagError(
            "Leave out the full account number. The last four digits are enough."
        )
    return text


def recipients(person: Person) -> tuple[str, ...]:
    """Where a flag is emailed: always the address they sign in with.

    The sign-in address is the one Google has verified as theirs, so the flag
    reaches the right person even when the thing flagged is their contact
    email. Their contact email gets it too when it is a different address.
    """
    seen: list[str] = []
    for address in (person.email, person.contact_email):
        text = (address or "").strip()
        if text and text.lower() not in (s.lower() for s in seen):
            seen.append(text)
    return tuple(seen)


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def _audit(
    session: Session, actions: tuple[str, ...], entity_ids: list[str]
) -> list[AuditLog]:
    """Audit rows of these kinds on these entities, newest first."""
    if not entity_ids:
        return []
    return list(
        session.scalars(
            select(AuditLog)
            .where(AuditLog.action.in_(actions), AuditLog.entity_id.in_(entity_ids))
            .order_by(AuditLog.at.desc())
        )
    )


def latest(session: Session, person: Person, kind: str) -> Flag | None:
    """The most recent flag of one kind on this person, with its status."""
    if kind == BANK:
        entity_ids = [str(row.id) for row in repo.bank_history(session, person.id)]
    else:
        entity_ids = [str(person.id)]

    raised = _audit(session, (FLAG_ACTIONS[kind],), entity_ids)
    if not raised:
        return None
    entry = raised[0]

    # The first thing the person did about it, oldest first among what came
    # after the flag.
    ending: tuple[datetime, str] | None = None
    for row in reversed(_audit(session, _ENDING_ACTIONS[kind], entity_ids)):
        if row.at <= entry.at:
            continue
        status = _ending(kind, row, flagged_id=entry.entity_id)
        if status is not None:
            ending = (row.at, status)
            break

    return Flag(
        kind=kind,
        person_id=person.id,
        target_id=entry.entity_id,
        reason=(entry.after or {}).get("reason", ""),
        raised_at=entry.at,
        raised_by=entry.actor_email,
        status=ending[1] if ending else OPEN,
        closed_at=ending[0] if ending else None,
    )


_ENDING_ACTIONS = {
    BANK: (ANSWER_ACTIONS[BANK], "bank.confirm", "bank.change"),
    LETTERHEAD: (ANSWER_ACTIONS[LETTERHEAD], LETTERHEAD_UPDATE),
}


def _ending(kind: str, row: AuditLog, *, flagged_id: str) -> str | None:
    """What one later audit row means for the flag, if anything.

    For bank details: answering it, or confirming the flagged account itself,
    says it is right; confirming any other account, or typing a new one,
    replaced it. For a letterhead: answering it, or saving the letterhead.
    """
    if row.action == ANSWER_ACTIONS[kind]:
        return CORRECT
    if row.action == "bank.confirm":
        return CORRECT if row.entity_id == flagged_id else CHANGED
    if row.action in ("bank.change", LETTERHEAD_UPDATE):
        return CHANGED
    return None


def open_flags(session: Session, person: Person) -> dict[str, Flag]:
    """This person's open flags, by kind. Empty when there is nothing to answer."""
    found: dict[str, Flag] = {}
    for kind in (LETTERHEAD, BANK):
        flag = latest(session, person, kind)
        if flag is not None and flag.is_open:
            found[kind] = flag
    return found


def open_flags_for_everyone(session: Session) -> list[tuple[Person, Flag]]:
    """Every open flag, oldest first, for the queue."""
    waiting = [
        (person, flag)
        for person in repo.all_people(session)
        for flag in open_flags(session, person).values()
    ]
    return sorted(waiting, key=lambda pair: pair[1].raised_at)


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def flag_letterhead(
    session: Session, *, person: Person, reason: str, actor_email: str
) -> Flag | None:
    """Accounts ask a person to check their letterhead. Changes nothing on it."""
    text = check_reason(reason, kind=LETTERHEAD)
    repo.record_audit(
        session,
        actor_email=actor_email,
        action=FLAG_ACTIONS[LETTERHEAD],
        entity="people",
        entity_id=str(person.id),
        after={"reason": text},
    )
    session.add(
        repo.queue_email(
            kind=OUTBOX_LETTERHEAD_FLAGGED,
            payload={
                "person_id": str(person.id),
                "to_person": list(recipients(person)),
                "reason": text,
                "flagged_by": actor_email,
                "subject": "Your letterhead needs a look",
            },
        )
    )
    session.flush()
    return latest(session, person, LETTERHEAD)


def answer_correct(
    session: Session,
    *,
    person: Person,
    kind: str,
    actor_email: str,
    finance_inbox: str,
) -> Flag | None:
    """The person says the flagged thing is already right.

    Only their own open flag can be answered, and only by them: the person comes
    from the verified sign-in, never from the request (R-SEC-06). Nothing open
    means nothing to do, which is not an error — a second press, or a flag that
    was closed in another tab.
    """
    if actor_email.strip().lower() != person.email.strip().lower():
        raise FlagError("Only the person it is about can answer a flag.")

    flag = latest(session, person, kind)
    if flag is None or not flag.is_open:
        return None

    repo.record_audit(
        session,
        actor_email=actor_email,
        action=ANSWER_ACTIONS[kind],
        entity="bank_details" if kind == BANK else "people",
        entity_id=flag.target_id,
        after={"answer": CORRECT},
    )
    what = WHAT[kind]
    verb = "is" if kind == LETTERHEAD else "are"
    session.add(
        repo.queue_email(
            kind=OUTBOX_FLAG_ANSWERED,
            payload={
                "person_id": str(person.id),
                "to_finance": finance_inbox,
                "person_name": person.display_name,
                "what": what,
                "reason": flag.reason,
                "subject": f"{person.display_name} says their {what} {verb} correct",
            },
        )
    )
    return flag
