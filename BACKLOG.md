# BACKLOG.md — what is left

Rewritten 5 September 2026. Ordered by what blocks what. Requirement IDs refer
to `SPEC.md`, except the kudos ones, which live in `KUDOS.md` until somebody
folds them into the spec.

**The only fixed date is the 24th**, when the window opens.

---

## 0. Where we are

| Build order step | State |
|---|---|
| 1. Skeleton | done |
| 2. Seed | done, `--commit` not yet run against real data |
| 3. The invoice | done |
| 4. The wizard | done, except receipts |
| 5. Admin | done |
| 6. Plumbing | storage, kudos and the container done; **mail and Drive outstanding** |
| 7. Dry run | **not started** |

**453 tests** (247 with no database, 126 against Postgres, 80 rendering,
config and fixtures). `ruff` clean. Both Neon databases are live; staging is at
`0003_kudos_ledger`, **production is still at `0002_outbox_backoff`**. Staging
runs image `0.3.0` and holds invented demo data including 20 kudos.

---

## 1. The one that blocks the most

### 1.1 Cloud Scheduler cannot get through IAP

**Nothing scheduled has ever run.** Not the outbox dispatcher, not the
reminders, not the summary, not the nightly backup, not the Drive mirror, not
the kudos sync.

`scheduler.tf` used to claim these endpoints "sit outside IAP". They do not:
`iap_enabled` is on the service, so IAP is in front of every path. The job
fails `UNAUTHENTICATED` and **the container is never invoked**, so there is
nothing in the application log — which is why it went unnoticed.

Two fixes are already in (`accb8b7`): the scheduler service account now has
`roles/iap.httpsResourceAccessor` as well as `run.invoker`, and its OIDC
audience is the IAP resource path rather than the service URL. It got further —
the job now records HTTP 200 — but the container still never sees the request,
which means IAP is answering with its own page.

Options, none yet chosen:

- Find the audience/credential shape IAP actually wants for programmatic
  access on Cloud Run, and keep one service.
- Split the task endpoints onto a second Cloud Run service with no IAP,
  protected by the OIDC check the app already performs. Costs nothing, and
  `/tasks/*` verifies its own caller today.
- Drive the jobs from somewhere that is already inside the perimeter.

This is the next thing worth doing, because everything in §2 waits behind it.

---

## 2. Blocked on you

| # | What is needed | Blocks |
|---|---|---|
| 2.1 | **A Postmark account and a sending domain** | every email. The invoice to Robin, the copy to the person, both reminders, the bank-change alerts, the monthly summary. The code is written and queues correctly; nothing has ever been sent. |
| 2.2 | **Which Shared Drive to use** | R-STO-05, the human-browsable mirror |
| 2.3 | **Confirm Marta Jamie's payee is "Bridge Building"** | her first automated payment (R-SEED-08) |
| 2.4 | **A role for each person** | nobody has one and it prints on the letterhead. Collected at first login, so the first cycle needs chasing (R-WIZ-02) |
| 2.5 | **Remy's review of the seed dry run** | running the seed with `--commit` |
| 2.6 | **Rotate both Neon passwords** | they were pasted into a chat transcript |
| 2.7 | **Migrate production to `0003`** | production is a version behind staging |

---

## 3. Built but not finished

### 3.1 Receipts (R-WIZ-04) — the attachment question

The parts that exist: magic-byte sniffing, EXIF stripping, HEIC → JPEG
conversion, size and count limits, the `pending-receipts/` prefix so the
lifecycle rule cannot delete an attached receipt, and the mailer code that
attaches every receipt to Robin's email and still sends the email, saying what
is missing, if one cannot be fetched.

The part that does not exist: **any way for a person to upload one.**
`additional.html` has no file input and there is no route. So
`expenses.receipt_uri` is always NULL and there is never anything to attach.

What it needs: a file input per expense row, a route that issues a V4 signed
upload URL so the bytes go browser → GCS without passing through Cloud Run, and
a move from `pending-receipts/` to the permanent prefix when the invoice is
submitted.

### 3.2 A finance-side PDF download

A contractor can download their own invoice. Finance cannot — they are meant to
get PDFs by email (§2.1) and through the Drive mirror (§2.2), and neither works
yet. So right now nobody in finance can see a PDF at all.

### 3.3 Kudos perks

Deliberately out of v1 (`KUDOS.md` §6). Money is unaffected by a trade-in, so
this is purely additive. Needs the perk catalogue, who approves one, and
whether a perk taken is recorded anywhere today.

---

## 4. Spec gaps found while building

| Gap | Where |
|---|---|
| Someone who misses a month entirely has no way to invoice for it afterwards | SPEC §7.1 |
| `SPEC.md` §6 still describes kudos as it was imagined, not as built. R-KUD-07 … R-KUD-23 live in `KUDOS.md` | KUDOS.md §7 |
| Self-service PDF download is not in the spec at all; the only route to a PDF is the emailed copy in R-SUB-08 | commit `7746534` |
| The written kudos policy says the £20 is paid to "all kudos recipients"; leadership is excluded in practice | KUDOS.md §2 |

---

## 5. Smaller things

- `infra/staging.tfvars` is gitignored, so the pinned digest and the finance
  list exist only on one laptop and in Terraform state. A `staging.tfvars.example`
  would at least record the shape.
- The "Sent" screen says *"A copy is on its way to you as well"*, which is not
  true until §2.1 is done.
- The announcer's `LEADERSHIP_TEAM` matches by substring, so `"Castellanos"`
  catches Remy as well as Charlie. Right answer today, wrong method, and not
  our code.
- Production has never been deployed. Only staging exists.
