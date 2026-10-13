# app/db

Persistence: the models, the queries and the migrations.

| File | What it is |
|---|---|
| `migration_target.py` | Which database a migration is about to change, and how it says so |
| `models.py` | SQLAlchemy models (R-DATA-01 .. R-DATA-11) |
| `repo.py` | Query functions. The only place raw queries live (CLAUDE.md §5) |
| `session.py` | Engine and session factory |

`repo.py` is the only place queries are written; services and routes call its
functions rather than building their own. Bank columns hold ciphertext: the
encryption happens in `app/services/crypto.py` before a value reaches a model.

## Migrations

Alembic, in `migrations/versions`. Run from the repository root:

```bash
uv run alembic upgrade head
```

The target database comes from settings, never from `alembic.ini`, and the run
names the database it is about to change before changing it
(`migration_target.py`). `tests/test_migration.py` checks that the migrations
match the models and run both ways; the integration tests apply them for real.
