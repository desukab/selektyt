# SelekyT — Use of Funds: $1M over 18 months

**Date of record:** 2026-09-19. Checked against repository state at `4cd542c` on `main`
(154 commits; the repo is being committed to by parallel workstreams, so hashes
and counts are accurate at the timestamp and should be re-read, not inherited).

## The one thing funded before any of this money is spent

**The NLE import verification costs zero dollars and gates everything else in this
budget.** It is line 0, not line 1, because it requires no capital and no
headcount — only the experiment `docs/NLE_COMPATIBILITY.md` already specifies:

> Install DaVinci Resolve (free tier) on a machine with a display. Import
> `out/web/pipeline/project/timeline/01.edl` and `01.fcpxml`. Record whether the
> project opens, whether the clips are online, whether the timeline duration
> equals the rendered `facebook/01.mp4`, whether the source in-points match the
> selection, and whether a relink dialog appears.

**Pass criterion, stated in advance:** the sequence opens with all clips online
and its duration matches the render within two frames. Anything less is a defect
to fix, not a result to report.

The rationale is structural, not cosmetic. `docs/MOAT.md` ranks the two
defensible assets — the per-channel editorial profile and the in-the-loop
position — and states that **both depend on the export being finished in a real
editor and read back**. `docs/CUSTOMER_DISCOVERY.md` calls gap G2 the single
largest open risk and conditions every editor-facing recommendation on closing
it first. `docs/RED_TEAM.md` names the same gap as kill mode 6 and as half of
"the single most likely way this company dies."

**This budget is therefore sequenced, not merely allocated:** the founder should
run the $0 experiment before raising, and no investor material should imply
interchange works until it carries a real NLE name and version in
`docs/NLE_COMPATIBILITY.md`. Today it does not, and no amount of funding changes
that without the experiment.

---

## Round shape

**Pre-seed, approximately $1M, SAFE or small priced round, 18 months of runway**
(~$55.6K/month all-in). This follows `docs/INVESTOR_READINESS.md` and
`docs/FUNDRAISING.md` §12: the round is sized to a sequence — PRODUCT → FIRST
USERS → FIRST REVENUE — not to ambition. The $100M+ ambition belongs to a later
round and is reached by clearing milestones, not by pricing it now.

---

## The budget

| # | Line | Amount | What it buys | Evidence it produces | What it de-risks | Gap closed |
|---|---|---|---|---|---|---|
| **0** | **NLE import verification** | **$0** | One afternoon with DaVinci Resolve free tier on any macOS/Windows machine with a display | A dated entry in `docs/NLE_COMPATIBILITY.md` naming the NLE, the exact version, and the result | The differentiator. Nothing else in this budget produces a return if this fails | **G2** |
| 1 | Founding engineer (18 mo) | **$265K** | The first full-time engineer: NLE interchange ownership, security remediation, hosted deployment, independent review of the machine-written core | Imported timelines in Resolve then Premiere/FCP; the three Critical security findings closed; the product reachable at a URL | The only gap that compounds — `docs/INVESTOR_READINESS.md` names a missing second engineer as the cause behind every BLOCKING item | G2, security, hosting |
| 2 | Second engineer (from month 10) | **$135K** | A second engineer once the evidence programme justifies the spend | Second-engineer review of the export emitters and scoring core | Single-point-of-knowledge risk on the machine-written core; diligence credibility | — |
| 3 | Editorial lead, part-time (18 mo) | **$95K** | The first professional editor on the product: the rating panels, the corpus review, the editorial judgement that is currently one founder's intuition on one video | A named, credentialed editor who has rated SelekyT's cuts against an incumbent's | `docs/RED_TEAM.md` kill mode 4 — "who is your editor?" currently answers with an algorithm | **G6** |
| 4 | Founder living costs (18 mo) | **$81K** | Full-time founder focus, no consulting income | The whole programme | Founder distraction; the round buys founder time, not founder salary | — |
| 5 | Editorial-quality measurement | **$55K** | The blind head-to-head in `docs/RED_TEAM.md`: 10+ sources per cohort, 3–5 blinded working editors, SelekyT vs incumbent, plus the "misrepresents its source" mark | A published win rate and a published misrepresentation rate, with n and method | Kill modes 3 (commodity floor) and 5 (clip-generator trap), and the $100M question gets its first real number | **G6** |
| 6 | NLE verification lab | **$9K** | One macOS and one Windows machine, Final Cut Pro and Premiere Pro licences, test media. Resolve stays on the free tier | Per-NLE import results with versions recorded | Kill mode 6. Avid is **not** supported and is not purchased — SelekyT emits neither AAF nor OMF | **G2** |
| 7 | Hosted deployment, telemetry, cloud (18 mo) | **$46K** | A URL a non-founder can reach; the activation/retention events defined in `docs/TRACTION_DASHBOARD.md`; object storage and egress | The first non-zero cells on the traction dashboard | Kill mode 1 (distribution zero) — there is currently no path to the first 100 users that does not begin with an install path | G3-adjacent, hosting |
| 8 | Design-partner programme | **$70K** | Five design partners: participant engagement, source-media logistics, timed revision-vs-from-scratch sessions | 5 partners, each with one real source through the pipeline; 3 of their editors opening and modifying a SelekyT timeline in their own NLE; revision-time ratio measured | The "editor in the loop" pillar has never been exercised once. This is the first exercise | **G2, G6** |
| 9 | Security hardening, IP and legal | **$78K** | Remediation of the TECHNICAL_AUDIT Criticals; AutoCut permission in writing; dependency licence scan; DPA and retention-policy templates; trademark filings | Written AutoCut confirmation; a clean or remediated licence scan; executable DPAs | `docs/TECHNICAL_AUDIT.md` — an enterprise reviewer fails this product on `guardedPath` in under a minute | IP, security |
| 10 | Distribution and credibility | **$46K** | Publishing the audit corpus with its failures listed publicly; professional-editor community presence; two advisors with post-production credibility | 3 published audit reports with named failures; 2 engaged advisors | Kill mode 1's second half. The disclosure set is the strongest marketing asset the company has — it should be the marketing | — |
| 11 | Operations, entity, accounting, contingency | **$120K** | Accounting, entity and compliance hygiene, and deliberately large contingency | 18 months of runway with no follow-on required; clean financials that survive diligence | Round-level execution risk; the audit-corpus and design-partner lines will move, and the contingency is what absorbs the movement | — |
| | **Total** | **$1,000K** | | | | |

---

## Grounding: what these numbers are actually built on

Every cost basis below is either a measurement from this repo or a published
public source. Where it is my inference, it says so.

### Headcount — the largest line, and the one that has to be defensible

- **Software developers, US: median $135,980/yr** (May 2025, BLS Occupational
  Outlook Handbook). RESEARCHED, retrieved 2026-09-19:
  <https://www.bls.gov/ooh/computer-and-information-technology/software-developers.htm>.
  Loaded-cost multiplier of **1.3** (benefits, employer taxes, equipment) is my
  **ASSUMPTION** — a standard range, but an assumption. At 1.3×, a senior engineer
  costs ~$177K/yr, so 18 months ≈ **$265K** (line 1) and 9 months ≈ **$135K**
  (line 2).
- **Film and video editors: median $75,420/yr** (editors alone, May 2025, BLS
  OOH). RESEARCHED, retrieved 2026-09-19:
  <https://www.bls.gov/ooh/media-and-communication/film-and-video-editors-and-camera-operators.htm>.
  The page's Quick Facts show **$36.10/hr** for the *combined* film-and-video-
  editors-and-camera-operators group — not editors alone — so the hourly figure
  is used here only as a scale anchor, not as the editorial lead's rate. The
  part-time editorial lead (line 3) is priced at ~$65/hr loaded — a
  **ASSUMPTION** premium over the median for a senior, contract-rate post-
  production editor — which buys ~2 days/week. Line 5's freelance rating panels
  are priced at $45/hr blended, also **ASSUMPTION**.
- **Founder costs (line 4):** $4,500/mo × 18. A living-cost floor, not a market
  salary. **ASSUMPTION**, deliberately below every other line in the budget.

### Compute — the honest surprise, and why the budget is not built around it

All rates below are measured or computed in `docs/BUSINESS_MODEL.md` (retrieved
2026-09-19), benchmarked on the real artifacts of the reference run on this host
— a **4-core, 15 GB RAM, CPU-only** machine:

| Quantity | Value | Basis |
|---|---|---|
| Transcription, faster-whisper `small` int8 | 0.165 RTF | measured on the real source |
| Render, 16:9 / 9:16 | **0.8221 / 0.3567 RTF** | measured 2026-09-28 on the real source with the shipped `slow`/crf18 encoder and a real ASS from the run burned in — `corpus/render/runs/pentagon-30s-ass.json`, the captioned figures, because a real deliverable always carries captions (the uncaptioned pair is 0.8284 / 0.3562, `pentagon-30s.json`). The single 0.1232 RTF this row quoted until then was measured against the pre-`56253b5` `veryfast`/crf20 encoder and on a lower-resolution source, and was stale by 7× — see the correction note in `docs/BUSINESS_MODEL.md` §1. Swept rather than single-window: the encoder ladder in `engine/render.py:_encoder_args` now publishes ranges for both orientations |
| Reference cloud instance | AWS `c7i.xlarge`, 4 vCPU, $0.1785/hr on-demand | CITED in `docs/BUSINESS_MODEL.md` §2 |
| Always-on cost | $180/mo ($130 compute + $50 control plane, the latter ASSUMED) | `docs/BUSINESS_MODEL.md` A9 |
| All-in, CPU self-hosted, delete source after run | **$0.001233 / source-min** | computed |
| All-in, transcription API + CPU render, 7-day staging | **$0.004962 / source-min** | computed |

Now the arithmetic on what this round's evidence programme actually consumes:

- **Editorial corpus (line 5):** 40 sources × 45 min = 1,800 source-minutes.
  $1.86 self-hosted; **$8.58** if transcription is fully outsourced to an API.
- **Design partners (line 8):** 5 partners × 8 episodes × 60 min = 2,400
  source-minutes. $2.48 self-hosted; **$11.43** API mode.
- **Always-on control plane (line 7):** $180/mo × 18 = **$3,240**.

**Total variable compute for the entire evidence programme is on the order of
$50–$200, not tens of thousands.** Line 7's $46K is therefore overwhelmingly the
always-on runner, the telemetry pipeline, burst capacity and unspent contingency —
not GPU time.

This is a deliberate finding, not an oversight. `docs/RED_TEAM.md` rates compute
economics the *ninth* of ten kill modes (LOW-MED likelihood, MED impact), and
`docs/BUSINESS_MODEL.md` §7's sensitivity analysis concludes the unit economics
are a demand-side problem, not a supply-side one. **A budget that spent $90K on
"AI/compute" would be mislabelling human judgement as cloud spend.** The money in
this plan buys editors' time and engineers' time, because that is what the
evidence gaps actually are.

One cost lever that is real and is handled by policy, not by budget: **source
media is ~883 MB per tape and retention is the expensive variable**
(`docs/BUSINESS_MODEL.md` §4: holding sources 30 days costs 3.4× the entire
compute run). Sources are deleted on job completion and only the ~10 MB
deliverable is retained. A storage line that ignored this would be the budget's
largest avoidable error.

---

## Defence against the Red Team's strongest objections

`docs/RED_TEAM.md` ranks ten kill modes. Each line of this budget answers at
least one; the four it does not fully answer are stated as such.

| Objection | Likelihood / impact | How this budget answers it | Where it does not |
|---|---|---|---|
| **Distribution zero** — no users, no installable product | HIGH (present) / **FATAL** | Line 7 makes the product reachable by a non-founder with telemetry from day one — the precondition for any usage claim. Line 10 publishes the audit corpus, which is the company's strongest distribution asset because it cannot be faked | Does not solve distribution. A hosted instance is a precondition, not a channel. The consumer segment remains closed by design |
| **Incumbent crushing** — Descript ships FCPXML+EDL; NLEs ship reframing natively | HIGH / **FATAL** | Lines 5 and 6 buy the measurement that separates "we are behind" from "we are ahead" — blinded, against the incumbent's own output. An answer arrives in months either way | Cannot prevent an incumbent roadmap item. The monitoring cadence (`docs/CUSTOMER_DISCOVERY.md`: monthly, not annually) is a discipline, not a budget line |
| **Commodity floor** — stack is Whisper+ffmpeg+requests | HIGH / HIGH | Line 3 and line 5. If blinded editors prefer the incumbent's clips, this budget produces that fact early enough to pivot to the audit/verifiability layer, where the differentiator is structural rather than taste-based | — |
| **No editor on staff** | HIGH (present) / HIGH | Line 3 hires the first one; line 5 pays working editors to judge | — |
| **Clip-generator trap** — the category buyers name is "clip generator" | HIGH / HIGH | Lines 5 and 8 measure whether the timeline is the thing editors keep, which is the only test of the "not a clip generator" claim | If editors take the MP4s and never open the timeline, the wedge has no value and no budget line changes that |
| **NLE indifference / zero imports** | MED-HIGH / HIGH | Line 0 ($0) then line 6 and line 8's three-editor target | — |
| **No data moat** | MED / FATAL *if the thesis depends* | **This budget deliberately does not fund the data loop.** `docs/MOAT.md` is explicit that the moat is downstream of the NLE round trip, and that a corpus at 10 users is a spreadsheet. Funding the loop before the round trip exists would build the second half of a flywheel whose first half is unbuilt | The loop stays unfunded by design. That is the honest sequencing, and it is stated rather than hidden |
| **$100M question** | MED / definitional | Line 5's misrepresentation rate is the first quantified evidence for the premium-priced enterprise pitch | $100M remains a market-creation claim, not a market-size claim. No market figure is asserted anywhere in this document set |
| **Compute economics** | LOW-MED / MED | Not a threat: measured all-in cost is under $0.005/source-min, and the retention policy handles the one real cost lever | — |
| **Market / geography risk** | MED / MED | Line 10's published corpus is mixed by genre and language, testing the multilingual claim instead of asserting it | The `#india` default tag in `engine/content_pack.py` is a code defect, not a budget item, and should be fixed regardless |

**The Red Team's own verdict** is that this company dies because the two things it
sells — editorial intelligence that *learns*, and an editor-in-the-loop workflow —
are mutually exclusive and neither is yet real. This budget attacks exactly those
two: lines 5, 6 and 8 make the workflow real (it has never been exercised once),
and lines 3 and 5 make the judgement measurable. It deliberately funds the
*measurement* of editorial quality rather than the *learning loop*, because
`docs/MOAT.md` shows the loop is downstream of the measurement, not upstream of
it.

---

## What this budget deliberately does not buy

Each of these is a later-round decision that this round's evidence is supposed to
inform. Stated plainly so that a reviewer can check it:

- **A marketing engine or a sales team.** There is nothing to sell to a market
  this size yet, and `docs/CUSTOMER_DISCOVERY.md` §5 is explicit that the first
  channel is direct founder work in the communities editors already occupy.
- **A fine-tuned editorial model.** `docs/MOAT.md` ranks a fine-tuned model as a
  moat in its *data*, not its existence; a model trained on 10k decisions is a
  commodity. Zero rows exist in that corpus today.
- **A consumer free tier.** The category clears at $24–29/mo (RESEARCHED,
  retrieved 2026-09-19: <https://www.opus.pro/pricing>,
  <https://www.descript.com/pricing>) against funded incumbents with free tiers.
  `docs/CUSTOMER_DISCOVERY.md` calls the creator segment a trap.
- **A desktop application.** The Tauri shell compiles and passes clippy but has
  never produced a binary or rendered a webview (gap G4). It stays unbuilt until
  the hosted deployment proves what users actually need.
- **A data warehouse, telemetry warehouse, or training infrastructure.** See
  "no data moat" above.
- **A YouTube-fetch workaround.** YouTube's blocking from this host is
  per-video and intermittent rather than a hard IP ban — one fetch succeeded
  this session and a second was blocked mid-transfer (VERIFIED — commit
  `22c8ab7`, which regraded the gap from "entirely unverified" to "partially
  verified"). That is an environment fact, not a code defect, and it is not
  solved by buying proxy infrastructure. Note that this finding supersedes the
  "bot-blocked datacenter IP" wording still carried in `docs/product-gap-map.md`
  — another reason every external claim is re-verified the week it is made.

---

## Reconciliation with `docs/FUNDRAISING.md` §12

`docs/FUNDRAISING.md` §12 allocates the same $1,000K into eight buckets
(Engineering $450K, AI/compute $90K, Infrastructure $75K, Product $60K, Pilots
$85K, Distribution $60K, Security/IP $60K, Operations $120K). The totals agree
and the intent agrees. The differences are in bucketing, and they are deliberate:

- **The $90K "AI/compute" line is re-split here.** As shown above, measured
  variable compute for the whole evidence programme is on the order of tens of
  dollars. That line is really buying human editorial judgement, and this plan
  names it as such (lines 3 and 5) rather than as cloud spend. A reviewer who
  asks "what does $90K of compute actually cost at your measured rates?" deserves
  the answer this document gives.
- **The engineering line is split** into the two hires and the founder's living
  costs, so the headcount arithmetic is checkable against the BLS medians.
- **Milestones are preserved.** This plan keeps every milestone
  `docs/FUNDRAISING.md` §12 commits to — imported timeline by week 6, published
  20–50-source corpus by month 6, five design partners by month 6, three editors
  modifying a timeline in their own NLE by month 9 — and attaches the evidence
  artifact and the de-risked objection to each.

**Total: $1,000,000.** Eighteen months. One imported timeline, one measured
editorial benchmark, five design partners, and the first non-zero cells on the
traction dashboard — or the honest evidence that those things are not achievable,
which is worth at least as much as the money.
