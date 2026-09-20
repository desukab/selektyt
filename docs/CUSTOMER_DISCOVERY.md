# Customer discovery — SelekyT

**Date: 2026-09-19. Author: agent 4 (customers).**

## 0. Scope, method, and honesty ledger

**What this document is:** an evidence-graded comparison of seven candidate first-customer
segments for SelekyT, ending in one recommended beachhead, one first-pilot profile, explicit
falsifiers, and the trap list.

**What this document is not:** it is not the result of customer interviews.

> **I have talked to zero customers.** SelekyT has zero users, zero revenue, no pilots, no
> partnerships, no investors, and no conversations of any kind with any person in any of the
> segments below. Nothing in this document is a quote, a deal, or traction. Every statement
> about a customer's feelings or behaviour is either sourced third-party research or an explicit
> assumption. If a claim below reads like it came from a human being, it did not.

**Evidence tiers used throughout:**

| Tag | Meaning |
| --- | --- |
| **VERIFIED** | Observed directly on this host or in this repo (product capability, test status, gap state). |
| **RESEARCHED** | Read live from a named third-party source on 2026-09-19. URL given. The source's date is noted where it is older than today. |
| **ASSUMPTION** | My reasoning, not sourced. No live source was obtainable in this environment. These are the weakest claims and are flagged inline. |

**Two environment limits that shaped the research, stated so the reader can discount it:**

1. The `WebSearch` tool returned **zero results for every query** in this environment, and
   `WebFetch` is blocked (HTTP 403) from a long list of domains including `goldmansachs.com`,
   `niemanlab.org`, `cnbc.com`, `reuters.com`, `nytimes.com`, `washingtonpost.com`,
   `grandviewresearch.com` and `mordorintelligence.com`. Bing's HTML search returns a page with
   **no organic external links** in this environment. So all market-size figures below come from
   the subset of sources that were reachable (BLS, Descript, Opus, Adobe, Blackmagic,
   Reuters Institute, SSRS/Edison, TechCrunch, Wikipedia). Where I could not reach a primary
   source, the number is **not included** rather than guessed.
2. Several market sizes I would normally cite (IAB/PwC podcast ad revenue, L&D spend per learner,
   agency retainer sizes, freelance-editor marketplace volumes) **could not be verified live**.
   They are marked **ASSUMPTION** and are deliberately **excluded from the load-bearing argument**.
   The recommendation rests on capability match, incentive alignment and competitive structure —
   all of which I *could* source — not on any market-size number.

---

## 1. The wedge, established before the segments

Segment choice is downstream of what actually differentiates the product. Three product pages,
read today, define a triangle, and SelekyT is the only product inside it.

**Vertex 1 — autonomous clipping tools deliver finished clips, not timelines.**
OpusClip advertises ClipAnything and ReframeAnything, ~25+ languages, one-click multi-platform
publishing, a free-forever tier (60 minutes/month), Starter $15/mo, Pro $29/mo, Business custom.
Its own product page contains **no mention** of exporting an editable timeline, or of Premiere,
Final Cut, DaVinci Resolve, FCPXML, XML or EDL — I checked specifically for those strings.
RESEARCHED: <https://www.opus.pro/> and <https://www.opus.pro/pricing>

**Vertex 2 — timeline tools make the human do the selecting.**
Descript ships exactly the export capability SelekyT leads with. Its help centre documents
timeline exports to **Premiere Pro (XML), Final Cut Pro (FCPXML), Reaper (EDL), Adobe Audition
(SESX) and Pro Tools/Logic (AAF)**, with DaVinci Resolve also in the XML interchange matrix.
The feature is gated to Creator, Business and Enterprise plans, and carries a long, candidly
documented incompatibility list (no animation, transitions, titles, video effects, scenes,
sequences, speed; no captions on Premiere export; no markers on Resolve). That documentation is
itself the competitive bar: Descript tells editors precisely what survives the round trip.
RESEARCHED: <https://help.descript.com/export-and-share/timeline-exports.md>,
<https://www.descript.com/pricing>

**Vertex 3 — the professional NLEs are adding per-shot AI, not editorial selection.**
Adobe Premiere's own product page advertises text-based editing ("transcribe your video on
import, then create a rough cut fast simply by copying and pasting text"), Media Intelligence
search, Generative Extend, Object Mask and Enhance Speech — **but does not advertise an
autonomous long-form-to-short-form clip generator**. DaVinci Resolve advertises a DaVinci AI
Neural Engine for smart reframing, facial-recognition bin sorting, text-based editing (Studio
only), IntelliSearch, CineFocus, Voice Isolation and IntelliTrack — all **per-shot assistance**,
nothing that ranks story units across a 60-minute source and emits a first-pass cut.
RESEARCHED: <https://www.adobe.com/products/premiere.html>,
<https://www.blackmagicdesign.com/products/davinciresolve>

**The intersection, and who lives in it:**

|  | Autonomous editorial selection | Editable NLE timeline |
| --- | --- | --- |
| OpusClip | yes | **no** |
| Descript | **no** (human edits from text) | yes |
| Premiere / Resolve | no (per-shot AI only) | n/a |
| **SelekyT** | **yes** | **yes** |

> **This table is the core strategic finding of the document.** SelekyT is the only product in
> the intersection — and the people who want *both* halves are a much smaller, much more specific
> group than "creators."

**The honest caveat that follows immediately.** The half of the wedge that is unique — the
timeline — is also the half with **zero real-world evidence** behind it:
`docs/product-gap-map.md` records that no NLE on this host has ever opened a SelekyT EDL or
FCPXML, and `docs/AI_EXECUTION_STATE.md` lists that as outstanding work item G2. **VERIFIED.**
Selling "a timeline an editor can open," against Descript's documented format matrix, while never
having opened one yourself, is the central credibility risk of any editor-facing go-to-market.
Every recommendation below is conditioned on closing G2 first.

---

## 2. Segment comparison

Scoring legend: 1 = weak for SelekyT, 5 = strong. Scores are my judgement **(ASSUMPTION)** applied
to **RESEARCHED** inputs; the per-segment notes below carry the evidence.

| Dimension | Individual creators | Podcasters | Pro editors / small post studios | Agencies | Newsrooms | Media / publishers | Enterprise (L&D, corp comms) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Pain severity (long-form → short-form + timeline) | 4 | 4 | 5 | 4 | 3 | 3 | 3 |
| Pain frequency / weekly volume | 3 | 5 | 5 | 4 | 4 | 3 | 2 |
| Fits current NLE tooling (FCPXML/EDL matters) | 1 | 2 | 5 | 5 | 4 | 4 | 2 |
| WTP / budget owner reachable | 2 | 2 | 4 | 4 | 2 | 2 | 5 (but unreachable) |
| Low switching cost | 4 | 3 | 4 | 3 | 2 | 2 | 1 |
| Distribution access for a tiny co. | 1 | 2 | 4 | 3 | 2 | 2 | 1 |
| Retention / expansion | 3 | 4 | 5 | 4 | 3 | 3 | 4 |
| AI first-pass assists (not threatens) | 4 | 4 | 3 * | 3 | 2 | 2 | 4 |
| **Weighted fit** | **weak** | **medium** | **strong** | **medium** | **trap** | **trap** | **trap (timing)** |

\* Editors are the one segment where the assist/threat question is genuinely two-sided. See below.

### 2.1 Individual creators

**Content fit is excellent; the business case is poor, and the wedge is wasted on them.**

- Market: Goldman Sachs projects the creator economy could approach half a trillion dollars by
  2027, but the same body of coverage notes most creators derive no monetary gain and "as few as
  0.1% of creators are able to earn a living through their channels."
  RESEARCHED (via Wikipedia's citation of the GS report and of The Drum / Value Walk):
  <https://en.wikipedia.org/wiki/Creator_economy>. I could not load Goldman's own page (403), so
  this is a secondary citation — discount it accordingly.
- **The wedge does not apply.** Creators do not open Premiere or Resolve. OpusClip's free tier
  (60 min/month) and $15/mo Starter are the actual comparison. **RESEARCHED.** A creator comparing
  SelekyT to OpusClip is comparing on *clipping quality and price*, on which SelekyT has no
  measured advantage, while surrendering its only differentiator.
- Competitive intensity is extreme and current. TechCrunch (2026-07-15) describes "a growing wave
  of AI startups that are reshaping how content is created," naming Opus Clip and Captions, and
  the newest entrant monetises credit bundles — 5 videos for $15, $25/mo for 10 videos, $100/mo
  for 60 — aimed at "founders and small businesses that need regular social content but lack
  editing time." RESEARCHED:
  <https://techcrunch.com/2026/07/15/reelfuls-ai-turns-your-camera-roll-into-short-form-videos-for-social-media/>
- Distribution: requires paid acquisition, app-store presence, or virality. SelekyT today is a
  CLI plus a local web shell with **no video upload** (`docs/product-gap-map.md`, G3) and
  **no packaged desktop binary** (G4). **VERIFIED.** That is not a creator product.
- **Verdict: a trap.** The largest and most glamorous segment, zero defensibility for this
  product, and the 0.1% statistic means the overwhelming majority cannot pay at all.

### 2.2 Podcasters and podcast networks

**The best content fit of any segment. The worst first customer, for a specific competitive reason.**

- Video podcasting is real and large. Edison Research's Infinite Dial 2025: 70% of Americans 12+
  have ever listened to a podcast, **51% have watched one**, 55% are monthly consumers, and
  **YouTube is the service used most often to listen to podcasts (33% of weekly listeners)**.
  RESEARCHED: <https://ssrs.com/insights/the-infinite-dial-2025/>
- Bloomberg (2025, cited in Wikipedia): **about a billion people watch podcasts on YouTube every
  month.** RESEARCHED (secondary): <https://en.wikipedia.org/wiki/Podcast>
- Repeat-usage engine: a weekly show is a weekly pipeline run. This is the strongest
  frequency score in the table.
- **Why it is not the first customer:** Descript is already entrenched precisely here. It has the
  FCPXML/Premiere/Resolve/Pro Tools export SelekyT would lead with **and** one-click publishing to
  Buzzsprout, Castos, Podbean, Transistor, Captivate and Blubrrr — i.e. it owns both the edit and
  the distribution of the podcaster's workflow, from $16/mo.
  RESEARCHED: <https://help.descript.com/llms.txt> (integration index),
  <https://www.descript.com/pricing>
- Budgets: podcast ad revenue is small and concentrated at the top; I could **not** verify an
  IAB/PwC figure live (**ASSUMPTION**, deliberately unused). Independent podcasters are
  audience-obsessed and tool-cheap — an **ASSUMPTION**, but consistent with the 0.1%-earn-a-living
  finding above.
- **Verdict: the ideal future market and the ideal content type, deferred.** Reach podcasters
  *through* the editors who serve them, not by selling to them directly.

### 2.3 Freelance / professional video editors and small post-production studios — **recommended**

**The only segment that wants both halves of the intersection, with income that goes up when the
product works.**

- Segment size and economics: US employment is about **39,400 film and video editors** (2025),
  projected to grow **4% over 2025–35**, median pay **$75,420/yr** ($36.10/hr), roughly 5,600
  openings a year. RESEARCHED: <https://www.bls.gov/ooh/media-and-communication/film-and-video-editors-and-camera-operators.htm>
  Global headcount and freelance-marketplace volume could not be verified live (**ASSUMPTION**).
- **The wedge is their file format.** Premiere XML and FCPXML/EDL are not a nice-to-have here;
  they are how the work is handed off. Descript's decision to gate timeline exports to paid plans
  and to publish a per-format compatibility matrix is indirect evidence that this capability is
  what professional buyers evaluate. RESEARCHED (as above).
- **Incentive alignment — the crucial asymmetry.** An editor who bills per project converts
  saved hours directly into capacity and margin. SelekyT does not take their client; it gives them
  a first-pass timeline to refine, finish and invoice. At the BLS median of $36.10/hr, saving
  3–5 hours a week is worth roughly $110–190/week to one editor — an order of magnitude above the
  $15–29/mo creator price points. *The specific hours saved are ASSUMPTION; the hourly rate and
  employment figures are RESEARCHED.*
- **The threat side, stated honestly.** Editors are also the segment most likely to read "AI
  first-pass editor" as a replacement notice. BLS projects only 4% growth for the occupation,
  which is consistent with a profession already feeling automation pressure (**RESEARCHED**;
  the *feeling* itself is **ASSUMPTION** — I have no editor survey). Positioning is therefore
  load-bearing: ship the timeline, the editorial report and the provenance — the artifacts that
  say *the editor is still the author* — not the rendered clip as the deliverable. This is
  precisely the framing SelekyT already states in its README ("not another black-box AI clip
  generator"), which is a genuine positioning asset here.
- Distribution: bottom-up, no procurement. Editors buy their own tools, congregate in identifiable
  communities, and are tool-tolerant enough that a CLI and a local web shell are credible rather
  than a blocker. This is the only segment where SelekyT's current packaging is an acceptable
  starting point rather than a defect.
- Retention and expansion: a seat is used every working day across many client shows, so volume
  per seat is the highest of any candidate. Multi-seat expansion into a 2–10 person studio is a
  natural second step, and studios resell the output to creators, podcasters and brands — meaning
  the editor is also the channel into the segments that are otherwise distribution-blocked.
- **Verdict: the beachhead.** Weak only on the threat-perception axis, which is a positioning
  problem rather than a structural one.

### 2.4 Video / creative agencies

- Similar profile to editors but with an extra layer: agencies resell short-form packages as
  retainers, so the ROI case is clean and multi-seat deals are natural. Their margins on
  high-volume social-cut work are exactly what an AI first pass protects.
- Slower than editors (the buyer is an owner or head of post, not the practitioner) and they
  often standardise on one toolchain, raising switching cost.
- **ASSUMPTION** throughout — no agency pricing or margin data could be verified live.
- **Verdict: expansion path from the editor beachhead, not the first door.**

### 2.5 Newsrooms

- Demand-side reality is deteriorating, not growing. Reuters Institute Digital News Report 2026:
  **for the first time, platforms are more popular globally than both TV and news websites and
  apps as sources of news**; the US country report shows **trust in news at 25% (−5, the lowest
  since tracking began in 2015), only 16% paying for online news (−4), and 45% avoiding news
  sometimes or often (+3)**. RESEARCHED:
  <https://reutersinstitute.politics.ox.ac.uk/digital-news-report/2026> and
  <https://reutersinstitute.politics.ox.ac.uk/digital-news-report/2026/united-states>
- The report *does* flag online news video as a major growth area and news creators as gaining
  influence in politics and media — but that growth accrues to creators and platforms, not to
  newsroom tooling budgets. **RESEARCHED.**
- Structural blockers: slow procurement, editorial-ethics review of machine-made editorial
  judgement, and a breaking-news workflow where *speed to publish* matters more than *ranked
  selection* — the exact axis on which autonomous editorial ranking is a liability, not an asset.
  **ASSUMPTION** on procurement and ethics review (no live source), but consistent with the
  shrinking-revenue picture above.
- **Verdict: a trap.** Glamorous, reference-worthy, slow, cash-constrained, and a values fight
  about AI judgement that a pre-revenue company cannot win quickly.

### 2.6 Media companies and publishers

- Same declining-audience dynamics as newsrooms, plus platform dependence. The DNR's "news
  creators are gaining influence" finding is directly a statement that SelekyT's likely buyers
  here are losing ground to the creator segment. **RESEARCHED.**
- Video teams at publishers do use professional NLEs, so the wedge technically applies — but the
  budget and cycle-time problems are the same as newsrooms.
- **Verdict: a trap for the same reasons. Revisit only after the editor beachhead produces a
  referenceable case.**

### 2.7 Enterprise content teams (corp comms, L&D, internal media)

- Technically a decent fit: high volume of long-form talk content (webinars, town halls,
  training), a genuine need for both finished cuts and editable timelines, and real budgets.
- But this is the most distribution-blocked segment of all for a tiny company: security review,
  SOC 2 / SSO / DPA requirements, and 6–12 month cycles. Selling into enterprise while the
  product's headline feature has never been opened in an NLE (G2, **VERIFIED**) and while there is
  no packaged desktop app (G4, **VERIFIED**) is not a slow sale — it is an impossible one.
  The procurement reality is **ASSUMPTION** (no live source), but the product gaps are not.
- **Verdict: a trap of timing, not of fit.** Target it in year 2–3 once SOC 2, packaging and a
  reference exist. Do not spend on it now.

---

## 3. Recommendation

### Primary beachhead: professional video editors and small post-production studios (2–10 people)
### that sell short-form social cuts as a service

**Why, in order of strength of evidence:**

1. **They are the only buyers in the intersection.** OpusClip has selection but no timeline;
   Descript has the timeline but no autonomous selection; the NLEs have neither
   (**RESEARCHED**, section 1). An editor buying SelekyT is not choosing between overlapping
   products — they are buying the one thing nobody else ships.
2. **Their incentives make them adopters rather than resisters.** Saved hours convert to billable
   capacity at roughly $36/hr (BLS median, **RESEARCHED**). The product assists a profession BLS
   projects to grow only 4% (**RESEARCHED**), rather than replacing it — provided the deliverable
   is a *timeline they finish*, not a finished clip they hand over.
3. **They are the channel to everyone else.** Editors and small studios sit between SelekyT and
   the creator, podcaster, brand and publisher markets. Winning them imports the product into
   engagements SelekyT could never reach directly — which is how a company with no marketing
   budget and no app-store presence distribution-unlocks the attractive segments anyway.
4. **SelekyT's current packaging is a fit, not a handicap — only here.** A CLI plus local web
   shell, provenance records and an editorial audit script read as professional tooling to an
   operator, while reading as friction to a creator.
5. **The Telugu/India thesis survives this choice — as a testing thesis, not a quality thesis.**
   ~96 million Telugu speakers (83M native, 13M L2, 2011 census), official in Andhra Pradesh and
   Telangana. RESEARCHED: <https://en.wikipedia.org/wiki/Telugu_language>. Keep Telugu as the
   *proving ground* for multilingual robustness — language coverage is table stakes, since OpusClip
   advertises ~25+ languages (**RESEARCHED**), and being forced to confront a hard language early
   is worth more than a flag that lists it. But the differentiator claim that used to sit here is
   now measured and it does not hold: on real Telugu audio (OpenSLR SLR66, 50 clips) the pipeline
   scores **WER 151.08%** on `small` and **91.54%** on `medium`, with **0 of 50** and **1 of 50**
   clips inside a 25% bar — against 3.38% for English on that same `small` model. Telugu is a
   differentiator in the sense that SelekyT is measurably *worse* at it than incumbents are at
   their own languages, not better. The testing-advantage framing is the one that survives, and
   only because the failure is now quantified rather than assumed. Do not build a
   go-to-market on low-ARPU regional-language creators. India is a testing advantage, exactly as
   the brief states, not the boundary.

### First-pilot customer profile (concrete)

> **A senior freelance video editor, or a 2–5 person post/social-cut studio, that:**
>
> - runs **Adobe Premiere Pro or DaVinci Resolve** as its primary NLE (Resolve's free tier reads
>   CMX 3600 EDL — which is also the cheapest path to closing G2, per `docs/AI_EXECUTION_STATE.md`);
> - serves **3 or more recurring clients** with **weekly long-form talk content** — video
>   podcasts, interviews, panels, webinars, conference talks (i.e. exactly the unscripted
>   two-host speech the NASA 360 validation run exercised, **VERIFIED**);
> - currently delivers, per episode, a 16:9 cut plus roughly 5–10 vertical clips with captions,
>   spending on the order of **3–6 hours per episode** on selection and rough-cut;
> - is paid **per episode or on a monthly retainer**, so time saved lands directly in margin;
> - has at least one person willing to **open an FCPXML/EDL and judge it**, and is comfortable
>   either running a CLI or letting SelekyT run the pipeline for them during the pilot.
>
> **Volume at pilot scale:** 3–5 client episodes per week, 45–90 minutes each, producing roughly
> 20–30 short-form clips per week. That is enough weekly usage to expose the editorial-judgement
> risk (G6) fast, which is the point.
>
> **Not the pilot:** a creator editing their own channel; an enterprise L&D team; a newsroom; a
> podcaster who already edits inside Descript.

---

## 4. What would falsify this recommendation

Ordered by how quickly each would kill it:

1. **Editors want finished MP4s, not timelines.** If pilot editors take the rendered clips and
   never open the EDL/FCPXML, the wedge has no value to the one segment I chose it for. That
   would say: reposition as a clip generator against OpusClip (bad odds) or stop.
2. **The timeline does not actually import.** If the EDL/FCPXML fails in Resolve or Premiere —
   relinking errors, wrong frame-rate handling, the "invalid edit with no respective media" class
   of failure Descript documents — then SelekyT's differentiator is unverified at the exact point
   it is sold. **This is the single largest risk and it is currently wide open: G2 is VERIFIED
   unresolved.**
3. **The editorial judgement does not survive contact with real clients.** If pilot editors
   report that the AI's selections need so much rework that no time is saved, the product fails
   on value regardless of segment fit. `docs/AI_EXECUTION_STATE.md` already flags editorial
   judgement as "unmeasured at scale" — **VERIFIED** that this is an open risk.
4. **Threat perception beats utility.** If editors read "AI first-pass editor" as replacement and
   refuse to trial it, adoption is blocked regardless of capability. Mitigation is positioning
   (ship the timeline, report and provenance; never ship the clip as the deliverable), but that
   is a hypothesis, not a fact.
5. **The intersection closes.** Descript is one roadmap item away from adding autonomous
   selection to its existing export pipeline, and OpusClip is one item away from adding a
   timeline. If either ships, SelekyT's wedge compresses to "open-source, self-hosted,
   auditable." That residual position is real — but it is a different, smaller business, and it
   should be monitored monthly, not annually.

---

## 5. The cheapest experiment (ordered, mostly free)

**Step 0 — zero cost, and it gates everything else.**
Import the EDL and FCPXML already on disk from the NASA 360 run into DaVinci Resolve (free tier;
it reads CMX 3600). This is outstanding item G2 from `docs/AI_EXECUTION_STATE.md`. Until one
timeline opens cleanly in one real NLE, **no editor-facing claim in this document is testable.**
Hours, not dollars.

**Step 1 — days, under $50.**
A one-page offer aimed only at professional editors, placed in the communities they already
occupy: *"Send us one long-form episode. We'll return an EDL/FCPXML you can open in Premiere or
Resolve, plus rendered cuts and the editorial report — free, in exchange for 15 minutes and
honest criticism."* Measure four things:

| Metric | What it answers |
| --- | --- |
| Reply rate per community | Is there demand at all? |
| How many actually open and refine the timeline | Falsifier 1 — do they want the timeline? |
| Self-reported hours saved on rough-cut | Falsifier 3 — is there real value? |
| Would-you-pay at $30–60/mo | Willingness to pay, above OpusClip's $15–29 |

**Step 2 — 5 to 10 structured conversations.** Pass criterion, stated in advance: **at least 3 of
5 editors report ≥50% of rough-cut time saved and would pay ≥$30/month.** Below that, kill or
pivot before building anything.

**Step 3 — only after Steps 1–2 pass.** Add video upload to the web shell (G3) and package one
desktop build (G4), so editors stop needing the CLI. Sequencing matters: these are real gaps
(**VERIFIED**), but they are gaps for *scale*, not for *learning*.

**Total cost:** effectively zero capital and roughly 10–20 hours of work. The experiment is
designed so that its most likely failure mode — "editors don't want a timeline" — is discovered
before any product, packaging or compliance work is spent.

---

## 6. Trap list, stated plainly

- **Individual creators.** Big, glamorous, and the wedge is worthless to them (no NLE, no
  timeline). They compare on clipping quality and price against funded incumbents with free
  tiers, in the most crowded AI category of 2026. And 0.1% of creators earn a living — the
  majority cannot pay at all.
- **Newsrooms.** Slow, risk-averse, shrinking (US news trust 25%, lowest since 2015; only 16%
  pay for online news), and their highest-value workflow — breaking news — is the one where
  autonomous editorial ranking is a liability.
- **Enterprise content teams.** Real budgets and real fit, but they are unreachable by a
  pre-revenue company with no SOC 2, no packaged app, and a headline feature never opened in a
  real NLE. A trap of timing, not of fit. Year 2–3.
- **Media companies / publishers.** Same dynamics as newsrooms, plus they are losing audience to
  the creator segment — the wrong direction of travel for a tool-buying budget.
- **Podcasters — a *first-customer* trap, not a market trap.** The content fit is perfect and the
  volume engine is the strongest of any segment, but Descript already ships the FCPXML/Premiere
  export and one-click host publishing from $16/mo, so SelekyT would lead with its least
  differentiating feature against an entrenched incumbent. Serve this market **through** editors
  in phase 2.
- **Agencies.** Not a trap — a good expansion step once the editor beachhead has a reference.
  Slower and more toolchain-locked than individual editors; not the first door.

---

## 7. Sourcing appendix

**Live-read sources (all accessed 2026-09-19):**

| Source | Used for |
| --- | --- |
| <https://www.bls.gov/ooh/media-and-communication/film-and-video-editors-and-camera-operators.htm> | US editor employment 39,400; +4% 2025–35; median $75,420/yr ($36.10/hr); ~5,600 openings/yr |
| <https://help.descript.com/export-and-share/timeline-exports.md> | Descript exports to Premiere XML, FCPXML, EDL, SESX, AAF; Resolve in matrix; gating and incompatibility list |
| <https://www.descript.com/pricing> | Descript tiers: Hobbyist $16, Creator $24, Business $50, Enterprise custom (annual) |
| <https://help.descript.com/llms.txt> | Descript's integration index (Buzzsprout, Castos, Podbean, Transistor, Captivate, Blubrry, Wistia, HubSpot) |
| <https://www.opus.pro/> | OpusClip: ClipAnything, ReframeAnything, ~25+ languages, free 60 min/month, **no NLE/timeline export advertised** |
| <https://www.opus.pro/pricing> | OpusClip: Free, Starter $15, Pro $29, Business custom |
| <https://www.adobe.com/products/premiere.html> | Premiere: text-based editing, Media Intelligence, Generative Extend, Object Mask; **no autonomous short-form clip generation** |
| <https://www.blackmagicdesign.com/products/davinciresolve> | Resolve: DaVinci AI Neural Engine — smart reframe, facial-recognition bins, text-based editing (Studio), IntelliSearch, CineFocus; **no story-unit ranking** |
| <https://reutersinstitute.politics.ox.ac.uk/digital-news-report/2026> | DNR 2026: platforms now more popular than TV and news sites/apps; news video growth; news creators rising |
| <https://reutersinstitute.politics.ox.ac.uk/digital-news-report/2026/united-states> | US: trust 25% (−5, lowest since 2015); pay for online news 16% (−4); avoid news 45% (+3) |
| <https://ssrs.com/insights/the-infinite-dial-2025/> | Edison/SSRS Infinite Dial 2025: 70% ever listened, 51% watched, 55% monthly; YouTube top platform at 33% of weekly listeners |
| <https://en.wikipedia.org/wiki/Podcast> | Bloomberg 2025: ~1 billion people watch podcasts on YouTube monthly |
| <https://en.wikipedia.org/wiki/Creator_economy> | GS "half a trillion by 2027"; most creators earn nothing; ~0.1% earn a living |
| <https://en.wikipedia.org/wiki/Telugu_language> | ~96M Telugu speakers (83M L1 + 13M L2, 2011 census); official in Andhra Pradesh and Telangana |
| <https://techcrunch.com/2026/07/15/reelfuls-ai-turns-your-camera-roll-into-short-form-videos-for-social-media/> | 2026 competitive intensity: Reelful, a16z Speedrun, $25–100/mo, "wave of AI startups" with Opus Clip and Captions |

**Internal evidence (VERIFIED, from this repo):** product capability and gap status from
`docs/product-gap-map.md`; outstanding-work list including G2 (no NLE import) and G3 (no video
upload) from `docs/AI_EXECUTION_STATE.md`; product framing from `README.md`; the one green
end-to-end run (NASA 360 Episode 5, 24:29, rights-permitted) from `docs/release-validation.md`.

**Could not verify live — deliberately excluded from the argument:** IAB/PwC US podcast
advertising revenue; enterprise L&D spend per learner; agency retainer and margin data; freelance
editor marketplace volumes and hourly rates; any survey of editors' attitudes toward AI; newsroom
procurement cycle lengths. All are **ASSUMPTION** where mentioned and none is load-bearing.
