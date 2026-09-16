# Invoice Portal

An internal web app for the company's contractors. Once a month each person checks
their figures, adds any expenses, and sends a correctly numbered invoice in one
standard format. It goes to accounts by email, and finance get a payment run.

It replaces everyone keeping their own Word template and accounts chasing the
ones who forget.

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
the run still looks green. Check the count: around 550 pass and 8 skip when
everything ran.

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
4. If there is a migration, deploy the code first, then run it (`docs/setup.md`).

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
| Secrets | Secret Manager: `portal-database-url-*`, `portal-bank-encryption-key-*`, `portal-csrf-secret-*`, `portal-resend-api-key-*` |
| Database | Neon, one project each for staging and production |
| Email | Resend, sending as `accounts@example.com` |
| Kudos | The existing Google Sheet, read-only, synced daily |
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

## Scripts

All run from the repository root, and all are a dry run until `--commit`.

| Script | For | What it does |
|---|---|---|
| `app.scripts.seed` | Production, once | Imports the spreadsheet: people, their invoice numbering, bank details (unconfirmed) |
| `app.scripts.seed_kudos_aliases` | Production, once | Maps names in the kudos sheet to people |
| `app.scripts.seed_demo`, `seed_demo_kudos` | Staging only | Invented people and kudos. Refuse to run against production |
| `app.scripts.reset_person` | Staging only | Removes one person so first sign-in can be tested again. Refuses production |

## Other documents

| Document | What it is |
|---|---|
| `docs/setup.md` | Setting up Neon, the keys, the local database, seeding, and migrations |
| `infra/README.md` | The infrastructure, and how to apply it |
| `TUTORIAL.md` | A written walkthrough of the app. Partly out of date; the in-app tutorial (the ? button) is current |
| `BACKLOG.md` | A snapshot from early September. Superseded by the git history |
| `design_handoff_invoice_portal_ui/` | The original UI design |
