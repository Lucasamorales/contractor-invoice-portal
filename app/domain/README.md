# app/domain

The rules, as pure functions: no database, no network, no file access. Anything
here can be tested with plain values, and is, in `tests/domain`.

This folder must not import from `app/db`, `app/services` or any I/O library
(`CLAUDE.md` §5). When a rule needs data, the caller passes it in.

| File | What it is |
|---|---|
| `banking.py` | Bank-detail parsing, normalisation and validation |
| `formatting.py` | Presentation helpers for the invoice (R-PDF-06, R-PDF-11, R-PDF-15) |
| `iban.py` | IBAN validation (R-SEED-07, R-BANK-04) |
| `invoice.py` | Invoice composition: lines, totals and the printed detail line |
| `kudos.py` | Kudos rules (R-KUD-07 .. R-KUD-23). See KUDOS.md for the reasoning |
| `money.py` | Money rules |
| `numbering.py` | Invoice numbering rules (R-NUM-01 .. R-NUM-06) |
| `period.py` | Period and submission-window rules (R-PER-01 .. R-PER-06) |
| `seeding.py` | Turning one spreadsheet row into a proposed set of records (R-SEED-*) |

Money is integer pence everywhere in here. It is only formatted for display at
the template boundary.
