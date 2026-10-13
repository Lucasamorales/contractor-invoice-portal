# app/scripts

Command-line tools for seeding and for the rare correction no screen offers.
Run from the repository root:

```bash
uv run python -m app.scripts.seed_demo
```

Every script is a **dry run until `--commit`**: it prints what it would do and
writes nothing. The ones that create or delete invented data refuse to run
against production.

| File | What it is |
|---|---|
| `reset_person.py` | Put one person in staging back to how they were before they ever logged in |
| `seed.py` | Spreadsheet to database, with a report (R-SEED-01 .. R-SEED-13) |
| `seed_demo.py` | Invented people for staging, so the screens have something in them |
| `seed_demo_kudos.py` | Invented kudos for staging, shaped exactly like the real sheet |
| `seed_kudos_aliases.py` | Seed the kudos name mappings (R-KUD-13) |
| `set_counter.py` | Correct one person's invoice counter (R-NUM-01, R-NUM-05, R-SEED-09) |
| `set_payout_region.py` | Move somebody between payout regions (R-SEED-03, R-BANK-02, R-BANK-05) |
