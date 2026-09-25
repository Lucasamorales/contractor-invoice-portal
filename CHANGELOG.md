# Changelog

What changed, newest first. Versions are the image tags deployed to staging and
then production; dates are when the change reached production, or staging if it
has not been released yet.

---

## 25 September 2026

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
