# app/services

Where the work happens. A service takes a database session and plain values,
calls the rules in `app/domain`, reads and writes through `app/db/repo.py`,
and talks to the outside world (storage, mail, the kudos sheet).

| File | What it is |
|---|---|
| `admin.py` | Read models and writes for the admin portal (R-ADM-01 .. R-ADM-09) |
| `backup.py` | Nightly database backup (R-JOB-05) |
| `bank_changes.py` | The bank-details lifecycle (R-BANK-01 .. R-BANK-08) |
| `crypto.py` | Encryption of the bank columns (R-SEC-03, CLAUDE.md §2.5) |
| `flags.py` | Flags: accounts asking a person to check something of theirs (R-BANK-08) |
| `invoicing.py` | The submit transaction (R-SUB-01 .. R-SUB-10) |
| `jobs.py` | The work behind the scheduled endpoints (R-JOB-01 .. R-JOB-06) |
| `kudos_admin.py` | The two decisions a person has to make about kudos |
| `kudos_sync.py` | Reading the kudos sheet (R-KUD-01 .. R-KUD-03, R-KUD-07 .. R-KUD-16) |
| `mailer.py` | Mail: the Resend client and the outbox dispatcher (R-SUB-06, R-JOB-04) |
| `pdf.py` | Rendering the invoice (R-PDF-01, R-PDF-13, R-SUB-04) |
| `receipts.py` | Receipt files: what we accept, and what we do to them (R-WIZ-04, R-SEC-10) |
| `storage.py` | Object storage for invoices and receipts (R-STO-01 .. R-STO-04) |

Three things are worth knowing before changing anything here:

- **Mail is never sent inline.** A service writes a row to the outbox in the
  same transaction as the thing it describes; `mailer.dispatch` sends it later
  and retries. Staging uses a client that records and discards, or one that
  redirects everything to a single address.
- **Storage is an interface.** `ObjectStore` has a Google Cloud Storage
  implementation and a local-directory one, which is what the tests use.
- **Bank fields pass through `crypto.Cipher`** on the way in and out, with the
  person and field name bound into each value so one cannot be swapped for
  another.
