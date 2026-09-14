"""The bank-details lifecycle (R-BANK-01 .. R-BANK-08).

States: ``unverified`` -> ``active`` -> (``pending`` -> ``active``), plus
``rejected`` and ``superseded``.

The single rule this module exists to enforce is CLAUDE.md §2.2: **only the
owner can make a bank change payable.** An admin may read, review, flag back
and propose. A proposal lands ``pending`` and stays there until the person
themselves confirms it.

That is the control which means a taken-over admin account cannot redirect the
payroll, so the activation path takes the owner's own identity and compares it
against the row's person before it does anything.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from app.db import repo
from app.db.models import BankDetail, BankSource, BankState, Person
from app.domain.banking import (
    ParsedBank,
    PayoutRegion,
    normalise_account_number,
    parse_bank_fields,
    same_account,
    validate_bank,
)
from app.domain.iban import mask
from app.services.crypto import Cipher, field_aad

ENCRYPTED_FIELDS = ("account_number", "sort_code", "iban", "swift_bic")

OUTBOX_BANK_CHANGED = "bank_changed"
OUTBOX_BANK_PROPOSED = "bank_proposed"
OUTBOX_BANK_FLAGGED = "bank_flagged"


class BankChangeError(Exception):
    """A change was refused, with a message safe to show the person."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.errors = errors or []


@dataclass(slots=True)
class Entry:
    """What somebody typed into the change form."""

    account_name: str = ""
    bank_name: str = ""
    sort_code: str = ""
    account_number: str = ""
    account_number_again: str = ""
    iban: str = ""
    swift_bic: str = ""
    parsed: ParsedBank = field(default_factory=ParsedBank)


def validate_entry(entry: Entry, region: PayoutRegion) -> ParsedBank:
    """Normalise and check typed details (R-BANK-03, R-BANK-04).

    The account number has to be typed twice and match. Double entry catches
    transpositions that no checksum can: 12345678 and 12435678 are both
    perfectly valid account numbers, and only one of them is yours.
    """
    # Compared after normalisation, so spacing alone is not a mismatch.
    typed = normalise_account_number(entry.account_number.strip())
    typed_again = normalise_account_number(entry.account_number_again.strip())
    if (typed or typed_again) and typed != typed_again:
        raise BankChangeError("The two account numbers do not match. Type it again.")

    parsed = parse_bank_fields(
        account_name=entry.account_name,
        bank_name=entry.bank_name,
        sort_code=entry.sort_code,
        account_number=entry.account_number,
        iban=entry.iban,
        swift_bic=entry.swift_bic,
    )
    errors = validate_bank(parsed, region)
    if parsed.unparsed:
        # Each entry is "field_name: value". The value is left out: it is what
        # they just typed, and a message has no business repeating bank digits.
        errors.extend(
            f"We could not read the {line.partition(':')[0].replace('_', ' ')} "
            "you typed."
            for line in parsed.unparsed
        )
    if errors:
        raise BankChangeError("These details are not right yet.", errors)
    return parsed


def _decrypt(detail: BankDetail, cipher: Cipher) -> ParsedBank:
    """The stored row as plaintext, for comparison only. Never returned out."""
    parsed = ParsedBank(account_name=detail.account_name, bank_name=detail.bank_name)
    for name in ENCRYPTED_FIELDS:
        stored = getattr(detail, f"{name}_enc")
        if stored:
            setattr(
                parsed,
                name,
                cipher.decrypt(stored, aad=field_aad(str(detail.person_id), name)),
            )
    return parsed


def _write_row(
    session: Session,
    *,
    person: Person,
    parsed: ParsedBank,
    cipher: Cipher,
    source: BankSource,
    state: BankState,
    now: datetime,
    verified_by: str | None,
) -> BankDetail:
    """Insert a new row. Rows are never edited in place (R-BANK-03)."""
    detail = BankDetail(
        person_id=person.id,
        account_name=parsed.account_name,
        bank_name=parsed.bank_name,
        source=source,
        state=state,
        verified_at=now if state is BankState.ACTIVE else None,
        verified_by=verified_by,
        valid_from=now if state is BankState.ACTIVE else None,
        encryption_key_id=cipher.key_id,
    )
    for name in ENCRYPTED_FIELDS:
        value = getattr(parsed, name)
        if value:
            setattr(
                detail,
                f"{name}_enc",
                cipher.encrypt(value, aad=field_aad(str(person.id), name)),
            )
    session.add(detail)
    session.flush()
    return detail


def _supersede(session: Session, previous: BankDetail | None, *, now: datetime) -> None:
    """Close the row a change replaces (R-BANK-05)."""
    if previous is None:
        return
    previous.state = BankState.SUPERSEDED
    previous.valid_to = now


def _queue_change_emails(
    session: Session,
    *,
    person: Person,
    old: ParsedBank | None,
    new: ParsedBank,
    finance_inbox: str,
) -> None:
    """Tell the person and Robin, showing last four digits only (R-BANK-05).

    Both of them, always. The person because it is their money, and Robin
    because he is the one who would notice a change nobody asked for.
    """
    payload = {
        "person_id": str(person.id),
        "person_name": person.display_name,
        "to_person": person.contact_email or person.email,
        "to_finance": finance_inbox,
        # CLAUDE.md §2.8: never email a full account number.
        "old_account": mask(old.account_number or "") if old else "",
        "new_account": mask(new.account_number or ""),
        "old_iban": mask(old.iban or "") if old else "",
        "new_iban": mask(new.iban or ""),
        "subject": f"Bank details changed — {person.display_name}",
    }
    session.add(repo.queue_email(kind=OUTBOX_BANK_CHANGED, payload=payload))


def change_own_details(
    session: Session,
    *,
    person: Person,
    entry: Entry,
    cipher: Cipher,
    now: datetime,
    actor_email: str,
    finance_inbox: str,
) -> BankDetail:
    """The owner changes their own details (R-BANK-03, R-BANK-05).

    Goes straight to ``active``: the person entering it is the person who has
    to confirm it, and they have just typed the account number twice.
    """
    if actor_email.strip().lower() != person.email.strip().lower():
        # Belt and braces. No route reaches this with a mismatch, and if one
        # ever did it would be the whole control failing at once.
        raise BankChangeError("Only the account holder can change these details.")

    region = (
        PayoutRegion(person.payout_region.value)
        if person.payout_region
        else PayoutRegion.UK
    )
    parsed = validate_entry(entry, region)

    previous = repo.active_bank_detail(session, person.id)
    old_plain = _decrypt(previous, cipher) if previous else None
    if old_plain is not None and same_account(old_plain, parsed):
        raise BankChangeError("Those are the details we already have.")

    _supersede(session, previous, now=now)
    # Flushed before the insert on purpose. SQLAlchemy emits INSERTs ahead of
    # UPDATEs within one flush, so without this the new active row would meet
    # the one-active-row index before the old row had stopped being active.
    session.flush()
    detail = _write_row(
        session,
        person=person,
        parsed=parsed,
        cipher=cipher,
        source=BankSource.SELF,
        state=BankState.ACTIVE,
        now=now,
        verified_by=actor_email,
    )

    repo.record_audit(
        session,
        actor_email=actor_email,
        action="bank.change",
        entity="bank_details",
        entity_id=str(detail.id),
        before={"account": mask(old_plain.account_number or "")} if old_plain else None,
        after={"account": mask(parsed.account_number or ""), "source": "self"},
    )
    _queue_change_emails(
        session,
        person=person,
        old=old_plain,
        new=parsed,
        finance_inbox=finance_inbox,
    )
    return detail


def propose_change(
    session: Session,
    *,
    person: Person,
    entry: Entry,
    cipher: Cipher,
    now: datetime,
    actor_email: str,
) -> BankDetail:
    """An admin proposes a correction (R-BANK-07, R-ACC-07).

    Lands ``pending`` and does not touch the active row. Robin can never make
    this payable himself; the person has to confirm it. This is the control
    that means a taken-over admin account cannot redirect the payroll.
    """
    region = (
        PayoutRegion(person.payout_region.value)
        if person.payout_region
        else PayoutRegion.UK
    )
    parsed = validate_entry(entry, region)

    existing = [
        row
        for row in repo.bank_history(session, person.id)
        if row.state is BankState.PENDING
    ]
    if existing:
        raise BankChangeError(
            "There is already a proposed change waiting for them to confirm."
        )

    detail = _write_row(
        session,
        person=person,
        parsed=parsed,
        cipher=cipher,
        source=BankSource.ADMIN_PROPOSED,
        state=BankState.PENDING,
        now=now,
        verified_by=None,
    )
    repo.record_audit(
        session,
        actor_email=actor_email,
        action="bank.propose",
        entity="bank_details",
        entity_id=str(detail.id),
        after={"account": mask(parsed.account_number or ""), "state": "pending"},
    )
    session.add(
        repo.queue_email(
            kind=OUTBOX_BANK_PROPOSED,
            payload={
                "person_id": str(person.id),
                "to_person": person.contact_email or person.email,
                "proposed_account": mask(parsed.account_number or ""),
                "proposed_by": actor_email,
                "subject": "A change to your bank details needs your approval",
            },
        )
    )
    return detail


def confirm(
    session: Session,
    *,
    person: Person,
    detail_id: uuid.UUID,
    cipher: Cipher,
    now: datetime,
    actor_email: str,
    finance_inbox: str,
) -> BankDetail:
    """The owner confirms a row, making it payable (R-BANK-01, R-BANK-07).

    This is the only function in the application that can put a row into
    ``active``, and it refuses unless the caller is the person the row belongs
    to. Confirming a pending proposal supersedes whatever was active before.
    """
    detail = session.get(BankDetail, detail_id)
    if detail is None or detail.person_id != person.id:
        raise BankChangeError("Those are not your bank details.")
    if actor_email.strip().lower() != person.email.strip().lower():
        raise BankChangeError("Only the account holder can confirm these details.")
    if detail.state is BankState.ACTIVE:
        return detail
    if detail.state in (BankState.SUPERSEDED, BankState.REJECTED):
        raise BankChangeError(
            "Those details have been replaced. Refresh and try again."
        )

    previous = repo.active_bank_detail(session, person.id)
    replacing = previous is not None and previous.id != detail.id
    old_plain = _decrypt(previous, cipher) if replacing else None
    if replacing:
        _supersede(session, previous, now=now)
        session.flush()

    before = {"state": detail.state.value}
    detail.state = BankState.ACTIVE
    detail.verified_at = now
    detail.verified_by = actor_email
    detail.valid_from = now

    repo.record_audit(
        session,
        actor_email=actor_email,
        action="bank.confirm",
        entity="bank_details",
        entity_id=str(detail.id),
        before=before,
        after={"state": "active", "source": detail.source.value},
    )

    if old_plain is not None:
        _queue_change_emails(
            session,
            person=person,
            old=old_plain,
            new=_decrypt(detail, cipher),
            finance_inbox=finance_inbox,
        )
    return detail


def flag_back(
    session: Session,
    *,
    detail_id: uuid.UUID,
    reason: str,
    actor_email: str,
) -> BankDetail:
    """An admin flags a row back to its owner (R-BANK-08).

    Sends the person an email saying what needs fixing. Does not change what is
    payable — flagging is a request, not an edit.
    """
    if not reason.strip():
        raise BankChangeError(
            "Say what needs fixing, otherwise the email gives them nothing to act on."
        )

    detail = session.get(BankDetail, detail_id)
    if detail is None:
        raise BankChangeError("No such bank row.")
    person = session.get(Person, detail.person_id)
    if person is None:
        raise BankChangeError("That row has no person.")

    repo.record_audit(
        session,
        actor_email=actor_email,
        action="bank.flag",
        entity="bank_details",
        entity_id=str(detail.id),
        after={"reason": reason.strip()},
    )
    session.add(
        repo.queue_email(
            kind=OUTBOX_BANK_FLAGGED,
            payload={
                "person_id": str(person.id),
                "to_person": person.contact_email or person.email,
                "reason": reason.strip(),
                "flagged_by": actor_email,
                "subject": "Your bank details need a look",
            },
        )
    )
    return detail
