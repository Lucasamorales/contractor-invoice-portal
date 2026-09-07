"""The on-screen tutorial's copy (design handoff, "The tutorial").

Kept here rather than in the templates so all of the app's explanatory English
sits in one reviewable place, and so a screen's markup only has to carry a
stable key.

Cadence drove the design: people use this once a month and come back knowing
most of it but needing one specific answer. So the steps are numbered pins you
can jump between, not a tour you have to walk through.

**No personal names.** Roles outlive the people holding them, and this copy
should not need editing when somebody leaves. "accounts" and "the IT
department", never a first name.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Step:
    """One pin. ``key`` matches a ``data-tut`` attribute in the template."""

    key: str
    title: str
    body: str


MEMBER_MONTH = (
    Step(
        "member.month.fee",
        "Your fee",
        "Set by finance, never by you, and nobody else can see it. If it says "
        "not set, accounts has already been told automatically.",
    ),
    Step(
        "member.month.kudos",
        "Kudos",
        "Worth £20 each and paid quarterly — they land "
        "on your March, June, September and December invoice. Two months in "
        "three this line says when, not how much. That is the policy, not a "
        "fault.",
    ),
    Step(
        "member.month.total",
        "Running total",
        "Fee plus kudos, before anything you add yourself. Expenses come on the "
        "next screen and change this figure.",
    ),
    Step(
        "member.month.blocks",
        "Anything in your way",
        "Every block appears here with its fix attached. Confirming your bank "
        "account is the usual one, and it is the one thing no admin can do for "
        "you.",
    ),
    Step(
        "member.month.steps",
        "Three screens a month",
        "Check these figures, add anything extra, send. Your letterhead and "
        "bank account live on their own pages because they almost never change.",
    ),
    Step(
        "member.month.window",
        "The window",
        "Send opens on the 24th and closes on the 28th. After the 28th nobody "
        "is locked out: your invoice is still accepted, marked late, and still "
        "gets your next number in sequence.",
    ),
)

MEMBER_BANK = (
    Step(
        "member.bank.decision",
        "Why this is a decision",
        "These details came out of the old spreadsheet. Nothing has verified "
        "them — a person has to. Confirming is what makes the account payable; "
        "until you do, you cannot invoice.",
    ),
    Step(
        "member.bank.masked",
        "Last four digits only",
        "The full number is encrypted and is never shown back on screen, to you "
        "or to anyone. It appears in exactly two places: the invoice PDF and "
        "the payment-run file.",
    ),
    Step(
        "member.bank.owner",
        "Only you can change it",
        "Accounts can propose a correction and flag a row back to you, but can "
        "never make one payable. That is the control which means an admin "
        "account in the wrong hands still cannot redirect your pay.",
    ),
)

MEMBER_ADDITIONAL = (
    Step(
        "member.additional.kudos",
        "Kudos are read, not typed",
        "They come from the sheet, so the figure cannot be edited here. If the "
        "sheet is unreadable it says so, and you have to consciously tick "
        "“invoice without them” — it will never quietly bill you £0 "
        "of kudos.",
    ),
    Step(
        "member.additional.receipts",
        "Receipts",
        "One file per row, straight from your phone. Location data is stripped "
        "and an iPhone HEIC is converted to JPEG so accounts can open it. "
        "Leaving every row empty is fine and means zero.",
    ),
)

MEMBER_PREVIEW = (
    Step(
        "member.preview.fixed",
        "The document is fixed",
        "This layout matches the template accounts already use, down to the "
        "margins. The portal around it changed; the invoice did not.",
    ),
    Step(
        "member.preview.real",
        "Not a summary",
        "This is the invoice itself, rendered by the same template that "
        "produces the PDF. What you approve is byte-for-byte what accounts "
        "receives.",
    ),
    Step(
        "member.preview.send",
        "One press",
        "The button disables itself the moment you click it, and pressing twice "
        "cannot produce two invoices — the second press returns the first one. "
        "Nothing has been sent until you press it.",
    ),
)

MEMBER_ME = (
    Step(
        "member.me.masked",
        "Last four, always",
        "Your own account number is never shown back in full. If something here "
        "looks wrong, change it — you do not need an admin.",
    ),
    Step(
        "member.me.history",
        "This list is a tripwire",
        "Every change ever made to the account, as old to new last-four pairs. "
        "If you see a change here you did not make, tell the IT department "
        "straight away. That is the only reason this list exists.",
    ),
    Step(
        "member.me.invoices",
        "You never need the email",
        "Every invoice you have ever issued stays downloadable here. Rows that "
        "say not sent are drafts that never finished.",
    ),
)

ADMIN_OVERVIEW = (
    Step(
        "admin.overview.questions",
        "The only two questions",
        "Between the 24th and the 29th you need to know who has not invoiced "
        "and what is stuck. Both are already computed — nothing on this screen "
        "is a new metric.",
    ),
    Step(
        "admin.overview.stuck",
        "Needs fixing",
        "The panel that answers “what if an invoice silently does not "
        "arrive?”. An invoice that took a number and never finished counts "
        "as received on Period, because a row exists. Only here is it named as "
        "unfinished. If this panel is empty, nothing is stuck.",
    ),
    Step(
        "admin.overview.missing",
        "The chasing list",
        "Who has not invoiced yet, with the reason where there is one. This is "
        "the list that used to be a phone call.",
    ),
    Step(
        "admin.overview.flags",
        "Flags, not blocks",
        "Late, fee changed mid-period, bank changed inside 72 hours, an expense "
        "over the threshold. None of them stop anything, there is nothing to do "
        "about them, and they clear themselves when the month rolls over.",
    ),
    Step(
        "admin.overview.waiting",
        "Waiting on somebody",
        "The ordinary backlog: people with no fee, bank changes their owner has "
        "not answered, imported rows nobody confirmed. Each opens the queue.",
    ),
)

ADMIN_PEOPLE = (
    Step(
        "admin.people.fees",
        "Fees are bullets on purpose",
        "A screenshare of this page should not leak the payroll. Reveal one at "
        "a time, and the reveal writes your address to the audit log. Reading a "
        "fee is part of the job; reading it unrecorded is not.",
    ),
)

ADMIN_PERSON = (
    Step(
        "admin.person.bank",
        "Read, propose, flag — never confirm",
        "You can reveal the account because you pay it, and every reveal is "
        "recorded in the audit log. A change you propose sits pending until "
        "its owner agrees, and the account number is typed twice because "
        "double entry catches transpositions no checksum can. There is no "
        "button here that makes a row payable.",
    ),
    Step(
        "admin.person.invoices",
        "Fixing a wrong invoice",
        "An invoice is immutable once issued, so a correction is a void and a "
        "reissue. The reason you type is the confirmation, and it is required. "
        "The number is never reused: the gap explains itself later.",
    ),
    Step(
        "admin.person.fee",
        "The note is required",
        "Effective-dated: a new fee closes the current row the day before, "
        "rather than overwriting it. A change with no reason is refused, "
        "because in six months that note is the only thing that explains the "
        "number.",
    ),
)

ADMIN_PERIOD = (
    Step(
        "admin.period.table",
        "Everything, with its flags",
        "The full month. No flag blocks a payment. Late means after the 28th "
        "— still valid, still paid, still next in that sequence.",
    ),
)

ADMIN_QUEUE = (
    Step(
        "admin.queue.fee",
        "Blocked at the door",
        "No fee, so they cannot invoice at all. They can see it is with you. "
        "Set one on their page and they leave this list.",
    ),
    Step(
        "admin.queue.pending",
        "Proposals you cannot complete",
        "Bank changes you proposed, waiting on their owner. Only they can make "
        "one payable, so these stay until the person answers. You cannot "
        "approve your own proposal.",
    ),
    Step(
        "admin.queue.unconfirmed",
        "Everything stuck, in one list",
        "Nothing else in the app reports these. A stuck invoice clears when "
        "they send it again; an email when it sends; a bank row when its owner "
        "confirms it, which no admin can do for them; a kudos name when "
        "somebody says who it is. An empty panel means nothing is stuck.",
    ),
)

# Exact paths only. A prefix match would put the member steps on /admin.
BY_PATH: dict[str, tuple[Step, ...]] = {
    "/": MEMBER_MONTH,
    "/bank": MEMBER_BANK,
    "/additional": MEMBER_ADDITIONAL,
    "/preview": MEMBER_PREVIEW,
    "/me": MEMBER_ME,
    "/admin": ADMIN_OVERVIEW,
    "/admin/": ADMIN_OVERVIEW,
    "/admin/people": ADMIN_PEOPLE,
    "/admin/period": ADMIN_PERIOD,
    "/admin/queue": ADMIN_QUEUE,
}


def steps_for(path: str) -> list[dict[str, str]]:
    """The steps for one screen, ready to serialise.

    A screen with no steps returns nothing and the ``?`` button says so rather
    than opening an empty tutorial.
    """
    if path.startswith("/admin/people/") and path.count("/") == 3:
        found: tuple[Step, ...] = ADMIN_PERSON
    else:
        found = BY_PATH.get(path, ())
    return [{"key": s.key, "title": s.title, "body": s.body} for s in found]
