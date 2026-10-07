"""Invented kudos for staging, shaped exactly like the real sheet.

    uv run python -m app.scripts.seed_demo_kudos              # dry run
    uv run python -m app.scripts.seed_demo_kudos --commit

**Every name and kudo here is made up.** R-ENV-02 is absolute: production data
never reaches staging. What this copies from the live sheet is its *structure*
and its awkwardness, never its content.

The point is to prove the pipeline, so the rows go in as the Sheets API would
hand them over — six columns, dates as serial numbers — and then through
``kudos_sync.sync`` itself. Everything after the HTTP call is the real code:
the same parser, the same matching, the same cut-over, the same ledger, the
same snapshots. Only the fetch is swapped, so this cannot pass while the thing
it is standing in for is broken.

The data deliberately contains every case that matters:

* a multi-value kudo, which is **one** kudo and not four (R-KUD-08)
* kudos before the cut-over, which count for the standings and are never
  billable (R-KUD-12)
* a leadership member who receives kudos and cannot cash them (R-KUD-20)
* two names nobody has mapped, so ``/admin/queue`` has real work on it
* a clear first place and a **tie for second**, so the award button matters
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from modules.invoices.config import Environment, Settings, get_settings
from modules.invoices.db.models import KudosAlias, KudosEvent, Person
from modules.invoices.db.session import get_sessionmaker
from modules.invoices.domain.kudos import SHEETS_EPOCH, normalise_name, parse_rows
from modules.invoices.services import kudos_sync

DEMO_DOMAIN = "@example.com"


class DemoRefused(RuntimeError):
    """Refusing to touch something that might be real."""


# The company values, as the real sheet spells them.
OWN = "Own it"
BEYOND = "Go beyond"
CURIOUS = "Be curious"
SOLUTION = "Be the solution"
BIG = "Think big"

# Sheet name -> the email it belongs to. ``None`` is left unmapped on purpose,
# so the admin screen has something to do and the ignore path can be exercised.
ALIASES: dict[str, str | None] = {
    "Amy Demo": f"demo.amy{DEMO_DOMAIN}",
    "Ben Demo": f"demo.ben{DEMO_DOMAIN}",
    "Carla Demo": f"demo.carla{DEMO_DOMAIN}",
    "Dan Demo": f"demo.dan{DEMO_DOMAIN}",
    "Eve Demo": f"demo.eve{DEMO_DOMAIN}",
    "Femi Demo": f"demo.femi{DEMO_DOMAIN}",
    # The portal knows this person by their login, and the sheet by their name.
    # That mismatch is the normal case, not the exception.
    "Leo Marsh": f"leo{DEMO_DOMAIN}",
    "Priya Nobody": None,
    "Departed Demo": None,
}

# Whose kudos are counted and displayed but never payable (R-KUD-20).
LEADERSHIP = (f"demo.eve{DEMO_DOMAIN}",)

# (day, giver, value, receiver). June is before the 1 July cut-over.
KUDOS = [
    # --- June 2026: history. Counted for Q2, never billable. ---
    (date(2026, 6, 3), "Riley Demo", OWN, "Femi Demo"),
    (date(2026, 6, 17), "Toni Demo", BEYOND, "Femi Demo"),
    (date(2026, 6, 22), "Riley Demo", OWN, "Amy Demo"),
    # --- July ---
    (date(2026, 7, 2), "Riley Demo", OWN, "Amy Demo"),
    (date(2026, 7, 9), "Ben Demo", BEYOND, "Amy Demo"),
    (date(2026, 7, 15), "Toni Demo", CURIOUS, "Ben Demo"),
    (date(2026, 7, 21), "Amy Demo", OWN, "Carla Demo"),
    (date(2026, 7, 28), "Riley Demo", SOLUTION, "Priya Nobody"),
    (date(2026, 8, 4), "Carla Demo", BEYOND, "Leo Marsh"),
    # A multi-value kudo. One kudo, £20, not four (R-KUD-08).
    (date(2026, 8, 11), "Toni Demo", f"{OWN}, {BEYOND}, {CURIOUS}, {BIG}", "Amy Demo"),
    (date(2026, 8, 18), "Riley Demo", OWN, "Eve Demo"),
    (date(2026, 8, 19), "Amy Demo", CURIOUS, "Eve Demo"),
    (date(2026, 8, 25), "Ben Demo", OWN, "Dan Demo"),
    # --- September ---
    (date(2026, 9, 1), "Riley Demo", BEYOND, "Amy Demo"),
    (date(2026, 9, 2), "Toni Demo", OWN, "Ben Demo"),
    (date(2026, 9, 3), "Dan Demo", SOLUTION, "Carla Demo"),
    (date(2026, 9, 3), "Riley Demo", OWN, "Leo Marsh"),
    (date(2026, 9, 4), "Amy Demo", CURIOUS, "Eve Demo"),
    (date(2026, 9, 4), "Toni Demo", BEYOND, "Priya Nobody"),
    (date(2026, 9, 4), "Riley Demo", OWN, "Departed Demo"),
]

HEADER = ["Date", "Giver", "Value", "Receiver", "Space", "Message"]


def as_serial(day: date) -> int:
    """A date as Sheets hands it over with SERIAL_NUMBER rendering."""
    return (day - SHEETS_EPOCH).days


def sheet_rows(settings: Settings) -> list[list[object]]:
    """The invented sheet, in the shape the values API returns."""
    del settings
    rows: list[list[object]] = [HEADER]
    for day, giver, value, receiver in KUDOS:
        rows.append(
            [
                as_serial(day),
                giver,
                value,
                receiver,
                None,
                f"Demo kudos for {receiver}.",
            ]
        )
    return rows


def demo_event_keys(settings: Settings) -> set[str]:
    """Exactly the events this fixture produces, by identity.

    Matched on the event key rather than the receiver's name. The live sheet
    and the fixture share a name — the real Leo Marsh has kudos in both —
    so purging by name would leave four real rows behind and quietly inflate
    one person's count.
    """
    result = parse_rows(sheet_rows(settings), aliases={})
    return {event.event_key for event in result.events}


def purge_foreign(session: Session, settings: Settings) -> tuple[int, list[str]]:
    """Remove kudos that did not come from this fixture.

    Staging was pointed at the real sheet once and the scheduled sync did
    exactly what it was told: it pulled the live log in alongside the invented
    one. Mixed, the figures look plausible and mean nothing, so a clean test
    needs the real rows gone.

    A claimed event is never deleted, whatever it is (R-KUD-15): an issued
    invoice states what it billed, and removing the row behind that destroys
    the record. If one is found this refuses outright rather than deleting the
    rest and leaving a half-purged database.
    """
    keep = demo_event_keys(settings)
    doomed = [
        event
        for event in session.scalars(select(KudosEvent)).all()
        if event.event_key not in keep
    ]
    claimed = [e.receiver_name for e in doomed if e.claimed_by_invoice_id is not None]
    if claimed:
        raise DemoRefused(
            "these have already been billed and must not be deleted: "
            + ", ".join(sorted(set(claimed)))
        )
    names = sorted({event.receiver_name for event in doomed})
    for event in doomed:
        session.delete(event)
    return len(doomed), names


def refuse_production(settings: Settings) -> None:
    if settings.environment is Environment.PRODUCTION:
        raise DemoRefused("This writes invented data. Never in production.")


def refuse_real_kudos(session: Session) -> None:
    """Refuse if the ledger already holds anything not from here.

    A real sync and this seeder must never end up mixed in one database: the
    figures would look plausible and mean nothing.
    """
    known = {normalise_name(name) for name in ALIASES}
    for name in session.scalars(select(KudosEvent.receiver_name).distinct()).all():
        if normalise_name(name) not in known:
            raise DemoRefused(
                f"kudos_events already holds {name!r}, which did not come from "
                "here. Refusing to mix invented kudos with real ones."
            )


def seed_aliases(session: Session, *, actor: str) -> int:
    written = 0
    people = {
        email.lower(): person_id
        for person_id, email in session.execute(select(Person.id, Person.email)).all()
    }
    for sheet_name, email in ALIASES.items():
        if email is None:
            # Left unmapped deliberately: the queue needs something on it.
            continue
        key = normalise_name(sheet_name)
        if session.scalar(select(KudosAlias).where(KudosAlias.normalised_name == key)):
            continue
        person_id = people.get(email)
        if person_id is None:
            continue
        session.add(
            KudosAlias(
                normalised_name=key,
                display_name=sheet_name,
                person_id=person_id,
                ignored=False,
                note="demo",
                confirmed_by=actor,
            )
        )
        written += 1
    return written


def mark_leadership(session: Session) -> int:
    changed = 0
    for email in LEADERSHIP:
        person = session.scalar(select(Person).where(Person.email == email))
        if person is not None and person.kudos_eligible:
            person.kudos_eligible = False
            changed += 1
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--actor", default="seed-demo")
    parser.add_argument(
        "--purge-foreign",
        action="store_true",
        help="delete kudos that did not come from this fixture, then reseed",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    try:
        refuse_production(settings)
    except DemoRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2

    now = datetime.now(UTC)
    factory = get_sessionmaker()
    with factory() as session:
        purged, purged_names = 0, []
        if args.purge_foreign:
            try:
                purged, purged_names = purge_foreign(session, settings)
            except DemoRefused as exc:
                print(str(exc), file=sys.stderr)
                return 2
            session.flush()
        else:
            try:
                refuse_real_kudos(session)
            except DemoRefused as exc:
                print(str(exc), file=sys.stderr)
                return 2

        people = session.scalar(select(func.count()).select_from(Person)) or 0
        print(f"{people} people in this database, {len(KUDOS)} invented kudos to load.")
        print(
            f"cut-over: nothing before {settings.kudos_claimable_from} is billable.\n"
        )

        aliases = seed_aliases(session, actor=args.actor)
        leaders = mark_leadership(session)
        session.flush()

        # The real sync, with the rows injected instead of fetched. Parsing,
        # matching, the cut-over and the snapshots are all the shipping code.
        report = kudos_sync.sync(session, settings=settings, now=now, fetch=sheet_rows)

        if args.purge_foreign:
            print(f"  purged foreign       {purged} kudos")
            for name in purged_names:
                print(f"                       {name}")
        print(f"  aliases written      {aliases}")
        print(f"  marked leadership    {leaders}")
        print(f"  events new           {report.events_new}")
        print(f"  events already known {report.events_seen}")
        print(f"  unreadable rows      {len(report.problems)}")
        print(f"  unmapped names       {list(report.unmatched)}")

        if not args.commit:
            session.rollback()
            print("\nDry run. Nothing written. Re-run with --commit to apply.")
            return 0

        session.commit()
        print("\nWritten.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
