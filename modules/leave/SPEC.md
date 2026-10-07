# modules/leave/SPEC.md — Leave

**Status: draft, 7 October 2026.** Replaces Leave Dates (leavedates.com) for the
whole team, as a module of the company hub (`docs/decisions/0001-modular-hub.md`).

Only the rules in `modules/leave/domain/` exist so far. Nothing is wired into the
running app, nothing is stored, and nothing about any person is held anywhere.
What is missing, and what each missing piece is waiting on, is in
[`BACKLOG.md`](BACKLOG.md): **every gap in this document has a `L-nn` there.**

## Sources

| Tag | Where it comes from |
|---|---|
| **brief** | Remy's brief: the four core functions, the basics, the hard parts, what is out of scope for v1 |
| **policy §n** | *Company Policy: Leave*, section n (document dated March 2025, last page says reviewed January 2026) |
| **procedure** | *Company Procedure: Requesting Leave Days*, the four steps |
| **assumed** | Mine. A reasonable reading where the documents are silent. Each is open to being overruled and each has a backlog entry |

## Not in scope for v1 (brief)

Reports, overtime, sick leave tracking, a native mobile app, Slack integration.

## Units and the leave year

| ID | Requirement | Source | Built |
|---|---|---|---|
| R-LEAVE-01 | The leave year is the calendar year, January to December. A date's leave year is its year | policy §4.1 | `years.py` |
| R-LEAVE-02 | Leave is counted in **integer half-days**: a whole day is 2, a morning or an afternoon is 1. No floats, ever, for the reason money is integer pence | brief (half days are v1), assumed (the representation) | `days.py` |
| R-LEAVE-03 | A request is a run of half-days from a date and a part of the day to a date and a part of the day, both ends inclusive. A single morning is expressible | assumed | `days.Span` |
| R-LEAVE-04 | A request is charged only for half-days on a day the person works that is not a public holiday. A weekend or a bank holiday inside a request costs nothing. Which days a person works, and which days are holidays, are **inputs** | policy §3.2 and §4.2 (the entitlement already *includes* bank holidays, so charging one again would take it twice); inputs open: L-02, L-16 | `days.half_days_by_date` |
| R-LEAVE-05 | A request that crosses the year end is split into one piece per leave year, each charged to its own year, with the original start and end parts kept | brief (a named hard part) | `days.split_by_leave_year` |

## Requests

| ID | Requirement | Source | Built |
|---|---|---|---|
| R-LEAVE-06 | A request is pending, approved, rejected or cancelled. Only a pending request can be approved or rejected | brief (approve/reject), assumed (the state names) | `requests.py` |
| R-LEAVE-07 | A person's live (pending or approved) requests may not share a half-day. A morning and the afternoon of the same day do not clash | brief (a named edge case) | `requests.overlaps` |
| R-LEAVE-08 | **Nobody approves or rejects their own leave**, however the email is spelt. Held in the domain so that no screen or script can be the one that forgets | assumed. Who approves a manager is open: L-10 | `requests.approve` |
| R-LEAVE-09 | A pending or approved request can be cancelled. Cancelling is only a change of state; the days come back through the ledger (R-LEAVE-14) | brief (cancelling approved leave is a named edge case) | `requests.cancel` |

## The balance

| ID | Requirement | Source | Built |
|---|---|---|---|
| R-LEAVE-10 | A balance is the sum of **ledger entries, which are only ever added**: never edited, never deleted. "Why is my number wrong" has an answer in the entries | assumed. The invoice side of this system learned it the hard way: voided invoices once kept their kudos and their numbers because releasing them was a step somebody had to remember | `ledger.py` |
| R-LEAVE-11 | Entries are a grant (positive), an adjustment (either sign), a booking (negative) or a cancellation (positive). The sign is fixed by the kind, so a malformed entry cannot exist | assumed | `ledger.Entry` |
| R-LEAVE-12 | A person's balance for a leave year is **allowance, used, booked and remaining**. Entries from other years are ignored, which is where the leave-year boundary is enforced. There is **no carry-over by default**; an exception is an adjustment somebody added on purpose | brief (the four numbers); policy §4.8 (no carry-over), §4.9 (exceptions by consent) | `ledger.balance` |
| R-LEAVE-13 | *Used* is leave on or before today and *booked* is leave after it. Today counts as used. `today` is always a parameter. `remaining` equals the plain sum of the year's entries, whichever day it is read | assumed | `ledger.balance` |
| R-LEAVE-14 | Cancelling leave appends the days back as cancellation entries and edits nothing. Cancelling **from a date** reverses only the days on or after it, so somebody who took Monday and Tuesday and cancels the rest gets Wednesday to Friday back | brief; assumed (the from-date form) | `ledger.cancellation_entries` |

## Joiners and leavers

| ID | Requirement | Source | Built |
|---|---|---|---|
| R-LEAVE-15 | `accrued = (time worked this leave year ÷ time in the leave year) × annual allowance` and `owed = accrued − leave taken`, where *taken* is leave used up to and including the last day | brief | `leaver.py` |
| R-LEAVE-16 | The result is an **exact fraction and is never rounded** by the domain. `worked` and `in_year` only have to be in one common unit. Policy §4.2 says weeks and the brief says days, so the unit and the rounding wait for an answer: L-05 | brief; policy §4.2 | `leaver.accrued` |
| R-LEAVE-17 | A negative `owed` is a real result and is reported, not hidden. Whether anything is deducted is accounts' decision | policy §4.13 (the company reserves the right to deduct for leave taken in excess) | `leaver.leaver_calculation` |
| R-LEAVE-18 | A birthday that falls on a weekend or a restricted date is taken on the nearest Friday. Two Fridays are never equally near a non-Friday. When the nearest Friday is itself restricted, the earlier of the two equally near Fridays is taken, and the search widens if that is restricted too | policy §4.14; the restricted-Friday tie is assumed: L-06 | `birthday.py` |

## Time

| ID | Requirement | Source | Built |
|---|---|---|---|
| R-LEAVE-19 | **No rule in the domain reads the clock.** Every date it depends on is a parameter, so the same inputs give the same answer next year. A test fails the build if one does | assumed. A calculation somebody will check months later must be reproducible, and two tests in the invoice suite failed once the real date moved on | `tests/architecture` |

## What these rules are not

They do not store anything, route anything or decide who may press a button. They
are the arithmetic. Everything that needs a person's data, a database, a screen
or a decision is in the backlog and is **not specified here**, deliberately,
because the documents do not yet answer it:

| Area | Where it stands |
|---|---|
| Who is covered, and the base entitlement | L-01, L-03 |
| Holiday calendars per person | L-02, L-26 |
| The length-of-service extra days (the policy's table contradicts its text) | L-04, L-30 |
| Weeks versus days, rounding | L-05 |
| The birthday day inside or on top of the allowance | L-06 |
| Closure days and Team Week | L-07, L-29 |
| Who enters a request, who approves, the approver being off | L-09, L-10 |
| Notice periods and the three-week rule | L-12, L-32 |
| Leave types other than annual leave | L-13, L-33 |
| What the wall chart reveals | L-14 |
| Migrating from Leave Dates | L-17, L-34 |

## Requirement → test

| Requirement | Tests |
|---|---|
| R-LEAVE-01 | `tests/leave/domain/test_years.py` |
| R-LEAVE-02 .. 05 | `tests/leave/domain/test_days.py` |
| R-LEAVE-06 .. 09 | `tests/leave/domain/test_requests.py` |
| R-LEAVE-10 .. 14 | `tests/leave/domain/test_ledger.py` |
| R-LEAVE-15 .. 17 | `tests/leave/domain/test_leaver.py` |
| R-LEAVE-18 | `tests/leave/domain/test_birthday.py` |
| R-LEAVE-19 and the module boundaries | `tests/architecture/test_boundaries.py`, `tests/domain/test_purity.py` |
