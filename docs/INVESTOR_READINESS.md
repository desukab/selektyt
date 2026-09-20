# SelekyT — Investor Readiness: honest gap analysis

**Date: 2026-09-19.** This document exists to answer one question: *what does an
investor in September 2026 actually need to see, and what does SelekyT actually
have?* Each dimension is scored **READY / GAP / BLOCKING** with the evidence,
where the evidence is a file, a test count, or an observed run — never an
intention. Nothing is graded on what the code is "capable of"; that distinction
is the whole point of [product-gap-map.md](product-gap-map.md) and it is applied
here too.

Verified on this host today: **1034 tests passing, 12 skipped (113 s), re-run 2026-09-20**; the 12
skips are all `SELEKYT_PROXY_MEDIA`-gated optional backends absent from this host
(sherpa-onnx, silero, ollama) — environment skips, not failures. One real rights-permitted 24:29 source (NASA 360 Episode 5, US
Government public domain) ran the full chain green through the web shell;
one YouTube download observed (640x360, 11.8 MB) while most videos returned the
sign-in wall from this datacenter IP; EDL/FCPXML structurally valid but never
opened in a real NLE; no desktop binary; **zero users, revenue, pilots,
investors, or employees.**

---

## The stage: what "investor-ready" means for SelekyT right now

SelekyT is at the boundary between **PRODUCT** and **FIRST USERS** in the
sequence PRODUCT → FIRST USERS → FIRST REVENUE → PMF → REPEATABLE GROWTH →
SCALE → CATEGORY LEADERSHIP. It has not reached FIRST USERS. Therefore the
correct round is a **pre-seed**, and the correct size is set by what the
evidence can carry.

2026 norms, verified:

| Fact | Source |
| --- | --- |
| Median US seed round ~$3M; upper-quartile median ~$5.6M; lowest quartile $1M; median Series A $15M | Crunchbase News, May 26, 2026 — https://news.crunchbase.com/seed/data-bigger-deals-longer-seriesa-2026/ |
| Bay Area seed rounds $3M–$8M ("sometimes up to $10M"), seed valuations $20M–$50M post-money; **rounds below $3M are "generally considered pre-seed"** | Crunchbase News, April 29, 2026 — https://news.crunchbase.com/venture/average-seed-funding-amounts-deals-grew-2025/ |
| A $10M seed at $40–45M post is now "typical… especially if you are an AI company"; YC W26 companies asking $5M at $40M post | TechCrunch, March 31, 2026 — https://techcrunch.com/2026/03/31/its-not-your-imagination-ai-seed-startups-are-commanding-higher-valuations/ |
| Series A now requires **$2M–$4M ARR** (up from the old $1M threshold); inception-stage rounds typically $3M–$5M | Crunchbase News quoting Andy McLoughlin, Uncork Capital, May 26, 2026 (same URL as above) |
| Seed→Series A graduation: 55%+ (pre-2020) → 24% (2023) → **16%** (2024); seed-to-A now >2 years | Crunchbase News, May 26, 2026 (same URL as above) |
| Of ~200 first rounds of $100M+ over 15 years, only ~1% produced venture-justifying returns; AI winners' first rounds were Cursor <$10M, ElevenLabs $2M, Sierra $25M, Cohere $5M | Crunchbase News, July 16, 2026 — https://news.crunchbase.com/venture/billion-dollar-seed-ai-biotech-mcdonald-bison/ |

**Caveat carried forward:** median pre-seed check-size data from a primary
provider could not be verified (Carta and PitchBook were inaccessible to the
fetcher). The "$500K–$2M pre-seed" framing is consistent with the two verified
Crunchbase datapoints ("<$3M is pre-seed"; lowest-quartile seed $1M) but is not
independently confirmed by a data-provider page. Treat it as an inference.

**The stage-appropriate ask.** A **pre-seed of roughly $1M**, structured as a
SAFE or a small priced round, sized to do exactly one job: convert a working
repo into a product with a measured editorial-quality record and its first
paying design partners. This is deliberately below the current AI-seed fashion
and deliberately below the founder's stated $100M+ ambition — because the
purpose of this round is to *earn the right* to the next one, not to mark a
valuation. Given the Series A bar of $2M–$4M ARR and a 16% seed-to-A graduation
rate, the most capital-efficient strategy is the smallest round that buys the
evidence, not the largest round the narrative can support.

---

## Readiness scorecard

### TEAM — **BLOCKING** (for an institutional seed) / **GAP** (for a pre-seed)

| Question | Evidence |
| --- | --- |
| Who is building it? | One person: Bharath Bheemreddy, copyright holder in `LICENSE`. No employees, no co-founders, no advisors of record. |
| Is there a second engineer? | No. 193 commits across three committer identities, all traceable to the same individual plus this build host. |
| How was it built? | Largely by AI coding agents in a multi-agent loop — five preserved agent worktrees are catalogued in [agent-worktrees.md](agent-worktrees.md). ~23,000 lines Python, ~3,900 lines Rust. |
| Is that a capability or a risk? | Both. It is a real, unusual leverage: a full cross-language pipeline with 1034 tests for one person's time. It also means no human has audited the machine-written core and no second person understands the system. |

**Why this is the binding constraint:** every other gap in this document is
mechanical and cheap. Only the team gap compounds. Specifically it blocks (a)
NLE import verification — nobody has installed an NLE; (b) independent review of
the machine-written export emitters; (c) any institutional investor's
key-person underwriting at seed size.

**What unlocks it:** one senior engineer hired out of this round, plus 2–3 named
advisors with professional-post-production credibility. Until a second engineer
exists, only pre-seed and angel capital is appropriate.

### PRODUCT — **GAP** (close to READY on the core; BLOCKING on two narrow items)

What is verified, not merely coded:

| Capability | Status |
| --- | --- |
| Local file → full chain (download-free) | **Verified.** A real 535.5s local source ran the whole pipeline. |
| URL source → full chain (web shell) | **Verified.** NASA 360 Episode 5, 24:29, genuine two-host unscripted speech, 25 artifacts, QA `ok: true`, 0 errors, through `POST /api/run_pipeline`. |
| Transcription on real human speech | **Partly verified.** 425 segments and chunked decode (5 × 5 min) with no boundary artefacts are machine-checkable and verified. "Names, numerals and humour transcribed correctly; no manual correction needed" is **NOT verified** — the run completed green, but no human has compared the transcript to the audio, so per-segment accuracy on this source is unmeasured. `docs/EVALUATION.md` §3 calls this an impression, not a measurement, and that is the honest status. |
| Editorial scoring, context guard, story units | **Verified** — per-candidate scores and rejection reasons in `report.json`; guard observed rejecting candidates. |
| Dead-air removal, pause-aware retention | **Verified** in the real run's durations; pinned by `test_cue_times_account_for_removed_pauses`. |
| 16:9 render | **Verified** by ffprobe read-back. |
| 9:16 vertical render | **Verified after one real bug.** The first claim was false: on the anamorphic NTSC source the output displayed at 1088:2187 ≈ 1:2. The SAR-aware fix now lands display ratio at 0.5633 vs a 0.5625 target. Found by reading rendered geometry, not by trusting the code's comment. |
| Captions (ASS/SRT/JSON, word-timed, 4 presets) | **Verified.** Cue-for-cue ASS/SRT parity pinned by tests. |
| EDL (CMX 3600) and FCPXML 1.11, per cut | **Structurally verified.** 6/6 timelines re-validated by hand; record spans monotonic. One real one-frame overlap bug found and fixed (`tests/test_edl_record_advance.py`, verified to fail pre-fix). **Never imported into a real NLE.** |
| Determinism | **Verified** — the web-shell run reproduced the CLI run's selection, durations and cue counts exactly. |
| Editorial audit | **Verified** — `tools/editorial_audit.py` on the real project: ALL CHECKS PASS (cuts between words, no split sentences, no shaved tail words, cue text matches transcript, cues fit render, silence share 3.6–40.8%). **Caveat a diligent reviewer will find:** the "silence share" figure is computed from transcript word timings, not audio — clip 05's 40.8% is a music bed the transcript correctly has no words for, not dead air. See `docs/METRICS.md` A2.4. The check's name overstates what it measures; the underlying transcript and word timings are sound |
| CLI, `--doctor`, web shell | **Verified**, both pass and fail paths. |
| CI | pytest on Python 3.11–3.13 + `cargo test` + `cargo clippy -D warnings` on every push. |

The two items that are BLOCKING, and note they are narrow:

1. **NLE import compatibility is entirely unclaimed.** No NLE is installed on
   this host. The files are valid, self-consistent, and derived from the
   rendered intervals, but no editor has opened them. This is the single
   cheapest credibility milestone available: DaVinci Resolve has a free tier
   and reads CMX 3600.
2. **The web shell has no video upload.** A video must already be inside the
   project tree (`guardedPath` confines it), while the CLI has no such
   restriction — so the two entry points disagree about what a user can point
   at. For a product whose buyer will hand over source files, this is a
   first-week fix, not a research problem.

Other gaps, real but lower-severity: desktop shell unbuilt (Tauri compiles and
passes clippy, no binary packaged, webview never rendered); optional backends
(sherpa-onnx, silero, ollama) wired and degrading cleanly but unit-tested only;
the web shell does not expose the Ollama URL (a UI gap, not a broken path).

**Honest framing:** the product is materially further along than a typical
pre-seed "working prototype" claim — it has test-pinned, real-input-verified,
cross-language-agreeing behaviour. It is a *product-verified, product-unshipped*
system: nothing is deployed, nothing is hosted, no user has ever used it.

### TRACTION — **BLOCKING**

**Zero users. Zero revenue. Zero pilots. Zero conversations with customers.**

The evidence is structural, not merely an absence of reporting:

- **No telemetry exists.** A repository search for analytics/telemetry
  integrations (`posthog`, `mixpanel`, `segment`, `telemetry`) returns only
  transcript *segment*-merging code in `providers/whisper.py` and
  `providers/sherpa.py`. There is no event the system could have recorded.
- **No billing exists.** No Stripe, no usage metering, no licence-key check. No
  code path can accept a payment.
- **No deployment exists.** The only infrastructure is a GitHub Actions CI
  workflow. SelekyT runs locally — CLI, or `node web/server.mjs` on
  `127.0.0.1`. Nothing is on the public internet.

**Why this is BLOCKING rather than GAP:** every verified 2026 investor
expectation puts distribution and usage ahead of capability, and SelekyT cannot
currently demonstrate either.

- Sequoia's 2026 guidance: build moats **"from the customer back"**
  (https://www.sequoiacap.com/article/ai-ascent-2026, May 8, 2026).
- TechCrunch's investor survey, December 26, 2025: Black Ops VC's James Norman —
  "Raising in 2025 requires a shift from 'visionary' to 'battle-tested'";
  founders must show "a distribution advantage and repeatable sales engines, not
  a flashy demo"; Flybridge's Dorothy Chang — founders need "proprietary
  advantages — data, networks, or tech — not just something easy to vibe code"
  (https://techcrunch.com/2025/12/26/whats-ahead-for-startups-and-vcs-in-2026-investors-weigh-in/).
- Crunchbase News, September 18, 2026, from an investor who reviewed ~25,000
  applications: 82% of applicants still in business a year later had "a strong
  go-to-market foundation in their deck"; "If we see classic strategies in a
  pitch deck, it is an auto-reject"; "Everyone is fast. It's no longer a unique
  attribute"
  (https://news.crunchbase.com/seed/startup-funding-rules-ai-gtm-golbin-lvlup/).

**What unlocks it:** not "100 users". **5 named design partners, 3 of whose
editors have opened and modified a SelekyT-exported timeline.** That is the
traction claim the architecture is uniquely able to produce, and it is
achievable on a pre-seed budget.

### MARKET — **GAP** (real, but the framing must not overclaim)

The market exists; the honest problem is that every player in it is larger
than SelekyT, and the market *for SelekyT's specific position* is unproven.

**Demand-side anchors (each definition stated separately — these are not
interchangeable):**

| Market | Size | Source |
| --- | --- | --- |
| Creator economy, global | $255.66B (2025) → $323.48B (2026) → $820.83B (2030), CAGR ~26.2% | The Business Research Company, Sept 2026 — https://www.thebusinessresearchcompany.com/report/creator-economy-market-report |
| Video editing software, global | $2.52B (2025) → $2.68B (2026) → $3.41B (2030), CAGR 6.2% | The Business Research Company, Sept 2026 — https://www.thebusinessresearchcompany.com/report/video-editing-software-global-market-report |
| Short video platforms, global | $2.22B (2025) → $2.5B (2026) → $3.99B (2030), CAGR 12.4% | The Business Research Company, Sept 2026 — https://www.thebusinessresearchcompany.com/report/short-video-platform-global-market-report |
| US digital video ad spend | Surpasses **$80B in 2026** | IAB, 2026 Digital Video Ad Spend & Strategy Report, July 14, 2026 — https://www.iab.com/insights/video-ad-spend-report-2026/ |
| YouTube creator payouts / Shorts scale | "$100B+ paid to creators over the past four years"; Shorts averages **200 billion daily views**; >1M channels used YouTube's AI creation tools daily in Dec 2025 | YouTube CEO Neal Mohan, January 21, 2026 — https://blog.youtube/inside-youtube/the-future-of-youtube-2026/ |
| India creator economy | "Over 2–2.5 million" monetized creators influencing "$350–400 billion" in consumer spending, "projected to drive $1 trillion+" by 2030 | BCG, May 3, 2025 — https://www.bcg.com/publications/2025/india-from-content-to-commerce-mapping-indias-creator-economy |

**The buyer population, from a government source rather than a forecast.** Every
figure above is a third-party estimate; this one is a count, and it is the number
that actually sizes SelekyT's wedge:

| Fact | Source |
| --- | --- |
| **39,400 employed film and video editors in the U.S. (2025)**; median annual wage **$75,420** (May 2025); projected growth **+4% to 2035** (to 40,800); ~5,600 openings projected annually across the combined occupation | U.S. Bureau of Labor Statistics, Occupational Outlook Handbook, page last modified **August 27, 2026** — https://www.bls.gov/ooh/media-and-communication/film-and-video-editors-and-camera-operators.htm |

This matters twice over. First, it is a *primary* source where every other row is
a sell-side market estimate. Second, it defines the honest shape of the
opportunity: **the professional-editor wedge is a precision play, not a
mass-market one.** 39,400 editors at a $75K median wage is a small,
slow-growing, well-paid population — precisely the buyer a $1M pre-seed can reach
through five design partners, and precisely the buyer the consumer clip tools
ignore. It is not the population a "$100M on arrival" narrative would be built
on, and this document does not build one on it. A global editor count was sought
and **not found**; no worldwide figure is claimed.

**Methodological warning, stated for the record:** market-research estimates for
"creator economy" vary by roughly an order of magnitude depending on definition
(ad spend vs. creator earnings vs. software licence revenue). Grand View
Research and Precedence Research produce materially different figures for the
same nominal market; their pages were inaccessible to the fetcher and their
numbers were taken from indexed snippets, so they are **not** cited here. The
$3.41B video-editing-software figure is the narrow, licence-revenue definition
and must never be combined with the $820B creator-economy figure into one TAM.
No total addressable market is claimed in this document. A second third-party
estimate disagrees with the one cited: Straits Research dates the video editing
software market at **$2.41B (2025) → $2.54B (2026) → $3.80B (2034), CAGR 5.2%**
(https://straitsresearch.com/report/video-editing-software-market, report dated
September 4, 2026) versus the $2.52B/$3.41B figures used above. The two agree on
the order of magnitude and on the shape (single-digit billions, low growth) but
not on the base year; both are cited so an investor can see the disagreement
rather than be shown whichever number is more flattering.

**Competitive reality — the part that decides the score:**

| Company | Verified position |
| --- | --- |
| **Higgsfield** | $400M Series B at **$5.4B** valuation, August 17, 2026, led by DST Global; **$700M annualized revenue**; 30M users across 200 countries; 390 of the Fortune 500 — https://techcrunch.com/2026/08/17/higgsfield-raises-400m-series-b-quadrupling-its-valuation-in-8-months-to-5-4b/ |
| **Runway** | $315M Series E at **$5.3B**, February 10, 2026, led by General Atlantic, with Adobe Ventures among others — https://techcrunch.com/2026/02/10/ai-video-startup-runway-raises-315m-at-5-3b-valuation-eyes-more-capable-world-models/ |
| **PixVerse** | $439M total Series C, valuation "crossed over $2 billion," July 13, 2026; 150M+ registered users — https://techcrunch.com/2026/07/13/video-generation-startup-pixverse-raises-439m-valuation-soars-past-2b/ |
| **Mirage (formerly Captions)** | $75M growth financing from General Catalyst's Customer Value Fund, March 24, 2026; **$28.4M in trailing-365-day in-app revenue**; 3.2M downloads; 200M+ videos created — https://techcrunch.com/2026/03/24/mirage-raises-75m-to-continue-building-models-for-its-ai-video-editing-app-captions/ |
| **OpusClip** | $20M at a **$215M post-money** valuation, March 11, 2025, led by SoftBank Vision Fund 2 — https://techfundingnews.com/opusclip-grabs-20m-for-ai-powered-video-editing-tool-that-turns-long-content-into-short-viral-clips/ (earlier round: $30M total, Aug 2024, "6M users and eight-figures in ARR" — https://www.opus.pro/blog/opusclip-celebrates-30m-in-funding-and-the-launch-of-clipanything) |
| **Submagic** | **No publicly announced funding round found**; bootstrapped or undisclosed. Displays Y Combinator and PSG logos; claims "4M+ businesses." Report as undisclosed, not as a valued competitor. |
| **Adobe** | Generative Media Tool **inside the Premiere Pro timeline**, September 8, 2026 — https://blog.adobe.com/en/publish/2026/09/08/generate-create-directly-in-your-timeline-with-new-ai-powered-innovations-in-premiere-after-effects |
| **Blackmagic DaVinci Resolve** | Resolve 20 (Apr 2025) shipped AI IntelliScript (timelines from text script) and AI IntelliCut (silence removal, per-speaker split); 21.1 (Sep 7, 2026) added a native MCP server for AI assistants — https://www.01net.it/blackmagic-design-announces-davinci-resolve-20/ , https://www.newsshooter.com/2026/09/07/blackmagic-design-davinci-resolve-21-1/ |
| **Descript** | **Last known round November 2022** ($50M Series C, OpenAI Startup Fund); no 2025–2026 round verified. Revenue **~$55M (2025)**, with ARR estimated at $55M in late 2024 up from ~$31M in Aug 2024 — **a Sacra estimate, not a company disclosure** — https://sacra.com/research/descript (round: https://techcrunch.com/2022/11/15/ai-powered-media-editing-app-descript-lands-fresh-cash-from-openai/) |
| **Veed.io** | **$35M total funding** (Series A Feb 2022, Sequoia); ARR ~**$50M (May 2026)**, up from $45M (Oct 2025); 10M MAU, 25,000 paying customers — **Sacra estimates** — https://sacra.com/research/veed |

**Reading of the table:** the AI-video category is exceptionally well funded at
the generative and consumer-clip layers, with at least four companies at
multi-billion-dollar valuations and one at $700M annualized revenue. SelekyT
cannot compete there and does not propose to. The specific position SelekyT
occupies — *editorial decision + editable timeline handoff to a professional
editor's own NLE* — has no verified dedicated entrant, but that is equally
consistent with "open niche" and "niche nobody wants". Which one it is can only
be answered by design partners, which is the same conclusion the TRACTION
section reaches.

### MOAT — **BLOCKING**

There is no moat today, and this document will not construct one.

1. **No model-layer asset.** No fine-tuned model, no proprietary training data,
   no channel-specific retention learning. The speech-to-text layer is fully
   commodity and freely commercializable: Whisper's **code *and* model weights**
   are MIT ("Whisper's code and model weights are released under the MIT
   License", https://github.com/openai/whisper) and faster-whisper is MIT
   (https://github.com/SYSTRAN/faster-whisper, CTranslate2-backed, "up to 4 times
   faster than openai/whisper for the same accuracy"); ffmpeg is LGPL/GPL;
   OpenCV is Apache-2.0; Ollama is MIT. **A defensible position cannot rest on
   the transcription layer** — anything proprietary must live above it. The
   optional LLM pass is *not even installed* on this host.
2. **No network effect.** Zero users means no two-sided anything. Greylock's
   Seth Rosenberg names two-sided networks as one of three ways out of the
   ChatGPT "God Box" — SelekyT has none, and cannot acquire one without
   distribution (https://greylock.com/blog/breaking-out-of-the-god-box, July
   28, 2026).
3. **No data feedback loop.** Retention is encoded as editorial *principles*
   ([EDITORIAL.md](EDITORIAL.md)), not learned from any channel's real
   analytics. The roadmap lists channel-specific learning as future work.
4. **Part of the mechanical core is not original.** 11 files are copied or
   ported from AutoCut by Ali Cobanov, by the author's explicit personal
   permission rather than by licence — see
   [autocut-provenance.md](autocut-provenance.md). This is a diligence item,
   and it means the claim "we built the engine" is true only of the
   editorial-decision layer above it.
5. **The code is copyable in a weekend.** The repository itself demonstrates
   this: it was built by AI agents.

**What is real, in descending strength:** a tested, real-input-verified system
where competitors have decks; an audit-and-provenance governance property that
would be hard to retrofit onto a black-box clip generator; and the
editable-timeline handoff as a *workflow* bet. Those are a starting position,
not a moat.

**The only path to one,** endorsed by the public guidance: own the workflow and
the customer. a16z reports Notion's paid AI attach rate went from 20% to **more
than 50%** in a year — "AI features now account for roughly half of the
company's ARR" — and that standalone image-gen traffic compressed as platforms
bundled native models, with Midjourney falling from top-10 to #46
(https://a16z.com/100-gen-ai-apps-6/, March 9, 2026). Sequoia's framing: build
moats from the customer back. Translated for SelekyT, a moat — if one is earned
— comes from design partners whose editorial feedback retunes the scorer, plus
a published, measured quality record. Not from Whisper.

### BUSINESS MODEL — **GAP** (no model exists; the position is chosen, pricing is not)

- **No monetization code and no price.** No billing, no metering, no
  subscription tiers, no free tier, no paywall. The business model is a
  decision that has not been made.
- **The position, as decided:** sell the *first pass* to organizations that
  already employ a human editor, not the finished cut to a creator. This is the
  workflow gap Adobe's per-seat model is structurally unable to fill.
- **Verified competitive pricing (fetched from each company's own pricing page,
  2026-09-19)** — the honest use of this table is to show what the market
  already pays, not to assert SelekyT's price:

| Company | Tiers (monthly) |
| --- | --- |
| OpusClip | Free; Starter $15; Pro $29; Business custom (https://www.opus.pro/pricing) |
| Submagic | Starter $19; Pro $39; Business $69; Custom. No free tier. Annual $12/$23/$41. API add-ons $0.10–$0.23/min (https://submagic.co/pricing) |
| Descript | Free (60 min/mo); Hobbyist $24; Creator $35; Business $65; Enterprise custom. Annual $16/$24/$50 (https://www.descript.com/pricing) |
| Mirage/Captions | Free; Max $24.99 (500 credits); Scale $69.99/$139.99/$279.99; Enterprise custom (https://www.captions.ai/pricing) |

- **Unit economics are unmeasured.** Nothing has been rendered on hosted
  infrastructure; the CPU transcription path is verified locally (faster-whisper
  `small`, chunked 5-minute decode) but no cost-per-render-minute exists.
  Submagic's published $0.10–$0.23 per minute is the nearest verified public
  benchmark for what the market currently pays for AI video processing.

**What unlocks it:** a design partner who states a price they would pay, then
pays it. Not a pricing study.

### REGULATORY / IP — **GAP** (one specific, fixable IP exposure)

| Item | Status |
| --- | --- |
| **AutoCut provenance** | The clearest exposure. 11 files copied or ported from AutoCut, which carries **no OSI-approved licence**; reuse rests on the author's explicit permission granted via Instagram, email and Discord — **those messages are not committed to this repository**. Attribution is in source headers and [autocut-provenance.md](autocut-provenance.md). Remediation is cheap and specific: obtain written confirmation, commit it (with the author's consent), and have counsel review the dependency. |
| **Licence posture** | Proprietary and source-available (commits `7a882de` / `c6b46c9`, replacing an earlier MIT licence). Commercial use, hosting and derivative products reserved to written permission. This protects the editorial layer but narrows external contribution — a deliberate, reversible trade. |
| **Component licences** | Whisper and faster-whisper MIT; ffmpeg/ffprobe LGPL/GPL; OpenCV Apache-2.0; yt-dlp Unlicense; Ollama MIT. No copyleft contamination of the SelekyT-authored layer is identified, but a formal licence scan has not been run. |
| **Content rights** | The only full-chain evidence run used a US-Government public-domain source (NASA 360 Episode 5), deliberately chosen so that no rights question confounded the engineering record. No user content has ever been processed, so no rights or retention policy has been exercised. |
| **Platform monetization policy — a real, current risk** | **This is the one item in the regulatory section that is not hypothetical.** YouTube clarified its Partner Program "inauthentic content" policies on **July 16, 2026**, daemonetizing three categories — the first of which is *"generic, repetitive, or template-based content… videos easily made with AI, CGI, or templates with little variation"*; trust-and-safety chief Matt Halprin framed it explicitly as cutting down "content farming", adding "we've heard from our viewers that that's not something that they like" (https://techcrunch.com/2026/07/20/youtube-clarifies-policies-around-ai-slop-and-upsetting-videos/). Snapchat followed on **July 31, 2026**: fully AI-generated videos are no longer eligible for Spotlight recommendations, though AI-*assisted* editing remains permitted (https://techcrunch.com/2026/07/31/snapchat-no-longer-rewards-fully-ai-generated-spotlight-content/). **Relevance, stated precisely:** SelekyT does not publish content, so it is not itself a policy target — but its *customers'* distribution economics are. A tool whose output reads as templated AI volume puts its user's channel at monetization risk. This is a structural argument *for* SelekyT's thesis, not against it: an editor-supervised, per-source-reasoned cut with a human named in the decision record is the opposite of "template-based content farming", and a clip farm with no human in the loop is precisely what the policies target. It is also a reason the capital plan sells to post houses rather than to volume publishers. |
| **AI-labelling / provenance law** | Not implemented and not required for the current product: SelekyT selects and reorders source speech and does not generate new claims or synthetic media. The labelling regimes that exist attach to *generated* content — China's CAC Order No. 15, Article 12 requires providers to "label generated content such as images and videos" (http://www.cac.gov.cn/2023-07/13/c_1690898327029107.htm, effective Aug 15, 2023), and the EU AI Act's general-purpose-AI obligations have applied since ~August 1, 2025 (https://en.wikipedia.org/wiki/Artificial_Intelligence_Act). Neither is triggered by reordering source footage. If generative features are added later, the existing provenance output is the natural carrier for any labelling requirement. |
| **Training-data copyright** | SelekyT does not train on third-party copyrighted content and does not ship a model, so the leading litigation is an *indirect* risk. For the record, it is unresolved: *NYT v. Microsoft and OpenAI* (S.D.N.Y., filed Dec 27, 2023) survived motions to dismiss in large part on March 26, 2025; the Times moved for summary judgment on **September 17, 2026**, and in **September 2026** the U.S. DOJ filed its first brief on the AI-training side, rejecting the argument that training on copyrighted text is infringement (https://en.wikipedia.org/wiki/The_New_York_Times_v._Microsoft_and_OpenAI). **"Fair use is settled" would be a false claim in either direction**, and none is made here. |
| **Data protection / SOC 2** | Nothing in place; nothing has been required yet. Design partners handing over source footage will require at minimum a DPA and a defined retention policy — a first-quarter task, not a blocker to this round. |

---

## Summary scorecard

| Dimension | Score | The single sentence |
| --- | --- | --- |
| **Team** | **BLOCKING** (institutional seed) / GAP (pre-seed) | One person, no second engineer, no human audit of a machine-written core — the only gap that compounds. |
| **Product** | **GAP** | Real, tested, real-input-verified, cross-language-agreeing — and never used by anyone, never hosted, with NLE import entirely unclaimed. |
| **Traction** | **BLOCKING** | Zero users, zero revenue, zero pilots, and no telemetry to have recorded any. |
| **Market** | **GAP** | Large and growing, but the consumer/generative layer is already held by $5B+ incumbents; SelekyT's specific niche is unproven. |
| **Moat** | **BLOCKING** | None. Commoditized components, no data loop, no network effect, part of the core is reused by permission not licence, and the whole thing is copyable in a weekend. |
| **Business model** | **GAP** | Position chosen, price unchosen, billing nonexistent, unit economics unmeasured. |
| **Regulatory / IP** | **GAP** | One specific exposure (AutoCut permission in writing), plus standard licence-scan and DPA clean-up. Cheap to fix. The one external risk that is *not* fixable — platform daemonetization of templated AI volume, July 2026 — cuts in SelekyT's favour, because editor-supervised cuts are the opposite of content farming. |

**The one BLOCKING gap that matters most:** TRACTION is the symptom; **TEAM** is
the cause. A solo founder with no second engineer cannot simultaneously ship
the NLE-import verification, run the design-partner programme, build the hosted
deployment, and audit the machine-written core. Every other BLOCKING or
near-blocking item in this table is solvable with one good hire and a modest
budget — which is precisely what the pre-seed is for.

---

## The ask, stated for the record

**A pre-seed of approximately $1M**, on a SAFE or small priced round, for a
company with: a working, test-pinned, real-input-verified editorial engine;
zero users; zero revenue; one founder; no moat; and two cheap-but-unclaimed
credibility milestones (NLE import, hosted deployment).

This is not a round sized to ambition. It is sized to the sequence:
PRODUCT → FIRST USERS → FIRST REVENUE. The $100M+ ambition is real but belongs
to a later round, and it is reached by clearing the milestones below — not by
pricing it now.

---

## Milestones that unlock the next round

The next round (a true seed, toward the $3M–$5M inception-stage norm and the
$20M–$50M post-money band) is unlocked by **evidence**, in this order.

**Tier 0 — credibility, near-zero capital (weeks 1–4)**

1. **One timeline imported into a real NLE** (Resolve free tier first; then
   Premiere or FCP). Converts "structurally valid" into "compatible". Zero
   capital cost; one engineer-day. EDL support is broadly attested — Premiere,
   Avid Media Composer, DaVinci Resolve, Final Cut Pro, Lightworks and Vegas Pro
   all read EDLs "to some extent"
   (https://en.wikipedia.org/wiki/Edit_decision_list, last edited July 22, 2026)
   — but the same source carries one concrete trap worth checking on day one:
   **"On FCP, in CMX3600 format, only eight characters are allowed" for reel
   names.** SelekyT's emitter must be verified against that limit before any FCP
   import is claimed; a reel name longer than 8 characters is an invisible
   failure mode that format validation will not catch.
2. **A hosted single-user instance with real telemetry.** The first time
   SelekyT is reachable by someone who is not the founder. This is also the
   moment a usage claim becomes possible at all.
3. **AutoCut permission confirmed in writing and committed**, and a formal
   licence scan run on the dependency tree.

**Tier 1 — the evidence that no competitor can fabricate (months 1–6)**

4. **The 20–50-source editorial audit corpus.** Run `tools/editorial_audit.py`
   across sources of differing length, language and production style; publish
   pass rates *and* failing checks. Directly attacks the repo's own stated gap:
   "editorial judgement is unmeasured at scale" (three clips, one source, is
   not a measurement). This corpus is the company's durable credibility asset.
5. **5 named design partners, free, in exchange for data and time.** The
   measured outcomes: revision time versus from-scratch, rejection rate per
   editorial check, editor sentiment.
6. **3 of those partners' editors open and modify a SelekyT-exported timeline
   in their own NLE.** This is the adoption claim the architecture exists to
   make, and no AI clip generator currently makes it.
7. **Real non-English sources through the audit** (Telugu, Hindi). Closes the
   India-as-testing-advantage claim with data instead of a flag. Partly measured
   now, and the data overturned the claim it was meant to support: Telugu scores
   WER 151.08% on `small` / 91.54% on `medium` against 3.38% for English, so
   language coverage is table stakes and language *fit* is the gap. Polish passes
   at 11.01%, which is what confines the failure to Indic. Hindi remains
   unmeasured.

**Tier 2 — the round-defining numbers (months 6–12)**

8. **First paid contracts** — converted design partners at a price they stated.
9. **Usage evidence a seed investor can underwrite:** activation, retention,
   and per-source economics from the hosted instance.
10. **The second and third engineers**, and 2–3 named advisors with
    professional-post-production credibility.

**Tier 3 — the seed pitch itself (months 12–18)**

11. A repeatable motion: **$500K–$1M ARR or signed pipeline**, with 3 reference
    customers who will say on the record that a SelekyT first pass saved their
    editor measurable time. That is the specific artefact that turns a
    pre-seed narrative into a seed round — against a 16% seed-to-Series A
    graduation rate and a $2M–$4M Series A ARR bar, it is the only artefact
    that will.

---

## What would make this document unnecessary

If, in 90 days, this repository gains: one verified NLE import, one hosted
instance with telemetry, three design partners on the record, and a published
20-source audit — then the readiness scorecard changes on four of seven
dimensions without a single line of new marketing, and the company can raise a
real seed on evidence rather than narrative. That is the entire purpose of the
$1M.

## Citations: verification limits, stated openly

- Carta, PitchBook, Statista, Grand View Research, Forbes, Business Insider and
  CNBC pages were inaccessible to the fetcher. Where a figure is quoted via
  secondary coverage, the secondary source is named.
- **Median pre-seed check size was not verified from a primary provider.** The
  "$500K–$2M pre-seed" framing is an inference from two verified Crunchbase
  datapoints ("<$3M is pre-seed"; lowest-quartile seed $1M).
- Sequoia's "Generative AI's Act Two" and "From Bundle to Breadth" essays were
  unavailable (old slugs 404, archive blocked). Current 2026 essays by the same
  authors are cited instead.
- OpusClip's *total raised* is not cited, because databases disagree ($36.8M /
  $50M / $68M). Only source-verified rounds ($30M total to Aug 2024; $20M in
  Mar 2025) are quoted.
- **Mirage's $75M round date is reported two ways.** TechCrunch (Ivan Mehta)
  dates the General Catalyst Customer Value Fund round **March 24, 2026**, and
  that is the date cited above because it is fetched, dated, primary reporting.
  Captions' own corporate blog gives the same round as **June 29, 2026**. The
  conflict is disclosed rather than resolved; an investor checking Captions'
  own blog will see the later date, and neither source states a valuation for
  the round.
- Descript and Veed ARR figures come from **Sacra company profiles**, which are
  third-party estimates, not company disclosures. Sacra pages carry no explicit
  publication date; their listed events run to late 2024 / May 2026
  respectively. They are cited because ARR is the most relevant metric and
  because the alternative is no figure at all — but they are labelled as
  estimates everywhere they appear.
- **Submagic has no publicly announced funding round.** It is reported as
  undisclosed, never as a valued competitor. TechCrunch returns no results for
  "Submagic" and Sacra carries no profile; Pictory, Fliki, Klap, Vidyo.ai,
  Chopcast and Munch are likewise **unquoted** — no funding, valuation or ARR
  figure for any of them could be verified, so none appears in this document.
- CapCut's ARR and user counts are **not cited**; ByteDance is private and
  capcut.com was fetch-blocked.
- **A worldwide professional-video-editor count was not found.** Only the U.S.
  BLS figure (39,400, 2025) is quoted, and it is quoted as a U.S. figure.
- **No statistic for short-form video fatigue or declining engagement was
  located**, so none is asserted. Any argument in this document that relies on
  "short-form fatigue" is built on the platform *policy* moves above
  (daemonetization, recommendation exclusion), which are documented and dated —
  not on an engagement statistic.
- Apple's own FCPXML specification page at developer.apple.com returns 404 and
  could not be cited. The version history used here is from Wikipedia's Final
  Cut Pro article; note it lists XML 1.10 (FCP 10.6) then 1.12 (FCP 10.8, June
  20, 2024) and 1.13 (FCP 11.0, Nov 13, 2024) without a 1.11 entry, while
  SelekyT emits **FCPXML 1.11** as pinned by `docs/autocut-parity.md`. That is a
  gap in the secondary source, not a contradiction — the emitted version is
  verified in-repo — but it means the emitter's version currency has not been
  checked against current FCP, and that check belongs in the week-6 NLE import
  milestone.
- No 2025–2026 funding round, valuation or ARR figure for Lightricks was found;
  its last verified round is 2021.
- California AI legislation (AB 2013 training-data transparency, SB 942
  provenance/watermarking) was researched but **not re-verified** against a
  citable primary source in time for this document, so it is not cited. It does
  not bear on the current product for the reasons given in the AI-labelling row.
- No notable 2025–2026 funding round in an Indian AI video-editing company was
  verified. The India story is a market tail, not a comparable.
