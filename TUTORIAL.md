# TUTORIAL.md — how to use the Invoice Portal

Written as the source text for a future on-screen tutorial. It describes the
app **as it actually is today**, not as the spec imagines it. Anything not
built yet is marked **[not yet built]** so nobody writes a tooltip pointing at
a button that does not exist.

Two audiences, in two parts:

1. **[Everyone](#part-1--everyone)** — the sixteen of us who send an invoice each month.
2. **[Finance](#part-2--finance)** — Robin, Charlie and Remy, who read them and pay them.

Robin is in both. He invoices for his own work through Part 1, exactly like
everybody else (R-ACC-08).

---

# Part 1 — Everyone

## The one-paragraph version

Once a month you open the portal, check three figures, add anything extra, look
at the invoice, and press one button. Your invoice goes to Robin, correctly
numbered, in the same format as everybody else's. You never keep a template,
you never remember a number, and Robin never phones you.

## Getting in

Open the portal link and sign in with your Your Company Google account. There is
no separate password and no account to create — if you can open your Your Company
email, you can open this. Your first visit creates your record automatically.

If you get *"You don't have access"*, that is not a broken password; your
address has not been added yet. Tell Leo.

## The screens, in the order you meet them

### Welcome — `/`

The first thing you see, and the only screen that shows everything at once:

- **Remuneration** — your monthly fee. **You cannot change it.** Only finance
  sets it. If it says *not set*, Robin has not entered it yet and has been told.
- **Kudos** — how many you earned this month and what they come to. Read from
  the kudos sheet, so it cannot be edited here either. **[not yet built:** the
  sheet is not connected yet, so this currently shows *unavailable*.**]**
- **Running total** — the two added up, before anything you add yourself.
- **Before you can invoice** — if anything is missing, it is listed here with a
  link straight to the fix. See [When it won't let you](#when-it-wont-let-you).

If you have already invoiced this month it says **Already sent**, with the
number and the total, instead of letting you do it twice.

If you started and something went wrong, it says **Not quite sent** with a
**Try again** button. Read that section below — it matters.

### Your details — `/details`

Five fields, and all five are required, because all five are printed on the
invoice itself:

| Field | What it is |
|---|---|
| Legal name | The name on the invoice. Your real one, not a nickname. |
| Role | Printed under your name, e.g. *Creative Director*. |
| Address | Your address, as it should appear on the letterhead. |
| Phone | Printed on the invoice. |
| Contact email | Where your own copy goes. |

You fill these in once. They are kept for next month, and you can change them
any time from **My details**.

### Bank details — `/bank`

The account Robin pays. You will only ever see the **last four digits** of your
own account number here — the full number is encrypted and is not shown back to
you, or to anyone, on screen.

Two things happen on this screen:

- **Confirming.** If your details came across from the old spreadsheet, they are
  not trusted until *you* say they are right. Press **Yes, that is right**.
  Until you do, you cannot invoice. This is deliberate: it is the one moment
  somebody checks that the money is going to the right place.
- **Changing.** **Change your bank details** asks you to type the account
  number **twice**, and the two must match. Nothing is accepted from a single
  typing.

**The rule that matters most:** *only you can make a bank change payable.*
Robin can suggest a correction, and if he has, you will see **"Robin has
suggested a change"** at the top of this screen with what he proposes. It
changes nothing until you agree to it. An admin cannot redirect your money, and
neither can someone who gets into an admin account.

If Robin has flagged something as wrong, you will get an email saying what
needs fixing. He still cannot fix it himself.

### Additional — `/additional`

Expenses and anything else for this month. Three blank rows to start; a
description and an amount each. **Leaving it entirely empty is fine and means
zero** — most months it will be.

Amounts are typed as ordinary pounds and pence, `34.50`. If you type something
that is not an amount, it tells you which row and why rather than quietly
dropping it.

Kudos are shown here too, read-only. They come from the sheet.

**[not yet built: receipts.]** The plan is one file per expense row, uploaded
straight from your browser, attached to Robin's email, HEIC photos converted to
JPEG so he does not have to. The validation and conversion are written; the
upload button is not on this screen yet.

### Preview — `/preview`

The actual invoice, rendered by the same template that makes the PDF. What you
see is exactly what Robin receives — not an approximation of it.

**Nothing has been sent at this point.** You can still go back.

### Send to Robin

One button. It disables itself the moment you click it, and pressing it twice
cannot produce two invoices even if you try — the second press returns the
first invoice.

Then you get the **Sent** screen with your invoice number, the total, and:

- **Download your copy** — the PDF, immediately.
- **See your invoices** — everything you have ever sent through the portal.

### My details — `/me`

Permanent, not a one-time step. It holds:

- Your current bank details, last four digits only.
- **Changes to it** — every change ever made, as *old → new* last-four pairs,
  with the date and where it came from. **If you see a change here you did not
  make, tell Leo straight away.** That is what this list is for.
- **Your invoices** — number, month, total, status, and a **Download** link for
  each one. Rows that say *not sent* are drafts that never finished.

You can download any invoice you have ever issued, any time. You do not need
to keep the email.

## Dates: when you can send

- The window is the **22nd to the 28th** of the month, inclusive.
- **Before the 22nd** you can use everything — details, bank, history — but
  **Send to Robin** is disabled and says when it opens.
- **After the 28th nobody is locked out.** The portal still takes your invoice.
  It is marked *late* and shows up on Robin's screen as late, and it still gets
  your next number in sequence. A locked door would just turn into a phone call
  from Robin, which is the thing this replaces.

*(In staging there is an orange banner saying the window is forced open, so it
can be tested on any day. Production refuses to start with that setting on.)*

## Your invoice number

Yours alone, counting up from whatever you had reached before this system
existed. Numbers are per-person, not shared — your 34 and somebody else's 34
are different invoices. It is printed plainly: `8`, `34`, `213`.

A number is allocated **once**. If a submit fails halfway, the number is
already yours and **Try again** reuses it rather than burning a new one. That
is why gaps never appear from a retry.

## When it won't let you

Every block is shown on the Welcome screen with a link to the fix:

| What it says | What to do |
|---|---|
| *Robin hasn't set your fee yet — he's been notified.* | Nothing. It is on Robin, and he has been told. |
| *Confirm your bank details first.* | Go to **Bank details** and confirm them. One click if they are right. |
| *We need a few details for your invoice letterhead.* | Fill in **Your details**. |

## "Not quite sent" — read this one

If you ever land back on the Welcome screen and it says **Not quite sent**, it
means your invoice took a number but did not finish. **Nothing has gone to
Robin.** The portal is telling you this on purpose rather than showing a
reassuring tick.

Press **Try again**. It picks up where it left off and keeps the same number.

This is the whole design principle in one screen: an invoice that does not
arrive must be visible to *someone*. If you do not notice, it also appears on
Robin's **Queue** screen under *Needs fixing*.

## Emails you will get

**[not yet built:** email is not connected yet, so none of these are arriving
in staging. The messages are written and queued; the sending account is not set
up. Anything below marked with this will start working the day it is.**]**

- **[not yet built]** A copy of your own invoice, when you send it.
- **[not yet built]** *"Time to invoice"* on the 22nd.
- **[not yet built]** *"Your invoice is not in yet"* on the 27th, only if it isn't.
- **[not yet built]** *"Your bank details need your approval"* if Robin proposes
  a change, and *"...need a look"* if he flags one back to you.

Until email works, **Download your copy** on the Sent screen and the download
links on **My details** are how you get your PDF. That is why they exist.

---

# Part 2 — Finance

Robin, Charlie and Remy. Everything in this part lives under `/admin` and
nobody else can reach any of it — the check happens twice, once at the door and
once in the app, so a misconfigured access rule is not the only thing between
someone and the payroll.

## The two rules you cannot get around

These are not settings. They are the shape of the system.

1. **You can read a bank account. You cannot change one to be payable.** You
   may propose a correction and flag a row back to its owner. Only the person
   themselves can confirm it. This exists so that an admin account in the wrong
   hands still cannot redirect anybody's money.
2. **Nothing you read is silent.** Revealing a fee, revealing a bank row and
   exporting the payment run each write an audit row with your address on it.
   Reading these things is a legitimate part of the job; reading them
   unrecorded is not.

There is also no generic table editor anywhere in the admin. Every change is a
specific form, with validation, that writes an audit row in the same
transaction. If something needs changing that has no form, that is a
conversation with Leo, not a query.

## People — `/admin`

Everyone, with **fees shown as bullets until you ask for one**. Click to
reveal, one at a time. This is so a screenshare of this page does not leak the
payroll — which is a thing that happens.

Each reveal is audited.

## One person — `/admin/people/{id}`

Everything about one contractor:

- **Profile** — their letterhead details.
- **Fee history** — every fee they have had, when it started, and the note
  explaining why.
- **Bank** — masked, with a **Reveal** button per row. Revealing is audited.
  You need this: you pay these invoices and the numbers are printed on every
  PDF you already hold.
- **Invoice history** — every invoice they have issued.

### Setting a fee

Three fields, all required:

| Field | Why |
|---|---|
| Amount | Pounds and pence. |
| Valid from | Fees are **effective-dated**, not overwritten. Setting a new one does not rewrite what past invoices were billed at. |
| Note | **Required.** A fee change with no reason is refused. In six months this note is the only thing that explains the number. |

If a fee changes inside the month it is being billed for, that month's invoice
is **flagged** on the Period screen. It is not blocked — just visible.

### Proposing a bank correction

If somebody's details are wrong, **Propose a change**. Type the account number
twice, same as they would.

It lands as **pending**. It changes nothing. They see it on their own bank
screen as *"Robin has suggested a change"* and they either agree or type their
own. It sits on your **Queue** until they do.

### Flagging a row back

**Flag** with a reason sends them an email saying what needs fixing. It changes
nothing about what is payable either — a flag is a request, not an edit.

### Deactivating someone

They can no longer invoice. **Nothing is deleted, ever** — their history stays
exactly as it was.

## Period — `/admin/period`

The month at a glance, and the screen you live on between the 22nd and the 29th.

- **X of Y received.**
- **Not yet in** — who has not invoiced. This is the chasing list, and it is
  the list that used to be a phone call.
- **Worth a look** — invoices that are fine but unusual:

| Flag | Means |
|---|---|
| `late` | Submitted after the 28th. Still valid, still paid. |
| `fee_changed` | Their fee changed during the month being billed, with the note. |
| `bank_changed` | Bank details changed close to the window closing. Flags, never blocks. |
| `large_expense` | An expense over the threshold, with what it was for. |

None of these stop anything. They tell you where to look.

Add `?month=2026-08` to the URL for a previous month.

## Payment run — `/admin/payment-run`

The month's invoices split into the two batches paid separately: **UK** and
**international**. Account numbers are **masked on screen**.

**Export CSV** gives you the file to pay from, with the numbers in full. It is
a button rather than a link on purpose — exporting the payroll is an action,
and the audit row is written before the file is handed over.

Every line is read from the **print snapshot** taken when the invoice was
issued, never from the person's current bank row. The account that was printed
is the account that gets paid, even if they have changed their details since.
An invoice and its payment can never disagree.

## Data check — `/admin/data-check`

The go-live grid: four ticks per person, and everything the spreadsheet import
flagged for a human to look at, such as:

- bank details on file but never confirmed by the person
- no bank details at all
- the legal name differs from the display name — *confirm this is the intended
  payee*
- no role for the invoice letterhead

This is the screen that answers *"will the first real month actually work?"*
It should be clean before go-live.

## Queue — `/admin/queue`

Anything waiting on somebody. Check it during the window.

**Needs fixing** is the important panel, and it is the answer to *"what if an
invoice silently doesn't arrive?"*:

- **An invoice took a number and never finished sending.** On the Period screen
  that person may still count as *received*, because a row exists. Here it is
  named as unfinished.
- **An email gave up** after its retries. Queued, never delivered.
- **Somebody has never confirmed their bank details**, so they cannot invoice
  at all and may not realise it.

None of these show up anywhere else. If this panel is empty, nothing is stuck.

The rest of the screen is the ordinary waiting list: people with no fee set,
bank changes you proposed that their owner has not answered, and imported bank
rows nobody has confirmed.

## What arrives on its own

Scheduled jobs, no button to press:

| When | What |
|---|---|
| Every 5 minutes | Queued email is sent, with retries and backoff. |
| Daily, 20th–28th | Kudos read from the sheet. **[not yet built: sheet not connected.]** |
| 22nd, 09:00 | *"Time to invoice"* to everyone. **[not yet built: email.]** |
| 27th, 09:00 | Nudge to whoever has not invoiced. **[not yet built: email.]** |
| 29th, 09:00 | Reconciliation summary to finance. **[not yet built: email.]** |
| Nightly, 02:00 | Database backup, thirty days kept. |

All times are London time.

## The invoice email you receive

**[not yet built: email is not connected.]** When it is, each invoice arrives as
`Invoice {number} — {legal name} — {Month} {Year}`, with the PDF attached, the
receipts attached, and the full breakdown in the body — fee, kudos, and each
expense line separately.

There is **no fee figure in the subject line**, so a notification on a locked
phone screen does not show somebody's pay.

If a receipt could not be attached for any reason, **the email is still sent**,
with a line saying what is missing. An invoice never waits on an attachment.

---

## What is not built yet, in one list

For whoever writes the on-screen tutorial — do not point at any of these:

| Not built | Blocked on |
|---|---|
| All email | DNS for the Resend sending domain |
| Kudos from the sheet | The sheet's file id and column layout |
| Receipt upload in the wizard | Nothing — validation and conversion are written, the UI is not |
| A finance-side PDF download | Built: the PDF link on a person's page. There is no Drive mirror |

One known gap in the spec itself: somebody who misses a month entirely has no
way to invoice for it afterwards. Not a bug in the code — the spec has no
answer for it yet.
