"""Move somebody between payout regions (R-SEED-03, R-BANK-02, R-BANK-05).

The case this was written for: somebody paid into an international account
moves to a UK one. The region is set once at import from the spreadsheet's
``NOTES`` column and nothing in the app can change it afterwards, so she was
stuck — the bank screen renders IBAN and Swift/BIC for an international payee
and the validation demands them, which leaves no way to type a sort code.

**The region and the old bank row have to move together**, which is why this
does both in one transaction rather than leaving the second half to somebody's
memory. Set to UK with the international row still active, her region says one
thing and her only account says another: nothing blocks her, she submits, and
the invoice renders the UK block with no sort code and throws. The number is
allocated before the PDF is rendered (R-SUB-03, R-SUB-04), so that failure
leaves a draft holding a number and no document — the exact mess this system
exists to avoid.

Retiring the row instead lands her on a path the app already knows: no active
account means R-BANK-02 blocks the invoice with "Confirm your bank details
first", and she enters the new account through the same screen everybody uses
on their first day.

    uv run python -m app.scripts.set_payout_region \\
        --email someone@example.com \\
        --region UK --actor accounts@example.com \\
        --reason "moving from an EUR account to a UK one"

Dry run by default; nothing is written until ``--commit``.

What this does **not** do is make anything payable. It retires an account and
changes which fields are asked for. Only the owner can enter a new account and
confirm it (CLAUDE.md §2.2), and that is unchanged.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from modules.invoices.db import repo
from modules.invoices.db.models import (
    BankDetail,
    BankState,
    Invoice,
    InvoiceStatus,
    PayoutRegion,
    Person,
)
from modules.invoices.db.session import get_sessionmaker

REGION_ACTION = "person.payout_region"
BANK_ACTION = "bank.retire"


class RegionChangeRefused(RuntimeError):
    """The change would leave something inconsistent, so nothing was written."""


@dataclass(frozen=True, slots=True)
class RegionChange:
    """What the change did, or would do."""

    person: Person
    before: PayoutRegion | None
    after: PayoutRegion
    retired: BankDetail | None

    @property
    def held(self) -> str:
        """Which fields the retired row carried. Never their values."""
        if self.retired is None:
            return "—"
        names = [
            name
            for name, column in (
                ("account number", self.retired.account_number_enc),
                ("sort code", self.retired.sort_code_enc),
                ("IBAN", self.retired.iban_enc),
                ("Swift/BIC", self.retired.swift_bic_enc),
            )
            if column
        ]
        return ", ".join(names) or "nothing"


def change_region(
    session: Session,
    *,
    email: str,
    region: PayoutRegion,
    actor: str,
    reason: str,
    now: datetime | None = None,
) -> RegionChange:
    """Move one person's payout region and retire their active account."""
    now = now or datetime.now(UTC)

    person = repo.get_person_by_email(session, email)
    if person is None:
        raise RegionChangeRefused(f"no person with email {email}")

    before = person.payout_region
    if before is region:
        raise RegionChangeRefused(f"{person.email} is already {region.value}")

    # A draft holds an invoice number and will be rendered from whatever the
    # region says when it finishes. Changing it underneath is how a number is
    # burned on a document that cannot be produced.
    draft = session.scalar(
        select(Invoice).where(
            Invoice.person_id == person.id,
            Invoice.status == InvoiceStatus.DRAFT,
        )
    )
    if draft is not None:
        raise RegionChangeRefused(
            f"{person.email} has an unfinished invoice ({draft.period}, number "
            f"{draft.number}). Finish or void it before moving the region, or "
            f"it will be rendered against the wrong set of bank fields"
        )

    # A proposal is written against the old region's rules, so it cannot be
    # left standing for the person to confirm after the fields have changed.
    pending = session.scalar(
        select(BankDetail).where(
            BankDetail.person_id == person.id,
            BankDetail.state == BankState.PENDING,
        )
    )
    if pending is not None:
        raise RegionChangeRefused(
            f"{person.email} has a proposed bank change waiting. Resolve it "
            f"before moving the region"
        )

    person.payout_region = region
    repo.record_audit(
        session,
        actor_email=actor,
        action=REGION_ACTION,
        entity="people",
        entity_id=str(person.id),
        before={"payout_region": before.value if before else None},
        after={"payout_region": region.value, "reason": reason},
    )

    active = repo.active_bank_detail(session, person.id)
    if active is not None:
        # R-BANK-05's own close: a row is never edited, only ended.
        active.state = BankState.SUPERSEDED
        active.valid_to = now
        repo.record_audit(
            session,
            actor_email=actor,
            action=BANK_ACTION,
            entity="bank_details",
            entity_id=str(active.id),
            before={"state": BankState.ACTIVE.value},
            after={
                "state": BankState.SUPERSEDED.value,
                "reason": f"payout region moved to {region.value}: {reason}",
            },
        )

    return RegionChange(person=person, before=before, after=region, retired=active)


def describe(change: RegionChange) -> str:
    """One block a human can check before it is written."""
    was = change.before.value if change.before else "not set"
    person = change.person
    lines = [
        f"  person        {person.display_name} <{person.email}>",
        f"  region        {was} -> {change.after.value}",
    ]
    if change.retired is None:
        lines.append("  account       none active; nothing to retire")
    else:
        lines.append(
            f"  account       retiring the {change.retired.source.value} row "
            f"holding {change.held}"
        )
        lines.append(
            "                they cannot invoice until they enter and confirm "
            "the new one"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="set_payout_region",
        description="Move one person between payout regions.",
    )
    parser.add_argument("--email", required=True, help="whose region to change")
    parser.add_argument(
        "--region",
        required=True,
        choices=[region.value for region in PayoutRegion],
        help="the region to move them to",
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

    from modules.invoices.config import get_settings

    print(f"environment: {get_settings().environment.value}")

    factory = get_sessionmaker()
    with factory() as session, session.begin():
        try:
            change = change_region(
                session,
                email=args.email,
                region=PayoutRegion(args.region),
                actor=args.actor,
                reason=args.reason,
            )
        except RegionChangeRefused as refused:
            print(f"Refused: {refused}")
            session.rollback()
            return 2

        print(describe(change))
        if not args.commit:
            session.rollback()
            print("\nNothing was written. Re-run with --commit to apply.")
            return 0

    print("\nWritten, with an audit row for each change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
