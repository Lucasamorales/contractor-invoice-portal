# Setup

Two environments, `staging` and `production`, with nothing shared between them
(R-ENV-01). Production data is never copied into staging, ever, for any reason
(R-ENV-02, CLAUDE.md §2.6) — the moment a real account number exists in
staging, staging *is* production with weaker access rules.

Cost target is £0/month (R-ENV-06). Everything below stays inside a free tier;
anything that would incur cost is raised rather than provisioned.

---

## 1. Neon (Postgres)

Two separate Neon **projects**, not two branches of one project. A branch shares
a project's access controls and connection settings, which is exactly the
separation R-ENV-01 asks for.

### 1.1 Create the staging project

1. Sign in at <https://console.neon.tech> with the Your Company Google account.
2. **New Project**.
3. Name: `portal-invoices-staging`.
4. Region: **AWS Europe (Frankfurt) `eu-central-1`**. The spec requires an EU
   region; London is not offered on the free tier at time of writing, and
   Frankfurt is the nearest.
5. Postgres version: the current default (17 or later). We use no
   Neon-specific features, so a `pg_dump` moves this to Cloud SQL if we ever
   outgrow the free tier.
6. Database name: `portal`.
7. Create.

### 1.2 Take the connection string

Neon shows a connection string once the project exists. It arrives looking like:

```
postgresql://portal_owner:PASSWORD@ep-something-123456.eu-central-1.aws.neon.tech/portal?sslmode=require
```

Three changes are needed before it goes anywhere:

- `postgresql://` becomes **`postgresql+psycopg://`** — we use psycopg 3.
- `sslmode=require` becomes **`sslmode=verify-full`** (R-SEC-07). `require`
  encrypts the traffic but does not prove who is on the other end, and Neon is
  a third party on the public internet. `app/db/session.py` refuses to start a
  production engine without it.
- **`sslrootcert`** must be added. `verify-full` needs a certificate authority
  to verify *against*, and libpq's default path — `%APPDATA%/postgresql/root.crt`
  on Windows, `~/.postgresql/root.crt` elsewhere — does not exist on a
  development machine or in a slim container. Without it the connection fails
  outright, which is the right failure but an obscure one.

  On Cloud Run, and anywhere with a populated OS trust store, use
  `sslrootcert=system`. On Windows this is not enough: libpq's bundled OpenSSL
  has no default store and verification fails with `certificate verify failed`.
  Point it at the Mozilla bundle that ships with certifi instead:

  ```bash
  uv run python -c "import certifi; print(certifi.where())"
  ```

Final form:

```
postgresql+psycopg://USER:PASSWORD@ep-something-123456.c-6.eu-central-1.aws.neon.tech/neondb?sslmode=verify-full&sslrootcert=system&channel_binding=require
```

Keep `channel_binding=require` if Neon includes it. It binds the SCRAM
authentication to the TLS channel, which is a free improvement on top of
`verify-full`.

### 1.2.1 Pooled and direct endpoints

Neon hands out a **pooled** host, with `-pooler` in the name. That is the one
the application uses. Migrations use the **direct** host — the same string with
`-pooler` removed — because DDL through PgBouncer's transaction pooling is not
something to rely on.

`PORTAL_MIGRATION_URL` holds the direct endpoint and Alembic prefers it when set,
falling back to `PORTAL_DATABASE_URL`.

### 1.3 Repeat for production

Same steps, named `portal-invoices-production`. Different project, different
credentials, different connection string.

Its password is break-glass (R-SEC-09): held by one person, used only when
something is genuinely wrong, and its use is logged. Everyday development uses
staging.

### 1.4 Put both in Secret Manager

No connection string is ever committed, pasted into `alembic.ini`, or put in a
Cloud Run environment variable in plain text (R-SEC-02).

Set the project first — `gcloud config get-value project` is very likely still
pointing at whatever you last worked on:

```bash
gcloud config set project PROJECT_ID
```

```bash
gcloud services enable secretmanager.googleapis.com
```

```bash
gcloud secrets create portal-database-url-staging --replication-policy=automatic
```

```bash
printf '%s' 'postgresql+psycopg://...' | gcloud secrets versions add portal-database-url-staging --data-file=-
```

`printf` rather than `echo`: `echo` appends a newline, and a trailing newline in
a connection string is a confusing failure to debug.

Repeat with `portal-database-url-production`. Cloud Run reads them through its
attached service account — there are no downloaded key files anywhere in this
system (CLAUDE.md §2.7).

### 1.5 Create the schema

```bash
uv run alembic upgrade head
```

That reads `PORTAL_MIGRATION_URL` from `.env`, so it runs against the direct
endpoint. For production, pass the credential inline rather than writing it
to a file — it is break-glass and belongs in Secret Manager, not on a laptop.

Check what it will do first, without connecting:

```bash
uv run alembic upgrade head --sql
```

The migration is reversible and `tests/test_migration.py` asserts that both
directions match the models (R-DATA-12).

### 1.6 A note on scale-to-zero

The free tier suspends a database after a few minutes idle, and the first
connection afterwards takes a second or two to wake it. `pool_pre_ping` is on
so a suspended endpoint surfaces as a brief wait rather than a dead connection.
For sixteen people invoicing once a month this is the right trade.

---

## 2. Local development

```bash
uv sync
```

```bash
uv run pytest
```

```bash
uv run ruff check . && uv run ruff format .
```

### 2.1 A local database for the integration tests

The integration suite (R-TEST-02) runs against a throwaway Postgres, never
against staging. With Docker Desktop running:

```bash
docker run --rm -d --name portal-test-db -e POSTGRES_PASSWORD=test -p 5433:5432 postgres:17
```

```bash
PORTAL_DATABASE_URL='postgresql+psycopg://postgres:test@localhost:5433/postgres' uv run pytest -m integration
```

### 2.2 WeasyPrint on Windows

WeasyPrint needs the Pango and Cairo libraries, which Windows does not ship.
Until they are installed, `render_pdf` raises on import and everything else —
the seed, the domain tests, the HTML preview — works normally.

Install the GTK3 runtime, then reopen the shell:

```bash
winget install --id=tschoonj.GTKForWindowsRuntimeEnvironment -e
```

Verify:

```bash
uv run python -c "import weasyprint; print(weasyprint.__version__)"
```

In the container this is a package install, not a manual step. The image needs
`libpango-1.0-0`, `libpangoft2-1.0-0`, `libcairo2`, `libgdk-pixbuf-2.0-0`,
`tzdata` (for `Europe/London`, R-PER-01) and `fonts-crosextra-carlito` — Carlito
is metric-compatible with the Calibri that Robin's template is set in.

---

## 3. The encryption key

Generate once, per environment, and store it in Secret Manager:

```bash
uv run python -c "from app.services.crypto import generate_key; print(generate_key())"
```

Losing this key means losing every stored bank detail — the database holds only
ciphertext (R-SEC-03). It is never written to a file by the application, and
the key id (`PORTAL_BANK_ENCRYPTION_KEY_ID`) is stored alongside every ciphertext
so a rotation does not require re-encrypting everything at once.

---

## 4. Seeding

Dry run first, always. It writes nothing:

```bash
uv run python -m app.scripts.seed --file "data/Data Invoices 1.xlsx"
```

Read the report with Remy before committing it (build order step 2), then:

```bash
PORTAL_DATABASE_URL='...staging...' uv run python -m app.scripts.seed --file "data/Data Invoices 1.xlsx" --commit
```

`--commit` refuses to run if any row has an error, or if no encryption key is
configured. Exit codes: `0` clean, `1` rows need human attention, `2` refused.

---

## 5. Still to be provisioned

These are the R-ENV-05 Terraform items, needed from build order step 6. Nothing
here has been created yet, and SPEC §19 lists the questions still open.

| What | Blocked on |
|---|---|
| GCS bucket, uniform access, versioning (R-STO-01) | nothing — can be done now |
| Cloud Run service and IAP backend (R-ACC-01) | needs the GCP project and the finance IAM group |
| Cloud Scheduler jobs (R-JOB-01..06) | needs the service deployed |
| Shared Drive folder (R-STO-05) | **which Drive to use has not been named** |
| Kudos sheet access (R-KUD-01) | **the file ID and column layout have not been provided** |
| Postmark (R-SUB-06) | **the account and sending domain do not exist yet** |
