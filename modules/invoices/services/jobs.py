"""The work behind the scheduled endpoints (R-JOB-01 .. R-JOB-06).

Every job is idempotent: Cloud Scheduler retries, and a double fire must not
double-send. The pattern throughout is to queue mail rather than send it, and
to check for an existing row before queuing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.config import Settings
from modules.invoices.db import repo
from modules.invoices.db.models import Outbox, OutboxStatus, PersonStatus
from modules.invoices.domain.period import (
    WINDOW_CLOSES_DAY,
    WINDOW_OPENS_DAY,
    Period,
    london_date,
)
from modules.invoices.services import admin

logger = logging.getLogger(__name__)

OUTBOX_WINDOW_OPEN = "window_open"
OUTBOX_REMINDER = "reminder"
OUTBOX_SUMMARY = "summary"


@dataclass(slots=True)
class JobReport:
    """What a run did, returned to the caller so Scheduler logs something useful."""

    job: str
    queued: int = 0
    skipped: int = 0
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "job": self.job,
            "queued": self.queued,
            "skipped": self.skipped,
            "detail": self.detail,
        }


def _already_queued(session: Session, kind: str, marker: str) -> bool:
    """Whether this exact message has been queued before.

    The idempotency key for a notification is its kind plus a marker that is
    unique to the thing being notified about — a period and a person. Cheaper
    and clearer than a dedicated table, and it survives a double fire.
    """
    return (
        session.scalar(
            select(func.count())
            .select_from(Outbox)
            .where(
                Outbox.kind == kind,
                Outbox.payload["marker"].astext == marker,
            )
        )
        or 0
    ) > 0


def _queue(
    session: Session, kind: str, marker: str, payload: dict[str, object]
) -> bool:
    """Queue one message unless its marker has been seen. True if queued."""
    if _already_queued(session, kind, marker):
        return False
    session.add(repo.queue_email(kind=kind, payload={**payload, "marker": marker}))
    return True


def remind(
    session: Session, *, settings: Settings, now: datetime | None = None
) -> JobReport:
    """R-JOB-02.

    On opening day everyone active gets the window-open note. On the 27th only
    those with nothing submitted get a nudge — reminding somebody who has
    already done it is how people start ignoring the emails.
    """
    moment = now or datetime.now(UTC)
    period = Period.containing(moment)
    # The window is a question about what day it is where the people are
    # (R-PER-01), so the day is read in London, not UTC.
    day = london_date(moment).day

    if day == WINDOW_OPENS_DAY:
        kind, marker_prefix = OUTBOX_WINDOW_OPEN, "open"
    elif day == WINDOW_CLOSES_DAY - 1:
        kind, marker_prefix = OUTBOX_REMINDER, "nudge"
    else:
        return JobReport(job="remind", detail=f"nothing to send on day {day}")

    submitted = {
        invoice.person_id for invoice in repo.invoices_for_period(session, period.key)
    }
    report = JobReport(job="remind")
    for person in repo.all_people(session):
        if person.status is not PersonStatus.ACTIVE:
            continue
        if kind == OUTBOX_REMINDER and person.id in submitted:
            report.skipped += 1
            continue
        queued = _queue(
            session,
            kind,
            f"{marker_prefix}:{period.key}:{person.id}",
            {
                "to_person": person.contact_email or person.email,
                "period": period.key,
                "period_label": period.label,
                "subject": (
                    f"Your invoice for {period.label}"
                    if kind == OUTBOX_WINDOW_OPEN
                    else f"Your {period.label} invoice is not in yet"
                ),
            },
        )
        report.queued += 1 if queued else 0
        report.skipped += 0 if queued else 1
    return report


def summary(
    session: Session, *, settings: Settings, now: datetime | None = None
) -> JobReport:
    """R-JOB-03. The 29th reconciliation to the finance group."""
    moment = now or datetime.now(UTC)
    period = Period.containing(moment)
    view = admin.period_view(session, period, settings=settings)
    attention = admin.needs_attention(session, now=moment)

    queued = _queue(
        session,
        OUTBOX_SUMMARY,
        f"summary:{period.key}",
        {
            "to_finance": settings.finance_inbox,
            "period": period.key,
            "period_label": period.label,
            "expected": view.expected,
            "received": view.received,
            "missing": [row.person.display_name for row in view.missing],
            "flagged": [
                f"{row.person.display_name}: "
                + "; ".join(flag.detail for flag in row.flags)
                for row in view.flagged
            ],
            # R-JOB-03 reconciles what came in. An invoice that is stuck or
            # whose email never sent looks received from the table alone, so
            # it is called out here or it is never noticed at all.
            "broken": [
                f"invoice {invoice.number} never finished sending "
                f"(stuck since {invoice.created_at:%d %b %H:%M})"
                for invoice in attention.stuck_drafts
            ]
            + [
                f"an email gave up after {row.attempts} attempts: {row.kind}"
                for row in attention.failed_mail
            ],
            "subject": f"Invoices for {period.label}",
        },
    )
    return JobReport(
        job="summary",
        queued=1 if queued else 0,
        skipped=0 if queued else 1,
        detail=f"{view.received} of {view.expected} received",
    )


def retry_failed(session: Session, *, older_than: date | None = None) -> JobReport:
    """Put failed mail back in the queue. Operated by hand, not on a schedule.

    R-JOB-04 gives up after five attempts and alerts. Once whatever broke is
    fixed, this is how the backlog goes out rather than being retyped.
    """
    del older_than
    rows = list(
        session.scalars(select(Outbox).where(Outbox.status == OutboxStatus.FAILED))
    )
    for row in rows:
        row.status = OutboxStatus.PENDING
        row.attempts = 0
        row.next_attempt_at = None
        row.last_error = None
    return JobReport(job="retry-failed", queued=len(rows))
