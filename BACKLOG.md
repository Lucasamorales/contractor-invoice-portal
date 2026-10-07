# BACKLOG.md — what is left

As at 28 September 2026, running 0.8.22. The first cycle is complete: 17
invoices for September, four of them cancelled and reissued. What was built and
when is in `CHANGELOG.md`.

---

## 0. The hub and the leave module

A decision is waiting on you: **should the invoice app be moved into
`modules/invoices/` (Phase 1) before the invoice window opens on 22 October, or
after it closes on the 28th?** Everything else about the hub stands without it.
The plan, the architecture diagram and what is decided versus proposed are in
`docs/decisions/0001-modular-hub.md`.

Leave (a replacement for Leave Dates) has its rules written as pure code and
tested, and nothing else. **Its backlog is its own:** `modules/leave/BACKLOG.md`
lists every open question, the problems in the policy document, what to build
once unblocked, and the hub work it depends on.

## 1. Waiting on accounts, this week

Three people are holding a live September invoice that carries the wrong
number, because it was issued before a cancelled invoice gave its number back
(R-NUM-05, 28 September). Accounts void the live one; the person sends again
and lands on their original number. Voiding emails them automatically.

| Person | Void | They then send as |
|---|---|---|
| Eze Coleman | 35 | **34** |
| Louisa Sutton | 33 | **32** |
| Pet Gilmore | 48 | **47** |

Marta Jamie needs nothing doing: her September invoice is already void, her
counter has been pointed back at 9, and she can send whenever she is ready.

The reissues will be flagged **late** on the Finance screens, the window having
closed on the 28th. That is cosmetic and blocks nothing.

## 2. Waiting on somebody else

| # | What | Why it matters |
|---|---|---|
| 2.1 | **Rotate both Neon passwords** | They were pasted into a chat transcript. Staging's was exposed again on 16 September. Nothing else has to change: add the new connection string as a new version of the secret |
| 2.2 | **Decide about Samantha Lane** | She receives kudos in the sheet but is not in the spreadsheet, so she has no account. Her six kudos sit unmatched on the Queue until she signs in and accounts match her, or accounts choose "Nobody" |
| 2.3 | **Protect `main` on GitHub** | Settings → Branches → require a pull request. `main` is what production runs |
| 2.4 | **Correct the spreadsheet's `NOTES` for Marta Jamie** | It still says `EUR`. Harmless today — a re-import only ever raises a counter and never touches a bank row somebody entered themselves — but it would put her back on international payouts |

## 3. Worth doing soon

| # | What | Why |
|---|---|---|
| 3.1 | **Kudos sync more often than once a day** | Partly done: accounts have a refresh button on the kudos screen (R-KUD-24, 25 September). The automatic read is still once a day at 07:00 London, which is 03:00 in Argentina. Three of September's four voids were "a kudo arrived after they had sent", and at a quarter end the next chance is three months away, which is why they are voided rather than left to roll. Options: read hourly through the window, move the daily read to the start of the Argentine working day, or let a late kudo land on the following month's invoice rather than waiting for the quarter |
| 3.2 | **A sending-only Resend key** | Both environments share one key, and it has full access to the account, including another domain |
| 3.3 | **Put the payout region in the print snapshot** | `render_and_store` says it reads everything from the snapshot and does not: the region comes from the live person row. Harmless while a PDF is only ever rendered once, but it contradicts R-DATA-09, and it would print the wrong block for anybody who has changed region |
| 3.4 | **A warning when voiding an invoice that is not the newest** | The number goes back to whoever holds it, which is right, but reusing a number below one already issued puts the dates out of order. Worth saying so on the confirmation rather than forbidding it |
| 3.5 | **Add `*.tfplan` to `.gitignore`** | A saved plan is a full dump of the infrastructure state and is one `git add -A` away from being committed |
| 3.6 | **Refresh `TUTORIAL.md`** | The written guide is behind the app again: it predates the refresh button, the region move and the numbering change. The in-app tutorial (the ? button) is current |

## 4. Known gaps, decided but not built

- **Withdrawing a proposed bank change.** Accounts can propose a correction and
  flag a row back, but cannot take a proposal back; it waits until the person
  answers. It needs a state-machine change, so it was raised rather than built.
- **Somebody who misses a month entirely** has no way to invoice for it
  afterwards. The spec has no answer for this; the workaround is a late
  submission inside the next window, which lands in the wrong period.
- **Kudos perks** (the Sleepy Morning and the rest of the written policy) are
  not in the portal. Only the cash is.
- **Changing a payout region has no requirement behind it.** R-SEED-03 sets it
  at import and the spec says nothing about afterwards. The script exists and is
  audited; the rule it follows is not written down anywhere but its docstring.

## 5. Nice to have

- **A short link** like `example.com/invoices` pointing at the portal.
  A redirect in Webflow does it for nothing; the address bar still shows the
  Cloud Run address. Serving the app on `invoices.example.com` needs a
  load balancer at about £15–18 a month, which breaks the £0 target (R-ENV-06).
- **Deleting `app/templates/home.html`**, a screen no route renders.
- **`docs/setup.md` §4's seed instructions** now describe something that has
  been run once, in production, and will not be run again.
