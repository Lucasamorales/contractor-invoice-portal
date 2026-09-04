# KUDOS.md — the kudos plan

Written before any code. `SPEC.md` §6 (R-KUD-01 … R-KUD-06) predates sight of
the real spreadsheet and is thin; this document is the detail underneath it and
proposes R-KUD-07 onwards. Where the two disagree, this file is the newer
thinking and `SPEC.md` should be amended to match — but not by me, and not
silently (CLAUDE.md §8).

Nothing here is built yet.

---

## 1. What the sheet actually is

Read from a downloaded copy of the live workbook on 4 September 2026.

`Raw Data` is an **event log**, which is the best case we could have hoped for:

| Col | A | B | C | D | E | F |
|---|---|---|---|---|---|---|
| | Date | Giver | Value | Receiver | Space | Message |

95 rows, 9 January – 1 September 2026. No blank givers or receivers. Twelve
rows carry a time as well as a date; the rest are midnight.

The `2026` tab is a **derived** per-person × per-month grid. It also holds
`TOTAL PER MONTH`, a `Winner after tiebreaker` row (row 28, where the announcer
writes), and a second *givers* table below that. We read the log and never the
grid — same tab the announcer reads, so the two cannot drift apart.

### Two things now verified rather than assumed

**One row = one kudo = £20.** I cross-checked the grid against the log across
24 receivers × 9 months: **zero mismatches**. The five multi-value rows — the
"Multi Kill" of `Own it, Go beyond, Be curious, Think big` — count as **one**
kudo. £20, not £80. The grid and the announcer already agree on this and Leo
has confirmed it.

**The log is clean.** Zero duplicate `(date, giver, value, receiver)` tuples in
95 rows, so that tuple is a usable identity today.

### A bug in the announcer, for information

`LEADERSHIP_TEAM` matches by substring, so `"Castellanos"` matches **both**
Charlie and Remy Castellanos. Remy is silently excluded from winning
anything. Not our code and not our fix — but worth someone knowing.

---

## 2. Names, and why matching must be explicit

Matching the sheet's 22 receivers against the 21-row email list, using surname
plus one given name:

- **16 match cleanly** — Marta Aldridge, Ezra Coleman, Ian Rhodes,
  Gabriel Robinson, Alexandra Cobb, Petra Gilmore, Louisa Sutton, Samantha
  Lane, Marco Farrow Ives, Leo Marsh, Jamie Vance Gray, Julian
  Foster, Felix Pearce, Remy Castellanos, Remy "Kit" Keller,
  Oscar Hale.
- **1 is a nickname** — the sheet says `Toni Grant`, the list says
  `Mitchell Grant`. Needs an alias.
- **5 are not in the email list because they have left the agency** — Remy
  Carter Bell, Carmen Dawson, Jamie Blake Irwin, Victor Harlow,
  Gabriel TITO Palmer. Between them they hold 15 historic kudos.

  **None of them has a kudo on or after the cut-over**, so putting them on the
  ignore list costs nobody a penny. Their rows stay in the log and stay
  ingested — deleting history to tidy a screen is how you lose the ability to
  explain a past payment.

The list also contains five people who have never received a kudo (Riley
Garner, Charlie Castellanos, Edgar Albert Castellanos, Ella Gardner, and
Mitchell Grant pending the alias above).

### The ignore list

*Decided.* Nine names never resolve to a person and never report as a problem:

| Name | Why |
|---|---|
| Remy Carter Bell | left the agency |
| Carmen Dawson | left the agency |
| Jamie Blake Irwin | left the agency |
| Victor Harlow | left the agency |
| Gabriel TITO Palmer | left the agency |
| Riley Garner | does not invoice through the portal |
| Toni Grant | does not invoice through the portal |
| Charlie Castellanos | does not invoice through the portal |
| Edgar Albert Castellanos | does not invoice through the portal |

That leaves **17 people** who can invoice here. `CLAUDE.md` says sixteen, so
either the number has moved or one of the seventeen also does not invoice —
worth reconciling before go-live, though nothing depends on it: a person with
no fee simply cannot invoice.

Being on the ignore list is not the same as being excluded from the standings.
*Decided:* someone who has left cannot win, so leavers are out of the standings
too.

### Leadership: receives kudos, cannot cash them

*Decided.* **Remy Keller invoices through the portal, but his kudos are
never billable.** Leadership can be given kudos and can appear on the
leaderboard; they cannot convert them to £20 and they cannot win a prize.

This is a **third** state, and it is worth naming because the first two do not
cover it:

| | Invoices here | Kudos billable | Can win |
|---|---|---|---|
| A contractor | yes | yes | yes |
| Ignore list | no | — | no |
| **Leadership who invoices** | **yes** | **no** | **no** |

So it is a flag on the person (`kudos_eligible`), not a name on a list.

*Decided:* **two people are in this state — Remy Keller and Remy
Castellanos.** Both invoice here, both may be given kudos, neither can cash
them and neither can win.

That also settles the announcer's substring bug: `"Castellanos"` matching
Remy as well as Charlie turns out to be the right answer reached by the
wrong route. It is still worth fixing there, because it will match the next
Castellanos too, and by then it may be wrong.

Note this is stricter than the written policy, which says the incentive is paid
*"to all kudos recipients, regardless of their position on the leaderboard"*
and does not carve leadership out. Leo's instruction is the decision; the
policy wording should be tightened to match, or someone will eventually read
the policy and expect to be paid.

Spellings drift: `Jamie Blake Irwin` in the 2026 tab is
`Jamie Blake irwin` in the 2025 tab. There are two people called Remy
Castellanos-or-similar. The winner row uses nicknames — Daws, Mari, Lenny,
Sutton.

**So: an explicit alias table, never fuzzy matching.** A near-miss that
resolves itself pays the wrong person, and paying the wrong person is the one
failure this system exists to prevent. Automatic matching gets 16 of 22 and a
human confirms the rest, once.

---

## 3. The claim ledger

The requirement, in Leo's words: *"we need to keep track of what kudos have
been already claimed, because if someone gave me a kudos in August and I put it
in my invoice for September, when I go to make the invoice for October the one
that was already claimed should not be added."*

Because `Raw Data` is an event log, this is clean.

```
kudos_events
  id                     uuid pk
  event_key              text unique      -- see below
  person_id              uuid null        -- null until the name is mapped
  receiver_name          text             -- exactly as the sheet spells it
  giver_name             text
  value_text             text
  awarded_on             date
  claimable              bool             -- false for pre-cut-over history
  claimed_by_invoice_id  uuid null
  first_seen_at          timestamptz
  withdrawn_at           timestamptz null
```

**`event_key`** is a hash of `(date, giver, receiver, value)` plus an ordinal
for exact repeats — deliberately **not** the message, so fixing a typo in the
text of a kudo does not conjure a second one. It is stable under reordering,
insertion above, and re-sorting, none of which a row number survives.

**The rule is then one line:** an invoice bills every event for that person
where `claimable AND claimed_by_invoice_id IS NULL`. The transaction that
allocates the invoice number stamps those events with the invoice id.

This is stronger than the month rule we were reaching for:

- Kudos given on the 26th, after someone invoiced on the 24th, roll into next
  month automatically. Nothing is ever lost.
- A kudo billed in September cannot appear in October. Not because we remember
  to apply a rule, but because the row is stamped.
- Nobody has to answer "which month does this kudo belong to" — the question
  that was going to cause the arguments.

### Two safety properties

**A claimed event is never deleted, even if the row disappears from the
sheet.** It gets `withdrawn_at` and appears on a report. Otherwise somebody
tidying the spreadsheet destroys the record of what was paid.

**Editing history in the sheet cannot change an issued invoice.** Already
guaranteed — kudos are frozen onto `invoice_lines` at submit (R-KUD-05) — but
the sync will *report* a divergence rather than swallow it.

### The existing snapshot stays

`kudos_snapshots` is unchanged and becomes the read model: `kudos_count` is the
number of claimable, unclaimed events at sync time. **The wizard, the preview
and the PDF need no changes at all.**

---

## 4. The cut-over and the cadence

*Both decided.*

**Kudos are billed quarterly**, following the written policy: *"This incentive
is paid quarterly to all kudos recipients… add the total amount accumulated
over that quarter to your invoice."* Kudos and quarterly awards appear on the
**quarter-end invoice** — March, June, September, December — and on no other.
Fees and expenses stay monthly. In the other two months the wizard says when
kudos will next appear rather than showing a zero, which would look like a bug.

**The cut-over is 1 July 2026**, the start of the first unpaid quarter. Q3 ends
on 30 September and has not been paid, so anything from 1 July is still owed.
Everything to 30 June belongs to Q2 and was settled the old way.

An earlier draft said 1 September. That was agreed while we both believed the
£20 was monthly, and under quarterly payment it would have dropped 14 kudos —
**£280** — that nine people are owed. One config value, `kudos_claimable_from`.

The whole 2026 log is still ingested; events before the cut-over are inserted
with `claimable = false`, counted for standings and never billed.

### What Q3 2026 therefore pays

The first quarter this feature touches, on the log as at 1 September:

| | Kudos | Value |
|---|---|---|
| Ezra Coleman | 4 | £80 |
| Marco Farrow Ives, Petra Gilmore, Marta Aldridge | 2 each | £40 each |
| Felix Pearce, Jamie Vance Gray, Oscar Hale, Alexandra Cobb, Samantha Lane, Leo Marsh | 1 each | £20 each |
| **Kudos total** | **16** | **£320** |
| Quarterly awards (§5) | | **£200** |
| | | **£520** |

No leadership member has a Q3 kudo, so the eligibility flag changes nothing
this quarter. September is not over, so these will move.

---

## 5. Quarterly and annual prizes

*Decided: in scope, and they appear on the invoice like kudos do.*

Taken from the written policy (effective 1 October 2024, last reviewed August
2025), **not** from the announcer's message text:

| | Cash | Threshold on the cash | Perk, and its threshold |
|---|---|---|---|
| Quarterly 1st | **£100** | none | LinkedIn/Instagram post, plus an Extra Freaky Friday **or** a Mid-Day Off — **5+ kudos** |
| Quarterly 2nd | **£75** | none | Social shout-out, plus 1× Sleepy Morning — **3+ kudos** |
| Quarterly 3rd | **£25** | none | — |
| Monthly winner | none | — | 1× Sleepy Morning — **3+ kudos in the month** |
| Annual podium | **none** | — | Not in the policy at all |

> **This corrects an earlier reading of this document.** The 5+ and 3+
> thresholds gate the **perks**, not the money. Cash for the top three is
> unconditional — being in the top three is the whole qualification, and 3rd
> place has no stated threshold precisely because of that. An earlier draft had
> the thresholds on the cash, which would have paid nothing where the policy
> pays £200.

Leadership are excluded from winning. Ties are resolved by a human, and the
policy names the tiebreakers: year-to-date total, consistency, kudos from
different teams, and longevity. Every one of those is a judgement, which is why
the award button exists.

### So: computed, then confirmed

```
kudos_awards
  id, person_id, period_key ('2026-Q3'), kind ('quarter_1st' | …),
  amount_pence, awarded_by, awarded_at, note,
  claimed_by_invoice_id null
```

A new `/admin/kudos` screen shows the computed standings for the quarter, the
thresholds each person clears, and any tie, and finance presses a button to
award. Nothing is awarded automatically. Once awarded it behaves exactly like a
kudo: unclaimed until an invoice picks it up, then stamped.

That is the "solid logic" Leo asked for, with the human exactly where the
policy already puts one, and nowhere else.

### Q3 2026, on the data as it stands

*Decided: a Q3 prize is paid through the portal if there is a winner.* Leavers
are out of the standings — they earned the kudos but they cannot win — which
removes Victor Harlow (2) and Remy Carter Bell (1). That leaves:

| | Kudos in Q3 | Cash | Perk |
|---|---|---|---|
| Ezra Coleman | 4 | **£100** | no — the Freaky Friday needs 5 |
| Petra, Marta, Marco — tied on 2 | — | **£75 and £25**, split by tiebreaker | no — the Sleepy Morning needs 3 |

So Q3 pays **£200 across three people**, and a three-way tie for 2nd and 3rd
has to be resolved by management against the policy's tiebreakers. Nobody
earns a perk. September is not over, so this will move.

This is the case the award button exists for: the cash is mechanical, the
allocation between Petra, Marta and Marco is not.

An invoice's Additional therefore becomes:

```
unclaimed kudos × £20   +   any unclaimed award   +   expenses
```

**This changes the invoice's detail line, which CLAUDE.md §8 says I must ask
about before touching.** Consider it asked: a quarterly prize needs its own
line, because "5 kudos × £20 = £100" and "Q3 1st place £100" are different
things and Robin will need to tell them apart.

---

## 6. Perks — deliberately not in v1

*Decided: the money is unaffected by a trade-in. Someone who trades kudos for a
Freaky Friday still invoices those kudos at £20.*

That is the answer that removes the risk. There is no money leak, because a
perk consumes nothing billable. It means the perk feature is **purely
additive** and can be built later with zero risk to invoicing.

Leo is right that it complicates things, so v1 does not include it. What it
would need before it can be designed: the catalogue of perks, who approves one,
whether there is a limit, and whether a perk taken is recorded anywhere today.

---

## 7. Proposed requirements

To be added to `SPEC.md` §6 if this plan is accepted.

- **R-KUD-07** The source is the `Raw Data` event log, never a derived grid tab.
- **R-KUD-08** One log row is one kudo at `rate_pence`, whatever the `Value`
  column lists. A multi-value kudo is one kudo.
- **R-KUD-09** An event's identity is a hash of date, giver, receiver and value
  plus an ordinal — never the row number, never the message.
- **R-KUD-10** An event is billed at most once. An invoice claims every
  claimable unclaimed event for that person.
- **R-KUD-11** Claiming happens in the same transaction that allocates the
  invoice number (R-NUM-02). A failed submit releases the claim with the number.
- **R-KUD-12** Events before `kudos_claimable_from` are ingested and counted
  for standings but are never billable.
- **R-KUD-13** Names resolve through an explicit alias table. Fuzzy or partial
  matching is forbidden.
- **R-KUD-14** An unmatched receiver is reported on `/admin/queue` and never
  guessed (restates R-KUD-03). Names on an explicit ignore list do not report.
  Ignoring a name never deletes its events: leavers and staff keep their
  history, they simply resolve to nobody.
- **R-KUD-15** A claimed event is never deleted. A row that leaves the sheet is
  marked withdrawn and reported.
- **R-KUD-16** The portal never writes to the spreadsheet. Read-only, one file,
  shared with the service account.
- **R-KUD-17** Quarterly awards are computed but only a human awards them.
- **R-KUD-18** An award is claimed exactly like a kudo, and prints as its own
  line.
- **R-KUD-19** Perks are out of scope for v1 and do not affect money.
- **R-KUD-20** A person may be marked not kudos-eligible: they invoice
  normally, their kudos are counted and displayed, and none of it is billable.
  Leadership is the case this exists for.
- **R-KUD-21** Someone who has left is out of the standings. They cannot win a
  prize they cannot be paid.
- **R-KUD-22** Quarterly cash for the top three carries no kudos threshold.
  Thresholds (5+, 3+) gate perks only, and perks are out of scope.
- **R-KUD-23** Management may reduce a person's kudos after an abuse review. An
  unclaimed event that disappears is simply withdrawn; one already billed
  cannot be reversed, because an issued invoice is immutable (CLAUDE.md §2.3).
  The correction is a void and reissue, and the sync must report it loudly
  rather than adjust anything itself.

---

## 8. Build order

Each step ships on its own.

1. `domain/kudos.py` — pure. Rows in; events, resolved aliases and problems
   out. Tested against the real oddities: accents, `Remy "Kit" Keller`,
   `Gabriel TITO Palmer`, multi-value rows, the mixed date/datetime column.
2. `kudos_events` + `kudos_aliases` + `kudos_awards`, and the migration.
3. `services/kudos_sync.py` — one `httpx` GET to the Sheets REST API with a
   token from Cloud Run's identity. **No new dependency**: `google-auth` and
   `httpx` are already direct dependencies, and `google-api-python-client`
   would be about fifty packages to avoid thirty lines.
4. Claiming, inside the existing submit transaction.
5. `/admin/queue` — unmatched names with a "map to this person" action.
6. `/admin/kudos` — quarterly standings and the award button.

Access is enforced by **sharing, not by scope**. There is no "one file" OAuth
scope; `spreadsheets.readonly` is account-wide. What scopes it to one file is
that the service account can only see what is shared with it. Share that one
sheet read-only with the Cloud Run service account and never grant a Drive
scope. The code cannot enforce this, which is exactly why it is written down.

The sheet is semi-public and holds no fee and no bank detail, so reading it
creates no leak (CLAUDE.md §2.1 is not engaged).

---

## 9. Settled, and still open

Settled on 4 September 2026: the cut-over (§4), trade-ins not affecting money
(§6), quarterly prizes being in scope and paid through the portal (§5), the
ignore list (§2), monthly cadence, and one kudo being £20 whatever the `Value`
column lists (§1). `Raw Data` is the tab we read — it is what the announcer
already reads, so the two cannot drift apart.

The prize table is now taken from the written policy (§5), the annual podium
carries no money and is dropped, leadership receives but cannot cash (§2),
leavers are out of the standings, and the sheet is:

```
YOUR_KUDOS_SHEET_ID
```

Settled on 5 September: quarterly cadence and a 1 July 2026 cut-over (§4),
Remy Castellanos is leadership alongside Remy Keller (§2), and the sheet
has been shared with the staging service account.

**Nothing is blocked. One prerequisite remains before the sync can run:**

- **`sheets.googleapis.com` is not enabled on the project.** Sharing the sheet
  is necessary but not sufficient — the API has to be on or every read returns
  403. It is one line in `infra/services.tf` and an apply.

Deferred by decision, not forgotten:

- **Perks** (§6). Needs the catalogue, who approves, and whether a perk taken is
  recorded anywhere today. No money depends on it.
- **The announcer's substring match** on `LEADERSHIP_TEAM`. Right answer today,
  wrong method, and it is not our code.
- **Production sharing.** When production is deployed it gets its own service
  account and the sheet must be shared with that one too.
- **`SPEC.md` §6 still says none of this.** R-KUD-07 … R-KUD-23 live here until
  someone amends it.
