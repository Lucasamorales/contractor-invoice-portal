# Invoice Portal

An internal web app for a company's contractors. Once a month each person
checks their figures, adds any expenses with receipts, and sends a correctly
numbered invoice in one standard format. The invoice goes to accounts by email
as a PDF, and finance get a payment run split by how each person is paid.

It replaces everyone keeping their own Word template and accounts chasing the
ones who forget.

> **Nothing is preconfigured.** The repository holds no credentials and points
> at no live service. "Your Company Ltd", its address, `example.com`, the
> people named in examples and tests, and every project, bucket and secret
> name in `infra/` are placeholders. Bank details in the tests are standard
> sample numbers. To run it you bring your own database, storage, mail account
> and sign-in, as set out under [What you need to provide](#what-you-need-to-provide).

- **What it must do, requirement by requirement:** [`SPEC.md`](SPEC.md)
- **The rules for working on it:** [`CLAUDE.md`](CLAUDE.md), and read §2 first
- **Kudos, and why they work the way they do:** [`KUDOS.md`](KUDOS.md)
- **What changed, and what is still open:** [`CHANGELOG.md`](CHANGELOG.md), [`BACKLOG.md`](BACKLOG.md)

## What it does

- **For a contractor:** a short wizard: confirm your details, confirm your
  bank account, review the month's fee and kudos, add expenses, preview the
  invoice, send it.
- **For finance:** who has and has not invoiced, a queue of things waiting on
  them, each person's fee and bank history, flags, voids, and a payment run.
- **On a schedule:** reminders, a monthly summary, the mail outbox, a kudos
  sync from a Google Sheet, and a nightly database backup.

The rules that shape the code: money is integer pence, an invoice is immutable
once sent (a correction is a void and a reissue), bank details are encrypted
before they reach the database, and nobody ever sees another person's fee or
bank details. The full list is `CLAUDE.md` §2.

## Architecture

One FastAPI service, server-rendered with Jinja2 and HTMX. No SPA and no build
step.

```
browser ──► Cloud IAP (Google sign-in) ──► FastAPI on Cloud Run ──► Postgres
                                              │
                                              ├─► Cloud Storage   PDFs, receipts, backups
                                              ├─► Resend          email, through an outbox table
                                              └─► Google Sheets   kudos, read-only

Cloud Scheduler ──► /tasks/* (OIDC-authenticated) ──► the same code, as a second service
```

| Folder | What it holds |
|---|---|
| [`app/domain`](app/domain/README.md) | Pure rules: totals, numbering, the submission window, bank validation, kudos. No I/O |
| [`app/db`](app/db/README.md) | SQLAlchemy models, the query functions, Alembic migrations |
| [`app/services`](app/services/README.md) | The work: submitting an invoice, PDF, mail, storage, encryption, jobs |
| [`app/web`](app/web/README.md) | Routes: the contractor wizard, the finance screens, the scheduler endpoints |
| [`app/scripts`](app/scripts/README.md) | Command-line tools for seeding and rare corrections |
| `app/templates`, [`app/static`](app/static/README.md) | Jinja templates, CSS and the browser scripts |
| [`infra`](infra/README.md) | Terraform for everything on Google Cloud |
| [`tests`](tests/README.md) | Python and browser-script tests |

## What you need to provide

| You need | Used for | Where it is configured |
|---|---|---|
| A Postgres database | All data | `PORTAL_DATABASE_URL`, `PORTAL_MIGRATION_URL` |
| A 32-byte encryption key | Bank details at rest | `PORTAL_BANK_ENCRYPTION_KEY` |
| A random secret | Signing CSRF tokens | `PORTAL_CSRF_SECRET` |
| Google Cloud project with IAP | Sign-in. The app has no login of its own | `PORTAL_IAP_AUDIENCE`, `PORTAL_WORKSPACE_DOMAIN` |
| A Cloud Storage bucket | PDFs, receipts, backups | `PORTAL_GCS_BUCKET` |
| A Resend account and a verified sending domain | Email | `PORTAL_RESEND_API_KEY`, `PORTAL_MAIL_FROM` |
| A Cloud Scheduler service account | Running the scheduled jobs | `PORTAL_SCHEDULER_AUDIENCE`, `PORTAL_SCHEDULER_SERVICE_ACCOUNT` |
| Optionally, a Google Sheet of kudos | The kudos lines on an invoice | `PORTAL_KUDOS_SHEET_ID` |

[`docs/setup.md`](docs/setup.md) walks through creating each one, and
[`infra/README.md`](infra/README.md) through applying the Terraform. Before
applying, put your own project ID, people and image in `infra/*.tfvars`.

## Configuration

Every setting is an environment variable with the prefix `PORTAL_`, read by
`app/config.py`. Copy [`.env.example`](.env.example) to `.env` for local use;
deployed environments read secrets from Secret Manager.

| Variable | What it is |
|---|---|
| `PORTAL_ENVIRONMENT` | `staging` or `production`. Production refuses to start with a required setting unset |
| `PORTAL_DATABASE_URL` | Postgres, pooled endpoint |
| `PORTAL_MIGRATION_URL` | The same database on its direct endpoint, for Alembic |
| `PORTAL_BANK_ENCRYPTION_KEY`, `PORTAL_BANK_ENCRYPTION_KEY_ID` | AES-256 key (base64) for the bank columns, and its label |
| `PORTAL_CSRF_SECRET` | Any long random string |
| `PORTAL_IAP_AUDIENCE` | The audience of the IAP assertion for your backend service |
| `PORTAL_WORKSPACE_DOMAIN` | The only email domain allowed to sign in |
| `PORTAL_FINANCE_EMAILS` | Comma-separated addresses that may use the finance screens |
| `PORTAL_FINANCE_INBOX` | Where invoices and summaries are sent |
| `PORTAL_MAIL_FROM`, `PORTAL_RESEND_API_KEY` | The sender and the Resend key |
| `PORTAL_MAIL_REDIRECT_TO` | If set, every email goes to this one address instead (staging) |
| `PORTAL_APP_URL` | The public URL, used for links in emails |
| `PORTAL_GCS_BUCKET`, `PORTAL_SERVICE_ACCOUNT_EMAIL` | The bucket, and the identity that signs download URLs |
| `PORTAL_SCHEDULER_AUDIENCE`, `PORTAL_SCHEDULER_SERVICE_ACCOUNT` | Both required before `/tasks/*` will run |
| `PORTAL_TASKS_ONLY` | Serve only `/tasks/*`, for the scheduled-jobs service |
| `PORTAL_KUDOS_SHEET_ID`, `PORTAL_KUDOS_SHEET_RANGE` | The kudos sheet and the range to read |
| `PORTAL_KUDOS_INELIGIBLE_EMAILS` | People whose kudos are counted but never paid |
| `PORTAL_KUDOS_RATE_PENCE`, `PORTAL_AWARD_FIRST_PENCE`, `PORTAL_AWARD_SECOND_PENCE`, `PORTAL_AWARD_THIRD_PENCE`, `PORTAL_KUDOS_CLAIMABLE_FROM` | Kudos money and the date it starts counting |
| `PORTAL_PAYMENT_TERMS_DAYS`, `PORTAL_WINDOW_ALWAYS_OPEN` | Invoice terms, and a staging switch that ignores the monthly window |
| `PORTAL_EXPENSE_FLAG_THRESHOLD_PENCE`, `PORTAL_BANK_CHANGE_FLAG_HOURS` | When an expense or a recent bank change is highlighted to finance |
| `PORTAL_MAX_RECEIPT_BYTES`, `PORTAL_MAX_RECEIPTS_PER_INVOICE`, `PORTAL_SIGNED_URL_TTL_SECONDS` | Upload limits and how long a download link lasts |

The company name and address printed on every invoice are `DEFAULT_BILL_TO` in
`app/config.py`. Change them there.

## Running it locally

Needs Python 3.12, [`uv`](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync
```

```bash
cp .env.example .env
```

Fill in `.env`: at least a database URL, an encryption key and a CSRF secret.
`.env.example` says how to generate each. Then create the tables and start it:

```bash
uv run alembic upgrade head
```

```bash
uv run uvicorn app.main:app --reload
```

There is no local sign-in. Every request is identified by Google's IAP, so a
locally started app answers 401 to a browser. Day to day, the integration tests
stand in for the signed-in person (`tests/integration`).

## Tests

The Python integration tests need a throwaway Postgres on port 5433:

```bash
docker run --rm -d --name portal-test-db -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=portaltest -p 127.0.0.1:5433:5432 postgres:17
```

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest
```

Without the container every integration test is **skipped**, not failed, and
the run still looks green, so check the skip count. The PDF tests also skip
where WeasyPrint's native libraries are missing, which is the normal case on
Windows (`docs/setup.md` §2.2); CI runs them and treats a skip as a failure.

The browser scripts in `app/static` have their own suite, run with Node 22:

```bash
npm ci
```

```bash
npm test
```

One script runs both suites with coverage and writes a single report to
`coverage/lcov.info`:

```bash
PORTAL_TEST_DATABASE_URL=postgresql+psycopg://postgres@localhost:5433/portaltest scripts/coverage.sh
```

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs on every pull request and on
every push to `main` or `staging`: lint, format, the Python suite against a
real Postgres, the PDF tests with WeasyPrint's native libraries, the
browser-script suite, and a Docker build.

## Deploying

Infrastructure is Terraform in [`infra/`](infra/README.md): one stack per
environment, as workspaces, plus `infra/shared` for the image registry and
budget alerts. In outline:

1. Build and push an image to your registry.
2. Put its digest in `infra/staging.tfvars`.
3. `terraform workspace select staging`, then plan and apply with
   `-var-file=staging.tfvars`. Read the plan before applying.
4. If there is a migration, deploy the code first, then run it, unless the
   migration only relaxes something the new code needs, which goes first
   (`docs/setup.md` §1.5).

Production is the same with the production workspace and `production.tfvars`,
using the digest staging already ran rather than a fresh build. A plan refuses
to run when the workspace and the tfvars disagree.

`staging` holds work in progress and `main` holds what production runs. Work
reaches `main` by a pull request from `staging` once it has been tried there.

## Data rules

The full list is `CLAUDE.md` §2. The ones that catch people out:

- **Production data is never copied into staging**, for any reason. Staging
  runs on invented people (`app.scripts.seed_demo`).
- **Never committed:** `data/` (a source spreadsheet would hold real bank
  details), `.env`, and Terraform state.
- Bank details are encrypted before they reach the database, and only ever
  shown as the last four digits.
- Only the owner can make a bank change payable. Finance may propose and flag,
  never confirm.
- **An invoice is immutable once sent.** A correction is a void and a reissue,
  and the reissue carries the same number so nobody's sequence has a hole in
  it (R-NUM-05). Two live invoices can never share a number.
- **A migration ignores `.env` when `PORTAL_ENVIRONMENT=production`**, and says
  which database it is about to change before changing it.

## Scripts

All run from the repository root with `uv run python -m <name>`, and all are a
dry run until `--commit`.

| Script | For | What it does |
|---|---|---|
| `app.scripts.seed` | Production, once | Imports a spreadsheet: people, their invoice numbering, bank details (unconfirmed) |
| `app.scripts.seed_kudos_aliases` | Production, once | Maps names in the kudos sheet to people |
| `app.scripts.seed_demo`, `seed_demo_kudos` | Staging only | Invented people and kudos. Refuse to run against production |
| `app.scripts.reset_person` | Staging only | Removes one person so first sign-in can be tested again. Refuses production |
| `app.scripts.set_counter` | Either, rarely | Corrects one person's next invoice number |
| `app.scripts.set_payout_region` | Either, rarely | Moves somebody between UK and international payouts |

## Other documents

| Document | What it is |
|---|---|
| `docs/setup.md` | Setting up the database, the keys, the local test database, seeding and migrations |
| `infra/README.md` | The infrastructure, and how to apply it |
| `TUTORIAL.md` | A written walkthrough of the app. The in-app tutorial (the ? button) is the current one |

## Licence

Proprietary. See [`LICENSE`](LICENSE).
