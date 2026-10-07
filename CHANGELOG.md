# Changelog

What changed, newest first. Versions are the image tags deployed to staging and
then production; dates are when the change reached production, or staging if it
has not been released yet.

---

## 7 October 2026

- **Seven wizard tests no longer fail in October.** They assume September (the
  invoice window, kudos due at a quarter end, period 2026-09) and read the real
  clock, so they passed through September and failed together once the month
  changed. CI had not run since the 29th, so nothing had gone red yet, but the
  next push to `staging` or `main` would have. The shared fixture now pins the
  clock. Test-only; nothing in `app/` changes and nothing needs deploying.

## 7 October 2026, leave module (on the `leave-module` branch, not deployed)

Nothing here is in the image: `app/`, the Dockerfile and the database are
untouched.

- **The leave module's rules**, as pure code and tests (R-LEAVE-01 to 19): the
  leave year, half-day counting over working days and holidays, a request's
  lifecycle and overlap, a balance kept as a ledger nobody edits so a
  cancellation gives days back by an entry, the leaver formula from the brief
  exact and unrounded, and the birthday rule. Nothing is wired in and nothing
  about any person is stored. The spec is `modules/leave/SPEC.md`; what is
  missing, and what each piece waits on, is `modules/leave/BACKLOG.md`.
- **The walls between modules are tests** (`tests/architecture`): the invoice
  app never imports a module, a module never imports the invoice app or another
  module, and a module's domain uses only the standard library and never reads
  the clock. `tests/domain/test_purity.py` now walks `modules/*/domain` too.
- `docs/decisions/0001-modular-hub.md`: the plan, the architecture diagram, and
  what is decided versus proposed.

## 29 September 2026

- **The invoice PDF is named after the month and the amount** (R-PDF-15):
  `September 2026 — Jane Smith — £2594.50.pdf`, where it used to be
  `2026-09 — Jane Smith — 34.pdf`. It is the name somebody reads in a downloads
  folder or an email attachment, and a period key and an invoice number answer
  neither of the questions being asked there. The stored object is unchanged:
  it is sorted by people who do want the period key, and its name has to stay
  unique now that a cancelled invoice gives its number back.
- In the download header's plain-ASCII fallback the pound sign becomes "GBP"
  rather than being dropped, so the name does not end in a bare number.


- **A receipt can no longer be lost by pressing on too quickly.** The file is
  uploaded the moment it is chosen, and the field holding its location is
  cleared until the reply arrives — so sending the invoice in between recorded
  an expense with no receipt while the file sat in the bucket, unclaimed and
  unmentioned. One September invoice lost a receipt exactly that way, with ten
  seconds between the two. The wizard now waits: the button says "Waiting for
  the receipt…" until every upload has answered.
- **The invoice email names any expense with no receipt.** Before, only a
  receipt that existed and could not be fetched was mentioned, so accounts had
  no way to tell "none was given" from "one was given and we lost it".
- A test that pinned the summary's clock to the 29th but let the database write
  `created_at` from its own failed once the real date caught up. The row is
  pinned too now.

## 28 September 2026

- **A counter can be pointed back at a number a cancelled invoice released.**
  `set_counter` refused to move backwards at all, which was right under the old
  rule and wrong under R-NUM-05: somebody voided before the change has a
  counter that was already pushed past the number they should get back, and
  nothing live to cancel. It now moves back onto a released number only, never
  onto one a live invoice holds and never into the invoices issued before this
  system existed.
- **A migration aimed at production can no longer land on staging.** `.env`
  holds staging's credentials for local development, including
  `PORTAL_MIGRATION_URL`, and a migration prefers that setting — so exporting a
  production URL was not enough and the file quietly won. When the environment
  says production the file is not read at all; those credentials are
  break-glass and live in Secret Manager (R-SEC-09).
- **A migration now names the database it is about to change** — its own name,
  its current revision and how many people are in it — before changing
  anything. Both databases are called `neondb`, so the name proves nothing on
  its own; the people count does.

  *Corrected 29 September: this entry first said the two environments shared a
  Neon endpoint and were told apart by a branch inside the connection string.
  They do not. They are separate Neon projects — production is
  `ep-curly-king-b2om6fwn`, staging is `ep-jolly-flower-b2d09ygh` — and the
  hostname distinguishes them perfectly well. Both guard messages named staging
  only because `.env` was supplying the URL both times.*


- **A cancelled invoice gives its number back** (R-NUM-05, reversing the
  original rule). The next invoice is now the lowest number no live invoice of
  theirs holds, so a voided invoice 10 is reissued as invoice 10. The first
  cycle left three people with a permanent gap — 34, 32 and 47 — and the
  numbering belongs to the contractor, continuing the sequence they kept before
  this system existed: a hole in it reads as a missing invoice and has to be
  explained to an accountant every time.
- Cancelling two in a row gives both numbers back, in order, so nobody fills
  the second gap and keeps the first.
- What has not changed: a voided invoice is never edited or deleted, two live
  invoices still cannot share a number, and the sequence can never walk back
  below where it started.
- **Voiding now emails the person and accounts** (R-SUB-11) — which invoice,
  which month, why, and that the replacement will carry the same number. It
  carries no figure. The gap in the numbering used to be the signal that
  something had been replaced; this is.
- **The reissue's email says `(resent)` in its subject**, because the cancelled
  invoice is sitting in the same inbox with the same number on it.
- Stored PDFs now carry the invoice id in their object name, so a reissue
  cannot overwrite the document it replaces. What a person downloads is
  unchanged.
- Migration `0005`: the one-number-per-person constraint now applies to live
  invoices only.

## 25 September 2026

- **Somebody can be moved between payout regions** (`app.scripts.set_payout_region`).
  The region is set once at import from the spreadsheet's NOTES column and
  nothing could change it afterwards, so a contractor moving from an EUR
  account to a UK one was stuck: the bank screen renders IBAN and Swift/BIC for
  an international payee and the validation demands them, leaving no way to
  type a sort code.
- The region and the old account move **together**, in one transaction with an
  audit row each. Moving the region alone would leave the region saying UK
  while the only account held an IBAN — nothing would block a submit, and the
  invoice would be rendered against UK fields that are not there, after the
  number had already been allocated. Retiring the account instead lands on the
  ordinary "confirm your bank details first" path.
- It refuses to run while an invoice is unfinished or a bank change is waiting
  to be confirmed, because both would be resolved against the old region.


- **Accounts can read the kudos sheet on demand** (R-KUD-24). A button on the
  kudos screen runs the same job the scheduler runs at 07:00, so a kudo given
  during the day no longer waits until tomorrow to appear — which matters in
  precisely the six days a month when people are invoicing. The daily sync is
  unchanged. The screen also states when the sheet was last read, so nobody has
  to press anything to find out whether it is current.
- **Two syncs can no longer run at once.** The job takes a transaction-scoped
  advisory lock; whichever of the button and the scheduler arrives second does
  nothing and says so. Before this there was only one caller, so nothing had to
  arbitrate, and a collision would have failed on the duplicate event key.
- **The sheet read no longer blocks the service it runs on.** It is a
  synchronous HTTP call of up to twenty seconds and now runs off the event loop,
  so it cannot stall somebody else's request.
- `SPEC.md` corrected: R-KUD-02 claimed the sync also ran when a member opened
  the wizard. It never did.

## 24 September 2026

- **A counter can be corrected for one person.** The spreadsheet's `INVOICE N`
  column is a hand-kept snapshot, and one row was 28 invoices out of date: it
  said 34 where the person had reached 62, so the portal offered her 35. The
  only fix until now was to correct the file and re-import, which takes the
  higher number correctly but also clears everybody's confirmed profile and
  sends the whole company back through the details step. `app.scripts.set_counter`
  changes one row, for one person, with a reason and an audit entry, and refuses
  to wind a counter back or to hand out a number already spent (R-NUM-05).
- That person's next invoice number is now 63. She had not submitted, so
  nothing had to be voided.

## 0.8.20 — 22 September 2026

- **Voiding an invoice gives its kudos back.** The claim was made inside the
  submit transaction and never released, so a voided invoice still counted as
  having billed its kudos and any quarterly prize: the reissue came out without
  them, and no later invoice could pick them up. Voiding now releases both, and
  the audit row records how many.
- A test that read the real clock failed every 22nd to 28th. The date is pinned.
- **Old images are deleted after seven days** rather than thirty, keeping the
  five most recent whatever their age. Every deploy pushes an image, and the
  registry had reached 447MB of its 500MB free allowance in three weeks.
- Documentation brought up to date with production, and this changelog started.

## 0.8.19 — 16 September 2026

- **Emails link to the portal without printing its address.** The portal's
  address is the long Cloud Run one and read as a mistake in the footer of every
  email and after each action in the plain-text copy. Buttons and the footer
  still link; the footer now reads "Invoices".

## Production launch — 16 September 2026 (0.8.16 to 0.8.18)

- **Production is live**: both services, the bucket, five scheduled jobs, and
  sign-in for everyone on the Workspace domain.
- **The 16 people were imported** from the spreadsheet with their invoice
  numbering and their bank details, unconfirmed, before anyone signed in. The
  kudos names were mapped, and the first kudos sync read the sheet.
- **A go-live switch** (`reminders_live`) holds back the window-open, reminder
  and summary emails until an environment is ready. Production launched with it
  off and it was switched on the same day.
- **The jobs service now knows the portal's address.** It sends every email, and
  without the address each email's button was silently left out — staging's
  emails had been going out that way.
- **Migrations connect the way the app does**, so they run from Windows against
  production instead of failing on the certificate store.
- **The finance inbox is configured**, and production refuses to start without
  one. Unset, invoices reached only the person who sent them.
- **Who may not cash kudos** is a configured list, applied at sign-in and by the
  import: leadership, and staff who do not invoice.

## Infrastructure — 16 September 2026

- **Cloud Run CPU is billed only while a request is handled.** Setting a memory
  limit had silently turned the provider's default off, and the scheduled-jobs
  service was billed around the clock: about £1.50 a day.
- **Budget alerts** on the billing account: staging £3, production £5, and £10
  for the whole project, emailing at 50%, 90%, 100% and on a forecast overspend.
- **Terraform state moved to a versioned bucket**, one workspace per
  environment, and a plan now refuses to run when the workspace and the settings
  disagree. Applying production settings over the staging state would have
  destroyed 17 things.
- **The image registry and the budgets moved to `infra/shared`.** The registry
  belonged to the environment stack and would have collided on the first
  production apply.
- **`staging.tfvars` and `production.tfvars` are committed.** They hold no
  secret, and access changes are now reviewed like code.
- **The outbox runs every 15 minutes instead of every five**, so the database
  can sleep between runs and stays inside its free allowance.
- **The Drive mirror was dropped** (migration `0004`). Invoices live in storage,
  finance receive every one by email and can open any from a person's page, and
  a Drive copy would have been a second place holding everyone's account number.

## 0.8.13 to 0.8.15 — 15 September 2026

- **Flags are visible to the person they are about.** Accounts can flag bank
  details, and now a letterhead too. The person sees a card on My details, and
  on the screen that fixes it, with two ways out: change it, or say it is
  correct. Accounts see whether a flag is waiting, was changed, or was answered,
  and open flags wait on the Queue.
- **The invoice window opens on the 22nd** rather than the 24th. It still closes
  on the 28th, and nobody is locked out after it.
- **A tutorial on every screen.** Six screens had none.
- **Every amount on a Finance screen opens hidden** and toggles when pressed, so
  a screenshare does not show anyone's pay. Payment run has show all.
- **Receipts upload again.** The screen's script was requested in a way that
  never loaded, so choosing a file did nothing; no receipt had ever reached the
  server. Receipts in the email are now named after their expense.

## 14 September 2026

- **The copy review.** Twenty-four wording changes from the review sheet, in the
  app, its refusals and its emails.

## 7 to 9 September 2026

- Night mode, decided on the server so no screen flashes on the way in.
- A person's record rebuilt as one column, with the panel in the URL.
- **Void an invoice**, the only correction there is.
- An email never gives up, and never gives up quietly: the outbox retries with
  backoff and anything stuck is named on the Queue.
- Emails laid out, with a link back to the portal.
- The explanatory copy left the screens and moved into the tutorial.
- The tab icon, rendered from the app's own font.
- Fixes: every boosted navigation landed on a blank page; the theme toggle
  cancelling itself out and reverting; a refused submit answering 200; the first
  run becoming one run in whichever order somebody starts; scrollbars inside the
  preview and the tutorial; a repeated account number checked as it is typed.

## 2 to 4 September 2026

- The foundations, in build order: the skeleton and the schema, bank-field
  encryption, the spreadsheet import with its report, the invoice template and
  renderer, the submit transaction, the member wizard, the admin screens,
  storage and the nightly backup, the mailer and its outbox, the kudos claim
  ledger, receipt validation and conversion, the Ledger design, and the
  Terraform that put staging up.
