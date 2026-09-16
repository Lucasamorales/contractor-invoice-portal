# SPEC.md — Invoice Portal

Version 1.0 · September 2026 · owner: Leo Marsh

Every requirement has an ID. Reference IDs in commits and PRs. If something you
need to build is not covered by an ID, the spec has a gap — **ask, don't
assume.**

Companion documents:
- `CLAUDE.md` — how we work in this repo, non-negotiables, stack, layout.
- Architecture and rationale: see `SPEC.md`
- Input data: `data/Data Invoices 1.xlsx` (16 rows, gitignored — contains real bank details).
- Invoice template: `data/invoice-template.pdf` (the layout to reproduce).

---

## 0. Glossary

| Term | Meaning |
|---|---|
| **Person** | A Your Company contractor who invoices monthly. All 16 are contractors, not employees. |
| **Robin** | Administrator / accounts / HR. Receives invoices, pays them, sets fees. |
| **Finance group** | Robin, Charlie, Remy. The only people who may see fees. |
| **Kudos** | Internal reward. **1 kudo = £20.** Counts live in an existing Google Sheet, semi-public. |
| **Fee** | A person's monthly remuneration. Sensitive. Only the finance group and the person may see it. |
| **Period** | One calendar month, `YYYY-MM`. The submission window is the 22nd–28th. |
| **Additional** | One combined figure on the invoice: kudos + expenses + any other item the person adds. |
| **Payout region** | `UK` or `INTL`. How Robin pays them, **not** a currency. Everything is invoiced in GBP. |

---

## 1. Actors and access

- **R-ACC-01** Every user authenticates through Cloud IAP with their
  `@example.com` Google Workspace account. The app writes no login code.
- **R-ACC-02** The app reads the IAP-signed JWT assertion header, verifies its
  signature and audience, and derives the user's email. It trusts nothing else
  for identity — no query parameter, no form field, no cookie.
- **R-ACC-03** A verified email is resolved to a `people` row. No row → the
  person is created with `status='pending'` (R-ONB-01).
- **R-ACC-04** `/admin/*` additionally requires membership of the finance IAM
  group. Checked in the app as well as at IAP — defence in depth.
- **R-ACC-05** Every query for personal data is scoped by `person_id` derived
  from R-ACC-02. There is no route anywhere that returns another person's fee or
  bank details to a non-finance user.
- **R-ACC-06** Robin (finance) may **read** bank details in full. He pays these
  invoices and the numbers are printed on every PDF he receives. Reveal actions
  are written to `audit_log`.
- **R-ACC-07** No admin may **confirm** a bank change. See R-BANK-07.
- **R-ACC-08** Robin is also a person: he uses the member wizard for his own
  invoice exactly like everyone else. Admin is a separate area, not a separate
  account.

---

## 2. Data model

Postgres. All money is `INTEGER` pence. All timestamps `TIMESTAMPTZ` in UTC.

- **R-DATA-01** `people`
  `id, email UNIQUE, display_name, legal_name, role, team, address, phone,
   contact_email, status ENUM(pending|active|inactive), payout_region ENUM(UK|INTL),
   profile_confirmed_at, created_at, updated_at`
  - `display_name` is how we address them in the UI; `legal_name` is what goes
    on the invoice letterhead and must match the bank account holder.
    They differ in the real data (see R-SEED-08).
  - `role` is the job title for the invoice ("Creative Director"), **not** `team`.
    Nobody in the spreadsheet has one — collected at first login (R-WIZ-02).

- **R-DATA-02** `fees`
  `id, person_id FK, fee_pence, valid_from DATE, valid_to DATE NULL, set_by, note, created_at`
  - Effective-dated. Never `UPDATE` a fee row; close it and insert a new one.
  - A March invoice must still explain itself in December.
  - No fee row for a period → that person cannot invoice (R-WIZ-09).

- **R-DATA-03** `bank_details`
  `id, person_id FK, account_name, bank_name, account_number_enc, sort_code_enc,
   iban_enc, swift_bic_enc, source ENUM(import|self|admin_proposed),
   state ENUM(unverified|active|pending|rejected|superseded),
   verified_at, verified_by, valid_from, valid_to, created_at`
  - `*_enc` columns hold ciphertext (R-SEC-03).
  - Exactly one row per person may be `active` at a time.
  - Rows are never edited in place; a change creates a new row.

- **R-DATA-04** `kudos_snapshots`
  `person_id, period, kudos_count, rate_pence, synced_at` — PK `(person_id, period)`.
  `rate_pence` defaults to 2000 (£20) and is stored per snapshot so a future rate
  change cannot rewrite history.

- **R-DATA-05** `expenses`
  `id, invoice_id FK, kind ENUM(expense|other), description, amount_pence, receipt_uri NULL, created_at`

- **R-DATA-06** `invoices`
  `id, person_id FK, period, number INTEGER, status ENUM(draft|submitted|void),
   is_late BOOLEAN, remuneration_pence, additional_pence, total_pence,
   detail_note TEXT NULL, currency CHAR(3) DEFAULT 'GBP',
   pdf_uri, idempotency_key UNIQUE,
   issued_at, due_at, voided_at NULL, replaced_by_id NULL, created_at`
  - **`UNIQUE (person_id, number)`** — a real database constraint, not app logic.
  - `CHECK (total_pence = remuneration_pence + additional_pence)`.
  - `UNIQUE (person_id, period) WHERE status <> 'void'` — one live invoice per
    person per period.

- **R-DATA-07** `invoice_lines`
  `id, invoice_id FK, kind ENUM(fee|kudos|expense|other), label, qty, unit_pence, amount_pence`
  - The breakdown the PDF collapses into "Additional". Frozen at issue time.

- **R-DATA-08** `counters`
  `person_id PK, next_number INTEGER, updated_at` — the row locked by R-NUM-02.

- **R-DATA-09** `print_snapshot`
  `invoice_id PK, letterhead JSONB, bill_to JSONB, bank_block JSONB, created_at`
  - Exactly what was printed, so an invoice can be re-rendered byte-identically
    and Robin can always answer "which account did we pay on 25 August".

- **R-DATA-10** `outbox`
  `id, kind, payload JSONB, status ENUM(pending|sent|failed), attempts, last_error, created_at, sent_at`

- **R-DATA-11** `audit_log`
  `id, actor_email, action, entity, entity_id, before JSONB, after JSONB, at`
  - Written in the same transaction as the change. Bank and fee values in
    `before`/`after` are masked to last four digits — the audit log is not a
    second copy of the payroll.

- **R-DATA-12** All migrations are Alembic, reversible, and tested both ways.

---

## 3. Seeding from the spreadsheet

Input: `data/Data Invoices 1.xlsx`, sheet `Data Invoices`, 16 data rows.
Columns: `NAME, TEAM, EMAIL, REMUNERATION, NOTES, BANK ACCOUNTS, INVOICE N°`.

- **R-SEED-01** The seed script runs `--dry-run` by default and prints a report.
  It only writes with an explicit `--commit` flag.
- **R-SEED-02** **`REMUNERATION` is never imported.** Every value is the
  placeholder `1000.0`. Fees are `NULL` until Robin types them (R-ADM-03).
- **R-SEED-03** `NOTES` maps to `payout_region`: `UK` → `UK`, `EUR` → `INTL`.
  It is not a currency. All invoicing is GBP (R-INV-03).
- **R-SEED-04** `BANK ACCOUNTS` is a free-text blob, one `Key: Value` per line.
  Keys are normalised case-insensitively with whitespace collapsed, using this
  map — **all of these variants occur in the real file**:

  | Normalised key | Field |
  |---|---|
  | `name` | `account_name` |
  | `bank`, `bank name` | `bank_name` |
  | `sort code`, `sortcode`, `bank branch`, `branch number`, `branch code` | `sort_code` |
  | `account number`, `account no` | `account_number` |
  | `iban` | `iban` |
  | `swift/bic`, `swift`, `bic` | `swift_bic` |

  Lines containing `use when sending` are ignored (they are captions from the
  template). Any line that is neither a known key nor an ignored caption is
  reported as `unparsed` and the row is flagged, never silently dropped.

- **R-SEED-05** Normalisation: strip surrounding whitespace (the real file has a
  name padded with ~70 trailing spaces); collapse internal runs of whitespace;
  uppercase and strip spaces from IBAN and BIC; store `sort_code` as six digits
  with no separators (the file has both `230801` and `23-08-01`).
- **R-SEED-06** Validation by `payout_region`:
  - `UK`: requires `sort_code` (6 digits) **and** `account_number` (8 digits).
    `iban`/`swift_bic` optional.
  - `INTL`: requires `iban` **and** `swift_bic`. A missing `sort_code` is **not**
    an error. (Jamie is the only INTL row: Luxembourg IBAN, no sort code —
    valid.)
- **R-SEED-07** IBAN validation: length and country-code shape, then **mod-97
  checksum**. For `GB` IBANs, additionally cross-check the embedded sort code
  (chars 9–14) and account number (chars 15–22) against the separately stated
  fields; a mismatch is a flag, not a silent overwrite. Most of the team bank
  with Wise, so this catches transpositions for free.
- **R-SEED-08** `NAME` → `display_name`; the blob's `Name:` → `legal_name`.
  Where they differ the row is flagged for the person to confirm. Real examples:
  "Gabriel Marlow (SIMON)" pays into "Simon Andrew Robinson"; **Marta
  Jamie's account name is "Bridge Building"** — a company, which Robin must
  confirm is intended (R-ADM-06). Three rows have no `Name:` line at all.
- **R-SEED-09** `INVOICE N°` is the **last invoice number that person issued**.
  `counters.next_number = INVOICE N° + 1`. Values in the file range 0–213;
  Gabriel's `0` means he has never invoiced, so his first is `1`.
- **R-SEED-10** Every imported bank row is written with `source='import'`,
  `state='unverified'`, `verified_at = NULL` — **regardless of how clean it
  looked.** Nothing in the spreadsheet is treated as true. The previous version
  of this file contained a 20-character GB IBAN (must be 22); that is why.
- **R-SEED-11** People are imported with `status='active'`, `profile_confirmed_at = NULL`.
- **R-SEED-12** The seed is idempotent: re-running matches on `email` and updates
  rather than duplicating. It never overwrites a `self`-sourced bank row with an
  imported one.
- **R-SEED-13** The report prints, per person: parsed fields (account number and
  IBAN masked to last four), every warning, and the resulting `next_number`. It
  ends with a count of rows that need human attention.

---

## 4. Invoice numbering

- **R-NUM-01** Numbers are **per person**, continuing their existing sequence.
  Two people may hold the same number.
- **R-NUM-02** Allocation happens inside one short transaction:
  `SELECT next_number FROM counters WHERE person_id = :id FOR UPDATE`, insert the
  invoice with that number, increment the counter, commit. Nothing slow (PDF
  rendering, GCS, email) happens inside this transaction.
- **R-NUM-03** `UNIQUE (person_id, number)` on `invoices` is the backstop. If it
  ever fires, that is a bug and must surface as a 500, not be swallowed.
- **R-NUM-04** Printed **plain, with no zero padding**: `8`, `34`, `213`.
- **R-NUM-05** A voided invoice does **not** release its number. Reissues take
  the next one. Gaps are acceptable; duplicates are not.
- **R-NUM-06** Submitting is idempotent on `idempotency_key` (R-SUB-02): a repeat
  returns the same invoice and never allocates a second number.

---

## 5. The period

- **R-PER-01** A period is `YYYY-MM`, evaluated in Europe/London.
- **R-PER-02** The submission window opens on the **22nd** and closes on the
  **28th** of that month, inclusive. *(Moved from the 24th on 15 September 2026.)*
- **R-PER-03** An invoice submitted during the window belongs to the month the
  window is in.
- **R-PER-04** **Nobody is ever locked out.** After the 28th the portal still
  accepts a submission; the invoice is stored with `is_late = true` and flagged
  in the admin period view. The obligation to invoice does not go away — a locked
  door just becomes a phone call to Robin, which is what this system removes.
- **R-PER-05** A late submission still receives that person's next number in
  sequence.
- **R-PER-06** Before the 22nd the wizard is readable (profile, bank, history)
  but the submit action is disabled with: *"The window for {Month} opens on the
  22nd."*

---

## 6. Kudos sync

- **R-KUD-01** A read-only Google Sheets API client, scoped to **one file ID**,
  using the Cloud Run service account. Never a broader Drive scope.
- **R-KUD-02** Sync runs on schedule (R-JOB-01) and on demand when a member opens
  the wizard, whichever is fresher.
- **R-KUD-03** Rows are matched to people by email. An unmatched row is reported,
  never guessed.
- **R-KUD-04** `amount = kudos_count × rate_pence`, rate 2000 (£20), stored on the
  snapshot. The rate is config, never a literal in code.
- **R-KUD-05** The snapshot is frozen onto the invoice at submit. Editing the
  sheet in November must not change October's invoice.
- **R-KUD-06** If the sheet is unreachable, the wizard shows kudos as
  "unavailable — Leo has been notified" and **still allows submission** with
  kudos = 0 only if the person explicitly acknowledges it. It must never silently
  invoice £0 of kudos.

---

## 7. Member wizard

All screens are Jinja + HTMX, server-rendered, one form per step.

- **R-WIZ-01** **Welcome** — greeting by `display_name`, the period, the fee, the
  kudos count and their value, and the running total. All figures read-only.
- **R-WIZ-02** **Your details** — `legal_name`, `role`, `address`, `phone`,
  `contact_email`. Shown on first login and whenever incomplete; reachable at any
  time from "My details". Sets `profile_confirmed_at`. All five are required
  because all five print on the invoice (R-PDF-02).
- **R-WIZ-03** **Additional** — kudos shown read-only; the person adds any number
  of items, each `description` + `amount` + optional receipt upload. Empty is
  valid and means £0.
- **R-WIZ-04** Receipts upload **directly from the browser to GCS** via a V4
  signed URL. Bytes never pass through Cloud Run. Accept `image/jpeg`,
  `image/png`, `image/heic`, `application/pdf`; max 10 MB each; max 10 per
  invoice. Reject anything else with a clear message naming the accepted types.
- **R-WIZ-05** **Bank details** — the current row is displayed with the account
  number and IBAN masked to last four. The person confirms it (sets
  `verified_at`) or corrects it (R-BANK-03).
- **R-WIZ-06** **Preview** — the invoice rendered from the *same* HTML template
  that produces the PDF (R-PDF-01), so what they approve is what gets sent.
- **R-WIZ-07** **Submit** — one button, disabled after the first click, posting an
  idempotency key. Success screen shows the invoice number and that it has gone
  to Robin.
- **R-WIZ-08** **My details** is permanent, not a one-time wizard step: current
  bank row plus the history of changes to it, shown as
  `12 Aug · ••••1955 → ••••4321`. This is how a person notices a change they did
  not make.
- **R-WIZ-09** Submission is blocked, with a specific message, when:
  - no fee is set → *"Robin hasn't set your fee yet — he's been notified."*
  - bank row is not `active`/verified → *"Confirm your bank details first."*
  - profile incomplete → *"We need a few details for your invoice letterhead."*
  - an invoice for this period already exists → show it instead.
- **R-WIZ-10** All copy is in English. Errors say what is wrong and how to fix it.

---

## 8. Bank details lifecycle

- **R-BANK-01** States: `unverified` → `active` → (`pending` → `active`), plus
  `rejected` and `superseded`.
- **R-BANK-02** **An invoice can only be produced from an `active`, verified row.**
  An imported row cannot produce an invoice.
- **R-BANK-03** Changing bank details creates a **new row**, never an update. The
  account number must be typed twice and must match (double entry catches
  transpositions no checksum can).
- **R-BANK-04** Validation on entry is the same as R-SEED-06 and R-SEED-07,
  including the Wise IBAN cross-check.
- **R-BANK-05** On a change, the previous row is closed (`valid_to`, `superseded`)
  and an email goes to **both the person and Robin**, showing old and new last
  four digits only.
- **R-BANK-06** A change made within 72 hours of the window closing does not block
  the invoice; it flags it in the admin period view with both last-fours.
- **R-BANK-07** Robin may read, review, flag back to the person, and **propose** a
  correction — a proposed row sits `pending` until the owner confirms it. He can
  never make a row payable himself. This is the control that means a taken-over
  admin account cannot redirect the payroll.
- **R-BANK-08** Flagging back sends the person an email saying what needs fixing.

---

## 9. The invoice PDF

Reproduce `data/invoice-template.pdf` exactly. A4 portrait. Only the changes
below are permitted.

- **R-PDF-01** One `templates/invoice.html` + CSS, rendered by WeasyPrint. The
  same template renders the on-screen preview.
- **R-PDF-02** Letterhead, centred: `legal_name` (bold), `role`, `address`,
  `phone`, `contact_email` — from `people`, frozen into `print_snapshot`.
- **R-PDF-03** Bill-to block, fixed, from config (versioned so a future address
  change does not rewrite old invoices):
  ```
  Charlie Castellanos
  Your Company Ltd
  Example House
  1 Example Street
  Example Town EX1 2AB
  ```
- **R-PDF-04** `Your ref: Your Company` — constant.
- **R-PDF-05** `Invoice No: {number}` — plain, unpadded (R-NUM-04).
- **R-PDF-06** `Invoice Date: {issued_at}` in `DD/MM/YYYY`.
- **R-PDF-07** `ROLE: {role}` (uppercase, bold) and beneath it
  **`{Month} {Year}`**. The period line is an addition to the template, agreed
  with Robin: the original says nothing about which period it covers.
  *Amended 3 September 2026 (Leo):* it read `Your Company — {Month} {Year}`.
  The "Your Company" was redundant three lines under `Your ref: Your Company`, so it
  is now the month and year alone.
- **R-PDF-08** Right-aligned figures: `Remuneration: £X`, `Additional: £X`,
  then `Total Invoice: £X`.
- **R-PDF-09** **New:** directly beneath `Additional`, an italic breakdown in
  smaller type, rendered **only when `additional_pence > 0`**. Kudos first,
  then each expense description and amount, **one per line**:

  ```
  3 kudos × £20
  travel £34.50
  hotel £120
  ```

  *Amended 3 September 2026 (Leo):* these were originally one run-on line
  separated by `·`. Three or four items that way is hard to read, and this is
  the part of the invoice an expense claim gets checked against.
- **R-PDF-10** `Please pay within 14 days of the invoice date.` — constant, and
  `due_at = issued_at + 14 days`.
- **R-PDF-11** Bank Details block, rendered **conditionally by `payout_region`**
  (the template already carries both captions):
  - `UK`: `Name`, `Bank`, `Account number`, `Sort code`, then
    *"Use when sending money from the UK"*; then `IBAN` and `Swift/BIC` with
    *"Use when sending money from outside the UK"* if present.
  - `INTL`: `Name`, `Bank`, `IBAN`, `Swift/BIC`, with the outside-the-UK caption
    only.
  Sort codes print as `NN-NN-NN`; IBANs in groups of four.
- **R-PDF-12** Amounts print as `£1,234.56`, two decimals, thousands separated.
- **R-PDF-13** The PDF must fit on one page for all 16 people. A visual
  regression test compares a rendered sample against a committed reference.
- **R-PDF-14** No VAT line, no tax ID — everyone invoices as a contractor.
- **R-PDF-15** Filename: `{period} — {legal_name} — {number}.pdf`.

---

## 10. Submitting an invoice

- **R-SUB-01** `POST /submit` with an `Idempotency-Key` header generated by the
  wizard when the preview is rendered.
- **R-SUB-02** If an invoice already exists for that key, return it. No second
  number, no second email.
- **R-SUB-03** Transaction 1 (short): lock the counter (R-NUM-02); insert
  `invoices` with `status='draft'`; insert `invoice_lines` snapshotting fee, kudos
  count and rate, and every expense; insert `print_snapshot` with the letterhead,
  bill-to and bank block as they are right now; increment the counter; commit.
- **R-SUB-04** Outside any transaction: render the PDF, upload it to GCS as a
  private object.
- **R-SUB-05** Transaction 2: set `status='submitted'`, `pdf_uri`, `issued_at`,
  `due_at`, `is_late`; insert an `outbox` row for the email; commit.
- **R-SUB-06** The email is **never** sent inline. The dispatcher (R-JOB-04)
  sends it. A provider outage must not lose an invoice Postgres already accepted.
- **R-SUB-07** The email to Robin: PDF attached, receipts attached, subject
  `Invoice {number} — {legal_name} — {Month} {Year}`. **No fee figure in the
  subject.** Body carries the full breakdown (fee, kudos, each expense) — this is
  where the detail lives regardless of what the PDF shows.
- **R-SUB-08** A copy goes to the person.
- **R-SUB-09** If PDF rendering fails, the invoice stays `draft`, the number
  stays allocated, and the person sees an error telling them to try again — the
  retry reuses the same draft.
- **R-SUB-10** Corrections: void + reissue with `replaced_by_id`. Never edit a
  submitted invoice's figures.

---

## 11. Admin portal

- **R-ADM-01** **People** — all 16 with team, status, whether a fee is set,
  whether they have invoiced this period. **Fees masked by default** (`£•••`,
  click to reveal) so a screenshare does not leak the payroll. Reveals are audited.
- **R-ADM-02** **Person** — profile, fee history, bank history, invoice history.
- **R-ADM-03** **Set fee** — effective-dated (R-DATA-02), with a required note and
  an audit entry. Setting a fee for a `pending` person activates them.
- **R-ADM-04** **Period** — expected vs received for the month, who has not
  submitted, and flagged rows: fee changed mid-period, bank changed inside 72h,
  an expense over a configurable threshold, `is_late`.
- **R-ADM-05** **Payment run** — the period's submitted invoices **split into a UK
  batch and an INTL batch**, because Robin pays them separately. Columns: payee,
  legal name, amount, account details, invoice number. On screen and as a CSV
  export. Export is audited.
- **R-ADM-06** **Data check** — the go-live grid: one row per person, four ticks —
  fee set / profile complete / bank confirmed / counter seeded. Plus the seed
  flags (name mismatches, missing bank name, the "Bridge Building" payee) for
  Robin to confirm or chase.
- **R-ADM-07** **Queue** — new people awaiting a fee, pending bank changes,
  anything blocked.
- **R-ADM-08** Every admin write goes through a typed form with validation. There
  is no generic table editor anywhere in this application.
- **R-ADM-09** Admins can deactivate a person (`status='inactive'`): no reminders,
  cannot invoice, history preserved. Nothing is ever deleted.

---

## 12. Scheduled jobs

All are `POST /tasks/*`, called by Cloud Scheduler with an OIDC token, verified
by the app. All are idempotent — a double fire must not double-send.

- **R-JOB-01** `sync-kudos` — daily during the window, and on demand.
- **R-JOB-02** `remind` — **22nd**: everyone active gets the window-open email
  with a link. **27th**: only those with no submitted invoice get a nudge.
- **R-JOB-03** `summary` — **29th**: reconciliation email to the finance group —
  expected vs received, who is missing, what is flagged.
- **R-JOB-04** `outbox` — every 5 minutes; sends pending mail with exponential
  backoff; after 5 failures marks `failed` and alerts.
- **R-JOB-05** `backup` — nightly `pg_dump` to GCS. Retain 30 days. This is the
  backup we rely on, not Neon's history.
- **R-JOB-06** *Removed 16 September 2026.* There is no Drive mirror: see R-STO-04.

---

## 13. Storage

- **R-STO-01** One private GCS bucket, uniform bucket-level access, no public
  objects, versioning on.
- **R-STO-02** Layout: `receipts/{person_id}/{invoice_id}/{uuid}.{ext}`,
  `invoices/{period}/{filename}.pdf`, `backups/{date}.sql.gz`.
- **R-STO-03** All reads via V4 signed URLs, TTL ≤ 15 minutes. No signed URL is
  ever logged or emailed.
- **R-STO-04** GCS is the system of record, and the only store. *There is no
  Drive mirror (dropped 16 September 2026):* finance receive every invoice by email
  (R-SUB-07) and can open any invoice from the person page, and a Shared Drive
  copy would have been a second place holding everyone's full account number.
- **R-STO-05** *Removed 16 September 2026, with the Drive mirror.*

---

## 14. Security

- **R-SEC-01** Cloud Run runs as an attached service account. No key files, ever.
- **R-SEC-02** All secrets from Secret Manager, referenced by env var. No secret
  in code, in the repo, or in a log line.
- **R-SEC-03** `account_number`, `sort_code`, `iban`, `swift_bic` are encrypted in
  the app before insert (AES-GCM, key in Secret Manager, key ID stored alongside
  so rotation is possible). The database holds ciphertext. This is deliberate:
  the database is a third party (Neon), and a breach there must not yield
  anything payable.
- **R-SEC-04** Fees stay in plaintext columns — they are aggregated in SQL for
  reconciliation and are not payment instructions.
- **R-SEC-05** No fee, full account number, IBAN or signed URL in any log, error
  message, exception payload, or email subject. Last four only.
- **R-SEC-06** All personal-data access is authorised from the IAP identity, never
  from a client-supplied ID.
- **R-SEC-07** Postgres connections use TLS with `verify-full`.
- **R-SEC-08** Rate-limit `/submit` and the bank-change endpoint per person.
- **R-SEC-09** Production database credentials are break-glass, held by one
  person, and their use is logged. Everyday development uses staging.
- **R-SEC-10** Uploaded files: verify the magic bytes, not just the extension;
  strip EXIF from images; never render an uploaded file inline in the app.
- **R-SEC-11** CSRF protection on every state-changing form. Secure headers.
- **R-SEC-12** Dependencies pinned; `uv lock` committed.

---

## 15. Environments

- **R-ENV-01** Two environments: `staging` and `production`. Separate Neon
  project, GCS bucket, Cloud Run service, IAP group and secrets for each.
- **R-ENV-02** **Production data is never copied into staging.** Staging is seeded
  with generated people and invented fees that share the real *structure*. The
  moment a real account number exists in staging, staging is production with
  weaker access rules.
- **R-ENV-03** Staging cannot send real email (a sandbox client). Enforced by
  config, asserted by a test.
- **R-ENV-04** The staging IAP group contains the developers; the production
  finance group does not.
- **R-ENV-05** All infrastructure in Terraform. No console clicking.
- **R-ENV-06** Cost target is £0/month: Neon free tier, Cloud Run / GCS /
  Scheduler within always-free limits, Resend free tier. Anything that would
  incur cost must be raised, not silently provisioned.

---

## 16. Testing

- **R-TEST-01** `tests/domain` — pure, no DB, runs in under a second. Must cover:
  totals and rounding; the kudos calculation; period-window logic including the
  boundary days and lateness; IBAN mod-97 including known-bad inputs; the bank
  key-normalisation map with every real-world variant from R-SEED-04; sort-code
  and account-number normalisation; the `£1,234.56` formatter.
- **R-TEST-02** `tests/integration` — against a throwaway Postgres. Must cover:
  the full submit path; **concurrent submits by the same person allocating
  exactly one number**; idempotent replay; void and reissue; a person being
  unable to read another person's data; non-finance access to `/admin` being
  refused.
- **R-TEST-03** A PDF regression test rendering a fixed fixture and comparing to a
  committed reference, for one UK person and one INTL person.
- **R-TEST-04** A seed test over a fixture spreadsheet containing every messy
  variant found in the real file, asserting the exact set of warnings.
- **R-TEST-05** CI runs `ruff check`, `ruff format --check`, `pytest`. No merge on
  red.
- **R-TEST-06** Before go-live, a full dry run: three real people, real
  submission, real email to Robin, and Robin producing a payment run from it.

---

## 17. Build order

Only fixed date: the window opens on the 22nd.

1. **Skeleton** — FastAPI on Cloud Run behind IAP, Neon connected, Alembic,
   `config.py`, CI. Someone logs in and sees their own name.
2. **Seed** — R-SEED-*, with its report, run against the real file in dry-run and
   reviewed with Remy before `--commit`.
3. **The invoice** — R-PDF-*, R-NUM-*, R-SUB-*. Hold a generated PDF next to
   Robin's template until they are indistinguishable. **This is the piece Robin
   will judge; do it before the wizard.**
4. **The wizard** — R-WIZ-*, R-BANK-*.
5. **Admin** — R-ADM-*, built and demoed in staging against invented data.
6. **Plumbing** — R-JOB-*, R-STO-*, mailer, backups.
7. **Dry run** — R-TEST-06, before the 22nd.

---

## 18. Decisions already taken — do not relitigate

These were settled during the design audit. Changing any of them requires asking
Leo first.

| | Decision |
|---|---|
| Auth | Cloud IAP. Everyone is on the Workspace domain. |
| DB | Neon free tier, EU region, plain Postgres. |
| Frontend | Jinja + HTMX. No SPA. |
| PDF | WeasyPrint. |
| Currency | GBP for everyone. `payout_region` is routing, not currency. |
| Numbering | Per person, continuing existing sequence, plain unpadded. |
| Period | 22nd–28th, never locked out. |
| Fees | Admin screen only. Never in a spreadsheet, never in Drive. |
| Kudos | Sheet stays as it is, read-only, £20 each. |
| Who sees fees | Robin, Charlie, Remy. |
| Bank writes | Owner-confirmed only; admin may propose, never confirm. |
| Storage | GCS is the only store. No Drive mirror (dropped 16 September 2026). |
| VAT | None. All contractors. |

---

## 19. Known gaps — ask before building

- Nothing in the data supplies `role` for anyone. Collected at first login
  (R-WIZ-02); the first cycle will need chasing via R-ADM-06.
- Jamie's account name is "Bridge Building". Robin must confirm this is
  the intended payee before her first automated payment.
- The kudos sheet's file ID and column layout have not been provided. Ask.
- Resend is the mail provider (decided 5 September 2026, over Postmark on
  cost and attachment size). The sending domain still needs SPF, DKIM and
  DMARC on example.com.
