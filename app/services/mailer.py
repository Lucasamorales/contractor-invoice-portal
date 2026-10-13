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
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import MailMode, Settings, mail_mode
from app.db.models import Outbox, OutboxStatus
from app.services import receipts as receipt_files
from app.services.storage import ObjectStore, StorageError

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"

# Resend caps a whole message, attachments included. Ours are capped lower
# still (R-WIZ-04) so an invoice with ten receipts cannot become an email
# nobody can send.
RESEND_MAX_MESSAGE_BYTES = 40 * 1024 * 1024
# Room for the body, the headers and the JSON around it all.
BODY_ALLOWANCE = 256 * 1024

BATCH_SIZE = 20

# Doubling from a minute, and then not doubling any further: 1, 2, 4, 8, 16,
# 32, 60, 60, 60... About twenty-four tries in the first day and hourly after
# that, for as long as it takes.
#
# There is deliberately **no attempt limit**. Giving up after five tries meant
# sixteen minutes of a provider being unwell could permanently lose an invoice
# email that Postgres had already accepted, and nothing would ever retry it.
# The cap is what makes retrying forever safe: even a row that will never
# succeed costs one call an hour rather than one every five minutes.
BACKOFF_BASE_SECONDS = 60
BACKOFF_CEILING_SECONDS = 60 * 60

# How long a row may go unsent before it is somebody's problem rather than the
# dispatcher's. It has not failed — it is still trying — but an invoice that
# has not reached accounts after this long is worth a person knowing about.
STALE_AFTER_HOURS = 6


class MailError(RuntimeError):
    """Sending failed. Retryable unless it says otherwise."""

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


@dataclass(frozen=True, slots=True)
class Attachment:
    """A file sent with a message, held in memory."""

    name: str
    content: bytes
    content_type: str


@dataclass(frozen=True, slots=True)
class Message:
    """One email as handed to a client: recipients, subject, plain text and optional
    HTML.
    """

    to: tuple[str, ...]
    subject: str
    text: str
    attachments: tuple[Attachment, ...] = ()
    # The same message laid out. Sent alongside the text, never instead of it:
    # a client that prefers plain text still gets something readable.
    html: str | None = None

    def recipients(self) -> str:
        """The recipients as one comma-separated string, for showing who a message
        was meant for.
        """
        return ", ".join(address for address in self.to if address)


class MailClient(Protocol):
    """Anything that can send a ``Message``. Production, staging and tests each
    supply one.
    """

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
        """Post the message to Resend. Raises so the outbox can retry or give up."""
        payload: dict[str, object] = {
            "from": self._sender,
            "to": [address for address in message.to if address],
            "subject": message.subject,
            "text": message.text,
        }
        if message.html:
            payload["html"] = message.html
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
        """Send to the redirect address with a banner naming the real recipients.

        Used in staging so a real message can be read without reaching anybody.
        """
        banner = [
            "[ REDIRECTED ]",
            f"This would have gone to: {message.recipients()}",
            "Sent here instead because mail_redirect_to is set.",
            "",
            "",
        ]
        # The HTML has to carry the banner too, or a redirected message read in
        # any ordinary client looks exactly like a real one.
        html = message.html
        if html:
            html = html.replace(
                '<table role="presentation"',
                '<div style="font:600 13px Helvetica,Arial,sans-serif;'
                "color:#c11f1f;background:#ffe9e9;padding:12px 16px;"
                'text-align:center;">[ REDIRECTED ] This would have gone to: '
                f'{message.recipients()}</div><table role="presentation"',
                1,
            )
        redirected = Message(
            to=(self._address,),
            subject=f"[redirected] {message.subject}",
            text="\n".join(banner) + message.text,
            attachments=message.attachments,
            html=html,
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
        """Record the message and send nothing. Only the subject and recipient count
        are logged.
        """
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
    """How long to wait before attempt ``attempts + 1`` (R-JOB-04).

    Doubles up to an hour and then stays there. Unbounded doubling would put
    the twentieth attempt weeks away, which for an unsent invoice is the same
    as giving up while looking like it has not.
    """
    seconds = BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1))
    return timedelta(seconds=min(seconds, BACKOFF_CEILING_SECONDS))


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
            # By when it is due, not when it was made: a row on an hour's
            # backoff must not keep taking a place in the batch ahead of a
            # freshly queued invoice that is ready to go now.
            .order_by(Outbox.next_attempt_at.nulls_first(), Outbox.created_at)
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

    # Only a failure that cannot succeed on a later try stops. A bad address
    # or an oversized message will be just as bad in an hour; a refused
    # connection will not. FAILED now means "needs a person", never
    # "abandoned" — nothing leaves the queue until it has actually been sent.
    if isinstance(exc, MailError) and exc.permanent:
        row.status = OutboxStatus.FAILED
        row.next_attempt_at = None
        report.failed += 1
        logger.error(
            "outbox row %s cannot be sent as it stands: %s", row.id, row.last_error
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
        # One address, or a list of them (a flag goes to the sign-in address
        # and, when it differs, the contact address as well).
        for item in address if isinstance(address, (list, tuple)) else [address]:
            text = str(item or "").strip()
            if text and text not in seen:
                seen.append(text)
    return tuple(seen)


# --------------------------------------------------------------------------
# Composing a message
#
# Every email is described once, as a short list of parts, and rendered twice
# — plain text and HTML. Writing each message out in both forms is how the two
# drift apart, and a wrong plain-text fallback is invisible until somebody
# reads mail in a client that prefers it.
#
# The HTML is deliberately old-fashioned: tables, inline styles, no web fonts
# and no images. An image would have to be hosted by the app, the app is
# behind IAP, and a mail client fetching it is not signed in — so it would
# render as a broken box in every inbox.
# --------------------------------------------------------------------------


def para(text: str) -> tuple[str, object]:
    """A paragraph block for ``compose``."""
    return ("para", text)


def rows(pairs: list[tuple[str, str]]) -> tuple[str, object]:
    """A label/figure table. Right-aligned in HTML, aligned in text."""
    return ("rows", pairs)


def note(text: str) -> tuple[str, object]:
    """Smaller, quieter. For a caveat, not for the point of the email."""
    return ("note", text)


def action(label: str, path: str = "/") -> tuple[str, object]:
    """The link back to the portal, deep-linked at the screen in question."""
    return ("action", (label, path))


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _link(settings: Settings, path: str) -> str:
    base = (settings.app_url or "").rstrip("/")
    if not base:
        return ""
    return f"{base}{path if path.startswith('/') else '/' + path}"


def render_text(parts: list[tuple[str, object]], settings: Settings) -> str:
    """Lay the blocks out as the plain-text body of the message."""
    out: list[str] = []
    for kind, value in parts:
        if kind == "para" or kind == "note":
            out += [str(value), ""]
        elif kind == "rows":
            width = max((len(label) for label, _ in value), default=0)
            out += [f"  {label.ljust(width)}   {figure}" for label, figure in value]
            out.append("")
        elif kind == "action":
            # The portal's address is never printed: it is the long Cloud Run
            # one, and it reads as a mistake. The HTML version carries it behind
            # the button; this fallback names where to go instead.
            label, _path = value
            out += [f"{label} (in the invoice portal)", ""]
    return "\n".join(out).strip() + "\n"


def render_html(parts: list[tuple[str, object]], settings: Settings) -> str:
    """The same message, laid out.

    Colours are the day tokens from app.css. Email has no theme and no
    prefers-color-scheme worth relying on, so it stays on paper.
    """
    body: list[str] = []
    for kind, value in parts:
        if kind == "para":
            body.append(
                '<p style="margin:0 0 16px;font-size:15px;line-height:1.6;'
                f'color:#55554f;">{_escape(value)}</p>'
            )
        elif kind == "note":
            body.append(
                '<p style="margin:0 0 16px;font-size:13px;line-height:1.55;'
                f'color:#8a8a82;">{_escape(value)}</p>'
            )
        elif kind == "rows":
            cells = "".join(
                "<tr>"
                '<td style="padding:9px 0;border-top:1px solid #eeebe5;'
                f'font-size:14px;color:#8a8a82;">{_escape(label)}</td>'
                '<td style="padding:9px 0;border-top:1px solid #eeebe5;'
                "font-size:15px;color:#0a0a0a;font-weight:600;text-align:right;"
                f'white-space:nowrap;">{_escape(figure)}</td>'
                "</tr>"
                for label, figure in value
            )
            body.append(
                '<table role="presentation" cellpadding="0" cellspacing="0" '
                f'width="100%" style="margin:0 0 20px;">{cells}</table>'
            )
        elif kind == "action":
            label, path = value
            url = _link(settings, path)
            if not url:
                continue
            body.append(
                '<table role="presentation" cellpadding="0" cellspacing="0" '
                'style="margin:4px 0 20px;"><tr><td style="border-radius:100px;'
                'background:#ff2b2b;">'
                f'<a href="{_escape(url)}" style="display:inline-block;'
                "padding:12px 22px;font-size:14px;font-weight:600;color:#ffffff;"
                f'text-decoration:none;border-radius:100px;">{_escape(label)}</a>'
                "</td></tr></table>"
            )

    # Linked, but never shown: the address is the long Cloud Run one.
    home = _link(settings, "/")
    footer = (
        f'<a href="{_escape(home)}" style="color:#8a8a82;">Invoices</a>'
        if home
        else "Invoices"
    )

    return (
        '<!doctype html><html><body style="margin:0;padding:24px 0;'
        'background:#f7f6f4;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" width="100%">'
        '<tr><td align="center">'
        '<table role="presentation" cellpadding="0" cellspacing="0" width="560" '
        'style="width:560px;max-width:100%;background:#ffffff;'
        "border:1px solid #e5e2dc;border-radius:10px;overflow:hidden;"
        'font-family:Helvetica,Arial,sans-serif;">'
        '<tr><td style="background:#0a0a0a;padding:16px 28px;">'
        '<span style="font-size:13px;font-weight:700;letter-spacing:0.14em;'
        'color:#ffffff;">your-company</span>'
        '<span style="font-size:13px;letter-spacing:0.14em;color:#8a8a82;">'
        "&nbsp;&nbsp;INVOICES</span>"
        "</td></tr>"
        f'<tr><td style="padding:28px;">{"".join(body)}</td></tr>'
        '<tr><td style="padding:16px 28px;border-top:1px solid #eeebe5;'
        f'font-size:12px;color:#8a8a82;">{footer}</td></tr>'
        "</table></td></tr></table></body></html>"
    )


def compose(
    to: tuple[str, ...],
    subject: str,
    parts: list[tuple[str, object]],
    settings: Settings,
    attachments: tuple[Attachment, ...] = (),
) -> Message:
    """Build a ``Message`` from layout blocks, rendering both bodies from one source.

    The plain-text and HTML versions cannot drift apart because neither is
    written by hand.
    """
    return Message(
        to=to,
        subject=subject,
        text=render_text(parts, settings),
        html=render_html(parts, settings),
        attachments=attachments,
    )


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
    breakdown = [
        ("Remuneration", _money(payload.get("remuneration_pence"))),
        ("Additional", _money(payload.get("additional_pence"))),
    ]
    # detail_note is newline separated, so each expense lands on its own line
    # here exactly as it does on the invoice.
    for part in str(payload.get("detail") or "").splitlines():
        if part.strip():
            breakdown.append(("", part.strip()))
    breakdown.append(("Total", _money(payload.get("total_pence"))))

    parts: list[tuple[str, object]] = [
        para(str(payload.get("heading", ""))),
        rows(breakdown),
    ]
    if payload.get("is_late"):
        parts.append(note("Submitted after the 28th, so it is marked late."))

    attachments: list[Attachment] = []
    missing: list[str] = []
    if store is not None:
        attachments, missing = _invoice_attachments(session, payload, store)

    # An expense that never had a receipt is not a failure, and it used to be
    # silent — so accounts could not tell "none was given" from "one was given
    # and we lost it". Said plainly, and separately from the failures below.
    without = _expenses_without_a_receipt(session, payload)
    if without:
        parts.append(para("No receipt provided for: " + ", ".join(without)))

    if missing:
        # Said out loud rather than left to a log nobody reads. Accounts can
        # ask for a file; they cannot ask for an email they never got.
        parts.append(para("Could not be attached: " + ", ".join(missing)))
        parts.append(
            note(
                "The figures above are complete and correct — only the files "
                "are missing. Tell the IT department."
            )
        )
    else:
        parts.append(note("The invoice is attached."))

    parts.append(action("Open the portal", "/me"))

    return compose(
        to=_recipients(payload.get("to_finance"), payload.get("to_person")),
        subject=str(payload.get("subject", "Invoice")),
        parts=parts,
        settings=settings,
        attachments=tuple(attachments),
    )


def _expenses_without_a_receipt(session: Session, payload: dict) -> list[str]:
    """Expense rows that carry no receipt at all.

    Distinct from the ``missing`` list, which is about files that exist and
    could not be fetched or were too large. This one is about a row where none
    was ever uploaded — which happens by accident: the file uploads the moment
    it is chosen, and until this was fixed somebody could press on before the
    upload answered and lose the link to it without being told.
    """
    from app.db.models import Expense

    invoice_id = payload.get("invoice_id")
    if not invoice_id:
        return []
    rows_without = session.scalars(
        select(Expense).where(
            Expense.invoice_id == invoice_id, Expense.receipt_uri.is_(None)
        )
    )
    return [expense.description for expense in rows_without]


def _invoice_attachments(
    session: Session, payload: dict, store: ObjectStore
) -> tuple[list[Attachment], list[str]]:
    """The PDF and any receipts (R-SUB-07), plus what could not be fetched.

    Nothing in here raises. An invoice that Postgres accepted must reach Robin
    even if a file is missing: a missing attachment is a thing he can chase,
    and an email that never arrives is not. What is missing goes in the body so
    it is visible rather than merely logged.
    """
    # Imported here, like the models below: the attachment name is the one
    # R-PDF-15 specifies, and invoicing owns it.
    from app.db.models import Expense, Invoice
    from app.services.invoicing import download_name

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
                    name=download_name(session, invoice),
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
    taken: set[str] = set()

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
        kind = receipt_files.sniff(content)
        attachments.append(
            Attachment(
                name=_receipt_name(
                    expense.description, _extension(str(expense.receipt_uri)), taken
                ),
                content=content,
                # The real type, so an inbox previews the photo or the PDF
                # rather than offering an unnamed download.
                content_type=kind.value if kind else "application/octet-stream",
            )
        )

    return attachments, missing


# Characters a mail client or a file system would refuse or mangle in a name.
_UNSAFE_IN_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def _extension(uri: str) -> str:
    last = uri.rsplit("/", 1)[-1]
    return last.rpartition(".")[2].lower() if "." in last else ""


def _receipt_name(description: str | None, extension: str, taken: set[str]) -> str:
    """What accounts sees on the attachment: the expense it belongs to.

    The stored object is named by a random id, which is right for the bucket
    and useless in an inbox. The description is already in the body of this
    same email, so naming the file after it tells nobody anything new. Two
    expenses with the same description get a number rather than one file
    silently standing in for both.
    """
    label = " ".join(_UNSAFE_IN_NAME.sub(" ", description or "").split())
    label = label[:60].strip(" .")
    stem = f"Receipt - {label}" if label else "Receipt"
    suffix = f".{extension}" if extension else ""

    name = stem + suffix
    number = 2
    while name.lower() in taken:
        name = f"{stem} ({number}){suffix}"
        number += 1
    taken.add(name.lower())
    return name


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
    """R-BANK-05. Last four digits only, to the person and to accounts."""
    del session, store
    payload = row.payload
    old = payload.get("old_account") or payload.get("old_iban") or "nothing on file"
    new = payload.get("new_account") or payload.get("new_iban") or ""
    return compose(
        to=_recipients(payload.get("to_person"), payload.get("to_finance")),
        subject=str(payload.get("subject", "Bank details changed")),
        parts=[
            para(
                f"The bank details for {payload.get('person_name', 'someone')} "
                "have changed."
            ),
            rows([("Was", str(old)), ("Now", str(new))]),
            action("Check the account", "/bank"),
            note("If this was not you, tell the IT department straight away."),
        ],
        settings=settings,
    )


def _bank_proposed(
    session: Session, row: Outbox, settings: Settings, store: ObjectStore | None
) -> Message:
    del session, store
    payload = row.payload
    return compose(
        to=_recipients(payload.get("to_person")),
        subject=str(payload.get("subject", "Your bank details need your approval")),
        parts=[
            para(
                "Accounts have suggested a correction to your bank details. It "
                "is not in use, and cannot be until you accept it."
            ),
            rows([("Proposed", str(payload.get("proposed_account", "")))]),
            action("Check it against your records", "/bank"),
            note("Nobody but you can make this change take effect."),
        ],
        settings=settings,
    )


def _bank_flagged(
    session: Session, row: Outbox, settings: Settings, store: ObjectStore | None
) -> Message:
    del session, store
    payload = row.payload
    return compose(
        to=_recipients(payload.get("to_person")),
        subject=str(payload.get("subject", "Your bank details need a look")),
        parts=[
            # A flag blocks nothing, so the email asks rather than tells.
            para("Accounts have asked you to check your bank details."),
            rows([("What to look at", str(payload.get("reason", "")))]),
            action("Check them", "/bank"),
            note("If they are already right, say so from the same page."),
        ],
        settings=settings,
    )


def _letterhead_flagged(
    session: Session, row: Outbox, settings: Settings, store: ObjectStore | None
) -> Message:
    del session, store
    payload = row.payload
    return compose(
        to=_recipients(payload.get("to_person")),
        subject=str(payload.get("subject", "Your letterhead needs a look")),
        parts=[
            para("Accounts have asked you to check the details on your invoice."),
            rows([("What to look at", str(payload.get("reason", "")))]),
            action("Check them", "/details"),
            note("If they are already right, say so from the same page."),
        ],
        settings=settings,
    )


def _flag_answered(
    session: Session, row: Outbox, settings: Settings, store: ObjectStore | None
) -> Message:
    """To accounts, when a person says a flagged thing is already right."""
    del session, store
    payload = row.payload
    name = str(payload.get("person_name", "Someone"))
    what = str(payload.get("what", "details"))
    return compose(
        to=_recipients(payload.get("to_finance")),
        subject=str(payload.get("subject", f"{name} answered a flag")),
        parts=[
            para(f"{name} says their {what} need no change."),
            rows([("You asked", str(payload.get("reason", "")))]),
            action("Open their page", f"/admin/people/{payload.get('person_id', '')}"),
        ],
        settings=settings,
    )


def _invoice_voided(
    session: Session, row: Outbox, settings: Settings, store: ObjectStore | None
) -> Message:
    """R-SUB-11. An invoice was cancelled, to the person and to accounts.

    It exists because the number comes back (R-NUM-05): the replacement
    carries the same one, so without this the two documents in the finance
    inbox differ only by the date printed inside them.

    No figure anywhere in it. This is not the invoice email and it does not
    inherit that email's exception to CLAUDE.md §2.8.
    """
    del session, store
    payload = row.payload
    number = payload.get("number", "")
    return compose(
        to=_recipients(payload.get("to_person"), payload.get("to_finance")),
        subject=str(payload.get("subject", f"Cancelled: invoice {number}")),
        parts=[
            para(
                f"Invoice {number} for {payload.get('period_label')} has been "
                "cancelled. Nothing is owed on it."
            ),
            rows([("Why", str(payload.get("reason", "")))]),
            para(
                f"When it is sent again it will carry the same number, {number}, "
                "and the subject will say it is a resend. Treat this one as "
                "replaced."
            ),
            action("Make your invoice"),
        ],
        settings=settings,
    )


def _window_open(
    session: Session, row: Outbox, settings: Settings, store: ObjectStore | None
) -> Message:
    """R-JOB-02, the day the window opens."""
    del session, store
    payload = row.payload
    return compose(
        to=_recipients(payload.get("to_person")),
        subject=str(payload.get("subject", "Time to invoice")),
        parts=[
            para(
                f"The invoice window for {payload.get('period_label')} is open "
                "until the 28th."
            ),
            para("Check your figures, add anything extra, and send."),
            action("Make your invoice"),
            note("Miss the 28th and you can still send it. It is marked late."),
        ],
        settings=settings,
    )


def _reminder(
    session: Session, row: Outbox, settings: Settings, store: ObjectStore | None
) -> Message:
    """R-JOB-02, the 27th, only to those with nothing submitted."""
    del session, store
    payload = row.payload
    return compose(
        to=_recipients(payload.get("to_person")),
        subject=str(payload.get("subject", "Your invoice is not in yet")),
        parts=[
            para(
                f"There is no invoice from you for {payload.get('period_label')} "
                "yet, and the window closes on the 28th."
            ),
            action("Make your invoice"),
            note(
                "You will not be locked out if you miss it, but accounts would "
                "rather not have to ask."
            ),
        ],
        settings=settings,
    )


def _summary(
    session: Session, row: Outbox, settings: Settings, store: ObjectStore | None
) -> Message:
    """R-JOB-03, the 29th, to the finance group."""
    del session, store
    payload = row.payload
    missing = payload.get("missing") or []
    flagged = payload.get("flagged") or []
    broken = payload.get("broken") or []

    parts: list[tuple[str, object]] = [
        para(
            f"{payload.get('period_label')} — {payload.get('received')} of "
            f"{payload.get('expected')} invoices received."
        )
    ]
    if missing:
        parts.append(para("Not in:"))
        parts.append(rows([("", str(name)) for name in missing]))
    if flagged:
        parts.append(para("Flagged:"))
        parts.append(rows([("", str(item)) for item in flagged]))
    if broken:
        parts.append(para("Needs fixing — these did not complete:"))
        parts.append(rows([("", str(item)) for item in broken]))
    if not missing and not flagged and not broken:
        parts.append(para("Everyone is in and nothing is flagged."))

    parts.append(action("Open the month", "/admin/period"))

    return compose(
        to=_recipients(payload.get("to_finance")),
        subject=str(payload.get("subject", "Invoice summary")),
        parts=parts,
        settings=settings,
    )


_BUILDERS = {
    "invoice_submitted": _invoice_submitted,
    "invoice_voided": _invoice_voided,
    "bank_changed": _bank_changed,
    "bank_proposed": _bank_proposed,
    "bank_flagged": _bank_flagged,
    "letterhead_flagged": _letterhead_flagged,
    "flag_answered": _flag_answered,
    "window_open": _window_open,
    "reminder": _reminder,
    "summary": _summary,
}
