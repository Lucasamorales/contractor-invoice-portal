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

Being on the ignore list is not the same as being excluded from the standings,
which is a separate decision — see §9.

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

## 4. The cut-over

*Decided: September 2026 forward for money; full history for standings.*

The whole 2026 log is ingested, because Q3 standings need July and August. Every
event dated before **1 September 2026** is inserted with `claimable = false`:
counted for winners, never billed. One config value, `kudos_claimable_from`.

Without this, Marta Aldridge's first portal invoice carries ten kudos and
£200 that she has in all likelihood already been paid.

---

## 5. Quarterly and annual prizes

*Decided: in scope, and they appear on the invoice like kudos do.*

From the announcer, the prize structure is:

| | Threshold | Reward |
|---|---|---|
| Monthly winner | 3+ kudos | A Sleepy Morning. **No money.** |
| Quarterly 1st | 5+ kudos | **£100** |
| Quarterly 2nd | 3+ kudos | **£75** |
| Quarterly 3rd | none stated | **£25** |
| Annual podium | none stated | No money stated |
| Trade-in | 2+ kudos in a month | A perk. Money is unaffected. |

Leadership are excluded from winning. Ties are resolved by a human — the
sheet's own `Winner after tiebreaker` row is proof that this cannot be fully
automatic, and its nicknames are proof that we should not try to read it back.

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

*Decided: a Q3 prize is paid through the portal if there is a winner.* On the
log as at 1 September, there is not one yet:

| | Kudos in Q3 | Threshold |
|---|---|---|
| Ezra Coleman | 4 | 1st needs **5** |
| Marco, Petra, Marta (tied) | 2 | 2nd needs **3** |

Nobody clears their bar, so as things stand **no Q3 award would be made at
all**. Most of September is still to come and one more kudo puts Ezra over
the line, so this is a snapshot rather than a result — but it is worth knowing
that the first quarter this feature touches may well award nothing, and that
this is the policy working rather than the code failing.

Two of the Q3 scores belong to people who have left (Victor Harlow 2, Remy
Carter Bell 1). They cannot be paid a prize, which is a second reason
the award is a human decision.

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

Still open, in the order they block work:

1. **The spreadsheet's file ID**, and the sheet shared read-only with the Cloud
   Run service account. Nothing can be built without this.
2. **Confirm the prize table in §5.** The £100/£75/£25 and the 5+/3+
   thresholds are read out of the announcer's message text, not out of a policy
   document. Is there a threshold for 3rd place? Does the annual podium carry
   money, or only glory? Money should not be derived from a paragraph that was
   written to be read aloud in Google Chat.
3. **Does Remy Keller invoice through the portal?** He is in the announcer's
   `LEADERSHIP_TEAM`, which excludes him from *winning*, but he was not named
   among the four who do not invoice — and he has a kudo. Leadership exclusion
   and invoicing are different things, so this needs saying rather than
   inferring. It decides whether his kudos are billable.
4. **Do people who have left count in the standings?** They cannot be paid a
   prize, but they were there and they earned the kudos. Excluding them changes
   who is 2nd and 3rd. Today it changes nothing in Q3; one quarter soon it will.
