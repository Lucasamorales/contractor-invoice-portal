# BACKLOG.md — what is left

As at 22 September 2026. The portal is live in production and in its first
cycle: 16 people imported, fees set, the window open since the 22nd, and the
first invoices sent. What was built and when is in `CHANGELOG.md`.

---

## 1. Waiting on somebody else

| # | What | Why it matters |
|---|---|---|
| 1.1 | **Rotate both Neon passwords** | They were pasted into a chat transcript. Staging's was exposed again on 16 September. Nothing else has to change: add the new connection string as a new version of the secret |
| 1.2 | **Confirm Marta Jamie's payee is "Bridge Building"** | Her account is in a company name (R-SEED-08). Julian confirms before her first payment |
| 1.3 | **Decide about Samantha Lane** | She receives kudos in the sheet but is not in the spreadsheet, so she has no account. Her six kudos sit unmatched on the Queue until she signs in and accounts match her, or accounts choose "Nobody" |
| 1.4 | **Protect `main` on GitHub** | Settings → Branches → require a pull request. `main` is what production runs |

## 2. Worth doing soon

| # | What | Why |
|---|---|---|
| 2.1 | **Kudos sync more often than once a day** | Partly done: accounts have a refresh button on the kudos screen (R-KUD-24, 25 September). The automatic read is still once a day at 07:00 London, which is 03:00 in Argentina, so a kudo given during the day still needs somebody to press the button or to wait. Remaining options: hourly through the window, or moving the daily read to the start of the Argentine working day |
| 2.2 | **A sending-only Resend key** | Both environments share one key, and it has full access to the account, including another domain |
| 2.3 | **Refresh `TUTORIAL.md`** | The written guide still describes parts as unbuilt. The in-app tutorial is current |

## 3. Known gaps, decided but not built

- **Withdrawing a proposed bank change.** Accounts can propose a correction and
  flag a row back, but cannot take a proposal back; it waits until the person
  answers. It needs a state-machine change, so it was raised rather than built.
- **Somebody who misses a month entirely** has no way to invoice for it
  afterwards. The spec has no answer for this; the workaround is a late
  submission inside the next window, which lands in the wrong period.
- **Kudos perks** (the Sleepy Morning and the rest of the written policy) are
  not in the portal. Only the cash is.

## 4. Nice to have

- **A short link** like `example.com/invoices` pointing at the portal.
  A redirect in Webflow does it for nothing; the address bar still shows the
  Cloud Run address. Serving the app on `invoices.example.com` needs a
  load balancer at about £15–18 a month, which breaks the £0 target (R-ENV-06).
- **Deleting `app/templates/home.html`**, a screen no route renders.
- **`docs/setup.md` §4's seed instructions** now describe something that has
  been run once, in production, and will not be run again.
