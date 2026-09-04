# CLAUDE.md — Invoice Portal

This file is the standing context for every Claude Code session in this repo.
Read it before doing anything. `SPEC.md` is the requirement map; this file is
how we work.

---

## 1. What we are building

An internal web app that lets Your Company contractors generate their monthly
invoice in a few clicks and sends it to Robin (administrator / accounts / HR),
correctly numbered and in one standard format.

It replaces: every contractor keeping their own Word template, editing three
numbers, and emailing it whenever they remember — with Robin phoning round to
chase the ones who forget.

**Full architecture and the reasoning behind every decision:**
see `SPEC.md`

That page and `SPEC.md` are the source of truth. If they disagree, `SPEC.md`
wins and you must flag the discrepancy.

**Scale:** 16 people, one invoice each per month. This is a small app that
handles sensitive data. Optimise for correctness and clarity, never for scale.

---

## 2. Non-negotiables

These are invariants. If a task appears to require breaking one, stop and ask.

1. **No one ever sees another person's fee or bank details.** Access to fees is
   limited to the finance group (Robin, Charlie, Remy). Every data access is
   scoped by the IAP-verified email.
2. **Only the owner can make a bank change payable.** Admins may read, review,
   flag and propose — never confirm. See R-BANK-*.
3. **An invoice is immutable once issued.** Corrections are void + reissue.
   Never `UPDATE` a submitted invoice's figures.
4. **Money is integer pence.** No floats, ever. Currency is always `GBP`.
5. **Bank columns are encrypted before they reach the database.** The database
   holds ciphertext for `account_number`, `sort_code`, `iban`, `swift_bic`.
6. **Production data is never copied into staging**, ever, for any reason.
7. **No downloaded service-account key files.** Cloud Run uses its attached
   identity. Secrets come from Secret Manager.
8. **Never log, email, or put in an error message:** a fee, a full account
   number, an IBAN, or a receipt URL. Last four digits only.
   *One agreed exception, decided 5 September 2026:* the invoice email
   (R-SUB-07) carries the full breakdown including the fee, because it goes
   only to the finance inbox and to the person the fee belongs to. It must
   never reach anyone else. Nothing else about this rule relaxes: no fee in a
   log, in an error message, in a subject line, or in any email with another
   recipient.
9. **The invoice number is allocated exactly once per invoice.** See R-NUM-*.

---

## 3. Stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.12 | |
| Web | FastAPI + Jinja2 + HTMX | One service. No SPA, no build step. |
| DB | Postgres (Neon free tier, EU region) | Plain Postgres — no Neon-specific features, so we can move to Cloud SQL with a `pg_dump`. |
| ORM / migrations | SQLAlchemy 2.x + Alembic | |
| PDF | WeasyPrint | HTML + CSS → PDF. No headless browser. |
| Hosting | Cloud Run | Scale to zero. |
| Auth | Cloud IAP | We write no login code. |
| Files | Google Cloud Storage | Private bucket, V4 signed URLs. |
| Mail | Resend | Via an outbox table, never sent inline. |
| Scheduling | Cloud Scheduler → OIDC-authenticated endpoints | |
| Tooling | `uv`, `ruff`, `pytest` | |
| IaC | Terraform in `infra/` | |

---

## 4. Commands

```bash
uv sync                        # install
uv run uvicorn app.main:app --reload
uv run pytest                  # all tests
uv run pytest tests/domain     # fast, no DB
uv run ruff check . && uv run ruff format .
uv run alembic upgrade head
uv run python -m app.scripts.seed --file data/Data_Invoices.xlsx --dry-run
```

Never commit if `ruff check` or `pytest` fails.

---

## 5. Repo layout

```
app/
  main.py              FastAPI app, middleware, router registration
  config.py            pydantic-settings; all config from env, no literals
  auth.py              IAP JWT verification, finance-group check
  domain/              PURE functions. No I/O, no DB, no network.
                       totals, rounding, numbering rules, period rules,
                       IBAN validation, bank-field normalisation
  db/
    models.py          SQLAlchemy models
    repo.py            query functions — the only place raw queries live
    migrations/        Alembic
  services/
    invoicing.py       the submit transaction
    pdf.py             WeasyPrint rendering
    kudos_sync.py      Google Sheets read-only
    mailer.py          Resend client + outbox dispatcher
    drive.py           monthly folder mirror
    crypto.py          encrypt/decrypt bank fields
  web/
    member.py          the wizard routes
    admin.py           admin routes
    tasks.py           scheduler endpoints
  templates/
    invoice.html       THE invoice — matches Robin's template exactly
    ...
  scripts/
    seed.py            spreadsheet → database, with a report
infra/                 Terraform
tests/
  domain/              pure unit tests, no DB, must run in <1s
  integration/         against a throwaway Postgres
data/                  the source spreadsheet (gitignored — contains bank details)
```

**`app/domain/` must have zero imports from `app/db`, `app/services`, or any
I/O library.** It is where correctness lives and it must be testable without a
database. If you find yourself wanting to query inside `domain/`, pass the data
in instead.

---

## 6. Coding standards

- Type hints everywhere. `ruff` config is in `pyproject.toml`; do not weaken it.
- Pydantic models for every request body and every external payload.
- No bare `except:`. Catch specific exceptions.
- Money: `int` pence in the database and in `domain/`. Format for display only
  at the template boundary, via one helper (`format_gbp`).
- Dates: store UTC, display Europe/London. The period window (R-PER-*) is
  evaluated in Europe/London.
- Every DB write that changes a fee or a bank row writes an `audit_log` row in
  the same transaction.
- Templates hold no business logic beyond formatting.
- Docstrings on anything in `domain/` explaining the rule, not the code.

---

## 7. Git and commits


---
description: "Strict standards for clean code comments and git commit nomenclature"
globs: "**/*"
alwaysApply: true
---

  # Documentation & Commit Standards

- **Prohibited Metadata**: It is strictly forbidden to include any comments or metadata referencing "Made with Cursor," "Generated by AI," or any other AI-related advertisements/comments within the codebase.
- **Commenting Focus**: Write clean, concise comments that explain the logic and the "why" behind decisions. Do not state the obvious.
- **Git Commit Nomenclature**: Every time a change is tested and functional, or has received explicit approval, use the following prefixes for commit messages:

| Prefix | Usage |
| :--- | :--- |
| **feat:** | New features or functional additions. |
| **fix:** | Bug fixes or error corrections. |
| **chore:** | Maintenance, dependency updates, or routine tasks. |
| **wip:** | Work in progress (use for intermediate saves). |
| **docs:** | Documentation updates or comment changes only. |
| **devops:** | CI/CD, build scripts, or infrastructure logic. |

- **Quality Gate**: Only commit with `feat`, `fix`, or `devops` once the code has been verified to work or the user has provided explicit approval.

---

## 8. How to work in this repo

**Ask, don't assume.** This spec is the product of a long audit precisely so
that nothing is left to interpretation. If something is genuinely unspecified,
stop and ask Leo. Do not pick a plausible default and carry on.

Specifically, always ask before:

- changing anything in section 2 (Non-negotiables);
- adding a dependency;
- changing the invoice layout in any way;
- changing who can see or write anything;
- storing a new piece of personal data;
- adding a field to the PDF.

**Work requirement by requirement.** Every requirement in `SPEC.md` has an ID.
Reference the IDs in commits and PRs. If you implement something not covered by
a requirement, say so explicitly in the PR — it means the spec has a gap.

**Definition of done for any unit of work:**

1. Requirement IDs satisfied, and no others silently changed.
2. Tests: pure rules covered in `tests/domain`, and any money- or
   numbering-related path covered in `tests/integration`.
3. `ruff check` and `pytest` clean.
4. No secret, fee, or account number in any log line you added.
5. Migration written if the schema changed, and it runs both ways.

**When you disagree with the spec, say so.** It was written before the code
existed and it will contain mistakes. Raising one is useful; silently working
around it is not.
