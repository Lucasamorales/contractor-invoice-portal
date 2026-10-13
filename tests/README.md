# tests

| Folder or file | What it covers | Needs |
|---|---|---|
| `domain/` | The pure rules in `app/domain` | Nothing |
| `integration/` | Services, routes and scripts against a real database | A throwaway Postgres |
| `test_auth_iap.py`, `test_scheduler_auth.py` | Token verification, with locally signed tokens and a local stand-in for Google's key endpoints | Nothing |
| `test_invoice_render.py`, `test_pdf_output.py` | The invoice template and the PDF it produces | WeasyPrint's native libraries for the PDF |
| `test_migration.py` | That the migrations match the models and run both ways, using Alembic's offline mode | Nothing |
| `js/` | The browser scripts in `app/static`, run in jsdom | Node 22 |

## Running them

```bash
docker run --rm -d --name portal-test-db -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=portaltest -p 127.0.0.1:5433:5432 postgres:17
```

```bash
uv run pytest
```

```bash
npm ci && npm test
```

The integration tests **skip** when no database is listening, and the PDF tests
skip when WeasyPrint's libraries are missing, so a green run with skips has not
tested everything. `PORTAL_TEST_DATABASE_URL` points the suite at a different
database; it must be on localhost, and the suite refuses anything else so it
can never be aimed at staging.

No test calls a live third-party service. Mail, storage, the kudos sheet and
Google's key endpoints are all replaced by local stand-ins.

## Coverage

`scripts/coverage.sh` runs both suites and writes `coverage/lcov.info` for the
whole repository.
