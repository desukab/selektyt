# SelekyT — Fundraising narrative

**Date: 2026-09-19.** This is the narrative an investor would hear. Every claim
about the product is backed by a file, a test, or an observed run; every claim
about the market is backed by a URL; every claim about traction is absent,
because there is none. The section on early users says so in the first line.

---

## 1. The problem

Long-form video is cheap to make and expensive to cut.

A 45-minute podcast, an hour-long interview, a 20-minute news segment — the
source is nearly free to record and nearly worthless until someone edits it.
The work that converts it — watching the whole thing, finding the three minutes
worth keeping, cutting on the right word, keeping the speaker intelligible,
reformatting for vertical, captioning, and handing the result to a human editor
who refines it — is skilled, sequential, and cannot be parallelized by the
person who recorded it.

The cost is not the software. A professional NLE is affordable. The cost is the
**first pass**: the hours of skilled attention spent deciding what is worth
keeping before any creative editing begins.

This is a labour problem wearing a software costume.

## 2. The existing workflow

Today, in a real post house or content team:

1. An editor is handed a source and watches it end to end, taking notes.
2. They mark candidate moments, often in a spreadsheet or on paper.
3. They build a rough assembly in their NLE — Premiere, Resolve, FCP, Avid.
4. Someone asks where the vertical cut is. A second pass begins.
5. Captions are added, usually by a different tool.
6. The reasoning behind every cut — why this moment, why this length, why this
   was rejected — lives in nobody's system. It is in the editor's head, and it
   leaves when they do.

The tools are good. The workflow's problem is that **the editorial decision is
not an artefact**. It cannot be inspected, audited, handed over, or reused.

## 3. Why AI clip generators fail at this

The current AI clip category — OpusClip, Submagic, Mirage/Captions, and the
consumer-facing features of the platforms — was built for a different buyer: a
creator who wants a finished vertical video and does not employ an editor.

That model has three structural failures for anyone who does employ an editor:

1. **The output is a video, not a decision.** You receive an MP4. You cannot see
   why a moment was chosen, why another was dropped, or what the machine
   thought it was doing. There is nothing to accept or reject.
2. **The editor is displaced, not assisted.** There is no editable timeline. If
   the cut is 80% right, the human must redo 100% of it in their own NLE,
   because there is no timeline to continue from.
3. **There is no accountability.** No provenance, no audit trail, no record of
   which transcription model ran or which candidate was rejected and why. For a
   newsroom or a brand, that is not a nitpick — it is a governance failure
   waiting to happen.
4. **The platforms have started punishing the output.** This is new in 2026 and
   it is the strongest external argument in this document. YouTube clarified its
   Partner Program policies on **July 16, 2026**, and the first category it
   daemonetizes is *"generic, repetitive, or template-based content — videos
   easily made with AI, CGI, or templates with little variation"*, which
   YouTube's trust-and-safety chief framed as fighting "content farming"
   (https://techcrunch.com/2026/07/20/youtube-clarifies-policies-around-ai-slop-and-upsetting-videos/).
   Snapchat stopped recommending fully AI-generated Spotlight content on
   **July 31, 2026**, while explicitly permitting AI-*assisted* editing
   (https://techcrunch.com/2026/07/31/snapchat-no-longer-rewards-fully-ai-generated-spotlight-content/).
   A clip generator that ships volume with no named human judgement is now
   shipping its customers a monetization risk. An engine that ships a
   *reasoned, editor-reviewable* first pass is not.

The category optimized for "did it produce a vertical video". It did not
optimize for "would a professional editor trust it".

## 4. SelekyT

SelekyT is an AI-native editorial engine that takes one long-form video and
produces, from a single run:

- publishable **16:9 and 9:16 cuts**,
- **word-timed captions** (ASS, SRT, JSON, four presets),
- **ranked story selections and a full editorial report** — every candidate
  scored, every rejection reasoned,
- **FCPXML 1.11 and CMX 3600 EDL timelines** built from the exact intervals
  that were rendered, so a human editor can open them and continue,
- machine-readable **cutlists and provenance**.

The design principle is one sentence: **let the machine do the first-pass
editorial work without taking the editor out of the loop.**

## 5. Differentiation

The distinction is not a feature list. It is what gets shipped alongside the
media.

| | AI clip generators | SelekyT |
| --- | --- | --- |
| Primary artefact | A finished MP4 | An **editorial decision record** plus the media |
| Editor's relationship | Replaced | **Handed an editable timeline in their own NLE** |
| Why each cut was made | Opaque | `report.json`: per-candidate scores and rejection reasons |
| Hallucination surface | Generative text/summary | **None** — the engine selects and reorders source speech; it writes no new claims |
| Verification | Trust the output | `tools/editorial_audit.py` — a repeatable audit that reads only artifacts a normal run already writes |
| Provenance | None | Backend, VAD, transcription model and LLM-pass state recorded per run; never reports a backend it did not use |

That last row is a governance property, and it is the thing a black-box clip
generator cannot retrofit. It is also the reason the product's first buyer is
an organization that has something to lose.

## 6. Technical foundation

Built and verified, not pitched:

- **1034 tests passing, 12 skipped** (re-run on this host, 2026-09-20, 110 s).
  A point-in-time figure, and a floor rather than a current count: the suite was
  **1287 passed / 13 skipped** at the last refresh of this section, and the
  number moves whenever a regression test lands. Run
  `.venv/bin/python -m pytest -q` before quoting one.
  The 12 skips are all `SELEKYT_PROXY_MEDIA`-gated optional backends absent from
  this host (sherpa-onnx, silero, ollama) — environment skips, not failures.
- **A three-layer architecture**, each layer independently usable: the CLI;
  a Rust mechanical core (`selekyt-mech`, JSON-RPC over stdio) for cut algebra,
  energy VAD and FCPXML/ffconcat export; and a Python intelligence layer for
  pluggable VAD backends, provenance and the audio stage.
- **Cross-language agreement is not a claim, it is a test.**
  `tests/test_sidecar_parity.py` spawns the compiled `selekyt-mech` binary and
  asserts that Python and Rust agree on segmentation, CutList inversion,
  SMPTE timecode, and **byte-identical multi-track FCPXML**.
- **CI on every push**: pytest across Python 3.11–3.13, plus `cargo test` and
  `cargo clippy -- -D warnings` (zero warnings mandated).
- **Local-first by design.** The whole pipeline runs on one machine with
  python3, ffmpeg, faster-whisper and yt-dlp. No API keys are required, no
  third-party model calls are mandatory, and the optional LLM pass runs on a
  local Ollama instance — which on this host is not even installed, so the
  engine demonstrably degrades to deterministic scoring and says so.

~23,000 lines of Python and ~3,900 lines of Rust, 193 commits, built largely by
an AI multi-agent loop operated by one person. That is stated plainly because an
investor should evaluate it as both a capability and a risk.

## 7. Editorial intelligence

The thing that is not commodity.

Transcription is commodity — Whisper's code *and* weights are MIT
(https://github.com/openai/whisper), faster-whisper is MIT
(https://github.com/SYSTRAN/faster-whisper). Encoding is commodity (ffmpeg).
What sits between them is SelekyT-authored:

- **Story units, not time windows** (`engine/story_units.py`). Candidates are
  complete thoughts, so a cut never severs a sentence to hit a duration target.
- **Multi-signal deterministic scoring** (`engine/editorial.py`,
  `engine/virality.py`): hook strength, curiosity, contrast, information
  density, surprise, emotional pull, payoff, context, visual potential — each
  candidate scored with its rejection reason written down.
- **Context / truth guard** (`engine/context_guard.py`): an attention-grabbing
  edit cannot outrun what the source actually says; absolute claims require
  stronger evidence. This is the anti-hallucination layer.
- **Pause-aware retention** (`engine/pause_classifier.py`,
  `engine/audio_fusion.py`): dead-air removal conserves delivery rather than
  applying a fixed cut rate.
- **The retention philosophy is encoded, not invented**
  ([EDITORIAL.md](EDITORIAL.md)): hook fast, promise a payoff, protect complete
  thoughts, cut when the *information* changes rather than every N seconds,
  and always pay off the opening. The platform guidance this derives from —
  YouTube's retention reporting and Meta's 9:16 safe-zone guidance — is cited
  in that document rather than asserted.

**What this actually produced, measured on a real source.** NASA 360 Episode 5
(24:29, two hosts on camera, genuine unscripted human speech, US Government
public domain), run end to end through the web shell: 25 artifacts, QA
`ok: true`, 0 errors. `tools/editorial_audit.py` reported **ALL CHECKS PASS** —
cuts land between words, no sentence is split, no shaved tail word, cue text
matches transcript text, cues fit the render, silence share 3.6–40.8%. Pacing:
average 19.91s per clip, 3.74 words/sec, hook latency 0.63s, payoff present in
3/3. The hooks are real sentences a viewer can follow without prior context:
*"Welcome to the world's largest clean room."* Transcript quality on real human
speech: 425 segments and a chunked decode with no boundary artefacts. Transcript
accuracy on this source is *unmeasured* — no human has compared it to the audio,
and `docs/EVALUATION.md` §3 calls the "no manual correction" impression, not
measurement. What is verified is that the pipeline ran green end to end.

**Every number in the paragraph above is self-measured.** SelekyT ran the source,
SelekyT's own audit tools graded the output, and SelekyT wrote the numbers down.
No second party has reproduced any of them. That is a real limitation on how much
weight the "ALL CHECKS PASS" line can carry: the checks are mechanical — cue text
matches transcript text, cuts land between words, durations agree — so they are
good evidence the machinery is self-consistent and poor evidence that the edit is
any good. The pacing figures (3.74 words/sec, 0.63 s hook latency) are computed,
not observed, and no viewer has ever watched one of these cuts.

**The honest limit, in the repo's own words:** three clips from one source
passing the audit is evidence the machinery is sound, **not** that the choices
would survive comparison with a human editor across sources and languages. That
measurement has never been taken. It is the first thing this round buys.

## 8. Early users

**None. Zero users. Zero revenue. Zero pilots. Zero customers.**

This is not understated traction. There is no telemetry in the codebase, no
billing, and no deployment — SelekyT today is a local CLI and a local web shell
(`node web/server.mjs` → `http://127.0.0.1:4242`), and nothing has ever been
hosted on the public internet. No person outside the founder has ever run the
pipeline. An investor will see no retention curve, no activation funnel, no
conversion data, because no system exists that could have recorded them.

What exists instead is **verified engineering evidence on real input** — one
real rights-permitted source through the full chain, one YouTube download
observed (640x360, 11.8 MB, unauthenticated android client) while most videos
returned the sign-in wall from this datacenter IP, and a repeatable editorial
audit that is designed to become the traction argument.

**The plan, in the order it must happen:** five named design partners, free, in
exchange for one real source each and 30 minutes of an editor's time. The
measured deliverable is not "users" but **revision time versus from-scratch,
rejection rate per editorial check, and editor sentiment** — and the adoption
milestone that matters: three of those partners' editors open a SelekyT-exported
timeline in their own NLE and change something in it.

## 9. Business model

**There is no business model yet, and no price has been set.** No billing code,
no metering, no tiers. What exists is a *position*:

Sell the **first pass** to organizations that already employ a human editor —
post houses, podcast networks, news and media teams, brand studios. Not the
finished cut to a creator. The buyer's pain is the hours of skilled attention
before creative editing begins; the buyer already owns the NLE and the editor;
and the incumbent NLE vendors' per-seat pricing is structurally unable to
capture "give me a first pass I can revise", because that product is a
substitute for opening their software, not a reason to.

Verified competitor pricing, fetched today from each company's own pricing page,
defines what the market already pays and hence the ceiling SelekyT must stay
under while its own value is unproven:

| Company | Tiers (monthly) | Source |
| --- | --- | --- |
| OpusClip | Free; Starter $15; Pro $29; Business custom | https://www.opus.pro/pricing |
| Submagic | Starter $19; Pro $39; Business $69 (no free tier); API add-ons $0.10–$0.23/min | https://submagic.co/pricing |
| Descript | Free; Hobbyist $24; Creator $35; Business $65 | https://www.descript.com/pricing |
| Mirage/Captions | Free; Max $24.99; Scale from $69.99 | https://www.captions.ai/pricing |

**The pricing commitment:** a price is set only when a design partner states one
and then pays it. Until then, any price in this document would be invented.
The honest expectation, given the buyer has a real editor on payroll, is a
per-seat professional price closer to the upper end of that table than the
lower — but that is a hypothesis to be tested, not a forecast. Unit economics
per rendered minute are likewise unmeasured; the nearest verified public
benchmark for what the market pays for AI video processing is Submagic's
published $0.10–$0.23 per minute.

## 10. Global market

The numbers are large. The caveat that follows them is the reason the strategy
does not depend on them.

| Market | Size | Source |
| --- | --- | --- |
| Creator economy, global | $255.66B (2025) → $323.48B (2026) → $820.83B (2030) | The Business Research Company, Sept 2026 |
| Video editing software, global | $2.52B (2025) → $2.68B (2026) → $3.41B (2030) | The Business Research Company, Sept 2026 |
| Short video platforms, global | $2.22B (2025) → $2.5B (2026) → $3.99B (2030) | The Business Research Company, Sept 2026 |
| US digital video ad spend | >$80B in 2026 | IAB, July 14, 2026 |
| YouTube | $100B+ paid to creators over four years; Shorts ~200 billion daily views; >1M channels used YouTube AI tools daily in Dec 2025 | YouTube CEO Neal Mohan, Jan 21, 2026 |
| India creator economy | "2–2.5 million" monetized creators influencing "$350–400 billion" in consumer spending, "projected to drive $1 trillion+" by 2030 | BCG, May 3, 2025 |

**The one number in this document that is a count, not an estimate.** Every row
above is a third-party market forecast. This one is a census: the U.S. Bureau of
Labor Statistics counts **39,400 employed film and video editors (2025)**, median
wage **$75,420**, projected to grow **+4% to 2035**
(https://www.bls.gov/ooh/media-and-communication/film-and-video-editors-and-camera-operators.htm,
last modified August 27, 2026). That is SelekyT's buyer: a small,
well-paid, slow-growing profession whose members are paid for judgement. It is a
precision target, not a mass market — which is why the go-to-market is five named
design partners rather than a free tier, and why a $1M round is the right
instrument for it. A global editor count was sought and not found, so none is
claimed.

**Two limits, stated for the record.** First, these definitions are not
interchangeable: the $3.41B video-editing-software figure is licence revenue
only, and combining it with the $820B creator-economy figure into one TAM would
be false. **No total addressable market is claimed.** Second, the competitive
layer is already occupied by well-funded incumbents: Higgsfield raised $400M at
a $5.4B valuation with **$700M annualized revenue** (August 17, 2026);
Runway raised $315M at $5.3B (February 10, 2026); PixVerse crossed $2B on a
$439M Series C (July 13, 2026); Mirage took $75M with $28.4M in trailing
in-app revenue (March 24, 2026); OpusClip raised $20M at a $215M post-money
valuation (March 11, 2025).

SelekyT does not compete in that layer. It competes in the layer above the NLE —
editorial decision plus timeline handoff to a professional editor — where no
dedicated, verified entrant exists, and where the incumbents have a structural
reason not to go: Adobe's and Blackmagic's revenue comes from selling the seat
that the editor edits *in*. Shipping "here is a first pass that means you open
our software less" is not in their interest. Adobe's September 8, 2026
Generative Media Tool and Resolve 21.1's MCP server confirm the direction they
*are* going: AI inside the NLE. That is a different product.

**India and Telugu: a testing advantage, not the market boundary.** The pipeline
preserves the detected source language and accepts `--language te`, `hi`, `ta`;
IAMAI/KANTAR reports 886M Indian internet users with Tamil, Telugu and Malayalam
the most popular Indic languages online. Multilingual robustness is forced early
instead of deferred, at low cost. But no notable 2025–2026 funding round in an
Indian AI video-editing company was verified, and the Telugu speaker data
available is the 2011 census (~96M speakers) — so no Telugu-specific TAM is
claimed. The pitch is global from day one.

## 11. Defensibility

Stated as it is, not as an investor would wish it.

**There is no moat today.** The components are commodity and MIT/LGPL/Apache
licensed; no model is fine-tuned; no channel data is being learned; there are
zero users and therefore no network effect; and the repository itself
demonstrates that this much code can be produced by AI agents, so it can be
copied.

What does exist, in descending order of durability:

1. **A tested system where competitors have decks.** Over a thousand tests,
   real-input verification, byte-identical cross-language export. A starting
   position — the exact count is a moving target, not a strength to quote.
2. **The audit + provenance contract.** Every decision scored, rejectable and
   reproducible; every run records which backends actually ran. A governance
   property that is genuinely hard to retrofit onto a black box. The seed of a
   moat.
3. **The editable-timeline handoff.** A workflow position the incumbents'
   business model discourages them from taking. Greylock's Seth Rosenberg names
   purpose-built product and two-sided networks as ways out of the ChatGPT "God
   Box": "every platform shift lets amateurs create, and the amateurs often win"
   (https://greylock.com/blog/breaking-out-of-the-god-box, July 28, 2026). Not
   yet realized — it requires editors actually opening the timelines.
4. **A regulatory tailwind the clip generators sail against.** The platforms have
   made human judgement a distribution asset. YouTube daemonetizes "generic,
   repetitive, or template-based content… easily made with AI" (July 16, 2026)
   and Snapchat excludes fully AI-generated content from Spotlight
   recommendations (July 31, 2026) — both cited in full in section 3. SelekyT's
   per-source editorial report, with its rejection reasons and its named
   decisions, is an artefact a channel can point at. A clip farm has nothing to
   point at. This is not a moat either — it is a *regulatory preference* that
   happens to favour the design point, and it is worth more than any technology
   claim in this section.

**The stated path.** a16z reports Notion's paid AI attach rate went from 20% to
**more than 50%** in a year, with AI features now roughly half its ARR, while
standalone single-purpose tools saw their traffic compress as platforms bundled
native models — Midjourney fell from top-10 to #46
(https://a16z.com/100-gen-ai-apps-6/, March 9, 2026). Sequoia's 2026 guidance is
to build moats "from the customer back"
(https://www.sequoiacap.com/article/ai-ascent-2026, May 8, 2026). Both point the
same way: when the technology is trivial, the workflow position, the measured
quality record, and the customer relationship are all that remain. So this round
buys design partners and an audit corpus, not a model.

**One IP item an investor's counsel will find anyway.** Eleven files are copied
or ported from **AutoCut** by Ali Cobanov — with attribution headers preserved —
and AutoCut carries **no OSI-approved licence**. Reuse rests on the author's
explicit personal permission (via Instagram, email and Discord), and those
messages are not committed to this repository; the record is
[autocut-provenance.md](autocut-provenance.md). SelekyT's own code is
proprietary and source-available under the repository `LICENSE`. Remediation is
specific and cheap: written confirmation obtained and committed, and a licence
scan run. This is a first-quarter task, not a dispute.

## 12. Use of funds — $1M mapped to measurable milestones

**Round: pre-seed, approximately $1M.** Sized to 18 months of lean runway (about
$55K/month all-in), and to one purpose: convert a verified repo into a product
with a measured quality record and its first paying design partners. Every line
below is tied to a milestone that can be checked, not an activity that can be
described.

| Spend | Amount | What it buys | The milestone it must produce |
| --- | --- | --- | --- |
| **Engineering** | $450K | One senior engineer (audit of the machine-written core, NLE import ownership, hosted product) plus a second engineer from month 9; the engineering hire is the single highest-leverage spend in the round. | **A timeline imported and opened in a real NLE (Resolve, then Premiere or FCP) by week 6**; the machine-written export emitters independently reviewed by a second engineer by month 3; a second engineer on board by month 9. |
| **AI / compute** | $90K | Transcription and render compute for the 20–50-source audit corpus, plus design-partner runs and model-quality experiments (faster-whisper `medium`/`large` comparisons, local LLM editorial passes). | **A published corpus of 20–50 audited sources** (varying length, language, production style) with per-check pass rates *and* the failing checks listed publicly, by month 6. |
| **Infrastructure** | $75K | Hosted single-tenant deployment, object storage for source media and renders, egress, hardened CI/CD, monitoring. | **SelekyT reachable at a URL by someone who is not the founder by month 2**, with real telemetry emitting activation/retention events from day one — the first moment a usage claim is possible at all. |
| **Product** | $60K | Video upload in the web shell (today a file must sit inside the project tree because `guardedPath` confines it, while the CLI has no such restriction — the two entry points disagree); NLE round-trip UX; review surface for accept/reject per editorial decision. | **Upload path shipped and verified end to end by month 2**; the entry-point disagreement resolved and tested. |
| **Pilots (design partners)** | $85K | Five named design partners: participant engagement, source-media logistics, on-site editor time, measurement instrumentation. | **5 design partners, each with one real source through the pipeline by month 6**; **3 of their editors open and modify a SelekyT-exported timeline in their own NLE by month 9**; revision-time-versus-from-scratch measured for all five. |
| **Distribution** | $60K | Publishing the audit corpus and its failures; professional-editor community presence; the credibility programme. | **3 published audit reports with named failures by month 8**, and 2 advisors with professional-post-production credibility engaged. |
| **Security / IP / legal** | $60K | Written confirmation of the AutoCut permission and its commitment; dependency licence scan; DPA and retention-policy templates for partners handling source footage; trademark filings. | **AutoCut permission in writing and committed by month 1**; licence scan complete and clean or remediated by month 3; DPAs executable before the first paid contract. |
| **Operations** | $120K | Founder and minimal operating runway, accounting, entity and compliance hygiene, and a deliberately small contingency. | **18 months of runway with no follow-on required**, and clean financials that survive diligence. |
| **Total** | **$1,000K** | | |

**What this budget deliberately does not buy:** a marketing engine, a sales
team, a fine-tuned model, a consumer free tier, or a desktop application. Each of
those is a *later-round* decision that this round's evidence is supposed to
inform. The Tauri desktop shell, for instance, compiles and passes clippy but has
never produced a binary or rendered a webview; it stays unbuilt until hosted
deployment proves what users actually need.

## 13. Milestones, 12–18 months

Sequenced so that each step is only reachable if the previous one succeeded.

**Months 0–3 — credibility, near-zero cost**
1. One SelekyT timeline imported into a real NLE. Resolve's free tier reads
   CMX 3600, so this is an engineering day, not a research programme. It
   converts "structurally valid" (verified: 6/6 timelines hand-validated, record
   spans monotonic, one real one-frame overlap bug found and fixed) into
   "compatible". One concrete check is done on the same day: FCP's CMX 3600
   importer accepts **only eight characters for a reel name**
   (https://en.wikipedia.org/wiki/Edit_decision_list), so the emitter's reel
   names are measured against that limit before any FCP claim is made.
2. Hosted single-user instance live with telemetry.
3. Video upload shipped; the CLI/web entry-point disagreement resolved.
4. AutoCut permission confirmed in writing and committed; licence scan run.
5. First design partner engaged.

**Months 3–9 — the evidence layer**
6. The 20–50-source audit corpus published, failures included.
7. All five design partners on real sources; revision-time measured.
8. Three editors open and modify an exported timeline in their own NLE — the
   adoption claim the architecture exists to make.
9. Real non-English sources (Telugu, Hindi) through the audit, replacing a claim
   with a number. (Today this is partly done, and the numbers changed the claim
   rather than just filling it in: real Telugu audio — OpenSLR SLR66, 50 clips —
   scores WER **151.08%** on `small` and **91.54%** on `medium`, with **0 of 50**
   and **1 of 50** clips inside a 25% bar, while Polish — one of the six languages
   this project's own criteria name — passes at **11.01%** on that same `small`
   model against 3.38% for English. The failure is localised to Indic, not to
   "everything outside English", and the wider-scope version of this item —
   Hindi and the remaining five C10 languages — is still open, with P9 needing
   3 of 6 measured to be claimed.)
10. Second engineer hired; 2–3 advisors engaged.

**Months 9–18 — the round-defining numbers**
11. First paid contracts, at prices the partners themselves stated.
12. Usage evidence a seed investor can underwrite: activation, retention,
    per-source economics — the artefact that a 16% seed-to-Series A graduation
    rate and a $2M–$4M Series A ARR bar demand
    (https://news.crunchbase.com/seed/data-bigger-deals-longer-seriesa-2026/,
    May 26, 2026).
13. The seed pitch itself, on evidence: $500K–$1M ARR or signed pipeline, with
    three reference customers willing to say on the record that a SelekyT first
    pass saved their editor measurable time.

## 14. "If someone invests $1M, what does SelekyT become 12–18 months later?"

**Not a $100M company, and anyone who says otherwise is selling something.**

What $1M actually produces:

**A product that is used, not merely built.** Hosted, with video upload, with
real telemetry, with a published 20–50-source audit corpus behind it, and with
its timelines proven to open in the editors' own NLEs. The single biggest
credibility gap in the company today — that no NLE has ever opened a SelekyT
timeline — is closed in the first six weeks and stays closed.

**A measured editorial-quality record, published with its failures.** This is
the durable asset and the one no competitor has. Today the evidence is three
clips from one source passing an automated audit. In 18 months it is dozens of
sources across languages and production styles, with per-check pass rates and a
public list of what the engine still gets wrong. An AI clip generator cannot
copy that by copying the code.

**Five design partners and three editors who have modified a SelekyT timeline.**
That is the adoption claim the whole architecture was built to make, and it is
the one thing that would make SelekyT different in a category full of
well-funded black boxes.

**A company that can raise a real seed on evidence.** With $500K–$1M in ARR or
signed pipeline, three reference customers, measured retention, and a second
engineer, SelekyT is positioned for the $3M–$5M inception-stage norm at the
$20M–$50M post-money band — raised because the numbers exist, not because the
narrative does. That is the difference between this round and the next one.

**And, stated as plainly as it can be: a company that knows whether its thesis
is right.** The one thing this round buys that cannot be faked is an answer to
whether a professional editor, handed a SelekyT first pass, finishes faster than
starting from scratch. That has never been measured. If the answer is no, the
round still bought a real answer rather than a story — and the company pivots
to the "proposed cut" review-surface model, where every decision is an object an
editor accepts or rejects, or it stops. The architecture already supports that
pivot, because every decision is already a scored, rejectable object.

**The honest summary.** $1M does not buy scale, a moat, or a valuation. It buys
the difference between a repository that works and a product with a measured
quality record, real design partners, and its first revenue — which is the only
sequence that ever reached a $100M company: **PRODUCT → FIRST USERS → FIRST
REVENUE → PMF → REPEATABLE GROWTH → SCALE → CATEGORY LEADERSHIP.** SelekyT is
at PRODUCT. This round exists to reach FIRST REVENUE. The ambition is not
smaller for being sequenced; it is simply sequenced.

---

## Sources

Primary and secondary sources used, with dates. Where a source could not be
fetched directly, that is noted in [INVESTOR_READINESS.md](INVESTOR_READINESS.md)
under "Citations: verification limits".

- Crunchbase News, "In Charts: Seed Deals Keep Getting Bigger As Odds Of Reaching Series A Fall Dramatically," May 26, 2026 — https://news.crunchbase.com/seed/data-bigger-deals-longer-seriesa-2026/
- Crunchbase News, "Seed Funding Is Bigger Than Ever — And Harder to Get," April 29, 2026 — https://news.crunchbase.com/venture/average-seed-funding-amounts-deals-grew-2025/
- TechCrunch, "It's not your imagination: AI seed startups are commanding higher valuations," March 31, 2026 — https://techcrunch.com/2026/03/31/its-not-your-imagination-ai-seed-startups-are-commanding-higher-valuations/
- Crunchbase News, "The Billion-Dollar Seed Isn't The Deal You Think It Is," July 16, 2026 — https://news.crunchbase.com/venture/billion-dollar-seed-ai-biotech-mcdonald-bison/
- Crunchbase News, "What 25,000 Startup Applications Reveal About The New Rules Of Seed-Stage Startups," September 18, 2026 — https://news.crunchbase.com/seed/startup-funding-rules-ai-gtm-golbin-lvlup/
- TechCrunch, "What's ahead for startups and VCs in 2026? Investors weigh in," December 26, 2025 — https://techcrunch.com/2025/12/26/whats-ahead-for-startups-and-vcs-in-2026-investors-weigh-in/
- Sequoia, "AI Ascent 2026," May 8, 2026 — https://www.sequoiacap.com/article/ai-ascent-2026
- a16z, "The Top 100 Gen AI Consumer Apps — 6th Edition," March 9, 2026 — https://a16z.com/100-gen-ai-apps-6/
- Greylock, "Breaking out of the God Box," July 28, 2026 — https://greylock.com/blog/breaking-out-of-the-god-box
- TechCrunch, "Higgsfield raises $400M Series B… to $5.4B," August 17, 2026 — https://techcrunch.com/2026/08/17/higgsfield-raises-400m-series-b-quadrupling-its-valuation-in-8-months-to-5-4b/
- TechCrunch, "AI video startup Runway raises $315M at $5.3B valuation," February 10, 2026 — https://techcrunch.com/2026/02/10/ai-video-startup-runway-raises-315m-at-5-3b-valuation-eyes-more-capable-world-models/
- TechCrunch, "Video generation startup PixVerse raises $439M, valuation soars past $2B," July 13, 2026 — https://techcrunch.com/2026/07/13/video-generation-startup-pixverse-raises-439m-valuation-soars-past-2b/
- TechCrunch, "Mirage raises $75M…," March 24, 2026 — https://techcrunch.com/2026/03/24/mirage-raises-75m-to-continue-building-models-for-its-ai-video-editing-app-captions/
- Tech Funding News, "OpusClip grabs $20M…," March 12, 2025 — https://techfundingnews.com/opusclip-grabs-20m-for-ai-powered-video-editing-tool-that-turns-long-content-into-short-viral-clips/
- Adobe Blog, "Generate and create directly in your timeline…," September 8, 2026 — https://blog.adobe.com/en/publish/2026/09/08/generate-create-directly-in-your-timeline-with-new-ai-powered-innovations-in-premiere-after-effects
- 01net (Business Wire syndication), "Blackmagic Design announces DaVinci Resolve 20," April 2025 — https://www.01net.it/blackmagic-design-announces-davinci-resolve-20/
- Newsshooter, "Blackmagic Design DaVinci Resolve 21.1," September 7, 2026 — https://www.newsshooter.com/2026/09/07/blackmagic-design-davinci-resolve-21-1/
- YouTube Blog, Neal Mohan, "What's coming to YouTube in 2026," January 21, 2026 — https://blog.youtube/inside-youtube/the-future-of-youtube-2026/
- The Business Research Company, creator economy / video editing software / short video platform market reports, September 2026 — https://www.thebusinessresearchcompany.com/
- IAB, "2026 Digital Video Ad Spend & Strategy Report," July 14, 2026 — https://www.iab.com/insights/video-ad-spend-report-2026/
- BCG, "From Content to Commerce: Mapping India's Creator Economy," May 3, 2025 — https://www.bcg.com/publications/2025/india-from-content-to-commerce-mapping-indias-creator-economy
- IT Voice, IAMAI & KANTAR ICUBE 2024, January 16, 2025 — https://www.itvoice.in/led-by-surge-in-indic-language-adoption-internet-users-in-india-set-to-cross-900-million
- Pricing pages, fetched 2026-09-19: https://www.opus.pro/pricing , https://submagic.co/pricing , https://www.descript.com/pricing , https://www.captions.ai/pricing
- U.S. Bureau of Labor Statistics, Occupational Outlook Handbook, "Film and Video Editors and Camera Operators," last modified August 27, 2026 — https://www.bls.gov/ooh/media-and-communication/film-and-video-editors-and-camera-operators.htm
- TechCrunch, "YouTube clarifies policies around AI slop and upsetting videos," July 20, 2026 — https://techcrunch.com/2026/07/20/youtube-clarifies-policies-around-ai-slop-and-upsetting-videos/
- TechCrunch, "Snapchat no longer rewards fully AI-generated Spotlight content," July 31, 2026 — https://techcrunch.com/2026/07/31/snapchat-no-longer-rewards-fully-ai-generated-spotlight-content/
- Wikipedia, "Edit decision list," last edited July 22, 2026 (EDL/CMX 3600 NLE support; FCP 8-character reel-name limit) — https://en.wikipedia.org/wiki/Edit_decision_list
- OpenAI Whisper README (MIT licence for code and weights) — https://github.com/openai/whisper
- SYSTRAN faster-whisper README (MIT licence) — https://github.com/SYSTRAN/faster-whisper
- Sacra company research, Descript and Veed.io profiles (third-party revenue estimates) — https://sacra.com/research/descript , https://sacra.com/research/veed
- Straits Research, "Video Editing Software Market," September 4, 2026 (conflicting third-party market estimate) — https://straitsresearch.com/report/video-editing-software-market
