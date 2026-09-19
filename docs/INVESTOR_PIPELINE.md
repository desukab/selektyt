# SelekyT — Investor Pipeline: process and gates

**Date of record:** 2026-09-19.

## The two sentences that govern this document

**Outreach is not authorized.** No investor, firm, partner or person has been
contacted on SelekyT's behalf, and none is named anywhere in this document — not
as a target, not as a prospect, not as an interested party, not even as a
category example with a name attached. There are zero investor conversations of
any kind to report, zero warm-introduction requests made, and zero indication
that any investor is aware SelekyT exists.

This document is the **process** for building a pipeline, to be executed only
after the founder explicitly authorizes outreach. It exists so that when that
authorization comes, the work is thesis-first and evidence-gated rather than
name-first and relationship-implying. It also exists so that any future claim of
"investor interest" can be checked against what was actually authorized and what
was actually said.

**Evidence convention:** **VERIFIED** — repo path or a command I ran today.
**RESEARCHED** — live URL, retrieved 2026-09-19. **ASSUMPTION** — my inference,
discardable.

---

## Stage 0 (current state) — Build the evidence first; no investor work at all

The pipeline starts here and cannot leave here until the gates pass. Rationale,
in the words of the sources an early-stage investor would actually cite:

- "Rapid growth is what makes a company a startup… If your numbers grow
  significantly between two investor meetings, investors will be hot to close."
  (RESEARCHED — Paul Graham, *Fundraising*, <https://www.paulgraham.com/fr.html>,
  retrieved 2026-09-19.) SelekyT's numbers cannot grow between meetings, because
  there are no numbers and no users. Fundraising effort spent now is spent
  against a metric that does not exist.
- "You can't wait for users to come to you. You have to go out and get them."
  (RESEARCHED — Paul Graham, *Do Things That Don't Scale*,
  <https://www.paulgraham.com/ds.html>, retrieved 2026-09-19.) Graham's argument
  is that founders prioritise investors over manual user recruitment; the
  Collison-installation and Airbnb door-to-door examples in that essay are both
  *user* work, and both preceded the rounds that made those companies fundable.

**Gates to leave Stage 0.** Each is measurable, and each is drawn from
`docs/DATA_ROOM.md` §"What must be true":

| Gate | Criterion | Cost |
|---|---|---|
| 0.1 — NLE import | One SelekyT timeline opens in a real NLE with all clips online and duration matching the render within 2 frames. Recorded in `docs/NLE_COMPATIBILITY.md` with the NLE name and exact version | **$0 capital** |
| 0.2 — Editorial benchmark | Blind head-to-head against the incumbent on 10+ sources, rated by 3–5 blinded working editors, with the "misrepresents its source" rate measured | ~$29 plus a few hundred dollars of editor time |
| 0.3 — Hosted, instrumented | The product is reachable by someone who is not the founder and is emitting the events defined in `docs/TRACTION_DASHBOARD.md` | Small |
| 0.4 — Security Criticals closed | The three Critical findings in `docs/TECHNICAL_AUDIT.md` remediated | Engineering time |

**Until 0.1 and 0.2 pass, there is no thesis to take to an investor that a
diligent one cannot falsify in an afternoon.** `docs/RED_TEAM.md` is the
document that says this most bluntly: today this is "a well-built feature wearing
a company's clothes," and a feature becomes a company when a stranger pays for
it and tells someone.

---

## Stage 1 — Build the target list (no contact, no names stored as relationships)

Only after Stage 0 gates pass, and only after the founder authorizes it in
writing.

**What is being built:** a list of *institutions and programmes fitting a written
thesis*, with the fit reasoning recorded per row. Not a list of people, and never
a list of names carrying an implied relationship.

**Selection criteria — right fit for a pre-seed in this category:**

1. **They write pre-seed cheques to technical solo founders before revenue.**
   The fit test is not what they say, it is their own published investment record
   at this stage. If their public record begins at seed with ARR requirements,
   they are the wrong fit regardless of enthusiasm in a meeting.
2. **They are thesis-aligned with machine-learning *systems*, not AI demos.**
   A firm whose published thinking engages with inference economics, evaluation
   methodology and model defensibility will diligence the right things. One that
   invests on demo delight will be the first to lose conviction when a comparable
   feature ships inside an incumbent.
3. **They are comfortable with a 12–18 month evidence programme, not a 6-month
   growth plan.** `docs/USE_OF_FUNDS.md` buys 18 months of runway to convert a
   repo into measured quality and design partners. An investor whose model
   expects a Series A on revenue in two quarters is buying a mismatch.
4. **They do not require control or a board seat for $1M.** The round is sized in
   `docs/INVESTOR_READINESS.md` as a SAFE or small priced round at approximately
   $1M.
5. **No conflict with the incumbent set.** A firm holding a position in a
   clip-generator or NLE company cannot diligence the differentiation claim
   cleanly, and should be excluded rather than briefed.

**Selection criteria — wrong fit, stated so it is usable as a filter:**

1. **Growth-stage or revenue-gated investors.** Zero users and zero revenue is
   not a "stretch" for them; it is an automatic no, and a no spent here is
   attention not spent on a fit.
2. **Consumer-AI thesis investors expecting a creator free tier.** `docs/CUSTOMER_DISCOVERY.md`
   is explicit that the individual-creator segment is a trap for this product:
   the wedge is worthless to a creator who has no NLE, and the category clears at
   $24–29/mo against funded incumbents with free tiers (RESEARCHED —
   <https://www.opus.pro/pricing> and <https://www.descript.com/pricing>, both
   retrieved 2026-09-19). An investor whose thesis is creator-consumer will push
   the product into the segment where it dies.
3. **Investors who need a market-size slide to write a cheque.** No verifiable
   TAM exists and none will be invented (see `docs/DATA_ROOM.md` §5). The right
   investor for this round accepts pricing anchors and a market-creation
   argument; the wrong one needs a number that would have to be fabricated.
4. **Accelerator programmes requiring relocation or a batch cycle that delays
   the evidence programme.** Some are excellent fits on every other axis; the
   cost is months of calendar time, which the Stage 0 gates do not have to spare.

---

## Stage 2 — Qualification and approach preparation (still no contact)

For each institution on the authorized list, the file must contain a written
answer to the six questions a16z poses to a thin application-layer AI company
(RESEARCHED — *Who Owns the Generative AI Platform?*,
<https://a16z.com/who-owns-the-generative-ai-platform/>, retrieved 2026-09-19).
Those questions, and SelekyT's honest position on each today:

| The question a diligence investor will ask | SelekyT's honest answer today |
|---|---|
| Is the product the model, or an application layer on shared models? | Application layer. "Applications lack strong product differentiation because they use similar models" — the essay's own diagnosis of the layer SelekyT occupies |
| Is this a standalone business, or a feature an incumbent absorbs? | **Open.** Descript already ships timeline export including Premiere and Final Cut Pro from $24/mo; OpusClip's pricing page now advertises "Export to Adobe Premiere Pro & DaVinci Resolve" on Pro and Business (RESEARCHED — both retrieved 2026-09-19). The intersection SelekyT claims is closing from both sides |
| Is retention/defensible, or inherent churn? | **Unmeasured.** Zero users, and the only candidate moat — the per-channel editorial profile — does not exist (`docs/MOAT.md`) |
| What happens when model performance converges? | The deterministic, auditable, non-fabricating selection layer is the answer the venture intends to give. It is currently an answer, not a measurement |
| Is there a data flywheel? | **No.** Zero lines of ingestion code; the local-first architecture forbids observing runs unless the loop is designed to be safe (`docs/MOAT.md` §6) |
| What are the margins? | Measured compute is $0.00098–$0.00471 per source-minute (VERIFIED — `docs/BUSINESS_MODEL.md`), so COGS is not the constraint. The a16z observation that app-layer margins sit at 50–60% rather than 90% because of inference cost does not bite here: even with transcription fully outsourced to an API, compute is under half a cent per source-minute — **$0.12 to run the whole 24.5-minute reference tape end to end**, and ~$1.20 to deliver a 300-minute month. The constraint on this business is demand, not inference cost |

**Why this table is the qualification step, not the pitch:** an investor who asks
these six questions and gets an honest answer is a fit. An investor who does not
ask them is diligence risk, not a fast path to a term sheet.

**Also required in the file before any approach:** the live competitor re-check.
The OpusClip export finding above materially differs from the record in
`docs/CUSTOMER_DISCOVERY.md` and `docs/RED_TEAM.md`, both written earlier on
2026-09-19. Any investor conversation that claims a unique intersection must be
re-verified the week of the meeting, and the file must carry the retrieval date.

---

## Stage 3 — Approach (requires explicit founder authorization, one institution at a time)

Rules for the approach itself, written in advance so they are not improvised:

1. **Warm introduction only, and never an implied one.** A referral must be
   offered by the person making it; SelekyT does not trade on a name that was
   never given.
2. **The first message states the zeros.** Zero users, zero revenue, zero pilots,
   one founder, one real end-to-end run, no NLE import performed. An investor who
   declines on those facts has saved both sides months. An investor who proceeds
   is diligence-safe.
3. **No named third party is ever used as social proof.** Not a customer
   (there are none), not an advisor (there are none of record), not a tool vendor
   whose software the stack runs on. The dependency list is in the repo; it is
   not an endorsement.
4. **No market size, valuation or term-sheet figure is volunteered.** Round size
   and structure (approximately $1M, SAFE or small priced round) are stated as
   the plan from `docs/INVESTOR_READINESS.md`. A valuation is not proposed, and
   none is asserted as a comparable — `docs/INVESTOR_FAQ.md` §10 handles this
   question the same way. Any figure an investor proposes is recorded, not
   adopted.
5. **Every contact is logged.** Date, institution, who initiated, what was sent,
   what was said. The log is the only way the phrase "investor interest" ever
   becomes checkable.

---

## Stage 4 — Diligence and the data room

Governed by `docs/DATA_ROOM.md`. Two rules specific to this stage:

1. **The disclosure set goes in as-is.** `docs/RED_TEAM.md`, `docs/TECHNICAL_AUDIT.md`,
   `docs/NLE_COMPATIBILITY.md` and `docs/MOAT.md` are included in full and
   unedited. An investor who reads them and declines has been treated fairly. An
   investor who never sees them has been misled, and the round is built on sand.
2. **Live re-verification before handover.** Test counts, audit output and
   competitor pricing are refreshed the day the room is opened. Today's numbers
   (VERIFIED, 2026-09-19): 834 tests passing, 10 skipped; NLE audit
   `ALL CHECKS PASS`, exit 0. Note that three documents in this set disagreed on
   the test count within the same hour (819, 824, 834) before the checkpoint was
   corrected — the room's own numbers must be re-run, never quoted forward.

---

## How early-stage AI deals are actually evaluated: the RESEARCHED basis

Grounding for the gates above, all fetched 2026-09-19:

| Source | What it establishes | URL |
|---|---|---|
| a16z, *Who Owns the Generative AI Platform?* | No systemic moats in generative AI; apps lack differentiation because they use similar models; the six diligence questions for an application-layer company | <https://a16z.com/who-owns-the-generative-ai-platform/> |
| Paul Graham, *Fundraising* | Growth is the defining metric and the real test is revenue; investor interest follows numbers that move between meetings | <https://www.paulgraham.com/fr.html> |
| Paul Graham, *Do Things That Don't Scale* | Founders must manually recruit the first users; direct early-user feedback is the best feedback they will ever get; startups take off because founders make them take off | <https://www.paulgraham.com/ds.html> |
| Y Combinator, Apply | YC funds on acceptance, not batch start; no published rubric, so the operative signal is a promising application plus an interview — not a relationship | <https://www.ycombinator.com/apply> |
| OpusClip pricing | Pro $29/mo; "Export to Adobe Premiere Pro & DaVinci Resolve" now advertised on Pro and Business — a change from the record in this repo's own docs | <https://www.opus.pro/pricing> |
| Descript pricing | Creator $24/mo annual with timeline export to Premiere, Final Cut Pro, Pro Tools/Logic, Audition, Reaper/Samplitude | <https://www.descript.com/pricing> |

The composite reading: **early-stage AI evaluation in this category is decided by
the defensibility questions and by moving numbers, not by narrative quality.**
Both of those are downstream of the Stage 0 gates. This is why the pipeline is
sequenced the way it is, and why no amount of outreach preparation substitutes for
one imported timeline and one blind editorial benchmark.

---

## What would make this document unnecessary

If the founder authorizes outreach and a first conversation goes well, this page
is superseded by a live pipeline log. That is the intended outcome.

What would make it *obsolete on principle* is the alternative path the evidence
actually supports: the product reaches paying design partners on the strength of
the Stage 0 evidence alone, and the round is raised on traction rather than on a
thesis. `docs/CUSTOMER_DISCOVERY.md` §5 costs that path at effectively zero
capital and 10–20 hours of work, and its pass criterion is stated in advance —
three of five editors report ≥50% rough-cut time saved and would pay ≥$30/month.
**That criterion, not an investor's calendar, is the next gate this company
should be working toward.**
