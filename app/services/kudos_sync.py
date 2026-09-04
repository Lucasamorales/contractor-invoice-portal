"""Reading the kudos sheet (R-KUD-01 .. R-KUD-03, R-KUD-07 .. R-KUD-16).

One GET against the Sheets values API, authenticated with Cloud Run's attached
identity. No key file (CLAUDE.md §2.7) and no new dependency: ``google-auth``
and ``httpx`` are already here for other reasons, and the official client
library is about fifty packages to avoid thirty lines.

**Access is enforced by sharing, not by scope.** There is no "one file" OAuth
scope — ``spreadsheets.readonly`` is account-wide. What confines this to one
spreadsheet is that the service account can only see what has been shared with
it. So the sheet is shared read-only with that account and no Drive scope is
ever requested. The code cannot enforce that, which is exactly why it is
written down here and in KUDOS.md.

The portal never writes to the spreadsheet. The Google Chat announcer owns it,
and two systems writing to one sheet is how you get a bad afternoon.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import KudosAlias, KudosEvent, KudosSnapshot, Person
from app.domain.kudos import ParseResult, parse_rows
from app.domain.period import Period

logger = logging.getLogger(__name__)

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"

# UNFORMATTED_VALUE with SERIAL_NUMBER, so a date arrives as a number rather
# than a rendered string. "09/01/2026" is the 9th of January here and the 1st
# of September in an American locale, and the log contains both months.
VALUE_PARAMS = {
    "valueRenderOption": "UNFORMATTED_VALUE",
    "dateTimeRenderOption": "SERIAL_NUMBER",
    "majorDimension": "ROWS",
}


class KudosSyncError(RuntimeError):
    """The sheet could not be read.

    R-KUD-06: the wizard shows kudos as unavailable and says so. It must never
    quietly become zero.
    """


@dataclass
class SyncReport:
    """What a sync did, in terms a person can act on."""

    rows_read: int = 0
    events_new: int = 0
    events_seen: int = 0
    withdrawn: int = 0
    unmatched: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()
    changed_after_claiming: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "job": "sync-kudos",
            "rows_read": self.rows_read,
            "events_new": self.events_new,
            "events_seen": self.events_seen,
            "withdrawn": self.withdrawn,
            "unmatched": list(self.unmatched),
            "problems": list(self.problems),
            "changed_after_claiming": self.changed_after_claiming,
        }


def fetch_rows(settings: Settings, *, timeout: float = 20.0) -> list[list[object]]:
    """The raw values of the configured range.

    Raises ``KudosSyncError`` for anything that goes wrong, with a message that
    says which of the three separate things is missing — the file id, the
    sharing, or the API — because the raw 403 mentions none of them.
    """
    if not settings.kudos_sheet_id:
        raise KudosSyncError("no kudos sheet configured")

    # Imported here so the domain tests, the seed and the rest of the app do
    # not need Google's auth stack merely to import this module.
    import google.auth
    from google.auth import exceptions as google_exceptions
    from google.auth.transport.requests import Request

    try:
        credentials, _ = google.auth.default(scopes=[READONLY_SCOPE])
        credentials.refresh(Request())
    except google_exceptions.GoogleAuthError as exc:
        raise KudosSyncError(f"could not authenticate to Google: {exc}") from exc

    url = f"{SHEETS_API}/{settings.kudos_sheet_id}/values/{settings.kudos_sheet_range}"
    try:
        response = httpx.get(
            url,
            params=VALUE_PARAMS,
            headers={"Authorization": f"Bearer {credentials.token}"},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise KudosSyncError(f"could not reach the sheet: {exc}") from exc

    if response.status_code == 403:
        raise KudosSyncError(
            "the sheet refused us: share it read-only with the service account, "
            "and check sheets.googleapis.com is enabled on the project"
        )
    if response.status_code == 404:
        raise KudosSyncError("no sheet with that file id, or no such tab")
    if response.status_code != 200:
        raise KudosSyncError(f"the sheet returned {response.status_code}")

    payload = response.json()
    return list(payload.get("values") or [])


def read_aliases(session: Session) -> tuple[dict[str, str], list[str]]:
    """The confirmed name mappings: emails to resolve to, names to ignore."""
    aliases: dict[str, str] = {}
    ignored: list[str] = []
    rows = session.execute(
        select(KudosAlias, Person.email).outerjoin(
            Person, KudosAlias.person_id == Person.id
        )
    ).all()
    for alias, email in rows:
        if alias.ignored or email is None:
            ignored.append(alias.normalised_name)
        else:
            aliases[alias.normalised_name] = email
    return aliases, ignored


def parse(session: Session, rows: Sequence[Sequence[object]]) -> ParseResult:
    aliases, ignored = read_aliases(session)
    return parse_rows(rows, aliases=aliases, ignored=ignored)


def store(
    session: Session,
    result: ParseResult,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> SyncReport:
    """Write the events. Idempotent: re-running changes nothing.

    Does not commit. The caller owns the transaction.
    """
    now = now or datetime.now(UTC)
    report = SyncReport(
        unmatched=result.unmatched_names,
        problems=tuple(f"row {p.row_number}: {p.detail}" for p in result.problems),
    )

    people = {
        email: person_id
        for person_id, email in session.execute(select(Person.id, Person.email)).all()
    }
    existing = {
        event.event_key: event for event in session.scalars(select(KudosEvent)).all()
    }

    seen_keys: set[str] = set()
    for event in result.events:
        seen_keys.add(event.event_key)
        person_id = people.get(event.email) if event.email else None
        row = existing.get(event.event_key)

        if row is None:
            report.events_new += 1
            session.add(
                KudosEvent(
                    event_key=event.event_key,
                    person_id=person_id,
                    receiver_name=event.receiver_name,
                    giver_name=event.giver_name,
                    value_text=event.value_text or None,
                    awarded_on=event.awarded_on,
                    # R-KUD-12. Before the cut-over it is history: counted for
                    # the standings, never billed.
                    claimable=event.awarded_on >= settings.kudos_claimable_from,
                    first_seen_at=now,
                )
            )
            continue

        report.events_seen += 1
        # A name that has since been mapped resolves an event that arrived
        # before anybody had confirmed who it belonged to.
        if row.claimed_by_invoice_id is None and row.person_id != person_id:
            row.person_id = person_id
        # It came back. Somebody restored a row they had deleted.
        if row.withdrawn_at is not None:
            row.withdrawn_at = None

    # R-KUD-15. A row that has left the sheet is marked, never deleted.
    for key, row in existing.items():
        if key in seen_keys or row.withdrawn_at is not None:
            continue
        row.withdrawn_at = now
        report.withdrawn += 1
        if row.claimed_by_invoice_id is not None:
            # An issued invoice is immutable (CLAUDE.md §2.3), so this cannot
            # be corrected by adjusting anything: it is a void and reissue, and
            # a person has to decide. Say so loudly rather than tidying it away.
            report.changed_after_claiming.append(row.event_key)
            logger.error(
                "kudos event %s was billed on invoice %s and has left the sheet",
                row.event_key,
                row.claimed_by_invoice_id,
            )

    session.flush()
    _write_snapshots(session, settings=settings, now=now)

    report.rows_read = len(result.events) + len(result.problems)
    return report


def _write_snapshots(session: Session, *, settings: Settings, now: datetime) -> None:
    """One snapshot per person for this period (R-DATA-04).

    Its job is no longer to be the count — the ledger is the count, because
    only the ledger knows what has already been billed. What the snapshot
    records is that the sheet **was successfully read**, which is the one thing
    the ledger cannot express: a person with no kudos and a person whose kudos
    we could not fetch both have zero unclaimed events, and R-KUD-06 says those
    two must never look the same on an invoice.
    """
    period = Period.containing(now)
    counts = dict(
        session.execute(
            select(KudosEvent.person_id, func.count())
            .where(
                KudosEvent.person_id.is_not(None),
                KudosEvent.claimable.is_(True),
                KudosEvent.claimed_by_invoice_id.is_(None),
                KudosEvent.withdrawn_at.is_(None),
            )
            .group_by(KudosEvent.person_id)
        ).all()
    )
    for (person_id,) in session.execute(select(Person.id)).all():
        snapshot = session.get(KudosSnapshot, (person_id, period.key))
        count = counts.get(person_id, 0)
        if snapshot is None:
            session.add(
                KudosSnapshot(
                    person_id=person_id,
                    period=period.key,
                    kudos_count=count,
                    rate_pence=settings.kudos_rate_pence,
                    synced_at=now,
                )
            )
        else:
            snapshot.kudos_count = count
            snapshot.rate_pence = settings.kudos_rate_pence
            snapshot.synced_at = now


RowSource = Callable[[Settings], Sequence[Sequence[object]]]


def sync(
    session: Session,
    *,
    settings: Settings,
    now: datetime | None = None,
    fetch: RowSource = fetch_rows,
) -> SyncReport:
    """Fetch, parse, store. The whole job (R-KUD-02).

    ``fetch`` is injected so the staging seeder can drive this exact path with
    invented rows. Everything after the HTTP call is then the real thing —
    parsing, matching, the cut-over, the ledger, the snapshots — rather than a
    separate write path that only staging ever runs and nobody ever tests.
    """
    return store(session, parse(session, fetch(settings)), settings=settings, now=now)
