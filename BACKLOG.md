# BACKLOG.md — what is left

Written 2 September 2026, after build order steps 1–5.

Ordered by what blocks what. Requirement IDs refer to `SPEC.md`. Anything
marked **blocked** needs something only a person can supply, and no amount of
work here moves it.

**The only fixed date is the 24th**, when the window opens.

---

## 0. Where we are

| Build order step | State |
|---|---|
| 1. Skeleton | done |
| 2. Seed | done, `--commit` not yet run |
| 3. The invoice | done |
| 4. The wizard | done |
| 5. Admin | done |
| 6. Plumbing | **not started** |
| 7. Dry run | **not started** |

299 tests passing (238 with no database, 61 against Postgres). `ruff` clean.
Both Neon databases are live and at `0001_initial`. Four secrets are in
Secret Manager in project `your-gcp-project`.

---

## 1. Blocked on you

Nothing below can start until the corresponding answer exists. Two of them
gate the whole of section 3.

| # | What is needed | Blocks | SPEC |
|---|---|---|---|
| 1.1 | **The kudos sheet's file ID and column layout** | all of R-KUD-*, and the wizard currently makes people tick "invoice without my kudos" | §19 |
| 1.2 | **A Postmark account and a sending domain** | every email: the invoice to Robin, reminders, the bank-change alert, the monthly summary | §19 |
| 1.3 | **Which Shared Drive to use** | R-STO-05, the human-browsable mirror | §19 |
| 1.4 | **Confirm Jamie's payee is "Bridge Building"** | her first automated payment | R-SEED-08, R-ADM-06 |
| 1.5 | **A role for each of the 16 people** | nobody has one; it prints on the invoice. Collected at first login, but the first cycle needs chasing | §19, R-WIZ-02 |
| 1.6 | **Remy's review of the seed dry run** | running the seed with `--commit` | build order 2 |
| 1.7 | **Rotate both Neon passwords** | they were pasted into a chat transcript | — |

---

## 2. Decisions waiting on you

These have a working answer in the code today. Each is reversible and each is
one I made rather than one you made.

| # | Decision | What I did | Where |
|---|---|---|---|
| 2.1 | The template PDF is US Letter; SPEC §9 says A4 | Built A4, since SPEC wins | `app/static/invoice.css` |
| 2.2 | R-PDF-08 says figures are right-aligned; the Word original has them at three different right edges | Right-aligned, as specified | `invoice.css` |
| 2.3 | `print_snapshot.bank_block` must hold "exactly what was printed" (R-DATA-09) but §2.5 forbids plaintext bank details in the database | Stored the ciphertext | `app/services/invoicing.py` |
| 2.4 | `people.payout_region` nullability is unstated | Nullable — a person created at first login has no known payout route | `app/db/models.py` |
| 2.5 | CLAUDE.md §7 forbids AI-related metadata in the codebase | Omitted the `Co-Authored-By` trailer from commits | git history |

---

## 3. Build order step 6 — plumbing

The largest remaining block. Roughly in dependency order.

### 3.1 Storage (R-STO-01 … R-STO-04)

- Create the private GCS bucket in Terraform: uniform bucket-level access,
  versioning on, no public objects.
- Replace `LocalObjectStore` with a GCS implementation. The interface the
  submit path uses already exists in `app/services/storage.py`, so this is one
  class and a dependency override, not a refactor.
- V4 signed URLs, TTL ≤ 15 minutes, never logged and never emailed.
- **Not blocked.** Can start today.

### 3.2 Mail (R-SUB-06 … R-SUB-08, R-DATA-10)

- Postmark client and the outbox dispatcher.
- The invoice email to Robin with the PDF and receipts attached, subject
  `Invoice {number} — {legal_name} — {Month} {Year}`, no fee in the subject.
- A copy to the person (R-SUB-08).
- **Blocked on 1.2.** The outbox rows are already being written correctly, so
  when Postmark exists the dispatcher has a queue waiting for it.

### 3.3 Kudos sync (R-KUD-01 … R-KUD-03)

- Read-only Sheets client scoped to one file ID, using the Cloud Run service
  account. Never a broader Drive scope.
- Match rows to people by email; report an unmatched row, never guess.
- **Blocked on 1.1.**

### 3.4 Scheduled jobs (R-JOB-01 … R-JOB-06)

All are `POST /tasks/*` with an OIDC token verified by the app, and all must be
idempotent — a double fire must not double-send.

| Job | When | Blocked on |
|---|---|---|
| `sync-kudos` | daily in the window | 1.1 |
| `remind` | 24th to everyone, 27th to those still missing | 1.2 |
| `summary` | 29th, reconciliation to the finance group | 1.2 |
| `outbox` | every 5 minutes, exponential backoff, alert after 5 failures | 1.2 |
| `backup` | nightly `pg_dump` to GCS, 30 days retained | 3.1 |
| `drive-mirror` | copy submitted PDFs into the period's folder | 1.3, 3.1 |

`backup` is worth doing early and is only blocked on the bucket. R-JOB-05 says
this is the backup we rely on, not Neon's history.

### 3.5 The Drive mirror (R-STO-05)

Restricted to Robin, Charlie and Remy — **not** "anyone at Your Company with the
link", because those PDFs carry every person's full account number. Terraform
must set this explicitly and a test must assert it.

**Blocked on 1.3.**

---

## 4. Not yet built, not part of step 6

These are gaps in what is already "done", not future phases.

### 4.1 The bank-change lifecycle (R-BANK-01, 03, 04, 05, 07, 08)

Today a person can **confirm** the bank row they already have. They cannot yet
**change** it. Still to build:

- Changing details creates a new row and closes the old one (`superseded`),
  never an update.
- The account number typed twice and matched — double entry catches
  transpositions no checksum can.
- Validation on entry reusing the existing domain rules (already written and
  tested — this is wiring, not new logic).
- On a change, email to both the person and Robin showing old and new last four
  only (**blocked on 1.2**).
- Robin proposing a correction that sits `pending` until the owner confirms it,
  and flagging a row back to the person with what needs fixing.

The `pending` state, the queue screen and the admin views for all of this
already exist and are tested; the write paths are what is missing.

### 4.2 Receipts (R-WIZ-04, R-SEC-10)

- Browser-to-GCS upload via a V4 signed URL, so bytes never pass through
  Cloud Run.
- Accept jpeg, png, heic, pdf; 10 MB each; 10 per invoice.
- Verify **magic bytes**, not the extension. Strip EXIF. Never render an
  uploaded file inline.
- **Blocked on 3.1.** Expenses without receipts work today.

### 4.3 Rate limiting (R-SEC-08)

`/submit` and the bank-change endpoint, per person. Not built.

### 4.4 The PDF regression test (R-PDF-13, R-TEST-03)

A rendered fixture compared against a committed reference, for one UK and one
international person, asserting one page.

Blocked locally by WeasyPrint's GTK libraries not being installed
(`docs/setup.md` §2.2 has the command). Not blocked in CI, where a Debian image
has them — this could be done CI-only first.

---

## 5. Deployment (R-ENV-04, R-ENV-05)

None of this exists yet.

- **Dockerfile.** Needs `libpango-1.0-0`, `libpangoft2-1.0-0`, `libcairo2`,
  `libgdk-pixbuf-2.0-0`, `tzdata` (for `Europe/London`) and
  `fonts-crosextra-carlito` — Carlito is metric-compatible with the Calibri the
  template is set in.
- **Cloud Run service** for each environment, scale to zero.
- **Cloud IAP** in front of it, and the `iap_audience` value that only exists
  once the backend service does. Until then nobody can actually log in.
- **The finance IAM group**, and `PORTAL_FINANCE_EMAILS` to match it.
- `roles/secretmanager.secretAccessor` granted **per secret**, not
  project-wide, to the Cloud Run service account.
- **Terraform in `infra/`** for all of the above. No console clicking.
- Staging IAP group contains the developers; the production finance group does
  not (R-ENV-04).

---

## 6. Step 7 — the dry run (R-TEST-06)

Before the 24th: three real people, a real submission, a real email to Robin,
and Robin producing a payment run from it.

This is the acceptance test for the whole thing and it depends on almost
everything above. It is the reason section 1 matters — 1.1 and 1.2 in
particular have a long tail behind them.

---

## 7. Gaps found in SPEC.md

Raised rather than worked around, as CLAUDE.md §8 asks.

### 7.1 A person who misses a month entirely cannot invoice for it

R-PER-04 says nobody is ever locked out, and R-PER-06 disables submission
before the 24th. Both are implemented. But the period is always *the current
month*, so somebody who misses September completely and comes back on 5 October
finds the button disabled, and cannot submit for September at all — which is
exactly the phone call to Robin the system exists to remove.

Three possible answers, all yours to pick:

1. Leave it. A missed month is a conversation anyway.
2. Let a person submit for the previous period while it has no invoice.
3. Let Robin open a specific period for a specific person from the admin queue.

**Not implemented either way. This is the most substantive gap I found.**

### 7.2 R-SEED-08 is stale

It says three rows have no `Name:` line. In the current spreadsheet exactly one
does, and it is also the only row with no bank name. The spec was written
against the earlier copy that R-SEED-10 mentions.

### 7.3 R-SEED-04 lists variants that are not in the file

`account no`, `sortcode`, `swift` and `bic` do not appear in this copy. The map
still accepts them; a superset costs nothing and the file has changed before.

### 7.4 R-PDF-09's worked example mixes two money formats

`3 kudos × £20 · travel £34.50` — £20 without decimals next to £34.50 with
them, while R-PDF-12 says two decimals everywhere. Implemented exactly as the
example reads, with a separate compact formatter used only on that line.

---

## 8. Smaller things

- `app/services/drive.py` and `app/services/kudos_sync.py` are named in
  CLAUDE.md §5 and do not exist yet.
- `PORTAL_MIGRATION_URL` is an addition to the documented configuration; it holds
  Neon's direct endpoint, because DDL through the pooler is not something to
  rely on.
- The staging database has no data in it. Seeding it is blocked on 1.6.
- `docs/setup.md` §1.1 says to name the Neon database `portal`; the real one is
  `neondb`, Neon's default. Harmless, but the doc and reality differ.
