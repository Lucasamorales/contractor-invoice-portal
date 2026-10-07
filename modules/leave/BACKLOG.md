# modules/leave/BACKLOG.md — what is missing

As at 7 October 2026. What exists is the pure rules in `domain/` and their tests
(see `SPEC.md`). Everything below is **not built**, and each item says what it is
waiting on.

**Blocked on you** means a question only you, Remy or accounts can answer.
**Blocked on a decision** means it needs a sign-off the project rules ask for
first (CLAUDE.md §8). **Ready** means nothing stands in the way except order.

---

## 1. Questions the documents do not answer

Asked in the order they were put to you. Where I have a default I will use it and
mark it as an assumption in the spec, unless you say otherwise.

### Who and where

| ID | Question | Why it matters | Default |
|---|---|---|---|
| L-01 | **Who is covered?** The policy says "team members" on "contracts of employment" and mentions salary. The invoicing app is for contractors. Same people, overlapping, or different? A list, any part-timers, any fixed-term | Decides whether leave sits on the same people directory as invoicing, and blocks the schema | none |
| L-02 | **Country and holidays.** The policy reads as UK law (jury service, statutory parental bereavement pay). Do team members in Argentina follow it unchanged, or have their own entitlement and holiday calendar? | Which public holidays are free, per person | none |
| L-03 | **The base full-time entitlement.** §3.1 says "as specified in their contracts"; the number is not in the policy. One number, or per person, country or contract? | Every balance starts from it | none |

### The numbers

| ID | Question | Why it matters | Default |
|---|---|---|---|
| L-04 | **The length-of-service table contradicts the text.** §3.4 says +1 day per completed year from year 2. The table agrees for years 1 to 4, then runs one higher from year 5 (5, 6, 7, 8, 9) and gives 11 at year 10 *plus* a milestone of 2, though the text says the maximum is 11. Which figures are right? When does an extra day start: the anniversary or the following January? Are the milestone days permanent or a one-off in that year? Does a part-timer get them (§3.4 says full-time)? | Cannot be implemented from the document as written. **Deliberately not built** | none |
| L-05 | **Weeks or days, and rounding.** §4.2 prorates by *weeks* worked; the brief's formula uses *days*. Our numbers must match Leave Dates during the parallel run, so what does it actually do for a partial week, and does it round to whole or half days? | The leaver and starter figures. The domain is exact and unit-agnostic until this is answered | follow Leave Dates, whatever it does |
| L-06 | **The birthday day (§4.14).** On top of the allowance or inside it? It needs a stored birthday (day and month is enough). When the nearest Friday is itself restricted the policy is silent; I take the earlier. Feb 29 birthdays. Anybody who does not work Fridays | R-LEAVE-18 | on top of the allowance; earlier Friday |
| L-07 | **Closure days and Team Week.** §4.3 lets the company require leave to cover closures, published each January. Do those days come out of the allowance automatically? Is Team Week a normal working week where nobody can take leave? | A company calendar, and whether it spends anybody's days | none |
| L-08 | **Half days.** The policy never mentions them; the brief wants them. How does Leave Dates record them (morning or afternoon)? | The model assumes morning/afternoon | AM/PM |

### The process

| ID | Question | Why it matters | Default |
|---|---|---|---|
| L-09 | **Who enters requests?** Today: chat or call, an email to the manager and accounts, approval by email, then **accounts enters the days** in Leave Dates and sends a calendar invite. Does the app replace all of that: the employee requests, the line manager approves in the app, accounts is notified? Do employees have Leave Dates logins today, or only accounts? The documents also disagree on order: the policy says approve, *then* email accounts; the procedure says email first, *then* approval | The shape of every screen | the employee requests, the manager approves, accounts is told |
| L-10 | **Line managers and teams.** Each person's line manager and team(s). Who approves a line manager's own leave? Who covers when the approver is off? | Routing. Until it is answered R-LEAVE-08 refuses self-approval | refuse self-approval; no routing |
| L-11 | **The calendar invite (procedure step 4).** Keep sending it from accounts@ to the team? Creating events as accounts@ means giving the app access to that mailbox's calendar, which needs a deliberate approval | Notifications | an email to the team with the dates, plus the wall chart |
| L-12 | **Notice, the three-week limit, "first come, first served".** Two weeks' notice for a week or more, three working days for single days, and no more than three weeks without approval. Block, or warn the approver? "A week or more": five working days, or seven calendar? "Three weeks": working or calendar days? The policy is silent on 2 to 4 days. Is there a hard minimum cover per team? | Validation. **Deliberately not built** | warn the approver, never block |

### Other absence types

| ID | Question | Why it matters | Default |
|---|---|---|---|
| L-13 | **Which types are in v1?** The policy also has unpaid leave (two weeks per rolling three years), compassionate leave (one day), public duties (three days a year, prorated), jury service, dependants, religious observance, and time off in lieu (§6.2) although overtime is out of scope. Record only, or enforce the caps? | Scope | annual leave and the birthday day |
| L-14 | **Confidentiality.** Compassionate leave is confidential (§7.7). What does the wall chart show? | Who sees what | "away" only; the type is visible to the person, their line manager and accounts |
| L-15 | **Carry-over exceptions (§4.9).** Who grants one, and on what evidence? | R-LEAVE-12 allows them as adjustments | accounts, with a reason, in the audit trail |

### Data and migration

| ID | Question | Why it matters | Default |
|---|---|---|---|
| L-16 | **New personal data I would need to store** (CLAUDE.md §8 says ask first): employment start date; contract type and end date; working pattern (days and hours per week); birthday; line manager; team(s); country. Yes or no to each | Nothing is stored until this is answered | none |
| L-17 | **Migration.** Can Leave Dates export balances and booked leave, and how many years back? Is its balance trusted? Real balances can only be loaded into production, behind a dry-run report: staging never holds real data | The brief: nobody's number may be wrong on day one | none |
| L-18 | **Policy owner.** Who signs off changes to the rules in the app? | Rules must be changeable without a deploy | none |

---

## 2. Problems in the documents themselves

| ID | Problem |
|---|---|
| L-19 | The contents lists "12. Policy owner" and the body has no such section, so the sections shift by one. The last page says reviewed **January 2026** while every footer says **March 2025**: which version is in force? The text still says **Leave Dates**, and will need updating at cutover |
| L-20 | **Where the policy lives.** It is a 21 MB PDF and is not in the repository. The spec cites section numbers so it can be checked, but a reader without the file cannot. Decide whether it is stored in `docs/`, linked, or summarised |

---

## 3. To build once unblocked

| ID | What | Waiting on |
|---|---|---|
| L-21 | **Database.** A schema for this module, a database role of its own, a reversible migration. One `neondb_owner` role does everything today, which is too loose for HR data | a decision (H-04), L-01, L-16 |
| L-22 | **A leave profile** per person: start date, working pattern, contract end, birthday, line manager, team, country | L-16 (new personal data) |
| L-23 | **Request and approval screens** | L-09, L-10 |
| L-24 | **My balance:** allowance, used, booked, remaining, before requesting | L-21, L-03 |
| L-25 | **The wall chart:** who is off when, with public holidays | L-02, L-14, L-21 |
| L-26 | **Where public holidays come from.** A library such as `holidays` would be a new dependency, which CLAUDE.md §8 says to ask about first; the alternative is a yearly list accounts maintains | a decision, L-02 |
| L-27 | **Notifications** through the existing outbox: requested, approved, rejected, cancelled | L-09, L-11 |
| L-28 | **The leaver screen** for accounts: the calculation with its working shown | L-05, L-21 |
| L-29 | **Closure days and Team Week calendar** | L-07 |
| L-30 | **Length-of-service extra days** | L-04 |
| L-31 | **Granting each person's entitlement** per year, prorated for starters, leavers, part-timers and fixed terms | L-03, L-05, L-16 |
| L-32 | **Notice and three-week rules** as warnings | L-12 |
| L-33 | **Other leave types** and their caps | L-13 |
| L-34 | **Importing from Leave Dates** with a dry-run report like the invoice import has, then a **parallel-run comparison** that lists every person whose balance differs | L-17 |
| L-35 | **Tutorial steps for every screen**, as every other screen has | the screens |
| L-36 | **Audit and logging rules for HR data:** every change attributed, and never a leave type or a reason in a log line. The invoice side bans fees and account numbers from logs; this is the equivalent | L-21 |
| L-37 | **Cancelling leave that has already been taken:** who may, whether the days are refunded, and how it is recorded. The ledger can reverse from a date; this is the *rule* | a decision |
| L-38 | **Asking for more than the balance holds:** refuse, warn, or allow it to go negative. Not in the brief and not in the policy | a decision |
| L-39 | **Part-time by hours, not just days per week.** The pattern today is which weekdays someone works | L-01, L-16 |

---

## 4. Hub work this module depends on

These belong to the whole hub rather than to leave, and are listed here so
nothing is lost. The reasoning and the plan are in
`docs/decisions/0001-modular-hub.md`.

| ID | What | Status |
|---|---|---|
| H-01 | **Phase 1: move the invoice app into `modules/invoices/`**, with a file-by-file split of the mixed files (models, mailer, config, templates). No behaviour change, no migrations | **Needs a go.** Prove it on staging before the invoice window opens on 22 October, or wait until after it closes on the 28th |
| H-02 | **Packaging:** `packages = ["app"]` in `pyproject.toml` and `COPY app` in the Dockerfile must list `modules/` and `core/`, or the image will not contain them. Nothing in the image imports them yet, so nothing is broken | lands with H-01 |
| H-03 | **Access per module** (`PORTAL_ACCESS_<MODULE>`), replacing the single finance list. Touches non-negotiable §2.1, so it needs your explicit sign-off, and version one must be provably identical in effect for invoices | needs sign-off |
| H-04 | **One database schema and one database role per module**, so the leave module cannot read bank details as a fact of the database rather than a convention | needs a decision |
| H-05 | **URLs and a landing page:** `/` as a module chooser, `/invoices` and `/leave`, with permanent redirects for every old path, because live emails link to `/`, `/me` and `/admin/...` | needs a decision |
| H-06 | **The module contract and registry:** name, routes, nav, roles, jobs, mail kinds, tutorial and settings declared in one place, with unknown or colliding modules refused at start | after H-01 |
| H-07 | **Scheduled jobs per module**, generated into Terraform | after H-06 |
| H-08 | **Do not rename the Cloud Run services.** The name sets the IAP audience, so a rename means reconfiguring IAP and everyone signing in again | decided: not now |
| H-09 | **Turn on Secret Manager data-access logging before any HR data is loaded.** Today nobody can say who read the production database credential | before L-21 |
