# BACKLOG.md — what is left

Written 2 September 2026 after build order steps 1–5.
Updated 3 September 2026: the bank-change lifecycle, the mailer, the
scheduler endpoints, rate limiting, the container and the PDF tests are
done. What remains is listed below and is mostly waiting on section 1.

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
| 6. Plumbing | mail, jobs and the container done; storage, kudos, Drive outstanding |
| 7. Dry run | **not started** |

346 tests passing (238 with no database, 100 against Postgres, 8 rendering
real PDFs). `ruff` clean. Both Neon databases are live and at
`0002_outbox_backoff`. Four secrets are in Secret Manager in project
`your-gcp-project`. The Cloud Run image builds and renders a
one-page A4 invoice inside it.

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

**The code is done.** `GcsObjectStore` reads, writes, lists, deletes and signs
V4 URLs, capped at fifteen minutes where the URL is made. A URI naming a
different bucket is refused. `build_store` falls back to local only when no
bucket is configured, and production refuses to start without one.

**The bucket is not created.** `infra/` has the Terraform for it —
uniform access, versioning, public access prevention, lifecycle rules, and a
service account with object access to that bucket alone plus per-secret
accessor grants.

**Waiting on you to run `terraform apply`**, because it provisions real
resources and one costs money (see `infra/README.md` — an EU bucket is outside
the always-free tier, which covers US regions only). Pennies a month, but
R-ENV-06 says raise it rather than provision quietly.

### 3.2 Mail (R-SUB-06 … R-SUB-08, R-DATA-10)

**Built.** The dispatcher, the retry and backoff, the give-up-and-alert, and
every email body. The client is a protocol: Postmark in production, a sandbox
that records and discards in staging (R-ENV-03), a stand-in in tests.

**Still blocked on 1.2 for the credentials only.** When the Postmark account
exists, set `PORTAL_POSTMARK_TOKEN` and `PORTAL_MAIL_FROM` and it sends. Nothing
else changes.

### 3.3 Kudos sync (R-KUD-01 … R-KUD-03)

- Read-only Sheets client scoped to one file ID, using the Cloud Run service
  account. Never a broader Drive scope.
- Match rows to people by email; report an unmatched row, never guess.
- **Blocked on 1.1.**

### 3.4 Scheduled jobs (R-JOB-01 … R-JOB-06)

**The endpoint layer is built**: all six are `POST /tasks/*`, each verifying
its own Cloud Scheduler OIDC token, and refusing with a 503 when that is not
configured. `remind`, `summary` and `outbox` do their work and are idempotent.
The other three report that they are not configured rather than pretending to
have run, so the Scheduler jobs can be created now.

What remains per job:

| Job | When | Blocked on |
|---|---|---|
| `sync-kudos` | daily in the window | **the sheet reader (1.1)** |
| `remind` | 24th and 27th | done; delivery needs 1.2 |
| `summary` | 29th | done; delivery needs 1.2 |
| `outbox` | every 5 minutes | done; delivery needs 1.2 |
| `backup` | nightly `pg_dump` to GCS | done; needs the bucket (3.1) |
| `drive-mirror` | PDFs into the period folder | **the job body (1.3, 3.1)** |

`backup` is written and proven: it dumped the real staging database from
inside the container over `verify-full` TLS — 17KB of SQL, all twelve tables,
compressed to 2.9KB — and prunes anything older than thirty days, touching only
objects whose names match the backup pattern exactly. It refuses rather than
reporting success when there is no bucket, because a backup job that quietly
writes to a container's filesystem is worse than none: it looks like it works.

**There is still no backup running**, and there will not be until the bucket
exists and a Scheduler job calls it.

### 3.5 The Drive mirror (R-STO-05)

Restricted to Robin, Charlie and Remy — **not** "anyone at Your Company with the
link", because those PDFs carry every person's full account number. Terraform
must set this explicitly and a test must assert it.

**Blocked on 1.3.**

---

## 4. Not yet built, not part of step 6

These are gaps in what is already "done", not future phases.

### 4.1 The bank-change lifecycle — **done**

A person can change their own details, typing the account number twice. Robin
can propose a correction and flag a row back. A proposal sits `pending` and
only the owner can make it payable; a test has Robin attempt exactly that
against somebody else's row and asserts nothing moved.

The emails are queued and will go out when 1.2 lands.

### 4.2 Receipts (R-WIZ-04, R-SEC-10)

- Browser-to-GCS upload via a V4 signed URL, so bytes never pass through
  Cloud Run.
- Accept jpeg, png, heic, pdf; 10 MB each; 10 per invoice.
- Verify **magic bytes**, not the extension. Strip EXIF. Never render an
  uploaded file inline.
- **Blocked on 3.1.** Expenses without receipts work today.

### 4.3 Rate limiting (R-SEC-08) — **done**

`/submit` and the bank-change endpoint, keyed by the IAP-verified email.

One caveat worth knowing: the counter is per process, so on more than one
Cloud Run instance the real ceiling is the limit times the instance count. For
sixteen people on a service that scales to zero that is almost always one
instance, and the failure mode of being wrong is that a few more requests get
through. A shared counter would mean a table and a write per request, which is
a worse trade here. `app/web/ratelimit.py` says so at the top.

### 4.4 The PDF regression test (R-PDF-13, R-TEST-03) — **done**

`tests/test_pdf_output.py` renders the real template through WeasyPrint for a
UK person and an international one and asserts one A4 page, the figures, the
detail line and the right bank captions. There is also a long-address case,
because that is the realistic way one page becomes two later.

The tests skip themselves where WeasyPrint's native libraries are absent — a
Windows machine without the GTK runtime — and CI fails the build if they skip,
because silently not testing the invoice is the failure that job exists to
prevent.

Not done: a **pixel** comparison against a committed reference image. The
assertions are on extracted text and page geometry, which catches content and
overflow but not, say, a font falling back. Worth adding once the layout is
settled with Robin.

---

## 5. Deployment (R-ENV-04, R-ENV-05)

- ~~**Dockerfile**~~ — **done.** Two stages, non-root, and it renders a
  one-page A4 invoice inside the image. CI builds it on every push.
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
