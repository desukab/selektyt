# SelekyT — Investor FAQ (honest answers)

**Date: 2026-09-19.** Every answer here is either (a) a fact about the repository
and its verified behaviour, evidenced by a file you can read, or (b) a plan,
labelled as a plan. Where the honest answer is "zero" or "none", that is what is
written. No user, customer, revenue figure, investor, or valuation exists to be
quoted, because none does.

Ground truth used throughout: 822 tests passing, 10 skipped (re-run on this host
today, 65s); one real rights-permitted 24:29 source (NASA 360 Episode 5, US
Government public domain) ran the full chain green; one YouTube download
observed (640x360, 11.8 MB, via an unauthenticated android client) while other
videos returned the sign-in wall from this datacenter IP; EDL/FCPXML
structurally valid but never opened inside a real NLE; no desktop binary; zero
users, revenue, pilots, investors, or employees. See
[AI_EXECUTION_STATE.md](AI_EXECUTION_STATE.md),
[release-validation.md](release-validation.md),
[product-gap-map.md](product-gap-map.md).

---

## 1. "How many users do you have?"

**Zero.** Not "we don't publish the number" — zero.

- There is no product telemetry in the codebase. I searched the repository for
  any analytics, telemetry, or event-emission integration
  (`posthog`, `mixpanel`, `segment`, `telemetry`) — the only hits are transcript
  *segment*-merging logic in `providers/whisper.py` and `providers/sherpa.py`.
- There is no billing, subscription, or payment code. No Stripe, no
  usage-metering, no licence-key check anywhere in the tree.
- There is no hosted deployment. The only infrastructure is a GitHub Actions CI
  workflow (`.github/workflows/ci.yml`) that runs pytest and `cargo test`.
  SelekyT today is a local-first CLI plus a local web shell
  (`node web/server.mjs` → `http://127.0.0.1:4242`). Nothing is on the public
  internet.
- Therefore there is no usage data to show, no retention curve, no conversion
  funnel, no churn number. An investor asking for these will get "not
  applicable yet", not a chart.

**The plan.** The first money goes to the evidence layer, not the growth layer:
ship a hosted single-user instance with real telemetry, run 20–50 design-partner
sources through it, and convert a subset to paying. The first credible
traction milestone is not "100 users" — it is **10 sources from 5 different
design partners, each with an editor who opened the exported timeline and
changed something**. That is the claim that no AI clip generator can currently
make and that SelekyT's architecture is specifically built to make.

## 2. "Who is on the team?"

**One person: Bharath Bheemreddy**, the copyright holder named in `LICENSE`.
The git history shows three committer identities, all traceable to the same
individual plus this build host; there are no employees, no co-founders, and no
advisors of record.

**The part an investor has to evaluate honestly:** the repository was largely
built with AI coding agents operating in a multi-agent loop (see
[agent-worktrees.md](agent-worktrees.md) — five preserved agent worktrees, each
classified and dispositioned). That is a genuine and unusual capability: 140
commits, ~23,000 lines of Python and ~3,900 lines of Rust, 822 passing tests,
byte-identical cross-language export, for the cost of one person and AI
operator time. It is also a genuine risk: there is no second engineer who
understands the system, and no human review of the parts the agents wrote. The
first hires must include a senior engineer who can audit the machine-written
core, and the design-partner programme doubles as that audit.

I am not claiming a team. I am claiming a working system and one person who can
operate an AI build loop, which is a different and weaker thing.

## 3. "Why won't Adobe just build this?"

**They already are building much of it.** This is the strongest bear case on
the company and I will not soften it.

- Adobe shipped a **Generative Media Tool inside the Premiere Pro timeline** on
  September 8, 2026 (IBC 2026), with Generate Video (using reference frames from
  the user's own project; model choice including Adobe Firefly plus Google Veo,
  Kling, Runway and Luma), Generate Sound Effects, Generate Soundscape and
  Generate Music betas, plus "Smarter Audio" (Enhance Audio, Separate Crosstalk,
  Dynamic Auto Ducking) and an After Effects AI Assistant.
  https://blog.adobe.com/en/publish/2026/09/08/generate-create-directly-in-your-timeline-with-new-ai-powered-innovations-in-premiere-after-effects
- Adobe MAX 2025 (October 28, 2025) shipped Firefly **Generate Soundtrack** and
  **Generate Speech**, and a **Firefly Video Editor** private beta described as
  a web multitrack timeline with transcript-based editing.
  https://news.adobe.com/news/2025/10/adobe-max-2025-firefly
- Blackmagic's DaVinci Resolve 20 (April 4, 2025) already shipped **AI
  IntelliScript** (build timelines from a text script), **AI IntelliCut**
  (silence removal, per-speaker split, ADR cue lists), **AI Multicam
  SmartSwitch** and AI Animated Subtitles; Resolve 21 (April 14, 2026) added AI
  IntelliSearch (object/spoken-word/face search); Resolve 21.1 (September 7,
  2026) added a native MCP server for AI assistants.
  https://www.01net.it/blackmagic-design-announces-davinci-resolve-20/
  https://itwire.com/reviews-sp-288/software/blackmagic-design-announces-davinci-resolve-21
  https://www.newsshooter.com/2026/09/07/blackmagic-design-davinci-resolve-21-1/
- Captions rebranded as **Mirage** in September 2025, explicitly pivoting "beyond
  creator tools to AI video research"
  (https://techcrunch.com/2025/09/04/captions-rebrands-as-mirage-expands-beyond-creator-tools-to-ai-video-research/),
  and took **$75M in growth financing from General Catalyst's Customer Value
  Fund on March 24, 2026**, having raised over $100M at a $500M valuation;
  Appfigures puts Captions at **$28.4M in trailing in-app revenue** on 3.2M
  downloads, with CEO Gaurav Misra noting only 25% of revenue comes from the U.S.
  (https://techcrunch.com/2026/03/24/mirage-raises-75m-to-continue-building-models-for-its-ai-video-editing-app-captions/).
  Its $24.99/month tier explicitly advertises "turn a long video into a batch of
  post-ready clips" (verified on its pricing page today) — the closest
  large-funded competitor to SelekyT's stated position, which is worth saying
  plainly rather than leaving an investor to find it.

**What Adobe will not do, and why that is the only defensible wedge.** Adobe's
business model is a per-seat licence for the NLE itself. Its AI features exist
to make the seat more valuable. It has no incentive to ship a product that
takes a long-form video and returns a *finished, publishable* cut with the
editorial reasoning attached — that product is a substitute for opening
Premiere, not a reason to. SelekyT's design point is the inverse: assume the
human editor is not in the tool, and hand them an **auditable decision record**
plus an **editable timeline** so they can continue in whatever NLE they already
own. That is a different workflow, sold to a different buyer, and Adobe's
pricing structure (per seat, per month, per app) is not built to capture it.

**The honest version of this answer:** the wedge is a workflow and
business-model position, not a technology position. It is defensible only for
as long as it takes Adobe to decide the category matters. That is why the
capital plan front-loads design partners in professional post houses rather
than consumer creators — a customer Adobe's per-seat model serves poorly is
worth more to SelekyT than a million free users.

## 4. "What is proprietary about Whisper + ffmpeg + an LLM?"

**Nothing, and I will not claim otherwise.** The components are commodity:

- OpenAI's Whisper is MIT-licensed — and the important detail: **the model
  weights are MIT too**, not just the code ("Whisper's code and model weights are
  released under the MIT License", https://github.com/openai/whisper).
  faster-whisper (Systran, CTranslate2-backed, "up to 4 times faster than
  openai/whisper for the same accuracy") is also MIT
  (https://github.com/SYSTRAN/faster-whisper). The entire speech-to-text layer is
  freely commercializable by anyone, including the incumbents.
- ffmpeg/ffprobe are LGPL/GPL. OpenCV is Apache-2.0. yt-dlp is Unlicense.
- The optional LLM pass is local **Ollama** (MIT-licensed), and on this host it
  is *not even installed* — the pipeline degrades to deterministic scoring by
  design (`fallback_reason` records what actually ran).

**What SelekyT actually owns.** The editorial decision layer sitting above those
commodities, which is SelekyT-authored code in `engine/`:

- `engine/story_units.py` — grouping transcript into complete thoughts rather
  than fixed time windows, so a cut never severs a sentence to hit a duration.
- `engine/editorial.py` + `engine/virality.py` — deterministic multi-signal
  scoring (hook, curiosity, contrast, information density, surprise, emotional
  pull, payoff, context, visual potential) with per-candidate rejection reasons
  written to `report.json`.
- `engine/context_guard.py` — the reason an attention-grabbing edit cannot
  outrun what the source actually says. This is the anti-hallucination layer,
  and it is also the thing a "summarize this video" prompt does not give you.
- `engine/pause_classifier.py` + `engine/audio_fusion.py` — pause
  classification fused against transcript words, so dead-air removal conserves
  delivery instead of applying a fixed cut rate.
- `tools/editorial_audit.py` — a repeatable audit that reads only artifacts a
  normal run already writes, and prints failing checks. On the one real source
  it ran **ALL CHECKS PASS**: cuts land between words, no sentence split, no
  shaved tail word, cue text matches transcript text, cues fit the render,
  silence share 3.6–14.7%, pacing avg 19.91s / 3.74 words/sec / hook latency
  0.63s, payoff present in 3/3 clips.
- The **provenance contract**: `qa.json`, `report.json` and per-run provenance
  say which transcription backend ran, which VAD fired, and whether the LLM
  pass happened at all. The pipeline never reports a backend it did not use.

**The IP diligence flag you will find anyway, so here it is first.** The
mechanical core is not entirely original. `docs/autocut-provenance.md` records
that 11 files are copied or ported from **AutoCut** by Ali Cobanov (Rust files
copied with attribution headers; `cuts.ts` and cut types ported to Rust;
`timecode.rs` and `export_fcpxml.rs` ported to Python). AutoCut does **not**
carry an OSI-approved licence; reuse is by the author's explicit permission
granted via Instagram, email and Discord, and those private messages are
**not** committed to the repository. Attribution lives in source headers and in
that document. An investor's counsel should treat that as an unlicenced-but-
permitted dependency requiring written confirmation before any acquisition or
IP-collateral event. The SelekyT-native layer — the editorial decision layer
above, `rust/core/src/fallback.rs`, `rust/sidecar`, and `engine/multitrack.py` —
is original. SelekyT's own code is proprietary and source-available under the
`LICENSE` in this repository.

## 5. "Why hasn't a real NLE opened your exported timeline?"

Because **no NLE is installed on the build host**, and I will not claim a
result I did not observe. Here is exactly what is and is not true:

- **True:** the EDL and FCPXML files are format-valid and self-consistent, they
  are derived from the exact interval list that was rendered (so the timeline
  describes the media that actually shipped), and they are validated at write
  time on the source's real frame rate. All 6 timelines from the real run were
  re-validated by hand: `01..03.edl` VALID, `01..03.fcpxml` VALID. Record spans
  advance monotonically (`02.edl`: frames `0→457`, then `457→512` — no overlap,
  no gap).
- **True:** one real bug was found and fixed here — independent per-event
  rounding let event 3 start one frame *before* event 2 ended, and the
  validator caught it after renders and captions were already written
  (`tests/test_edl_record_advance.py`, verified to fail on the pre-fix code).
- **Not true, and never claimed:** that Premiere, Resolve, FCP or Avid can
  *import* these files. Import compatibility is unclaimed. Nobody has opened
  them.
- **Also true:** the same emitter is verified in two languages. Python
  (`engine/fcpxml.py`) and Rust (`rust/core/src/export_fcpxml.rs`) produce
  **byte-identical** multi-track FCPXML, asserted by a live cross-language test
  (`tests/test_sidecar_parity.py`). That is strong evidence the emitter is
  disciplined, but it is not evidence an NLE accepts it.

**The cheapest fix in the whole gap map.** DaVinci Resolve has a free tier and
reads CMX 3600. Importing one timeline costs a download and an afternoon, and
it converts "structurally valid" into "compatible". It has not been done purely
because this host has no GUI and no licence. It is the first thing the
engineering hire does in week 1, and it is a **zero-capital** milestone.

**One specific trap that day-one import must check.** EDL support is broad —
Premiere Pro, Avid Media Composer, DaVinci Resolve, Final Cut Pro, Lightworks
and Vegas Pro all read EDLs "to some extent"
(https://en.wikipedia.org/wiki/Edit_decision_list, last edited July 22, 2026) —
but the same source records a hard limit: **"On FCP, in CMX3600 format, only
eight characters are allowed" for reel names.** This one is measured, not
assumed: every reel in all six shipped EDLs is `NASA_360` — exactly 8
characters, at the limit but inside it — and the emitter constructs reel names
with an explicit 8-character slice (`engine/nle_export.py:126`) that is pinned
by `tests/test_nle_export.py::test_edl_reel_names_are_ascii_and_unique`. So a
reel cannot ship over length, but the same construction *truncates silently*.
Verified on this host: `interview_take_1.mp4` and `interview_take_2.mp4`
produce `INTERVIE` and `INTERVI1` — unique, so they cannot relink to each
other, but the second no longer points back at its own filename, so an editor
walking event 002 to its footage has nothing to follow. That is a real defect
class for a multi-source timeline, and it has not been exercised because the
one real run has a single source. It gets checked before any FCP compatibility
is claimed rather than after.

## 6. "How do you get your first 100 customers?"

Not by marketing. By the order of operations the product dictates.

1. **Fix the zero-dollar credibility items first.** Import a timeline into a
   real NLE (item above). Observe a YouTube fetch from a residential network —
   one was already observed here (640x360, 11.8 MB via an unauthenticated
   android client) while other videos returned the sign-in wall, so the code
   works and the constraint is network egress, not software.
2. **Five named design partners, free, in exchange for the data.** The ask is
   one real long-form source each and 30 minutes of an editor's time. Target:
   small post houses, podcast networks, and news/video teams who already employ
   a human editor — the buyer whose pain is the *first pass*, not the final
   pass. This is deliberately not the consumer-creator market OpusClip serves
   at $15–$29/month.
3. **Publish the audit, not the hype.** `tools/editorial_audit.py` is a
   benchmark. Run it across 20–50 real sources of differing length, language
   and production style, publish the pass rates, and publish the failures. The
   credibility asset is "here is what our editor does badly, measured" — which
   is also the roadmap.
4. **Convert on the workflow, not the output.** The hook is that the editor can
   open the timeline and change it. A design partner who edits a SelekyT
   timeline in their own NLE has adopted the product in the way that matters.
5. **Then price.** Verified competitor pricing today (fetched from each
   company's own pricing page on 2026-09-19): OpusClip Starter $15/mo, Pro
   $29/mo; Submagic Starter $19/mo, Pro $39/mo, Business $69/mo, no free tier
   and API add-ons at $0.10–$0.23 per minute; Descript Hobbyist $24/mo, Creator
   $35/mo, Business $65/mo; Mirage/Captions Max $24.99/mo, Scale from $69.99/mo.
   SelekyT's buyer has a real editor on payroll, so the honest price point is
   closer to a per-seat professional tool than a creator subscription — but no
   price is committed until a design partner says they would pay it.

**The number that matters is not 100 users.** It is 5 design partners, 3 of
whom have editors who opened and modified an exported timeline. That is the
milestone that de-risks the company, and it costs almost nothing.

## 7. "What if the professional editor community rejects AI first passes?"

**That is the base case, not a risk.** The question is whether the product works
when it happens.

Professional editors are right to distrust a black box that hands them a
finished video they cannot inspect. That is precisely why SelekyT exports the
decision rather than only the media: `report.json` carries every candidate
scored, every candidate rejected, and the reason for each rejection; the
timeline is the same interval list that was rendered, so what the editor sees
in the NLE is what was actually produced; provenance records which
transcription and VAD backends ran.

The honest limit, stated in the repo's own words: editorial judgement is
**unmeasured at scale**. Three clips from one source passed the audit. That is
evidence the machinery is sound, not that the choices would survive comparison
with a human editor across sources and languages. Nobody has yet sat a human
editor in front of a SelekyT first pass and timed how long their revision
takes. That measurement is a first-quarter objective, and if the answer is
"longer than doing it themselves", the company's thesis is wrong and the money
should go to a different problem.

**The design response if rejection is severe:** collapse the ambition from
"produces the cut" to "produces the *proposed* cut" — a review surface inside
the NLE the editor accepts or rejects piece by piece. The architecture already
supports this because every decision is a scored, rejectable object. That
pivot is a product change, not a rewrite.

## 8. "Is this a Telugu / India product?"

**No.** Global product, global market, India used as a **testing advantage** —
exactly as positioned.

- Telugu is a real, structural test case, not a marketing angle: the pipeline
  accepts `--language te` (and `hi`, `ta`, etc.), preserves the detected source
  language by default, and the caption renderer handles Indic Unicode. Language
  forcing is exercised through fixtures; **no real non-English speech audio was
  available on this host**, so real-accuracy-on-Indic-audio remains unmeasured
  — that is a gap, and it is closed by running the audit on real Telugu and
  Hindi sources, which is cheap.
- The reason India is a testing advantage rather than the market boundary:
  multilingual robustness is forced early instead of being deferred, and the
  cost of being wrong is low.
- The market numbers are genuinely large and Indian, but they are a tail, not
  the plan. BCG's "From Content to Commerce" (May 3, 2025) counts "over 2–2.5
  million" monetized Indian creators influencing "$350–400 billion" in consumer
  spending, "projected to drive $1 trillion+" by 2030.
  https://www.bcg.com/publications/2025/india-from-content-to-commerce-mapping-indias-creator-economy
  IAMAI/KANTAR ICUBE 2024 reports 886M Indian internet users, 98% accessing
  content in Indic languages, with **Tamil, Telugu and Malayalam the most
  popular Indic languages online**.
  https://www.itvoice.in/led-by-surge-in-indic-language-adoption-internet-users-in-india-set-to-cross-900-million
- Telugu speaker counts come only from the 2011 census (~96M total speakers,
  4th-most-spoken native language in India)
  https://en.wikipedia.org/wiki/Telugu_language — no newer census-based Telugu
  internet-user figure was found, so no Telugu-specific TAM is claimed.

**The honest caution:** an investor who likes the India story should know that
no notable 2025–2026 funding round in an Indian AI video-editing company was
verified in this research. The India creator-economy TAM is real; a
venture-scale Indian AI-video software company is not yet demonstrated. The
pitch is global from day one.

## 9. "What is the moat today?"

**Almost none, and pretending otherwise would be the most damaging thing in
this document.** Today's assets, in descending order of strength:

1. **A working, tested system where competitors have demos.** 822 passing
   tests, byte-identical cross-language export, a repeatable editorial audit,
   and one real end-to-end run on genuine unscripted human speech. This is real
   but not a moat — it is a starting position.
2. **The audit + provenance contract.** Every editorial decision is scored,
   rejectable and reproducible. That is a *governance* property that matters to
   a newsroom or a brand, and it is hard to retrofit onto a black-box clip
   generator. It is the seed of a moat, not a moat.
3. **The editable-timeline handoff.** Shipping FCPXML 1.11 and CMX 3600 EDL
   from the same interval list that was rendered means the human editor is not
   displaced. Greylock's Seth Rosenberg argues AI applications escape the
   ChatGPT "God Box" through liability absorption, purpose-built product, and
   two-sided networks — and that "every platform shift lets amateurs create,
   and the amateurs often win"
   (https://greylock.com/blog/breaking-out-of-the-god-box, July 28, 2026). The
   professional-editor handoff is a purpose-built-workflow bet of exactly that
   kind. Not yet realised.
4. **Nothing in the model layer.** No fine-tuned model, no proprietary
   training data, no channel-specific retention learning. The roadmap lists
   channel-specific learning from exported analytics as future work; today
   retention is encoded as editorial *principles* (documented in
   [EDITORIAL.md](EDITORIAL.md)), not learned from any channel's real
   analytics.

**The stated path to a moat**, in the order investors have publicly endorsed:
own the workflow and the customer relationship, not the model. a16z's Olivia
Moore reports that Notion's paid AI attach rate went from 20% to **more than
50%** in a year, with AI features now "roughly half of the company's ARR", and
that standalone image-gen traffic was compressed as ChatGPT and Gemini bundled
native image models (Midjourney fell from top-10 to #46)
(https://a16z.com/100-gen-ai-apps-6/, March 9, 2026). Sequoia's 2026 guidance is
to build moats "from the customer back"
(https://www.sequoiacap.com/article/ai-ascent-2026, May 8, 2026). Translated:
SelekyT's moat, if it gets one, will come from being embedded in a real
editorial workflow with design partners whose feedback retunes the scorer —
not from Whisper.

## 10. "What is the valuation? What is the round?"

**There is no round and no valuation.** No term sheet exists, no investor has
committed, no SAFE or priced round has been issued, and no valuation has ever
been discussed with anyone. Any number I quoted would be invented.

What is appropriate, and why, is set out in
[INVESTOR_READINESS.md](INVESTOR_READINESS.md) — the short version: a **lean
pre-seed of roughly $1M**, sized to the evidence that actually exists (a
working system, zero users) rather than to the founder's eventual ambition.
This is deliberately below the 2026 AI-seed fashion. TechCrunch reported in
March 2026 that a $10M seed at $40–45M post-money had become "typical…
especially if you are an AI company", with YC W26 companies asking $5M at $40M
post (https://techcrunch.com/2026/03/31/its-not-your-imagination-ai-seed-startups-are-commanding-higher-valuations/).
The counter-argument is better evidenced: Crunchbase News analysed ~200 first
rounds of $100M+ over 15 years and found only ~1% generated venture-justifying
returns, while AI winners' *first* rounds were Cursor under $10M, ElevenLabs
$2M, Sierra $25M, Cohere $5M
(https://news.crunchbase.com/venture/billion-dollar-seed-ai-biotech-mcdonald-bison/, July 16, 2026).
A company with zero users raising $10M at $45M would be optimizing for a
headline, not for survival odds.

## 11. "Why now?"

Three things are true simultaneously in September 2026 that were not true in
2024.

1. **The components are free and local.** Whisper-class transcription, VAD,
   ffmpeg, and local LLM inference are commodity. The whole SelekyT pipeline
   runs on one machine with no API keys — which is also why the unit economics
   can be sane.
2. **The incumbents have declared their hand.** Adobe's September 8, 2026
   Generative Media Tool and Resolve 21.1's MCP server mean "AI inside the NLE"
   is now table stakes. That *removes* the ambiguity: the only open space is
   the layer above the NLE that hands work *to* it, and that space exists
   precisely because the incumbents will not cannibalize their seat licences.
3. **The bar for AI applications has moved from capability to outcome.**
   TechCrunch's year-end investor survey quotes M13's Morgan Blumberg
   expecting "fewer mega seed rounds" and Series A/B requiring "clear evidence
   of explosive momentum"; Black Ops VC's James Norman that founders must now
   show "a distribution advantage and repeatable sales engines, not a flashy
   demo"; Flybridge's Dorothy Chang that founders need "proprietary advantages
   — data, networks, or tech — not just something easy to vibe code"
   (https://techcrunch.com/2025/12/26/whats-ahead-for-startups-and-vcs-in-2026-investors-weigh-in/).
   SelekyT's audit-first design is built for that bar.

## 12. "What does $1M actually buy, and what does SelekyT become?"

Mapped line by line in [FUNDRAISING.md](FUNDRAISING.md). The one-sentence
answer: **$1M buys the difference between a repo that works and a product with
a measurable editorial quality record and its first paying design partners.**
Specifically it buys (a) one senior engineer to audit the machine-written core
and own NLE import compatibility, (b) hosted single-tenant deployment with real
telemetry, (c) the 20–50-source editorial audit corpus that becomes the
company's credibility asset, (d) 5–8 named design partners, and (e) the legal
and IP clean-up (AutoCut permission in writing, licence review). It does **not**
buy a marketing machine, a sales team, or a fine-tuned model — those come after
the evidence exists, in a later round.

## 13. "What happens if YouTube blocks downloading?"

It already does, for this host. Every yt-dlp variant returned "Sign in to
confirm you're not a bot" from this datacenter IP while plain `curl` to
youtube.com returned 200 — a block on the IP range, not a code defect. One
video *was* observed downloading to completion (640x360 h264+aac, 11.8 MB) via
an unauthenticated android client while other videos were refused on every
client, and a video served to android was refused on web/web_safari/ios — so
the wall is **per-video**, not per-IP, and `providers/youtube.py` now cycles
client candidates. Cookies (`--cookies`) and `SELEKYT_YOUTUBE_PLAYER_CLIENT`
are named in the failure message as remedies.

**Why this is not fatal to the product:** the input path is not the product.
The editorial engine is. Local file ingest is fully verified, archive.org and
other URL sources work end to end, and a user on a residential IP is probably
unaffected. But the risk is real and it is the reason the capital plan does not
depend on a consumer YouTube-clipper business: the professional design partners
in the plan hand over source files, not URLs. Platform-dependence is a reason
to sell to post houses, not a reason to avoid them.

## 14. "Why is the licence proprietary and source-available rather than open source?"

Because open-sourcing the editorial decision layer would hand the only
proprietary asset to companies that have distribution I do not. The `LICENSE`
file (effective with commits `7a882de` / `c6b46c9`, replacing the earlier MIT
licence) grants evaluation and collaboration rights only, and reserves
commercial use, hosting, and derivative products to written permission.

**The cost of that choice, stated honestly:** it narrows contribution. A
source-available proprietary licence will not attract the community an
Apache-2.0 project would, and the AI-agent build loop does not substitute for
external review. It also makes the AutoCut provenance question sharper, since
permission to reuse AutoCut was given person-to-person. If an investor prefers
an open-core model, the decision is reversible — but the investor should decide
that, not be surprised by it.

## 15. "What about hallucination and fabrication?"

This is a design constraint, not a caveat. SelekyT **selects and reorders
source speech; it does not write new claims.** There is no generative text
layer in the editorial path. The context/truth guard
(`engine/context_guard.py`) scores candidates down when sensational wording
outruns the source and requires stronger evidence for absolute claims; it was
observed rejecting candidates on the real run and is unit-tested.

The honest limit: the guard is rule-based and, per the repo's own record, the
context guard's absolute-claim detection was English-only in earlier versions
and multilingual support is partial. It is a guardrail, not a warranty. Any
claim of "AI-verified accuracy" would be false advertising, and none is made
here.

## 16. "What is the single biggest risk?"

**That the editorial quality does not survive contact with real human
editors.** Everything else in the gap map is cheap and mechanical: NLE import
is an afternoon, YouTube egress is a network condition, the desktop shell is a
packaging exercise. The one thing money cannot fix by hiring is whether a
professional editor, given a SelekyT first pass, finishes faster than they
would have started from scratch. That has never been measured. Three clips
from one source passing an automated audit is not that measurement.

The mitigation is not a slide — it is the first design-partner engagement,
instrumented: capture revision time, rejection rates per editorial check, and
editor sentiment, and report the numbers whether or not they are flattering.
If the answer is bad, the capital plan's engineering line still bought a real,
measured answer rather than a story — and the company pivots to the "proposed
cut" review-surface model or stops.

## 17. "Why not raise more, if the ambition is a $100M+ company?"

Because the capital strategy has to be built in the order the business actually
forms: **PRODUCT → FIRST USERS → FIRST REVENUE → PMF → REPEATABLE GROWTH →
SCALE → CATEGORY LEADERSHIP.** SelekyT is at PRODUCT (partial — see the gap
map) and has not reached FIRST USERS. Raising scale capital at the
"pre-first-user" stage does not accelerate the journey; it shortens the runway
available to clear the only milestones that matter, and it sets a valuation
that the current evidence cannot support at the next round.

The seed-to-Series A graduation rate has collapsed — Crunchbase News reports
cohorts reaching Series A falling from 55%+ pre-2020 to 24% (2023) to **16%**
(2024), with seed-to-A time now over two years
(https://news.crunchbase.com/seed/data-bigger-deals-longer-seriesa-2026/, May
26, 2026). Series A bars have risen with it: the same piece quotes Uncork
Capital's Andy McLoughlin that Series A now requires **$2M–$4M ARR**, up from
the old $1M threshold, with inception-stage rounds typically $3M–$5M. So the
round after this one is not won on narrative; it is won on revenue and
retention. A $1M pre-seed spent entirely on generating that evidence is the
most credible path to a real seed. Raising $5M at $45M now, with zero users,
would make that harder, not easier.

## 18. "What if the AI build loop gets better and this whole thing becomes trivial to copy?"

This is the risk I think about most, and it cuts both ways.

The repository was built by AI agents in a multi-agent loop — 140 commits,
~27,000 lines, 822 tests, for one person's time. If I can do that, so can
anyone. The barrier to "build an AI clip generator" is approaching zero, and
Flybridge's Dorothy Chang is already warning investors about companies that are
"easy to vibe code" (TechCrunch, December 26, 2025,
https://techcrunch.com/2025/12/26/whats-ahead-for-startups-and-vcs-in-2026-investors-weigh-in/).

The conclusion I draw is the opposite of "therefore build a harder
technology". It is: **when the technology is trivial, the workflow position,
the measured quality record, and the customer relationship are all that
remain.** That is why the capital plan buys design partners and an audit
corpus rather than a model. A competitor can copy the code in a weekend;
they cannot copy 20 sources of measured editorial performance, five editors
who trust the timeline handoff, or a reputation for publishing failures. If
those things turn out not to be worth money, then the company was not
defensible at any valuation — and it is better to learn that having spent $1M
than having spent $10M.

## 19. "Platforms are daemonetizing AI content. Isn't that a risk to this business?"

It is a risk to the *clip-farm* business, and that distinction is the most useful
thing in this answer.

What the platforms did, verified and dated:

- **YouTube, July 16, 2026** — clarified its Partner Program "inauthentic content"
  policies (first introduced 2025), rolling out to all YPP members. Three
  categories are daemonetized, and the first is **"generic, repetitive, or
  template-based content — videos easily made with AI, CGI, or templates with
  little variation; even tutorials count if they reproduce content already
  prevalent on the platform."** Trust-and-safety chief Matt Halprin framed it as
  fighting "content farming": "the same technology really enables great stuff,
  but it also enables stuff that's kind of content farming. We've heard from our
  viewers that that's not something that they like. They find it off-putting."
  https://techcrunch.com/2026/07/20/youtube-clarifies-policies-around-ai-slop-and-upsetting-videos/
- **Snapchat, July 31, 2026** — fully AI-generated videos are no longer eligible
  for Spotlight recommendations; only videos "created by real people" are.
  AI-*assisted* editing remains explicitly permitted.
  https://techcrunch.com/2026/07/31/snapchat-no-longer-rewards-fully-ai-generated-spotlight-content/
- Adjacent moves in the same period: Spotify labels and excludes AI-persona
  music from recommendations (Aug 11, 2026); Pinterest shipped AI-content
  controls (Apr 30 and Oct 16, 2025); Pangram raised $9M for AI-content detection
  (Jul 29, 2026).

**Why this is a reason to invest rather than a reason to pass.** The policies
draw a line that runs straight through SelekyT's category. On the wrong side:
volume output with no named human judgement — exactly what a one-click clip
generator produces, and exactly what YouTube now names as "template-based
content… easily made with AI". On the right side: an editor-supervised cut with a
written decision record, where a human accepted or rejected every candidate. The
whole SelekyT architecture — scored candidates, rejection reasons, an editable
timeline the editor continues in — produces an artefact that says *a person
decided this*. A clip generator produces no artefact at all.

**The honest limits of that argument.** First, SelekyT is not a publisher, so no
policy is aimed at it; the risk runs through customers' channels, and it is
unquantified because SelekyT has no customers yet. Second, "AI-assisted editing
is permitted" is Snapchat's current wording with **no published percentage
threshold** — the line could move. Third, the argument is a preference, not a
contract; nothing requires a platform to treat editor-supervised AI any better
than unsupervised AI, and if a platform later decides *any* AI-assisted cut is
tainted, this advantage evaporates. Fourth, I could not verify any statistic
showing short-form engagement is declining — the case rests on platform policy,
which is documented and dated, not on a fatigue trend I cannot source.

**What I will not do:** claim that SelekyT is "compliant" with policies that have
never been applied to it. No SelekyT output has ever been published anywhere, so
no channel has ever been reviewed under these rules. That is a fact, not a
defence.

---

## Questions I will answer with "I don't know" rather than guess

- **Growth model / CAC / LTV:** unknown; no users, no funnel.
- **Unit economics per rendered minute:** not yet measured in a hosted setting.
  Submagic's published API add-on rates ($0.10–$0.23 per minute, verified on its
  pricing page today) are the nearest public benchmark for what the market
  already pays for AI video processing.
- **Editorial accuracy vs. a human editor:** unmeasured. See FAQ 7 and 16.
- **Non-English transcription accuracy on real audio:** unmeasured; no real
  non-English source was available on this host.
- **Which NLE will import the timelines:** unknown. Claimed only as
  format-valid. See FAQ 5.
- **Whether the first 100 customers will be professionals or creators:** a
  strategic decision pending design-partner feedback, not a forecast.
