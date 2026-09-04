"""Mail: the Resend client and the outbox dispatcher (R-SUB-06, R-JOB-04).

Nothing in this application sends mail inline. Every message is written to the
``outbox`` table inside the transaction that caused it, and this dispatcher
sends it afterwards. A provider outage must not lose an invoice Postgres has
already accepted.

The client is a protocol with three implementations: Resend for production,
a sandbox that records and discards for staging (R-ENV-03), and whatever a
test passes in.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import MailMode, Settings, mail_mode
from app.db.models import Outbox, OutboxStatus
from app.services.storage import ObjectStore, StorageError

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"

# Resend caps a whole message, attachments included. Ours are capped lower
# still (R-WIZ-04) so an invoice with ten receipts cannot become an email
# nobody can send.
RESEND_MAX_MESSAGE_BYTES = 40 * 1024 * 1024
# Room for the body, the headers and the JSON around it all.
BODY_ALLOWANCE = 256 * 1024

MAX_ATTEMPTS = 5  # R-JOB-04
BATCH_SIZE = 20

# Doubling, from a minute. Attempt 5 lands about sixteen minutes after the
# first failure, which is long enough to ride out a provider blip and short
# enough that an invoice is not sitting unsent for hours.
BACKOFF_BASE_SECONDS = 60


class MailError(RuntimeError):
    """Sending failed. Retryable unless it says otherwise."""

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


@dataclass(frozen=True, slots=True)
class Attachment:
    name: str
    content: bytes
    content_type: str


@dataclass(frozen=True, slots=True)
class Message:
    to: tuple[str, ...]
    subject: str
    text: str
    attachments: tuple[Attachment, ...] = ()

    def recipients(self) -> str:
        return ", ".join(address for address in self.to if address)


class MailClient(Protocol):
    def send(self, message: Message) -> None: ...


class ResendClient:
    """The real one. Used only in production (R-ENV-03).

    Chosen over Postmark on cost and on attachment size. Sixteen invoices, two
    reminder rounds and a summary come to roughly a hundred messages a month,
    bunched into five days — right on Postmark's free ceiling, and paying for
    it would hand back the same £15 a month we avoided by not putting a load
    balancer in front of Cloud Run (R-ENV-06).
    """

    def __init__(self, token: str, sender: str, timeout: float = 30.0) -> None:
        self._token = token
        self._sender = sender
        # Longer than a plain request: this one can be carrying a PDF and ten
        # receipts, and giving up early would retry the whole upload.
        self._client = httpx.Client(timeout=timeout)

    def send(self, message: Message) -> None:
        payload: dict[str, object] = {
            "from": self._sender,
            "to": [address for address in message.to if address],
            "subject": message.subject,
            "text": message.text,
        }
        if message.attachments:
            payload["attachments"] = [
                {
                    "filename": attachment.name,
                    "content": base64.b64encode(attachment.content).decode("ascii"),
                    "content_type": attachment.content_type,
                }
                for attachment in message.attachments
            ]

        try:
            response = self._client.post(
                RESEND_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise MailError(f"could not reach Resend: {type(exc).__name__}") from exc

        if response.status_code in (200, 201):
            # A 2xx means accepted, not delivered. Logging the provider's id
            # is the only way to answer "you say it sent, I never got it"
            # without going to their dashboard and guessing by timestamp.
            try:
                logger.info(
                    "resend accepted %r as %s",
                    message.subject,
                    response.json().get("id"),
                )
            except ValueError:
                logger.info(
                    "resend accepted %r, no id in the response", message.subject
                )
            return

        # 4xx other than rate limiting will fail again identically, so there is
        # nothing to be gained by retrying them for an hour. 413 is the one
        # that matters here: too large, and no number of retries shrinks it.
        permanent = 400 <= response.status_code < 500 and response.status_code != 429
        raise MailError(f"Resend returned {response.status_code}", permanent=permanent)


class RedirectingClient:
    """Sends everything to one address instead of its real recipients.

    For proving that mail actually arrives, with its attachments, before the
    sending domain has DNS — and for letting staging send at all without any
    chance of reaching a real contractor (R-ENV-03).

    The intended recipients are written into the top of the body rather than
    dropped. A redirected email that does not say who it was for is a trap:
    somebody reads it, believes Robin got it, and nobody finds out otherwise.
    """

    def __init__(self, inner: MailClient, address: str) -> None:
        self._inner = inner
        self._address = address

    def send(self, message: Message) -> None:
        banner = [
            "[ REDIRECTED ]",
            f"This would have gone to: {message.recipients()}",
            "Sent here instead because mail_redirect_to is set.",
            "",
            "",
        ]
        redirected = Message(
            to=(self._address,),
            subject=f"[redirected] {message.subject}",
            text="\n".join(banner) + message.text,
            attachments=message.attachments,
        )
        logger.warning(
            "mail redirected to %s: %r was addressed to %d recipients",
            self._address,
            message.subject,
            len(message.to),
        )
        self._inner.send(redirected)


class SandboxClient:
    """Staging's client. Records what would have been sent and sends nothing.

    R-ENV-03: staging cannot send real email. This is enforced by config
    choosing this class, and asserted by a test.
    """

    def __init__(self) -> None:
        self.sent: list[Message] = []

    def send(self, message: Message) -> None:
        self.sent.append(message)
        # Recipients, never bodies: a body carries the fee breakdown.
        logger.info(
            "sandbox mail not sent: %r to %d recipients",
            message.subject,
            len(message.to),
        )


def build_client(settings: Settings) -> MailClient:
    """The client for this environment. Production is the only one that sends."""
    if mail_mode(settings) is MailMode.SANDBOX:
        return SandboxClient()
    token = settings.resend_api_key.get_secret_value()
    if not token or not settings.mail_from:
        raise MailError("production mail is not configured", permanent=True)

    client: MailClient = ResendClient(token, settings.mail_from)
    if settings.mail_redirect_to:
        client = RedirectingClient(client, settings.mail_redirect_to)
    return client


def backoff(attempts: int) -> timedelta:
    """How long to wait before attempt ``attempts + 1`` (R-JOB-04)."""
    return timedelta(seconds=BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1)))


@dataclass(slots=True)
class DispatchReport:
    """What one run of the dispatcher did. Returned to the task endpoint."""

    considered: int = 0
    sent: int = 0
    retried: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "considered": self.considered,
            "sent": self.sent,
            "retried": self.retried,
            "failed": self.failed,
        }


def due_rows(session: Session, *, now: datetime, limit: int) -> list[Outbox]:
    """Pending rows whose backoff has elapsed.

    ``FOR UPDATE SKIP LOCKED`` so two dispatcher runs overlapping — a slow run
    and the next tick — cannot send the same message twice (R-JOB-04).
    """
    return list(
        session.scalars(
            select(Outbox)
            .where(
                Outbox.status == OutboxStatus.PENDING,
                (Outbox.next_attempt_at.is_(None)) | (Outbox.next_attempt_at <= now),
            )
            .order_by(Outbox.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )


def dispatch(
    session: Session,
    *,
    client: MailClient,
    settings: Settings,
    store: ObjectStore | None = None,
    now: datetime | None = None,
    limit: int = BATCH_SIZE,
) -> DispatchReport:
    """Send what is due. Must be called inside a transaction.

    One row at a time, and a failure on one never stops the rest: a bad address
    on one invoice should not hold up the other fifteen.
    """
    moment = now or datetime.now(UTC)
    report = DispatchReport()

    for row in due_rows(session, now=moment, limit=limit):
        report.considered += 1
        try:
            message = build_message(session, row, settings=settings, store=store)
            client.send(message)
        except (MailError, StorageError, ValueError) as exc:
            _record_failure(row, exc, now=moment, report=report)
            continue

        row.status = OutboxStatus.SENT
        row.sent_at = moment
        row.attempts += 1
        row.last_error = None
        report.sent += 1

    return report


def _record_failure(
    row: Outbox, exc: Exception, *, now: datetime, report: DispatchReport
) -> None:
    """Count the attempt and decide whether to retry or give up."""
    row.attempts += 1
    # The message, never the payload: a payload carries the fee breakdown.
    row.last_error = f"{type(exc).__name__}: {exc}"[:500]

    permanent = isinstance(exc, MailError) and exc.permanent
    if permanent or row.attempts >= MAX_ATTEMPTS:
        row.status = OutboxStatus.FAILED
        report.failed += 1
        # Worth a human's attention: an invoice was accepted and not delivered.
        logger.error(
            "outbox row %s failed after %d attempts: %s",
            row.id,
            row.attempts,
            row.last_error,
        )
        report.errors.append(f"{row.kind} {row.id}: {row.last_error}")
        return

    row.next_attempt_at = now + backoff(row.attempts)
    report.retried += 1


# --------------------------------------------------------------------------
# Turning an outbox row into a message
# --------------------------------------------------------------------------


def build_message(
    session: Session,
    row: Outbox,
    *,
    settings: Settings,
    store: ObjectStore | None = None,
) -> Message:
    """Render one queued row (R-SUB-07, R-BANK-05, R-JOB-02, R-JOB-03)."""
    builder = _BUILDERS.get(row.kind)
    if builder is None:
        # Permanent: a row nothing knows how to render will never render,
        # so retrying it for twenty minutes achieves nothing.
        raise MailError(f"no builder for outbox kind {row.kind!r}", permanent=True)
    return builder(session, row, settings, store)


def _recipients(*addresses: object) -> tuple[str, ...]:
    seen: list[str] = []
    for address in addresses:
        text = str(address or "").strip()
        if text and text not in seen:
            seen.append(text)
    return tuple(seen)


def _money(pence: object) -> str:
    from app.domain.money import format_gbp

    return format_gbp(int(pence or 0))


def _invoice_submitted(
    session: Session, row: Outbox, settings: Settings, store: ObjectStore | None
) -> Message:
    """R-SUB-07 and R-SUB-08.

    The body carries the full breakdown regardless of what the PDF collapses
    into "Additional" — this is where the detail lives. The subject never
    carries a fee (R-SEC-05).
    """
    payload = row.payload
    lines = [
        f"{payload.get('heading', '')}",
        "",
        f"Invoice {payload.get('number')}",
        "",
        f"  Remuneration   {_money(payload.get('remuneration_pence'))}",
        f"  Additional     {_money(payload.get('additional_pence'))}",
    ]
    # detail_note is newline separated, so it lays out under Additional here
    # exactly as it does on the invoice.
    for part in str(payload.get("detail") or "").splitlines():
        if part.strip():
            lines.append(f"                 {part}")
    lines += [
        f"  Total          {_money(payload.get('total_pence'))}",
        "",
    ]
    if payload.get("is_late"):
        lines.append("Submitted after the 28th, so it is marked late.")
        lines.append("")
    lines.append("The invoice is attached.")

    attachments: list[Attachment] = []
    missing: list[str] = []
    if store is not None:
        attachments, missing = _invoice_attachments(session, payload, store)

    if missing:
        # Said out loud rather than left to a log nobody reads. Robin can ask
        # for a file; he cannot ask for an email he never got.
        lines += [
            "",
            "Could not be attached:",
            *(f"  {item}" for item in missing),
            "",
            "The figures above are complete and correct — only the files are",
            "missing. Tell Leo.",
        ]

    return Message(
        to=_recipients(payload.get("to_finance"), payload.get("to_person")),
        subject=str(payload.get("subject", "Invoice")),
        text="\n".join(lines),
        attachments=tuple(attachments),
    )


def _invoice_attachments(
    session: Session, payload: dict, store: ObjectStore
) -> tuple[list[Attachment], list[str]]:
    """The PDF and any receipts (R-SUB-07), plus what could not be fetched.

    Nothing in here raises. An invoice that Postgres accepted must reach Robin
    even if a file is missing: a missing attachment is a thing he can chase,
    and an email that never arrives is not. What is missing goes in the body so
    it is visible rather than merely logged.
    """
    from app.db.models import Expense, Invoice

    attachments: list[Attachment] = []
    missing: list[str] = []

    invoice_id = payload.get("invoice_id")
    if not invoice_id:
        return attachments, missing
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        return attachments, missing

    if invoice.pdf_uri:
        try:
            attachments.append(
                Attachment(
                    name=invoice.pdf_uri.rsplit("/", 1)[-1],
                    content=store.get(invoice.pdf_uri),
                    content_type="application/pdf",
                )
            )
        except StorageError:
            logger.error("invoice %s: the PDF could not be fetched", invoice.id)
            missing.append("the invoice PDF")
    else:
        missing.append("the invoice PDF")

    receipts = session.scalars(
        select(Expense).where(
            Expense.invoice_id == invoice.id, Expense.receipt_uri.is_not(None)
        )
    )
    # The PDF is already counted: it is never dropped for size, because an
    # invoice email without the invoice is not worth sending.
    budget = RESEND_MAX_MESSAGE_BYTES - _encoded_size(attachments) - BODY_ALLOWANCE

    for expense in receipts:
        try:
            content = store.get(str(expense.receipt_uri))
        except StorageError:
            logger.warning("invoice %s: receipt missing for %s", invoice.id, expense.id)
            missing.append(f"the receipt for {expense.description}")
            continue

        # R-WIZ-04 allows ten receipts and each may be several megabytes, so a
        # loaded invoice can exceed what any provider will accept. Named and
        # left out beats a 413 that fails the whole email, invoice included.
        cost = _encoded_size([Attachment("", content, "")])
        if cost > budget:
            logger.warning(
                "invoice %s: receipt for %s is too large to attach",
                invoice.id,
                expense.id,
            )
            missing.append(f"the receipt for {expense.description} (too large to send)")
            continue

        budget -= cost
        attachments.append(
            Attachment(
                name=str(expense.receipt_uri).rsplit("/", 1)[-1],
                content=content,
                content_type="application/octet-stream",
            )
        )

    return attachments, missing


def _encoded_size(attachments: list[Attachment]) -> int:
    """What these cost on the wire, base64 included.

    Attachments are sent base64-encoded, which is four bytes for every three.
    Budgeting against the raw size would leave us a third over the limit and
    discovering it as a rejected send.
    """
    return sum(-(-len(a.content) * 4 // 3) for a in attachments)


def _bank_changed(
    session: Session, row: Outbox, settings: Settings, store: ObjectStore | None
) -> Message:
    """R-BANK-05. Last four digits only, to the person and to Robin."""
    del session, settings, store
    payload = row.payload
    old = payload.get("old_account") or payload.get("old_iban") or "nothing on file"
    new = payload.get("new_account") or payload.get("new_iban") or ""
    return Message(
        to=_recipients(payload.get("to_person"), payload.get("to_finance")),
        subject=str(payload.get("subject", "Bank details changed")),
        text=(
            f"The bank details for {payload.get('person_name', 'someone')} have "
            f"changed.\n\n"
            f"  Was  {old}\n"
            f"  Now  {new}\n\n"
            "If this was not you, tell Leo straight away."
        ),
    )


def _bank_proposed(
    session: Session, row: Outbox, settings: Settings, store: ObjectStore | None
) -> Message:
    del session, settings, store
    payload = row.payload
    ending = payload.get("proposed_account")
    return Message(
        to=_recipients(payload.get("to_person")),
        subject=str(payload.get("subject", "Your bank details need your approval")),
        text=(
            f"{payload.get('proposed_by', 'An administrator')} has suggested a "
            f"correction to your bank details, ending {ending}.\n\n"
            "It is not in use and cannot be until you approve it. Open the "
            "portal and check it against your own records.\n\n"
            "Nobody but you can make this change take effect."
        ),
    )


def _bank_flagged(
    session: Session, row: Outbox, settings: Settings, store: ObjectStore | None
) -> Message:
    del session, settings, store
    payload = row.payload
    return Message(
        to=_recipients(payload.get("to_person")),
        subject=str(payload.get("subject", "Your bank details need a look")),
        text=(
            "Your bank details need correcting before your next invoice:\n\n"
            f"  {payload.get('reason')}\n\n"
            "Open the portal and update them."
        ),
    )


def _window_open(
    session: Session, row: Outbox, settings: Settings, store: ObjectStore | None
) -> Message:
    """R-JOB-02, the 24th."""
    del session, settings, store
    payload = row.payload
    return Message(
        to=_recipients(payload.get("to_person")),
        subject=str(payload.get("subject", "Time to invoice")),
        text=(
            f"The invoice window for {payload.get('period_label')} is open until "
            "the 28th.\n\n"
            "It takes a couple of minutes: check your details, add anything "
            "extra, and send.\n\n"
            "If you miss the 28th you can still send it — it is just marked late."
        ),
    )


def _reminder(
    session: Session, row: Outbox, settings: Settings, store: ObjectStore | None
) -> Message:
    """R-JOB-02, the 27th, only to those with nothing submitted."""
    del session, settings, store
    payload = row.payload
    return Message(
        to=_recipients(payload.get("to_person")),
        subject=str(payload.get("subject", "Your invoice is not in yet")),
        text=(
            f"There is no invoice from you for {payload.get('period_label')} yet, "
            "and the window closes on the 28th.\n\n"
            "You will not be locked out if you miss it, but Robin would rather "
            "not have to ask."
        ),
    )


def _summary(
    session: Session, row: Outbox, settings: Settings, store: ObjectStore | None
) -> Message:
    """R-JOB-03, the 29th, to the finance group."""
    del session, settings, store
    payload = row.payload
    missing = payload.get("missing") or []
    flagged = payload.get("flagged") or []
    lines = [
        f"{payload.get('period_label')} — {payload.get('received')} of "
        f"{payload.get('expected')} invoices received.",
        "",
    ]
    if missing:
        lines.append("Not in:")
        lines += [f"  {name}" for name in missing]
        lines.append("")
    if flagged:
        lines.append("Flagged:")
        lines += [f"  {item}" for item in flagged]
        lines.append("")
    broken = payload.get("broken") or []
    if broken:
        lines.append("NEEDS FIXING — these did not complete:")
        lines += [f"  {item}" for item in broken]
        lines.append("")
    if not missing and not flagged and not broken:
        lines.append("Everyone is in and nothing is flagged.")
    return Message(
        to=_recipients(payload.get("to_finance")),
        subject=str(payload.get("subject", "Invoice summary")),
        text="\n".join(lines),
    )


_BUILDERS = {
    "invoice_submitted": _invoice_submitted,
    "bank_changed": _bank_changed,
    "bank_proposed": _bank_proposed,
    "bank_flagged": _bank_flagged,
    "window_open": _window_open,
    "reminder": _reminder,
    "summary": _summary,
}
