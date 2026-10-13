"""Seed the kudos name mappings (R-KUD-13).

Seventeen names in the sheet map to a person, nine map to nobody. Every one of
them is a decision a human made — five people have left, four are staff who do
not invoice here, and ``Toni Grant`` is ``Mitchell Grant`` — so they are seeded
from a file that can be read and argued with rather than inserted from
somewhere in the code.

Dry run by default. Same shape as the invoice seed: print what would happen,
let somebody read it, then ``--commit``.

    uv run python -m app.scripts.seed_kudos_aliases --file data/kudos_aliases.csv
    uv run python -m app.scripts.seed_kudos_aliases --file ... --commit

The file is three columns: the name exactly as the sheet spells it, the email
it belongs to (blank to ignore the name), and why.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import KudosAlias, KudosEvent, Person
from app.db.session import get_sessionmaker
from app.domain.kudos import normalise_name


@dataclass(frozen=True, slots=True)
class Row:
    """One line of the alias file: a sheet name, the email it belongs to (blank to
    ignore), and why.
    """

    sheet_name: str
    email: str
    note: str

    @property
    def ignored(self) -> bool:
        """A name with no email is a decision to pay nobody for it, not a gap."""
        return not self.email


def read_file(path: Path) -> list[Row]:
    """Read the alias CSV, lower-casing emails and skipping lines with no sheet name."""
    rows: list[Row] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for record in csv.DictReader(handle):
            name = (record.get("sheet_name") or "").strip()
            if not name:
                continue
            rows.append(
                Row(
                    sheet_name=name,
                    email=(record.get("email") or "").strip().lower(),
                    note=(record.get("note") or "").strip(),
                )
            )
    return rows


def plan(session: Session, rows: list[Row]) -> tuple[list[str], list[str]]:
    """What would happen, and what is wrong with it."""
    people = {
        email.lower(): person_id
        for person_id, email in session.execute(select(Person.id, Person.email)).all()
    }
    existing = {
        alias.normalised_name for alias in session.scalars(select(KudosAlias)).all()
    }
    counts: dict[str, int] = {}
    for name, count in session.execute(
        select(KudosEvent.receiver_name, KudosEvent.id)
    ).all():
        del count
        counts[normalise_name(name)] = counts.get(normalise_name(name), 0) + 1

    actions: list[str] = []
    problems: list[str] = []
    seen: set[str] = set()

    for row in rows:
        key = normalise_name(row.sheet_name)
        if key in seen:
            problems.append(f"{row.sheet_name}: listed twice")
            continue
        seen.add(key)

        held = counts.get(key, 0)
        held_note = f", holding {held} kudos" if held else ""

        if key in existing:
            actions.append(f"  skip    {row.sheet_name} (already mapped)")
            continue
        if row.ignored:
            actions.append(f"  ignore  {row.sheet_name}{held_note} — {row.note}")
            continue
        if row.email not in people:
            # Never invent a person from a spreadsheet. If the email is not
            # already in the database, somebody has mistyped it or the person
            # has not been seeded, and both need a human.
            problems.append(f"{row.sheet_name}: no person with email {row.email}")
            continue
        actions.append(f"  map     {row.sheet_name}{held_note} -> {row.email}")

    return actions, problems


def apply(session: Session, rows: list[Row], *, actor: str) -> int:
    """Write the mappings and attach kudos already in the ledger to their person.

    Names that are already mapped are left as they were, and only kudos
    that nobody owns and no invoice has claimed are attached. Returns how
    many aliases were written.
    """
    people = {
        email.lower(): person_id
        for person_id, email in session.execute(select(Person.id, Person.email)).all()
    }
    existing = {
        alias.normalised_name for alias in session.scalars(select(KudosAlias)).all()
    }

    written = 0
    for row in rows:
        key = normalise_name(row.sheet_name)
        if key in existing:
            continue
        person_id = people.get(row.email) if not row.ignored else None
        if not row.ignored and person_id is None:
            continue
        session.add(
            KudosAlias(
                normalised_name=key,
                display_name=row.sheet_name,
                person_id=person_id,
                ignored=row.ignored,
                note=row.note or None,
                confirmed_by=actor,
            )
        )
        # An event that arrived before anybody had confirmed who the name
        # belonged to is resolved now. Only unclaimed ones: an issued invoice
        # is immutable, and moving a billed kudo to another person would be a
        # silent correction to money (CLAUDE.md §2.3).
        if person_id is not None:
            for event in session.scalars(
                select(KudosEvent).where(
                    KudosEvent.person_id.is_(None),
                    KudosEvent.claimed_by_invoice_id.is_(None),
                )
            ).all():
                if normalise_name(event.receiver_name) == key:
                    event.person_id = person_id
        written += 1
    return written


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point. Refuses to commit while any row has a problem."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--actor", default="")
    args = parser.parse_args(argv)

    if not args.file.exists():
        print(f"no such file: {args.file}", file=sys.stderr)
        return 2

    rows = read_file(args.file)
    factory = get_sessionmaker()
    settings = get_settings()
    if settings.is_production and not args.actor:
        print("--actor is required in production", file=sys.stderr)
        return 2
    actor = args.actor or "seed"

    with factory() as session:
        actions, problems = plan(session, rows)

        print(f"{len(rows)} rows read from {args.file}\n")
        for line in actions:
            print(line)
        if problems:
            print("\nProblems:")
            for line in problems:
                print(f"  ! {line}")

        if not args.commit:
            print("\nDry run. Nothing written. Re-run with --commit to apply.")
            return 1 if problems else 0

        if problems:
            # A partial mapping is worse than none: the unmapped names go on
            # reporting and somebody has to do this twice.
            print("\nRefusing to commit with problems outstanding.", file=sys.stderr)
            return 2

        written = apply(session, rows, actor=actor)
        session.commit()
        print(f"\nWrote {written} aliases.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
