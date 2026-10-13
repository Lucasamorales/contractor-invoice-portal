# Invoice Portal

> **Placeholders:** "Your Company Ltd", its address, `example.com`, the people named in examples and
> tests, and every project, bucket and secret name in `infra/` are examples. Bank details in the tests
> are standard sample numbers. Set your own company details and settings before deploying.

An internal web app for the company's contractors. Once a month each person checks
their figures, adds any expenses, and sends a correctly numbered invoice in one
standard format. It goes to accounts by email, and finance get a payment run.

It replaces everyone keeping their own Word template and accounts chasing the
ones who forget.

**Live in production since 16 September 2026**, currently **0.8.22**. What
changed and when is in [`CHANGELOG.md`](CHANGELOG.md); what is still open is in
[`BACKLOG.md`](BACKLOG.md).

- **What it must do, requirement by requirement:** [`SPEC.md`](SPEC.md)
- **The rules for working on it:** [`CLAUDE.md`](CLAUDE.md), and read §2 first
- **Kudos, and why they work the way they do:** [`KUDOS.md`](KUDOS.md)

---

## Branches and environments

| Branch | Holds | Runs on |
|---|---|---|
| `staging` | Work in progress, tested on staging | https://invoices-staging-xxxx.a.run.app |
| `main` | Exactly what is in production | `https://portal-invoices-production-000000000000.europe-west2.run.app` |

Work lands on `staging`. It reaches `main` only by a pull request from `staging`,
once it has been tried on staging. Production deploys the **same image digest**
staging ran, never a fresh build.

A fix needed urgently in production: branch from `main`, merge it back into
`main`, then merge `main` into `staging` so the two do not drift.

## Stack

Python 3.12, FastAPI, Jinja2 and HTMX (no build step), SQLAlchemy and Alembic,
WeasyPrint for the PDF. Postgres on Neon (EU). Google Cloud Run behind IAP (the
Google sign-in), Cloud Storage, Secret Manager and Cloud Scheduler, all in
Terraform. Email through Resend. `uv`, `ruff` and `pytest`.

## Running it locally

```bash
uv sync
```

Copy `.env.example` to `.env`. The tests need a throwaway Postgres on port 5433:

```bash
docker run -d --name portaltest -e POSTGRES_PASSWORD=test -e POSTGRES_DB=portaltest -p 5433:5432 postgres:16
```

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest
```

Without the container every integration test is **skipped**, not failed, and
the run still looks green. Check the count: 739 pass and 8 skip when everything
ran.

There is no local sign-in. Every request is identified by Google's IAP, so the
app cannot simply be started and clicked through on a laptop. The integration
tests stand in for the signed-in person (`tests/integration`).

## Tests and CI

GitHub Actions runs on every push to `main` or `staging` and on every pull
request: lint, format, the full suite against a real Postgres, the PDF tests
with WeasyPrint's native libraries (a skip there fails the build), and a Docker
build. Nothing merges on red.

## Deploying

Infrastructure is in [`infra/`](infra/README.md): one Terraform stack per
environment, as workspaces, plus `infra/shared` for the image registry and the
budget alerts. State is in the bucket `your-gcp-project-tfstate`.

Staging, in outline:

1. Build and push an image, tagged with the next version.
2. Put its digest in `infra/staging.tfvars`.
3. `terraform workspace select staging`, then plan and apply with
   `-var-file=staging.tfvars`. Read the plan before applying.
4. If there is a migration, deploy the code first, then run it — unless the
   migration only relaxes something the new code needs, which goes first
   (`docs/setup.md` §1.5).

Production is the same with the production workspace and `production.tfvars`,
using the digest staging already ran. A plan refuses to run when the workspace
and the tfvars disagree.

## Where things live

| What | Where |
|---|---|
| Google Cloud project | `your-gcp-project`, London (`europe-west2`) |
| Services | `portal-invoices-{staging,production}` (the portal), `portal-tasks-{staging,production}` (scheduled jobs only) |
| Files (PDFs, receipts, backups) | Bucket `portal-invoices-{staging,production}` |
| Images | Artifact Registry `portal/invoice-portal` |
| Secrets | Secret Manager: `portal-database-url-*`, `portal-bank-encryption-key-*`, `portal-csrf-secret-*` per environment, and one Resend key both use (`portal-resend-api-key-staging`) |
| Database | Neon, one project each for staging and production |
| Email | Resend, sending as `accounts@example.com` (staging redirects every email to one inbox) |
| Kudos | The existing Google Sheet, read-only. Synced daily at 07:00 London, and on demand from the Kudos screen |
| Budgets | Billing account "Your Company": staging £3, production £5, whole project £10 a month |

No secret values belong in this repository, a log line, or a chat.

## Data rules

The full list is `CLAUDE.md` §2. The ones that catch people out:

- **Production data is never copied into staging**, for any reason. Staging runs
  on invented people.
- **Never committed:** `data/` (the source spreadsheet holds real bank details),
  `.env`, and Terraform state.
- Bank details are encrypted before they reach the database, and only ever shown
  as the last four digits.
- Only the owner can make a bank change payable. Finance may propose and flag,
  never confirm.
- **An invoice is immutable once sent.** A correction is a void and a reissue —
  and since 28 September the reissue carries the **same number**, so nobody's
  own sequence has a hole in it (R-NUM-05). Two *live* invoices can still never
  share a number.
- **A migration ignores `.env` when `PORTAL_ENVIRONMENT=production`**, and says
  which database it is about to change before changing it. Read that line:
  both databases are called `neondb`, so only the endpoint and the people count
  tell them apart (`docs/setup.md` §1.5).

## Scripts

All run from the repository root with `uv run python -m <name>`, and all are a dry
run until `--commit`. They live in the invoice module, so the name starts
`modules.invoices.scripts.`

| Script | For | What it does |
|---|---|---|
| `modules.invoices.scripts.seed` | Production, once | Imports the spreadsheet: people, their invoice numbering, bank details (unconfirmed) |
| `modules.invoices.scripts.seed_kudos_aliases` | Production, once | Maps names in the kudos sheet to people |
| `modules.invoices.scripts.seed_demo`, `seed_demo_kudos` | Staging only | Invented people and kudos. Refuse to run against production |
| `modules.invoices.scripts.reset_person` | Staging only | Removes one person so first sign-in can be tested again. Refuses production |
| `modules.invoices.scripts.set_counter` | Either, rarely | Corrects one person's next invoice number. Moves forward freely, backwards only onto a number a cancelled invoice released (R-NUM-05) |
| `modules.invoices.scripts.set_payout_region` | Either, rarely | Moves somebody between UK and international payouts, retiring their old account in the same transaction so they cannot invoice against the wrong set of fields |

## Other documents

| Document | What it is |
|---|---|
| `docs/setup.md` | Setting up Neon, the keys, the local database, seeding, and migrations |
| `infra/README.md` | The infrastructure, and how to apply it |
| `TUTORIAL.md` | A written walkthrough of the app. Behind the app since 25 September; the in-app tutorial (the ? button) is current |
| `CHANGELOG.md` | What changed, release by release |
| `BACKLOG.md` | What is still open, and what was decided but not built |
| `docs/decisions/0001-modular-hub.md` | The plan to make this a hub of modules, with the architecture diagram, the phases, and what is decided versus proposed |
| `modules/leave/SPEC.md`, `modules/leave/BACKLOG.md` | The leave module (a replacement for Leave Dates): the rules built so far, and everything still missing. Draft; nothing is wired into the app |
| `design_handoff_invoice_portal_ui/` | The original UI design |
