# Competitive map — AI editorial video tooling

**Compiled 2026-09-19.** Every external fact below was gathered on that date by
fetching the cited page live. Where a fact could not be confirmed it is marked
**UNVERIFIED** rather than guessed. Nothing in this document is recalled from
memory and no user, revenue, investor, partnership or traction figure is
invented; scale numbers carry the date they were claimed.

## How to read this document

Every substantive claim carries one of three markers:

* **VERIFIED** — reproduced in this repository or in a real artifact on this
  host, with a file path a reader can open.
* **RESEARCHED** — cited to a named external source (publisher and page type,
  and the article date where the source carries one) fetched live on
  2026-09-19. Raw URLs are not reproduced because the fetches were made
  through a rendering proxy and the proxy URLs are not stable citations; the
  publisher-plus-date attribution is what the claim rests on, and any claim
  that cannot carry even that is downgraded to UNVERIFIED.
* **ASSUMPTION** — an inference or reading, not a fact. It is labelled so it
  can be discarded without losing anything.

Two research caveats that constrain the whole document. The web-search tool
was non-functional in this environment for the entire session (it returned
zero links and, on some queries, a stale model-memory fallback in another
language). All sourcing below is therefore direct page fetches of primary or
reputable press pages, not search results. Crunchbase, LinkedIn, Reddit,
web.archive.org and `helpx.adobe.com` article bodies were unreachable (403,
404 or bot challenge), so funding figures come from company About/press pages
and TechCrunch only, and several are explicitly **not found** rather than
zero. Where a page was JavaScript-rendered, prices were captured through a
rendering proxy of the same URL on the same date; that is noted inline.

## SelekyT's own position, stated before the map

Kept deliberately bare, because the comparison means nothing unless the
baseline is honest. All of this is **VERIFIED** against the repository.

| Claim | Basis |
| --- | --- |
| Story-unit segmentation + multi-signal ranking (hook, payoff, coherence, completeness, repetition, dead air, context guard, virality angle, visual potential) with per-candidate scores and rejection reasons | `engine/story_units.py`, `engine/editorial.py:383` (`rank`), `engine/context_guard.py:148`, `engine/virality.py`; real-run output in `out/url-e2e/project/report.json` |
| Dead-air and pause removal with retention intervals | `engine/pipeline_audio.py`, `engine/audio_fusion.py`, `engine/jumpcut.py`; pinned by `tests/test_cue_times_account_for_removed_pauses.py` |
| 16:9 and 9:16 renders, captions (ASS/SRT), per-cut **CMX 3600 EDL** and **FCPXML 1.11** | `engine/nle_export.py:45` (`FCPXML_VERSION = "1.11"`), `engine/fcpxml.py`; six real exports re-validated by hand after the one real run |
| One real end-to-end run on genuine unscripted speech (NASA 360 Episode 5, 24:29, public domain) | `docs/release-validation.md` |
| Editorial audit passes on that source (cuts between words, no split sentences, cue text matches transcript) | `tools/editorial_audit.py` |
| Zero users, zero revenue, zero investors, zero pilots | `docs/AI_EXECUTION_STATE.md`, `docs/MOAT.md` |
| No NLE has ever opened an exported timeline | `docs/AI_EXECUTION_STATE.md` gap G2 |
| No channel-specific learning exists; the loop is designed, not built | `docs/MOAT.md` — "There is no moat today, and there is no flywheel today" |

The last three rows are the ones that matter most when reading the map below.

---

## 1. The two axes, and the matrix

The market splits cleanly on two axes that SelekyT claims to occupy together.

**Axis A — editorial intelligence.** Does the product decide *what is worth
keeping*, at the level of whole story units, and does it expose *why*? Three
tiers:

* **Rank** — scores and selects discrete content units, and publishes the
  criteria.
* **Select** — picks moments or clips, but without published criteria,
  scoring, or ordering logic.
* **Mechanical** — transcribes, reframes, removes silence, masks, conforms to
  a script. No content judgement at all.

**Axis B — NLE interchange.** Does an output survive as an *editable timeline*
in a professional NLE, and in what formats?

### Matrix (all cells RESEARCHED unless noted)

| Company | Editorial tier | NLE timeline export | Formats |
| --- | --- | --- | --- |
| **Descript** | Select (shorts from "most engaging moments"; no published scoring) | **Yes, broadest** | Premiere XML, **FCPXML**, **EDL**, Audition SESX, Pro Tools/Logic AAF |
| **Adobe Premiere** | Mechanical (explicitly declines to judge) | **Yes** | EDL (CMX 3600), AAF, OMF, legacy FCP7 XML; **cannot import FCP X `.fcpxml`** |
| **DaVinci Resolve** | Mechanical (+IntelliScript script-conformity) | **Yes, fullest set** | FCPXML, CMX 3600 EDL, AAF, Premiere/FCP7 XML, OpenTimelineIO — both directions |
| **Avid Media Composer** | Mechanical at scale (phonetic search) | **Yes** | EDL via List Tool, AAF (first-class), OMF, MXF, OTIO import; **no FCPXML** |
| **Final Cut Pro** | Mechanical (+iPad Montage Maker highlight reels) | **Yes (owns the format)** | FCPXML, now schema **1.13** |
| **Simon Says** | Mechanical (human selects) | **Yes, broadest integration** | FCPXML to FCP; AAF, EDL, SubCap, ScriptSync to Avid; Assemble round-trip to FCP/Premiere/Resolve/Avid/Audition |
| **Reduct.video** | Mechanical (human assembles) | Yes, shallow | FCPXML **1.8** (FCP + Resolve), Premiere plugin; EDL/AAF claimed but not confirmable |
| **Timebolt** | Mechanical (purely subtractive) | **Yes, deep** | FCPXML to FCP (incl. multicam + speed values); XML to Premiere (with free plugin) and Resolve |
| **Gling** | Mechanical (silence/filler/bad takes) | Yes | "XML" to Premiere/FCP/Resolve — variant undocumented |
| **auto-editor** (open source) | Mechanical (silence/motion) | **Yes, widest NLE coverage** | Premiere XML, Resolve, FCP, Shotcut, Kdenlive, clip sequence |
| **OpusClip** | Select (scene virality rating; Hook/Flow/Value/Trend) | Yes, shallow | generic `.xml` + `.srt` only; **no FCPXML, no EDL** |
| **Riverside** | Select (Magic Clips/Magic episode, no scoring) | Yes, narrow | Premiere XML only, Business tier only |
| **VEED.io** | **Rank** (Flow/Impact/Clarity/Relevance, scored out of 10, published) | **No** | MP4 only |
| **Klap** | Select (topic extraction + viral-potential score) | **No** | finished clips only |
| **Kapwing** | Select ("suggests the best sections", no scores) | **No** | MP4 only; MCP returns MP4 only |
| **Mirage (ex-Captions)** | Select (clip moments via chat editor) | **No** | MP4 + SRT only |
| **Vizard.ai** | Select, undocumented | **No** | MP4 only |
| **Runway** | Mechanical (generative; Detect Shots = shot boundaries) | **No** | MP4, ProRes, PNG sequence — never a timeline |
| **AutoCut** (plugin) | Select (AutoViral clip moments, AutoCut Repeat best take) | **No interchange files** — it edits *inside* the host NLE | n/a |
| **Wisecut / Taja / Crayo / Submagic / Pictory** | Select or lower | **No** | MP4/SRT only |
| **SelekyT** | **Rank** (whole story units, published criteria, rejection reasons) **[VERIFIED]** | **Yes** | **FCPXML 1.11 + CMX 3600 EDL, per-cut** **[VERIFIED, but never opened by an NLE]** |

### The one finding that corrects the obvious reading

A research batch concluded that "editorial judgement plus NLE interchange is
uncontested." **That is wrong, and the correction is the most important line in
this document.** Descript occupies both axes at once. It selects content
("generates new compositions based on the most engaging moments it identifies",
accepting "a topic, goal, or criteria" up front) *and* exports five real NLE
timeline formats including FCPXML and EDL. It has $100M of funding, a desktop
and web client, an API and MCP that let Claude or ChatGPT drive its AI editor
without opening the app, and — as of 29 July 2026 — owns the capture layer
through SquadCast.

SelekyT's wedge is therefore not "we have both." It is narrower and must be
stated precisely, or it will be dismissed on sight. See §7.

---

## 2. The required competitors

### OpusClip — opus.pro

* **Target customer.** Creators, then marketing/business teams. Vertical
  landing pages for agencies, church, e-commerce, real estate and media. Pro
  adds a 2–4 user team workspace; Business adds SSO, MSA, SOC II and a
  dedicated Slack channel.
* **Editorial intelligence — Select, and the most developed ranking model
  among pure clippers.** ClipAnything is multimodal: it "analyzes each frame…
  identifying objects, scenes, actions, sounds, emotions, texts, and more,"
  and "each scene is then rated based on its virality potential." A Virality
  Score (0–99) scores each clip on **Hook, Flow, Value, Trend**. Prompt-driven
  clipping and narrative templates steer selection. Crucially it ranks
  *short-form clip moments*, not whole long-form story units — its unit of
  editorial work is the clip, not the story.
* **Automation vs human control.** Not a black box. Starter and above get
  "text & timeline editing", trim/extend, manual reframe, and "reprompt
  clipping" to regenerate from the same video.
* **NLE interop.** Real but the shallowest of the three that have it. Pro tier
  lists "Premiere Pro/DaVinci Resolve export." The download is a ZIP of the
  raw video, a generic `.xml`, and an `.srt`. Documented limits: **Pro Plan
  only**; "we do not support exporting multiple shorts" (one clip at a time);
  "XML export supports videos up to 1.5 hours"; auto-captions "exported as
  overlays and cannot be edited in Premiere Pro"; Resolve imports can leave
  audio channels muted and "there may be slight variations in how these
  editors interpret the xml format." **No FCPXML, no EDL, no AAF, no plugin.**
* **Channel-specific learning.** **No.** The Virality Score is a generic
  content model; docs never claim it learns from your channel's analytics or
  past clip performance. Brand Vocabulary only fixes transcription spelling.
  Business-tier "clip analytics, real-time trend analysis" is analytics *of*
  published clips, not a feedback loop into selection.
* **Pricing (found 2026-09-19).** Free $0 (1080p, 3-day export/storage expiry,
  watermarked captions, 9:16 only) · **Starter $15/mo** · **Pro $29/mo** ·
  Business custom. No annual prices are shown on the page — annual rates
  **UNVERIFIED**.
* **Distribution.** Web app only. Video Editing API + "OpusClip MCP for AI
  Agents", 6 SDKs, public OpenAPI spec; built-in social scheduler to six
  platforms.
* **Funding/scale.** **$30M total** including a Series A led by Millennium New
  Horizons (announced 31 Aug 2024, at which point it claimed 6M users and
  eight-figure ARR). SoftBank Vision Fund 2 invested at a **$215M valuation**
  (Business Insider, March 2025 — headline only, the article itself was not
  fetchable, so the raise amount is **UNVERIFIED**). Self-claimed scale "as of
  December 2025": "16M+ creators and businesses", "440M+ clips created",
  "160B+ total views". Those are marketing claims, not independently verified.
* **Weaknesses.** (a) Interchange is generic XML, Pro-only, one clip at a time,
  1.5-hour cap, captions baked as non-editable overlays — it cannot hand a
  real edit to a professional NLE. (b) Documented customer complaints: charges
  after cancellation, ~20-day refund reviews, credits forfeited after 12
  months, upload failures, caption spelling errors. (c) The free tier's 3-day
  storage expiry makes it a trial, not a usable tier.

### Descript — descript.com

* **Target customer.** Podcasters and video creators first; teams and
  enterprises second (Enterprise: SOC 2 Type II, SSO/SCIM, audit logs).
* **Editorial intelligence — Select.** Its clip tool "generates new
  compositions based on the most engaging moments it identifies" and accepts a
  topic, goal or criteria up front. Underlord, the "AI co-editor", does Edit
  for Clarity, Remove Retakes, Remove Filler Words and Quick Design. It
  removes fluff and picks moments for shorts. **There is no documented
  ranking or scoring system**, and for long-form the editorial default is that
  the human decides the cut.
* **Automation vs human control — the strongest in the set.** Full timeline
  editor, text-based editing, per-tool undo/rollback for every Underlord
  action, templates. Explicitly built for review and override.
* **NLE interop — the best of any competitor.** Five formats: **"Premiere
  (XML), Final Cut Pro (FCPXML), Reaper (EDL), Audition (SESX), or Pro
  Tools/Logic (AAF)"**, with DaVinci Resolve also listed as a destination.
  Available from the Creator plan. Documented limits, and they are severe:
  Sequences and Avatars are excluded, and across **all** formats
  "animation, audio effects, audio keyframes, automatic microfades, clip
  speed, HDR tone mapping, highlights, images, loops, room tone, scenes,
  sequences, shapes, speed, titles, transitions, and video effects" do **not**
  transfer. What does transfer: audio clips, clip trimming, ignored script,
  video clips, Studio Sound. FCP cannot place each file on its own track and
  layers may import in reverse order; AAF splits stereo to mono.
* **Channel-specific learning.** **No.** The full help index contains no
  article on analytics, learning from past videos, or channel performance. No
  virality score.
* **Pricing (found 2026-09-19).** Free $0 (60 media min/mo, 720p, **no timeline
  export**) · Hobbyist $24/mo or **$16/mo annual** · **Creator $35/mo or
  $24/mo annual** — timeline export enabled here · Business $65/mo or
  **$50/mo annual** · Enterprise custom.
* **Distribution.** Desktop (macOS 14+/Windows 11+) and web; **API and MCP**
  ("Edit video through code, or from inside your AI assistant"). On 29 July
  2026 Descript announced **SquadCast became part of Descript**, adding local
  remote recording to 4K with per-participant backups, free for paying
  customers, with full integration planned.
* **Funding/scale.** **"Descript has raised a total of $100M"**, primarily from
  the OpenAI Startup Fund, a16z, Redpoint and Spark Capital. Largest
  disclosed round: $50M Series C, 15 Nov 2022, led by OpenAI Startup Fund;
  valuation ~$550M per The Information (Oct 2022). **User counts: not found.**
* **Weaknesses.** (a) Timeline export silently drops most of the actual edit —
  effects, transitions, titles, speed changes and images do not survive the
  round trip, so the "editable timeline" is a cuts-only skeleton. (b)
  Reliability complaints: "the app crashes or loses saved edits entirely",
  "artificial intelligence credits drain extremely fast", 1080p export moved
  behind a more expensive plan. (c) The clip selection is opaque — no scores,
  no candidate list, no rejection reasons — so an editor cannot audit *why* a
  moment was chosen.

### Riverside — riverside.com

* **Target customer.** Podcast and video-podcast creators first; webinar
  hosts; businesses (SSO with Okta/Azure, SOC2, ISO27001, producer mode).
* **Editorial intelligence — Select.** "Magic Clips" ("AI automatically
  creates short clips from your recording's best moments"), "Magic episode"
  ("AI automatically turns your recording into a polished cut"), "Find Fluff",
  configurable silence/filler removal with "pace and sensitivity" controls, AI
  show notes, AI B-roll. It selects moments and assembles an episode cut; it
  does not rank discrete story units for long-form.
* **Automation vs human control.** Explicitly dual — "Hate editing? Let AI
  handle it. Love editing? Take full control" — with a chat-based AI editing
  agent ("cut out the part about tacos") and timeline cut/split/trim.
* **NLE interop.** Business tier only, "Non-destructive timeline exports". The
  format is a ZIP of media plus "an important XML file" that "can be opened in
  Adobe Premiere Pro, and all of your video and audio tracks will be laid out
  in a sequence all sync together." **No FCPXML, no EDL, no AAF.** The
  Premiere page is the only NLE-named page in the sitemap.
* **Channel-specific learning.** **No.** Grow tier includes podcast hosting
  and consumption analytics; no documented feedback into selection.
* **Pricing (found 2026-09-19).** Free $0 (2h multitrack, 720p, watermark,
  Magic Clips) · **Pro $24/mo annual** or $29/mo monthly · **Grow $34/mo
  annual** or $39/mo monthly · **Webinar $79/mo annual** or $99/mo monthly ·
  Business custom. (The rendered annual figure for Webinar was internally
  inconsistent with the monthly price; treat $79/mo annual as stated.)
* **Distribution — the widest footprint in the set.** Web, Mac app, mobile
  (iPhone/iPad/Android) with local recording to 4K/48kHz, multistream live,
  webinar hosting, podcast hosting, API at Business, HubSpot/Salesforce/Marketo
  integrations, Riverside MCP.
* **Funding/scale.** $9.5M Series A, 22 Apr 2021, led by Seven Seven Six
  (Alexis Ohanian), with Zeev Ventures, Casey Neistat, Marques Brownlee, Guy
  Raz, Elad Gil. **Later rounds: not found. User counts: not found.**
* **Weaknesses.** (a) Timeline export is Business-only (contact-sales) and
  Premiere XML only. (b) A known recording-loss failure mode ("host upload was
  marked complete with 0 bytes") is exactly the worst case for a recording
  tool. (c) Its editorial intelligence is episode-shaped, not story-shaped —
  it assumes the recording *is* the content.

### Captions → now Mirage — captions.ai → mirage.app

* **Rebrand, and it matters.** "Formerly known as Captions, we rebranded in
  2025"; legal entity NOCAP, Inc. d/b/a Mirage. TechCrunch dates the rebrand
  4 Sep 2025 and describes a pivot to "an AI research lab focused on
  multimodal foundational models", unifying the Captions app with Mirage
  Studio for brands.
* **Target customer.** Mobile-first creators; brands and agencies via Mirage
  Studio; enterprises with bulk credit discounts and "Training data
  exclusion".
* **Editorial intelligence — Select, generative-flavoured.** Max tier: "Turn a
  long video into a batch of post-ready clips"; "Access our library of curated
  AI Edit styles"; "Use a chat-based editor to handle changes for you." "AI
  Edit V3" shipped Jan 2026; "Clips Chat" (ask for different clips from the
  same long-form video) Aug 2026. Mirage Edit is "the first AI video editor
  built on top of your AI Twin." It selects clip moments; it does not rank
  story units, and no scoring is documented.
* **Automation vs human control.** Semi-automated — chat-based editor plus a
  timeline and a co-editor; the product flow is one-click AI Edit styles.
* **NLE interop — none, and it is the cleanest gap in the set.** The full help
  index contains **zero** articles on Premiere, Final Cut, XML, EDL, AAF or
  project-file export. The closest item in the release archive is SRT export
  (July 2025). Exports MP4 + SRT only. No plugin.
* **Channel-specific learning.** **None found** in the release-notes archive.
* **Pricing (found 2026-09-19).** Free $0 · **Max $24.99/mo** (500 credits) ·
  Scale 1x $69.99/mo (1,400) · Scale 2x $139.99/mo (2,800) · Scale 4x
  $279.99/mo (5,600) · Enterprise custom. Page caveat: "Features and prices
  reflect iOS plans only." Annual dollar figures are **UNVERIFIED**.
* **Distribution.** iOS, Android, web, macOS (returned July 2026); API;
  real-time project sync; Social Studio publishing.
* **Funding/scale — the best-funded in the creator cohort.** "Over $100
  million in venture capital to date at a valuation of $500 million" (Sep
  2025), plus **$75M growth financing announced 24 Mar 2026 from General
  Catalyst's Customer Value Fund**. The same article cites "downloaded over
  3.2 million times in the last 365 days", **"$28.4 million in in-app
  revenue"** (Appfigures), "more than 200 million videos so far" created, and
  "only 25% of its revenue coming from the U.S." The company plans to "expand
  in high-growth Asian markets" — see §8.
* **Weaknesses.** (a) Zero NLE handoff — MP4/SRT only, a hard blocker for any
  professional workflow. (b) Post-rebuild simplification complaints: "The new
  version is heavily simplified and lacks extra features"; credits allegedly
  taken away; rigid caption styling; web version missing iOS features. (c)
  Strategic attention has moved to foundation-model research, which signals
  the creator tool is no longer the company's sole focus.

### Adobe — Premiere Pro (rebranded "Adobe Premiere" in Jan 2026) and Firefly

* **Target customer.** Professional editors, film/TV post, enterprise media
  teams; prosumers via the mobile app.
* **Editorial intelligence — Mechanical, by explicit design.** Every shipped
  feature is retrieval, per-clip processing, or human-directed execution:
  * Text-Based Editing transcribes; you "highlight text to add clips to your
    timeline"; bulk-deletes pauses and filler words. **The human highlights
    and reorders.**
  * Generative Extend (Firefly) adds frames to clips already placed.
  * Auto Reframe keeps the focal point visible on reframe.
  * Enhance Speech cleans dialogue per clip; a separate Enhance Audio beta
    with dialogue/music/ambience controls was announced 8 Sep 2026.
  * Media Intelligence indexes clip content so you can find shots in natural
    language — it surfaces matches for a query **the human typed**.
  * **AI Assistant, public beta since 23 June 2026, is the one to watch**: it
    "orchestrates and executes" repetitive tasks and will "Build a stringout
    or rough assembly and place organized media on a timeline." But it
    assembles from media the editor already curated, with "Nothing is locked
    or hidden", every action undoable, and Adobe's own tester line that it
    "leav[es] the creative judgment exactly where it belongs, with the
    editor."
* **Automation vs human control — the gold standard.** A full professional
  NLE; every AI action is reviewable and overridable.
* **NLE interop — real, with one hard limit.** Export of EDL (CMX 3600), AAF,
  OMF and legacy Final Cut Pro XML. **The critical detail: Adobe's own
  migration doc states "Final Cut Pro X exports a `.fcpxml` file format that
  Premiere cannot directly import."** So Premiere writes and reads FCP7-era
  XML, never Apple's modern FCPXML. (Verification caveat: `helpx.adobe.com`
  returns 403 to direct fetch, so those Adobe pages are cited at URL level
  with crawl dates rather than full-page reads.) A widely repeated claim that
  Adobe *removed* the Final Cut Pro XML export is **UNVERIFIED with
  substantial counter-evidence** — do not assert it. A feature named "Edit
  Check" could not be found in any Adobe source; **do not cite it as an Adobe
  feature.**
* **Channel-specific learning.** **None.**
* **Pricing (found 2026-09-19).** Premiere single app **US$22.99/mo**
  (annual, billed monthly; includes 25 monthly generative credits); Teams
  US$37.99/mo. Creative Cloud Pro All Apps **US$69.99/mo** (promo US$34.99/mo
  for the first 3 months; 4,000 credits); Teams US$99.99/mo. No perpetual
  licence: "Adobe Premiere is available only as a membership." Firefly: Free ·
  Standard $9.99/mo (2,000 credits) · Pro $19.99/mo (4,000) · Pro Plus
  $49.99/mo (10,000) · Teams Premium $199.99/mo (50,000). Video generation
  burns roughly 100 credits per 5-second clip by Adobe's own worked examples
  (2,000 credits ≈ "20 five-second videos").
* **Distribution.** Creative Cloud desktop and web, mobile apps, Teams and
  Enterprise with admin console and SSO, a large third-party plugin
  marketplace (Runway ships its own Premiere panel), Adobe Stock.
* **Funding.** Adobe Inc. (NASDAQ: ADBE); startup funding metrics do not
  apply.
* **Weaknesses.** (a) Deliberately no story selection — every AI feature
  requires the editor to already know what they want; Adobe sells the
  assistant, never the editor. (b) Subscription-only and heavyweight; the
  single-app generative allowance (25 credits/mo) is trivial against
  ~100-credits-per-5s video generation. (c) Interchange is lopsided: no FCP X
  `.fcpxml` import at all, and EDL/AAF round trips reliably carry only cuts
  and transitions.

### Runway — runway.com (formerly runwayml.com)

* **Target customer.** Individual creators, brand/agency teams, and
  film/TV/media enterprises. Case studies include History Channel, Amazon
  Prime's *House of David*, BBC Studios, Coca-Cola (via Silverside AI),
  Lionsgate.
* **Editorial intelligence — Mechanical, and generative rather than editorial.**
  It decides model routing, prompt interpretation and per-shot generation —
  not what is worth keeping. "Detect Shots" splits clips at scene boundaries
  (shot detection, not story ranking). The Agent can "assemble and arrange
  them into a timeline" from clips **you** upload, and "generate and sequence a
  long-form video shot by shot" — but "from a script or outline", i.e.
  generation from your plan, not selection from existing footage.
* **Automation vs human control.** Edit Studio is a generative editor with a
  real track-based timeline. Runway's own FAQ is blunt: "Traditional
  editors… are non-destructive cut tools — they don't change what's inside the
  clip. Runway's AI video editor works on the content itself." Review
  checkpoint before commit; no keyframe/curve compositing layer system.
* **NLE interop — none, and it is the sharpest contrast in the set.**
  **Runway imports and exports no FCPXML, EDL, XML or AAF.** Help-center
  search for "FCPXML" returns zero results; "supported file types" covers
  media *inputs* only. Export offers exactly three formats — MP4, ProRes
  (.mov, six profiles, "made for editorial and broadcast pipelines") and PNG
  Sequence — with the telling line: "These export formats give you a file that
  drops directly into other video editing tools without transcoding or
  quality loss." **ProRes/PNG export is gated to Max, Unlimited (Legacy) and
  Enterprise.** The free Premiere/After Effects plugin runs the *other*
  direction — a panel inside Adobe where "results land at the playhead, not in
  an export queue" and "no exports, no round trips." No Final Cut Pro
  integration of any kind. A finished Runway edit lands in an NLE as flattened
  media, never as an editable timeline.
* **Channel-specific learning.** **None.** Brand Kits are brand-*visual*
  reference collections folded into a custom system prompt. The Agent page
  says "Share your campaign data and Agent finds what's driving results", but
  per the docs **you bring the data yourself**; there are no ad-platform
  integrations and no persistent tuning from historical performance.
* **Pricing (found 2026-09-19).** Free $0 (125 one-time credits) · Standard
  $15/mo or $12/mo annual (625 credits) · Pro $35/mo or $28/mo annual (2,250) ·
  Max $95/mo or $76/mo annual (9,500, ProRes/image sequences) · Team and
  Enterprise custom, the latter with SSO/SOC 2 and **model-weights
  licensing**. Published per-generation rates: Gen-4.5 = 60 credits/5s; Aleph
  2.0 = 140 credits/5s.
* **Distribution.** Web (primary), iOS and Android, API, Enterprise direct
  sales, model-weights licensing, free Premiere/After Effects CEP plugin,
  Runway MCP with connectors for Claude, ChatGPT, Grok.
* **Funding/scale — the best-funded company in this entire map.** **Series E:
  $315M at a $5.3B post-money valuation, announced 10 Feb 2026**, led by
  General Atlantic, with Nvidia, Fidelity, AllianceBernstein, Adobe Ventures,
  Mirae Asset, Emphatic Capital, Felicis, Premji Invest and AMD Ventures.
  **Total raised $860M.** A founder says the company "added $40 million in
  annual recurring revenue in the second quarter of 2026" — note the wording
  is ARR *added*, and a total-ARR figure was not found. "60m+ creatives" is a
  company marketing claim.
* **Weaknesses.** (a) Zero timeline interchange — the single clearest
  differentiator available against the best-funded competitor here. (b) Not a
  non-destructive editor by its own admission; "The output format is chosen at
  generation time and can't be changed after a video is generated." (c)
  Credit-gated and format-locked (ProRes needs Max at $95/mo), with real
  deprecation churn — Act-One disappeared with Gen-3's retirement on 8 July
  2026.

### Vizard.ai — vizard.ai

* **Target customer.** Creators and small marketing/agency teams. Customer
  logos claim Google, Stanford, Ubisoft, Hopper, Morningstar.
* **Editorial intelligence — the shallowest documented.** "AI-generated clips"
  from long video is the core; 1 credit = 1 minute of video, upload capped at
  600 minutes on paid tiers. The help centre covers manual clip creation and
  scene/subtitle management — **no scoring, no ranking criteria, no
  highlight-selection model documented.** A Trustpilot reviewer states flatly
  "AI editing doesn't really work."
* **Automation vs human control.** "Full access to video editor" on every tier
  including Free, with manual clip creation and scene/subtitle editing;
  override exists, but there is no multi-track timeline or NLE-style surface.
* **NLE interop.** **None found.** The entire help centre has no article on
  timeline editing, Premiere, Final Cut Pro, XML/EDL interchange or
  project-file download. Export articles cover MP4 resolution and Google
  Drive export. **MP4 only (720p free / 4K paid).**
* **Channel-specific learning.** **None found.**
* **Pricing (found 2026-09-19; page is JS-rendered, captured via rendering
  proxy of the same URL).** Free $0 (60 credits/mo, 720p, 3-day storage) ·
  Creator **$29/mo** or **$14.50/mo annual** · Business **$39/mo** or
  **$19.50/mo annual**.
* **Distribution.** Web app; API with "the same abilities as the web app";
  social account connection and publishing; Google Drive export.
* **Funding/scale.** Self-claimed "Trusted by 10 million+ creators &
  businesses" (marketing claim). **Total funding, latest round, investors,
  videos processed: not found.**
* **Weaknesses.** (a) No timeline or NLE export of any kind. (b) Documented
  complaints: "no refund policy whatsoever", unused credits forfeited on
  cancellation, "It is not free as advertised". (c) Real editing limits — "the
  BGM abruptly ends and the Outro is blank and there's no way to extend or
  fade out the music."

### Klap — klap.app

* **Target customer.** Creators and small teams repurposing talk-heavy
  long-form — podcasts, interviews, educational, product reviews — into
  vertical short-form.
* **Editorial intelligence — Select, and the closest to SelekyT's ranking
  thesis in the clipper cohort.** "AI extracts the best topics from your
  YouTube video and automatically edits them into many viral-worthy clips"
  and will "create engaging video clips and score their potential to go
  viral." So it does topic extraction **and** a viral-potential score — but at
  clip level, not whole-story level. Stated yield: ~1 minute of source ≈ 5
  clips.
* **Automation vs human control.** One-click generative. **No timeline editor
  anywhere on the site.** Customisation is clip-level: fonts, colours, logos,
  framing, "AI Reframe 2" layouts, caption styling.
* **NLE interop.** **None.** No FCPXML, EDL, XML, AAF or ProRes on the site or
  pricing page; clips export "in high-resolution" only.
* **Channel-specific learning.** "Analytics" is listed on all plans but its
  contents are undocumented and there is no evidence it feeds selection.
  **UNVERIFIED.**
* **Pricing (found 2026-09-19).** Basic **$14/mo**, Pro **$39/mo**, Pro+
  **$94/mo** — all labelled "billed yearly"; the page renders only
  yearly-billed prices, so monthly prices are **UNVERIFIED**. Plans differ by
  clip volume: 100 / 300 / 1,000 clips per month. The homepage separately
  states "upgrade to Klap Pro for just $29/month", which **conflicts** with
  the pricing page's $39/mo — treat as a discrepancy, not a confirmed price.
* **Distribution.** Web only; social publishing/scheduling; Discord for volume
  deals. No API, desktop or mobile app found.
* **Funding/scale.** Claims "3.5M happy users" and "8.5M clips made by 3.5M
  creators" (marketing claim). **Funding: not found.**
* **Weaknesses.** (a) Zero NLE interoperability — no professional handoff
  path. (b) An inconsistent pricing surface. (c) No timeline — an editor who
  wants to restructure a selection must regenerate it.

### VEED.io — veed.io

* **Target customer.** Creators, marketing teams, enterprises. "VEED for
  Teams" plus Enterprise with SOC 2; logos include Amazon, Google, Netflix,
  Visa, BBC, Meta, NBCUniversal.
* **Editorial intelligence — Rank, and the most transparent in the market.**
  The Clips feature "turns your long-form videos into ready-made short clips"
  with "Clip rating: Identifies and surfaces the most engaging moments", and
  users "will see ratings for different aspects that you can review." The AI
  Clip Generator "analyzes your video to identify compelling quotes and
  content patterns that perform well on social media," evaluating **"flow,
  impact, clarity, and relevance"**, and the FAQ states: "Each generated clip
  receives scores for Flow, Impact, Clarity, and Relevance (rated out of 10)."
  It keeps the human in charge: "the final choice of what resonates with your
  audience is always yours." Requires 1 min–3 hrs of speech. Magic Cut
  (separate) removes pauses, filler, mistakes and silence, is **English
  only**, and "Magic Cut edits are fully editable. You can revert or adjust
  cuts in the timeline."
* **Automation vs human control.** Reviewable, not black-box — real timeline
  editor, per-clip Trim and Edit-into-full-editor, clip favouriting/filtering,
  version control. Also **OpenEdit**, an agent-driven local pipeline that
  composes HTML/CSS and renders to MP4 — notably "There's no GUI and no
  timeline"; you steer it purely by prompting.
* **NLE interop.** **None.** The export documentation names exactly one
  format: **MP4**, with quality presets 720p–4K. No FCPXML, EDL, XML, AAF or
  ProRes appears anywhere in the help centre index, homepage or pricing page.
* **Channel-specific learning.** **No.** Brand Kits are brand *assets*, not
  performance data.
* **Pricing (found 2026-09-19; JS-rendered, captured via proxy).** Free
  (720p, watermarked, Clips one-time use per account) · Creator $12/user/mo
  billed yearly (6,000 AI credits) · Pro $22/user/mo yearly (30,000) · Studio
  $39/user/mo yearly (180,000) · Enterprise custom. **Monthly prices are not
  rendered — UNVERIFIED.**
* **Distribution.** Web app; iOS "LITE" app with project transfer; REST APIs
  integrated through fal.ai with usage-based rates (e.g. Lip Sync 2.0 at
  $0.07/sec); Teams and Enterprise with SOC 2 and SSO.
* **Funding/scale.** **$35M from Sequoia (TechCrunch, 2 Feb 2022)** — its
  first outside money since bootstrapping in 2018. The same (now stale)
  article cites "1 million users" and "annual recurring revenues of $7
  million" and a profitable company with "tens of thousands" of paying
  customers. **No later round found.**
* **Weaknesses.** (a) No timeline interchange — a VEED edit cannot continue in
  Premiere/FCP/Resolve as an editable sequence, which is exactly what its own
  published scoring would be most valuable for. (b) Pricing opacity: monthly
  prices unpublished, and the credit→video conversion is never explained.
  (c) Clip selection is gated (once per account on Free; speech-only sources).

### Kapwing — kapwing.com

* **Target customer.** Marketing, training and internal-comms teams, creators,
  educators. Claims "over 30 million creators" (site claim).
* **Editorial intelligence — Select, with no published criteria.** The
  Repurpose tool is an "AI-powered regeneration tool… suggesting the best
  sections of your video"; it needs a ≥10-minute source and generates 3–5
  clips by default in 9:16 with subtitles and a title. **No scoring system and
  no published ranking criteria** — strictly weaker editorial transparency
  than VEED or Klap. Also "Trim with Transcript" and "Smart Cut" (silence
  removal).
* **Automation vs human control.** Real drag-and-drop timeline with layers and
  overlays; clips move into the full Studio; iterative prompting;
  text-based editing to restore sentences the AI omitted.
* **NLE interop.** **None.** No FCPXML/EDL/XML/AAF anywhere in the help
  centre, pricing page or homepage. The Kapwing MCP can only "Access and save
  exported MP4 videos" — MP4 only.
* **Channel-specific learning.** **No.** Brand Kit, Brand Characters and
  "Custom Kai" (custom AI persona) are brand-*visual* consistency, not
  performance learning.
* **Pricing (found 2026-09-19).** Free $0 (10 credits, watermarked exports,
  ≤4 min, 720p, 30 min/mo export cap, 3-day storage, 3 projects) · **Pro
  $16/mo billed annually** ($192/yr) or $24/mo monthly (1,000 credits, 4K) ·
  **Business $50/mo billed annually** or $64/mo monthly (4,000 credits) ·
  Enterprise custom. FAQ: no free trials for paid plans and "we do not issue
  refunds for subscriptions once you decide to upgrade."
* **Distribution.** Web app; **Kapwing MCP** connector giving Claude/ChatGPT
  direct workspace access (beta, Business/Enterprise only); Teams/Enterprise.
  No native desktop or mobile app found (**UNVERIFIED**).
* **Funding/scale.** **$11M Series A led by CRV (TechCrunch, 24 Sep 2019)**,
  with Village Global, Sinai, Shasta, Jane VC, Harry Stebbings. The same
  (stale) article cited "more than 1 million" users and Pro at $20/mo. **No
  later round found.**
* **Weaknesses.** (a) No NLE interchange. (b) "Best sections" is unexplained,
  3–5 clips, hard to audit or trust at editorial scale. (c) Tight free tier
  and a strict no-refund policy.

---

## 3. Emerging and less-obvious players (2024–2026)

These matter more than the headline names, because several of them already
ship the NLE round-trip that the clip generators lack.

### AutoCut — autocut.com — NLE-native, edits *inside* the host

* **What it is.** "The AI video editing plugin for Premiere Pro & DaVinci
  Resolve." "Works inside Premiere Pro and DaVinci Resolve" with no
  exporting/re-importing, so "Your source quality stays intact." **Final Cut
  Pro is not supported.** Resolve support covers the Free and Studio versions.
* **Editorial intelligence — Select at clip level.** Eleven tools including
  AutoCut Silences, AutoCaptions, AutoCut Podcast (speaker detection →
  multicam angle switching), AutoZoom, **AutoViral** (long → short social
  clips), AutoB-Rolls, **AutoCut Repeat** (detects repeated takes, keeps the
  best), AutoProfanity Filter, AutoChapters, AutoResize. AutoViral and
  AutoCut Repeat are genuine selection, but there is **no ranking, no
  ordering, no story-level selection, no brief-driven rough cut.**
* **Automation vs human control.** Its structural advantage: the edit never
  leaves the NLE. The plugin modifies the sequence in place and the human
  reviews or undoes.
* **NLE interop.** It is a native extension — it produces **no interchange
  files at all**, relying on the host NLE's own format support.
* **Channel-specific learning.** **No evidence found.**
* **Pricing (found 2026-09-19).** Basic $9.90/mo or $6.60/mo annual —
  **AutoCut Silences only** · AI Plan $19.80/mo or $14.90/mo annual — all 11
  features · Enterprise $19.90/mo/license ($238.80/yr, minimum 2). 14-day
  trial. Footer states "AutoCut is an Adobe video partner."
* **Funding/scale.** **Not found** — a French team; no funding, founder,
  revenue or headcount verifiable. **Do not assume any.**
* **Weaknesses.** (a) No FCP support and no interchange output, so its work is
  locked to the host NLE. (b) Task automation, not editorial — it cannot
  select or order a story. (c) Opaque, fast-moving feature and pricing churn
  ("updated weekly"), and a Basic tier that is effectively a single-feature
  throwaway.

### Simon Says — simonsaysai.com — the broadest NLE integration

* **What it is.** Transcript-first transcription/translation/subtitling with
  native extensions inside Premiere Pro, Final Cut Pro and DaVinci Resolve,
  plus Avid Media Composer, Adobe Audition, a macOS app, web app, iPhone app,
  and an **air-gapped on-prem build** (SOC2, TPN, CDSA, MPAA, HIPAA).
* **Editorial intelligence — Mechanical, explicitly human-driven.** Their
  "Assemble" text-based editor is five steps: transcribe → **the user**
  "highlight[s] the soundbites and mark[s] the meaningful parts" → user
  drag/drop reorders → collaborate → export. The AI transcribes; **all
  selection, ranking and story decisions are made by the human.**
* **NLE interop — the strongest integration footprint of the emerging set.**
  FCPXML to Final Cut Pro (and an "SRT to FCPXML Titles" converter); to Avid,
  **AAF, EDL, SubCap and ScriptSync** with an EDL-back-to-Avid rough-cut
  workflow; Assemble exports to FCP, Premiere, Resolve, Avid and Audition
  where "the timeline magically recreate[s] and clips reconnect." The
  advertised FCPXML schema version is **UNVERIFIED** — none is published.
* **Pricing (found 2026-09-19).** Pay As You Go $0/mo + $0.25/min · Starter
  $15/user/mo annual ($20 monthly, 2 hrs/mo) · Pro $33/user/mo annual ($40
  monthly, 4 hrs/mo) · Pro+ $125/user/mo annual ($150 monthly, 30 hrs/mo) ·
  on-prem Standard and Enhanced with a **100-hour minimum commitment**.
* **Funding/scale.** Self-reported: established 2020, 14 employees, 100
  languages, 54,231 users. **Funding: not found.**
* **Weaknesses.** (a) Zero editorial judgement — SelekyT's ranking thesis has
  no analogue here at all. (b) Pay-per-minute economics do not fit huge
  corpora; overage at $0.25/min against 2–30 hr/month allowances is punishing
  for documentary volumes. (c) Round-trip depth varies sharply by NLE — the
  Avid path runs through EDL/SubCap/ScriptSync rather than a true timeline
  conform, and an EDL is single-video-track.

### Reduct.video — the legal/research vertical, with a real but ageing round-trip

* **What it is.** Cloud "video as text": transcribe, search ("Ctrl + F for
  video"), annotate, redact, assemble reels, videoboards.
* **Target customer — precise.** **Legal and public defence is the dominant,
  case-study-backed vertical**, qualitative research second. 12 of 18 case
  studies are legal/public defense (Colorado State Public Defender — 1,000+
  staff; Missouri State Public Defender; Federal Public Defender CDCA;
  Michigan SADO; several county defenders). There is a "Film & production" nav
  entry but **no documentary case studies** — that page is logo strips only.
* **Editorial intelligence — Mechanical, human-directed.** Search, AI
  summaries with traceable timestamps, human drag-and-drop reel assembly,
  videoboards for human sequencing. **Redaction is explicitly not automatic:**
  "Reduct.Video doesn't automatically detect faces and PII." No ranking, no
  story selection.
* **NLE interop — real, but two schemas behind.** A native **Premiere
  extension** pulls a Reduct reel in "to serve as a baseline rough cut"
  (Premiere 15.4.0+, imports as a nest, captions as SRT, title slides as
  comment markers). To FCP and Resolve it exports an option literally
  labelled **"Final Cut Pro X (xml 1.8 & srt)"** — i.e. **FCPXML 1.8**, not
  1.11 or 1.13. **EDL and AAF are advertised on one product page but appear
  nowhere in the integrations page or help centre — treat as not shipping.**
  Limitations: one-directional only; "The XML export does not include any
  title slides"; manual media relinking with exact filename matching required.
* **Channel-specific learning.** **No evidence found.**
* **Pricing (found 2026-09-19).** Personal $12/editor/mo (120 transcription
  hrs/yr, 720p) · Professional $40/editor/mo (300 hrs/yr, timeline + multicam
  sync + redaction, 2K, API) · Enterprise from $75/editor/mo (4K, SSO/SAML,
  SOC 2 Type II + annual pentest, GDPR/CCPA). Allowances are annual and pooled
  per editor — 120 hrs/yr ≈ 10 hrs/month.
* **Funding/scale.** **$4M announced 11 Feb 2021 (TechCrunch)**, led by
  Greylock and South Park Commons; angels include Dylan Field (Figma CEO),
  Armando Mann, Elad Gil. Founders Prabhas Pokharel (CEO), Robert Ochshorn
  (CTO). CEO framing: an "Iron Man suit" making editors better, not full
  automation. **No later round, revenue or headcount found.**
* **Weaknesses.** (a) A shallow, fragile round-trip — FCPXML 1.8 only, title
  slides lost, manual relinking, one-directional, with EDL/AAF unverifiable.
  (b) Manual redaction with no face/PII detection in the vertical that needs
  it most. (c) Multicam sync is metadata/filename-based, not timecode or
  waveform — it fails on footage with stripped metadata.

### Blackmagic DaVinci Resolve — the interchange reference implementation

* **Current version, verified from Blackmagic's own live API: DaVinci Resolve
  21.1.0 build 14**, identical for free and Studio across mac/Windows/Linux.
  (Wikipedia is stale on this — it stops at Resolve 20.)
* **Editorial intelligence — Mechanical, with one near-exception.** A
  full-text search of the 4,444-page official Resolve 21 Reference Manual
  finds **no "smart edit", no "AI cut points", no "auto edit"** feature. What
  exists:
  * Scene Cut Detection — "automatically analyze and split up an edited video
    into individual clips." Shot-boundary detection on *already-edited*
    material; "Dissolves and other transitions are not automatically
    detected."
  * **IntelliScript — the closest thing to editorial AI in any NLE here:**
    "uses the Transcription Engine to match and generate a timeline using an
    original script… quickly assemble rough cuts of scenes and even organizes
    multiple takes for you in the timeline." But the manual is explicit about
    its ceiling: **"IntelliScript chooses the 'best' take by matching the
    spoken dialog only. It does not use any good take metadata or make
    judgments based on anything other than dialog matching."** It also
    requires the human to supply the script and pre-select the clips.
  * The rest of the AI Tools menu is mechanical: Speech Generator, Dialogue
    Matcher, Audio Assistant, Audio Classification, Create Subtitles from
    Audio, Create ADR Cues, Voice Convert/Training, Remove Motion Blur,
    Analyze for Slate. v21 adds IntelliSearch, AI CineFocus, AI Face Age
    Transformer/Reshaper, AI UltraSharpen, AI Slate ID.
* **Automation vs human control.** High human control — every AI feature is a
  tool invoked on human-selected media; nothing assembles unsupervised.
* **NLE interop — the fullest set in the market.** From the official manual:
  **CMX 3600 EDL** import and export ("DaVinci Resolve supports the CMX 3600
  format for EDL import and export"), plus DEDL from Smoke/Flame; **FCPXML /
  Final Cut Pro XML** import and export (the Deliver page has a "Final Cut Pro
  7 or X XML" preset; the FCPX path is described as "exporting an FCPXML file
  from the Edit page"); **AAF** import and export; **Premiere XML**; **OpenTimelineIO
  (.otio/.otioz)** both directions, presented as the preferred interchange
  ("if supported. If not supported, fall back to using XML or AAF"); `.drt`
  import/export; `.adl` (AES31) **import only**. Limits: EDL is single
  video-track with all transitions flattened to cross dissolves; multi-track
  EDL requires per-track export then "Import > EDL to New Track." **The manual
  never names an FCPXML schema version — UNVERIFIED.**
* **The entire AI stack is Studio-only.** Manual section headings marked
  "(Studio Version Only)": Audio Transcription and Text Based Editing, Detect
  Speakers During Transcription, Export Audio Transcriptions, Smart Reframe,
  Magic Mask v2, Scene Cut Detection, IntelliScript. Free Resolve gets none
  of it. Built-in transcription covers 15 languages, with more via the
  Extended AI Transcription Support Package.
* **Pricing (found 2026-09-19).** **Free** download; **DaVinci Resolve Studio
  $295 one-time purchase, no subscription.** Hardware separate (Speed Editor
  $435, Editor Keyboard $669, Micro Panel $559, Advanced Panel $34,665).
* **Funding/scale.** Blackmagic Design is the manufacturer; Resolve is one
  product line. The strongest signal is release cadence — 21.1.0 build 14
  shipping across five platforms on 2026-09-19 with a 4,444-page manual in
  active revision.
* **Weaknesses.** (a) The whole AI/transcription stack is paywalled behind
  $295, and even there IntelliScript is dialog-matching, not judgement. (b) No
  editorial selection whatsoever — SelekyT's core thesis has no Resolve
  equivalent. (c) Interchange depth is uneven and undocumented by version, and
  the EDL path flattens transitions.

### Avid Media Composer — mechanical AI at enterprise scale

* **Target customer.** Broadcast news, episodic and feature post, enterprise
  media organisations on shared storage (NEXIS); MediaCentral is the newsroom
  platform.
* **"Avid Ada" does not appear to exist.** Roughly 35 URL attempts across
  avid.com, Wikipedia and trade press returned nothing. **Treat it as
  unconfirmed and do not cite it as a product.** If a deck mentions "Avid
  Ada", it is describing a gap.
* **Ownership correction.** Avid was **acquired by STG (Symphony Technology
  Group) for $1.4B in November 2023 and taken private**; it was formerly
  Nasdaq: AVID and no longer trades. No public financials exist to track.
* **Shipping AI (current version 2026.8, corroborated by live Avid Community
  forum threads dated 18 Sep 2026).** ScriptSync AI ("Match transcripts to
  audio waveforms for instant dialogue search"), PhraseFind AI ("Search spoken
  dialogue phonetically across hundreds of hours of footage"), native
  speech-to-text, SubCap automation, Speaker ID, Transcription Tool, and —
  new in 2026.8 — **PhraseFind Multicam Auto Cut** (multicam auto-cut by
  speaker). MediaCentral (separate product) adds object/scene detection,
  AI-generated summaries and on-prem translation.
* **Editorial intelligence — mechanical, at scale.** Its differentiator is
  *scale* (phonetic search across hundreds of hours of unscripted footage),
  not *judgment*: no story ranking, no selection, no brief-driven rough cut.
* **NLE interop.** EDL via the List Tool (the CMX 3600 variant specifically is
  **UNVERIFIED** — docs say "EDL" without naming it); **AAF first-class**
  (Avid co-developed the format); OMF/OMFI; MXF; **OpenTimelineIO import added
  2025.6**. **FCPXML / generic XML: not found in any reachable source.**
  Media Composer has no documented native FCPXML import/export; the bridge is
  third-party (XtoCC, EditReady).
* **Pricing (found 2026-09-19).** Media Composer First **free** · Standard
  **$259.99/yr** · **Ultimate $539.99/yr** — ScriptSync AI and PhraseFind AI
  live here and nowhere else · Enterprise $899.99/yr. Perpetual licences exist
  but on an unreachable page — price **UNVERIFIED**.
* **Weaknesses.** (a) The most expensive AI access in the market — Ultimate
  only at $539.99/yr, versus $295 once for Resolve Studio or base-subscription
  inclusion in Premiere. (b) Ecosystem isolation: no native FCPXML in a market
  where FCPXML is the common currency between NLEs, colour and audio apps.
  (c) No story-level intelligence, and behind Adobe and Blackmagic on
  text-based-editing maturity.

### Apple Final Cut Pro — owns the format SelekyT writes

* **Current version: Final Cut Pro 12.3** (Apple user guide versioned 12.3,
  minimum macOS 15.6). Release line: 11.0 Nov 2024 → 11.1 Mar 2025 → 11.2 Sep
  2025 → 12.0 Jan 2026 → 12.3 current.
* **Editorial intelligence — Mechanical.** 12.3 ships Generate Captions
  (**US English only**, Apple silicon), Auto Mask, AI Match Color, and **Edit
  Detection** — "Analyze any previously edited video to reveal its edit points
  and automatically split it into separate clips", which is reverse-engineering
  cuts someone else already made. Also Transcribe to Captions, Transcript
  Search, Visual Search, Smart Reframing, Object Tracker, Magnetic Mask, Beat
  Detection. **The closest thing to selection is iPad-only "Montage Maker"**,
  which "automatically selects the best visual highlights from your footage
  and cuts them to the beat of any song" — moment-level selection with no
  story reasoning. **Magnetic Timeline is a UX paradigm, not AI.**
* **Automation vs human control.** Highest human control of any product here.
* **NLE interop — Apple owns FCPXML.** **Current schema is 1.13**, introduced
  with Final Cut Pro 11.0 on 13 Nov 2024; prior documented versions 1.10 (FCP
  10.6) and 1.12 (FCP 10.8). **FCPXML 1.11 is the FCP 10.7-era schema.** Apple's
  FCPXML developer reference was unreachable from here (all paths 404), so
  whether FCP 12.3 still *imports* a 1.11 document is **UNVERIFIED**. Apple's
  own third-party ecosystem page names timeline-exchange tools X2Pro, EDL-X,
  Automatic Duck, EditReady, Intelligent Assistance.
* **Pricing (found 2026-09-19).** **$299.99 one-time** (Mac App Store); Apple
  Creator Studio $12.99/mo or $129/yr (students $2.99/mo or $29.99/yr).
* **Weaknesses.** (a) Mac/iPad only, with Apple-silicon gating on the newest
  AI features. (b) No editorial intelligence at all — transcription is
  captions-only (US English), no text-based-editing timeline like
  Resolve/Premiere, no ranking or selection. (c) A locked interchange
  ecosystem versioned at Apple's pace, with thin third-party documentation.

### Gling — gling.ai — the cleanest rough-cut-plus-round-trip play

* **Target customer.** YouTubers and creators making talking-head content;
  desktop app (macOS/Windows).
* **Editorial intelligence — Mechanical, micro-level.** Transcribes, then
  auto-removes silences, filler words and bad takes — it decides what is *not*
  worth keeping at sentence/phrase level. **No story-unit ranking, no
  reordering, no selection of what to keep.**
* **Automation vs human control.** Both — there is a timeline; users "refine
  manually by adjusting parts of the text or removing clips from the
  timeline", with undo.
* **NLE interop — verified, and notable for its honesty.** Pricing page lists
  "XML to Premiere/Final Cut Pro/Resolve" as a paid-tier feature, and the free
  tier FAQ states "you won't be able to export to Premiere, Final Cut or
  Resolve." The format is described only as "XML" — **never FCPXML or EDL**, so
  the exact variant is undocumented. Multicam/separate-mic workflows are
  "only if you aim to export your file to either DaVinci Resolve or Final Cut
  Pro" (Premiere/MP4 require pre-synced media).
* **Pricing (found 2026-09-19).** Free $0 (watermarked, no NLE export) · Plus
  $20/mo or $10/mo annual · Pro $40/mo or $20/mo annual · Elite $100/mo or
  $50/mo annual.
* **Funding/scale.** **Funding not found.** Only creator testimonials with
  184K–2.2M subscribers.
* **Weaknesses.** (a) Cuts are micro-level only — near-zero overlap with
  SelekyT's story-unit thesis. (b) A vaguely specified interchange format with
  no documented FCPXML/EDL path or per-NLE import steps. (c) Multicam and
  separate-audio constraints on the Premiere path limit pro workflows.

### Timebolt — timebolt.io — the interchange benchmark

* **Target customer.** Podcasters, streamers, agencies; regulated industries
  via the on-prem "Vault" tier. Desktop, Mac + Windows, "Local-first editing ·
  No cloud upload."
* **Editorial intelligence — Mechanical and explicitly subtractive.** Branded
  "The Video Subtractor"; detects silence, filler and bad takes by audio
  waveform threshold and creates jump cuts. **No story-unit ranking, no
  selection, no ordering — it only removes.** Claimed speed: an hour of video
  in 13 seconds with ".01s control."
* **Automation vs human control — a strong override model.** Full cut timeline
  with keyboard overrides ('L' playhead, 'O' toggle scenes, 'S' split, 'P'
  punch-in), configurable dB threshold, min silence duration, ignore window,
  left/right pad, and "Create Splits but don't delete detected silence."
* **NLE interop — the most specifically documented in the entire map.**
  * **Final Cut Pro: FCPXML**, double-click to import; FastForward/Punch speed
    values carry over; multicam via "FCPXML Multi-Cam."
  * **Premiere Pro: XML / XML Sequence**, plus a **free Premiere extension**
    (.zxp) that applies JSON cut data to an existing timeline — but
    single-file only, and speed ramps do not carry via JSON.
  * **DaVinci Resolve: XML** via File > Import > Timeline, with explicit
    FPS/clip-attributes steps; JSON integration single-file only; multicam
    must use XML.
  * **No EDL anywhere.**
* **Pricing (found 2026-09-19).** Free $0 (watermarked, "No XML / NLE export")
  · Pro Monthly $17/mo · Pro Annual $97/yr · **Pro Lifetime $347 once** · Teams
  $97/seat/yr · Vault (on-premise) custom. UMCHECK filler-word removal is
  pay-per-use at $0.03/min of video.
* **Funding/scale.** **Funding not found.** Claims **"11,700+ paid
  customers"**; operating since 2019, St. Paul, Minnesota.
* **Weaknesses.** (a) Purely subtractive — the opposite end of SelekyT's
  axis. (b) The JSON plugin paths are single-file and drop speed effects,
  pushing multicam back to plain XML. (c) Desktop-only, no API, no cloud — it
  cannot serve automated or pipeline customers.

### Taja.ai — taja.ai — the unverified ranking claim

* **Target customer.** Solopreneurs, realtors, congregations, podcasters,
  small agencies — YouTube-first creators wanting one-click multi-platform
  repurposing.
* **Editorial intelligence — Select, with the closest thing to a "ranking"
  claim in the SaaS clipper cohort, but unexplained.** Pricing page: "Virality
  Score — Get clips ranked based on their ability to go viral using our
  proprietary algorithm," plus AI clipping "by transcript, emotions, objects
  in video." The homepage tempers this to selection by "emotional tone, pace,
  punchlines, and retention spikes." It selects **segments**, not story units,
  and does not reorder narrative.
* **NLE interop.** **None found** — only MP4 and captions.
* **Channel-specific learning — implied only, and this is the cautionary
  tale.** The pricing page lists "Real-time trend analysis," "Video
  Optimization Analysis" and "Backlog Boost"; the homepage says "Taja will
  analyze your channel information and brand voice." **No page states that the
  channel's own performance data is ingested to tune future edits.** The
  Virality Score is a content-signal heuristic, not a channel-performance
  feedback loop. **Mark channel-learning claims UNVERIFIED.**
* **Pricing (found 2026-09-19).** Starter $19.99/mo ($15.99/mo annual) ·
  Professional $49.99/mo ($39.99/mo annual) · Teams/Agency $99.99/mo for 5
  users ($79.99/mo annual) · 7-day free trial.
* **Funding/scale.** **Not found.** Founder identified only as "Ibrahim"; the
  about page claims he built Kevin Garnett's KGCertified channel "from 0 to
  115k subscribers in 14 months." No investors disclosed.
* **Weaknesses.** (a) A proprietary score that is unexplained and
  unverifiable. (b) Zero NLE interoperability — terminal MP4s. (c) No evidence
  of true analytics-driven learning, despite the marketing; the ICP mix
  (realtors, congregations) suggests a performance-marketing business rather
  than a tooling business.

### Others worth one line each

* **Wisecut** (wisecut.ai) — standalone cloud AI editor; AI highlight
  detection, storyboard editing, Autocut Silence, Auto Punch-in, **Autopilot**
  scheduling, **WISH** ("describe the edit you want in plain words"). **No NLE
  export found.** Free / Starter+ $23.25/mo annual / Professional+ $83.25/mo
  annual. Self-claimed "1M+ creators"; funding not found.
* **Submagic** — a caption and stylisation utility, honestly assessed: near
  zero on the editorial axis (auto-captions, translation, auto-zoom, B-roll,
  plus "AI Remove bad takes & silences" and text-based trimming). **No NLE
  interchange whatsoever.** Starter $19/mo ($12 annual) · Pro $39/mo ($23) ·
  Business $69/mo ($41). Homepage claims "Join 4M+ businesses" — implausible
  as stated, treat as unverifiable marketing; Y Combinator and PSG logos
  appear in the logo bar with **no stated role**.
* **Pictory** — funded incumbent, but generation-first (script-to-video,
  URL-to-video, AI avatars, dubbing) with "Auto Highlights & Clips" and
  Enterprise AI chapters as its only selection. **No NLE interchange — its
  only interchange-style export is SCORM for LMS.** Starter $29 / Professional
  $59 / Team $199 / Enterprise custom (annual $25/$35/$119). **Funding:
  $2.1M seed announced 4 Jan 2022, led by FUSE**, with Voyager Capital and
  Seattle angels; no later round verifiable. Launched 2020 — not emerging.
* **Crayo AI** — a generator (fake-text, Reddit, split-screen, AI voiceovers,
  face swap), not an editor. **No NLE path at all.** Hobby $13/mo · Clipper
  $27/mo · Pro $55/mo (annual billing, credits do not roll over, no refunds).
  Funding not found; the /about page 404s.
* **2short.ai** — live and actively updated, but a thin clip extractor with no
  documented timeline export. Free 15–30 min/mo (page self-contradicts) · Lite
  $9.90/mo · Pro $19.90/mo · Premium $49.90/mo.
* **Recast (recast.studio)** — webinars/podcasts → clips plus show notes.
  Free / Starter $14/mo / Professional $26/mo. No NLE interchange or ranking
  details; funding/users not disclosed.
* **CapCut / CapCut Pro (ByteDance)** — **could not be verified directly**:
  capcut.com/pricing returned HTTP 451 (geo-blocked) from this environment. It
  is a full NLE with a native timeline, but current AI auto-cut specifics, Pro
  pricing, and FCPXML/EDL import/export are **UNVERIFIED — do not guess.**

### Open-source comparables — included because one of them is SelekyT's ancestor

* **auto-editor** (github.com/WyattBlue/auto-editor) — **VERIFIED via the
  GitHub API on 2026-09-19**: 5,248 stars, last pushed 2026-09-13, not
  archived, public domain (The Unlicense). "Automatically editing videos by
  analyzing with a variety of methods, including audio loudness, motion, and
  subtitles." Its README documents `--export premiere`, `--export resolve`,
  `--export final-cut-pro`, `--export shotcut`, `--export kdenlive` and
  `--export clip-sequence`. **Mechanical only — silence, motion and subtitle
  cutting, zero editorial ranking — but its NLE coverage (four NLEs plus a
  clip sequence) is wider than SelekyT's two formats, and it is free.**
* **cobanov/autocut** — **VERIFIED via the GitHub API on 2026-09-19**: 63
  stars, last pushed 2026-08-31, not archived, no OSI licence ("Drop a video,
  remove silences, export MP4 or DaVinci-friendly FCPXML. Rust + Tauri +
  Svelte 5"). This is SelekyT's own ancestor: `docs/autocut-provenance.md`
  records that its `cutlist.rs`, `export_fcpxml.rs`, `export_mp4.rs`,
  `timecode.rs` and related modules were reused with the author's explicit
  permission, and `docs/autocut-parity.md` pins behavioural parity against
  them. **SelekyT's FCPXML stack is not proprietary origin**, and any moat
  claim built on "we export FCPXML" has to survive that fact.

### Status changes worth recording

* **Munch pivoted.** getmunch.com/pricing 301-redirects to munchstudio.com,
  which is now a *done-for-you* social-media management platform ("Munch
  Studio's AI handles your social media presence for you") aimed at SMBs and
  agencies. The original clip-repurposing product appears deprecated; no
  export formats, no pricing tiers, no funding on the landing page. **Flag as
  repositioned.**
* **Captions rebranded to Mirage** (Sep 2025) and pivoted toward
  foundation-model research.
* **Descript acquired SquadCast** (29 July 2026), folding capture into edit.
* **Adobe rebranded Premiere Pro to "Adobe Premiere"** with 26.0 in January
  2026, dropping the "Pro" suffix after 23 years; 26.5 is stable as of
  September 2026.
* **AutoPod** (autopod.net) — **status unverifiable from this host.** The site
  refuses connections (ECONNREFUSED) on both http and https; the Wayback
  Machine is tool-blocked here, and Adobe Exchange is a JS shell. **There is
  no evidence available for whether AutoPod is live, acquired or shut down.**
  A human browser check is required before any claim about it.
* No acquisition or shutdown was found for Gling, Timebolt, Taja, Crayo,
  Submagic, Pictory, 2short, Recast or Wisecut; all sites were live and
  operating on 2026-09-19.

---

## 4. Channel-specific learning — the axis nobody occupies

**Not one company in this map ingests a channel's own performance or
retention analytics to tune future editorial selection.** The evidence is
absence-of-evidence, gathered by reading full help-centre indexes rather than
marketing pages, and it is consistent across all 20+ products:

* OpusClip's Virality Score is a generic content model (Hook/Flow/Value/Trend)
  that never claims to learn from your channel; Business-tier "clip analytics"
  measures published clips, it does not feed selection.
* VEED publishes scores but has no analytics→selection path.
* Runway's Agent will "find what's driving results" only from data **you
  upload yourself**; there are no ad-platform integrations and no persistent
  tuning.
* Riverside's analytics are podcast consumption reporting.
* Taja *implies* it and cannot evidence it.
* Resolve's 4,444-page manual never mentions channel or performance analytics
  at all. Neither do Avid, Adobe, Apple, Simon Says, Reduct, Gling, Timebolt,
  AutoCut, Klap, Kapwing, Vizard or Mirage.

**ASSUMPTION:** this axis is genuinely unoccupied because it requires
post-publish performance data plus a feedback loop into selection — a harder
data problem than clipping, and one that only a company with real distribution
can solve.

The uncomfortable counterpart, from SelekyT's own records: **SelekyT does not
occupy it either.** `docs/MOAT.md` is explicit — "There is no moat today, and
there is no flywheel today… a deterministic, entirely local editorial pipeline
whose every run emits a rich, well-provenanced *feature* record and a
completely empty *label* record," with zero users, zero channels and zero
performance observations. So the honest statement is: **the axis is open, and
SelekyT is the only product in this map whose architecture was designed to
capture it — but it has captured nothing yet.** An unclaimed axis is an
opportunity, not a moat.

---

## 5. Pricing landscape

All prices found 2026-09-19 on the cited pricing pages.

| Company | Entry paid price | Mid tier | Top published tier | Model |
| --- | --- | --- | --- | --- |
| OpusClip | $15/mo | $29/mo | custom | subscription, credits not published |
| Descript | $16/mo annual ($24 monthly) | $24/mo annual ($35) | custom | media minutes + AI credits |
| Riverside | $24/mo annual | $34/mo annual | custom | subscription |
| Mirage (ex-Captions) | $24.99/mo | $69.99/mo | $279.99/mo | credits |
| Adobe Premiere | $22.99/mo | — | $69.99/mo (All Apps) | subscription + credits |
| Runway | $12/mo annual ($15) | $28/mo annual ($35) | custom | credits |
| Vizard | $14.50/mo annual | $19.50/mo annual | $19.50/mo annual | credits |
| Klap | $14/mo (yearly only) | $39/mo | $94/mo | clips/mo |
| VEED | $12/user/mo annual | $22 | $39 | AI credits |
| Kapwing | $16/mo annual ($24 monthly) | $50/mo annual | custom | credits |
| AutoCut | $6.60/mo annual | $14.90/mo annual | $19.90/mo/license | per-license |
| Simon Says | $15/user/mo annual | $33 | $125 | transcription hours |
| Reduct | $12/editor/mo | $40/editor/mo | from $75/editor/mo | annual pooled hours |
| DaVinci Resolve | **$0** | — | **$295 once** | one-time |
| Avid MC | **$0** (First) | $259.99/yr | $899.99/yr | annual |
| Final Cut Pro | **$299.99 once** | — | $12.99/mo (Creator Studio) | one-time |
| Gling | $10/mo annual | $20/mo annual | $50/mo annual | subscription |
| Timebolt | $97/yr (~$8.08/mo) | — | **$347 lifetime** | one-time option |
| Taja | $15.99/mo annual | $39.99/mo annual | $79.99/mo annual | subscription |
| Wisecut | $23.25/mo annual | — | $83.25/mo annual | credits |
| SelekyT | **no price exists** | — | — | unfunded, no users |

Two observations. **[ASSUMPTION]** First, the creator cohort has settled at
$10–$30/mo with a ceiling around $50–$100 (Gling Elite $100/mo, Timebolt's
$347 lifetime is the outlier) — a band far below what an enterprise editorial
seat can support, which is where SelekyT's professional-NLE thesis would have
to be priced. Second, the two most serious professional tools are the two
cheapest: Resolve Studio at $295 once and FCP at $299.99 once. Any SelekyT
price above those has to be justified by editorial judgement, not by editing
capability, because editing capability is commoditised at roughly $300 for
life.

---

## 6. Where SelekyT could plausibly win

Stated as hypotheses with the evidence each one rests on, not as claims.

**1. Ranked story-unit selection with published criteria and auditable
rejection reasons. [VERIFIED that SelekyT does this]** SelekyT groups the
transcript into complete thoughts (`engine/story_units.py:421`), scores
candidates on named signals, and writes rejection reasons into
`report.json` — and `tools/editorial_audit.py` independently checks that cuts
land between words and no sentence is split. Nothing in this map publishes
its editorial reasoning. VEED publishes *scores* (Flow/Impact/Clarity/Relevance)
but not criteria or rejections; OpusClip publishes dimensions (Hook/Flow/
Value/Trend) but not why a candidate was dropped; Descript publishes nothing
at all. **This is the one axis on which SelekyT is demonstrably ahead — but
only of the documentation of judgement, and only on one source.**

**2. The combination of ranking *whole story units* with editable-timeline
export. [VERIFIED for SelekyT's output; RESEARCHED that no competitor has
both]** Descript is the exception that proves the rule and is treated
separately in §7. Among everyone else, the ranking products (VEED, Klap,
OpusClip, Taja, Mirage) emit terminal MP4s, and the interchange products
(Resolve, Avid, Timebolt, Gling, auto-editor, Simon Says) do mechanical or
human-directed cutting only. SelekyT emits FCPXML 1.11 + CMX 3600 EDL per cut
from the same interval list it rendered — a property nobody between those two
camps has.

**3. Local-first, private, provenance-bearing processing. [VERIFIED]**
SelekyT transcribes locally with faster-whisper, keeps media in the project
tree, and emits provenance (`transcript.json` carries `asr` provenance;
`audio_truth.json` carries VAD backend and `fallback_reason`). Simon Says and
Reduct charge per transcription minute and ship cloud or on-prem-enterprise
tiers to get there; Timebolt is local-first but has no editorial layer.
**ASSUMPTION:** for news, documentary, legal and enterprise work this is a
procurement requirement rather than a feature, and it is the segment where
per-minute SaaS economics fail (Simon Says $0.25/min overage; Reduct's pooled
annual hours).

**4. Language preservation as a first-class property, not a dubbing feature.
[VERIFIED that the code does this; ASSUMPTION that it is commercially
meaningful]** SelekyT preserves the detected source language and supports
`--language te`/`hi`/`ta`. Most competitors' language story is *translation
and dubbing* — i.e. change the language to English. Resolve's built-in
transcription covers 15 languages with an extended pack; FCP's Generate
Captions is **US English only**; Avid's translation is MediaCentral and
on-prem only. **ASSUMPTION:** non-English-first markets are where the
incumbents' AI features are weakest, and SelekyT's Telugu/India testing
advantage is a genuine head start — but see §8, because a funded competitor
is already moving that way.

**5. The unoccupied learning axis (§4), if and only if it is ever built.**
`docs/MOAT.md` documents that the feature half of the loop already exists and
the label half does not. **ASSUMPTION:** whoever captures post-publish
performance data tied back to editorial decisions owns the defensible asset.
SelekyT is the only product here architected for it, and the only one with
zero users, so this is a race against OpusClip's 440M clips as much as against
any feature list.

## 7. Where SelekyT would lose — honestly, per competitor

**To Descript, on the exact thesis.** Descript already ships AI selection
*and* five NLE timeline formats including FCPXML and EDL, with a real
timeline, per-tool undo, $100M of funding, an API and an MCP that lets Claude
or ChatGPT run edits unsupervised, and now the recording layer via SquadCast.
SelekyT would lose on breadth, polish, distribution and capital. Its only
surviving wedge is that Descript's selection is clip-level and undocumented —
no candidate list, no scores, no rejection reasons — and its export drops
effects, transitions, titles, speed and images. That wedge is real but narrow,
and Descript could erase it in a single release.

**To OpusClip, on data and distribution.** OpusClip has produced a
self-claimed 440M clips and rates every scene for virality at a scale SelekyT
cannot approach with zero users. It already exports XML to Premiere and
Resolve. It is one FCPXML/AAF export away from occupying the whole quadrant.
SelekyT would lose on scale, brand, price ($15/mo entry) and data.

**To Adobe, on "free and native."** Adobe's AI Assistant (public beta since
23 June 2026) already "build[s] a stringout or rough assembly and place[s]
organized media on a timeline" *inside the NLE*, with zero interchange
problem, zero migration cost, and Adobe's explicit "creative judgment…
with the editor" positioning that professional editors trust. For the exact
professional-editor ICP SelekyT targets, Adobe solves the surrounding problem
(first-pass assembly) without ever claiming to solve the core one (what is
worth keeping). SelekyT would lose to that on trust, distribution and price.

**To Resolve and Final Cut Pro, on interchange depth and price.** Resolve
imports and exports CMX 3600 EDL, FCPXML, AAF, Premiere XML and OpenTimelineIO
in both directions, with 4,444 pages of documentation, for $295 once — or $0
for everything except the AI stack. FCP owns the FCPXML format SelekyT writes
and is at schema 1.13 to SelekyT's 1.11. SelekyT's export is narrower, its
documentation of import steps nonexistent, and **no NLE has ever opened one of
its files** (`docs/AI_EXECUTION_STATE.md` gap G2). On interchange, SelekyT is
behind a free product, not ahead of the market.

**To Timebolt and Gling, on the subtractive job.** For silence and filler
removal specifically — a large part of what SelekyT's dead-air stage does —
Timebolt ships deeper NLE round-trip (FCPXML with multicam and speed values,
XML with a free Premiere plugin), a lifetime price of $347 and 11,700+ claimed
paid customers. SelekyT's editorial layer is the only thing that
differentiates it here, and for a customer who only wants the subtraction,
that layer is overhead.

**To Simon Says, on the enterprise/security segment.** Air-gapped on-prem,
SOC2/TPN/CDSA/MPAA/HIPAA, native extensions in four NLEs, Avid ScriptSync and
SubCap round-trips, 54,231 self-reported users. SelekyT has no compliance
posture at all.

**To Runway, on capital and ambition.** $860M raised, $5.3B valuation, and a
stated trajectory toward world models. If AI-generated video substitutes for
edited source video rather than complementing it, the entire editorial
category shrinks. SelekyT has no counter to that risk except the thesis that
edited *existing* footage remains the dominant volume — which is an
**ASSUMPTION**, not evidence.

**And to every one of them, on validation.** SelekyT's editorial judgement is
measured on **one** source, three clips, one audit pass. `docs/AI_EXECUTION_STATE.md`
states this itself: that is "evidence the machinery is sound, not that the
choices would survive comparison with a human editor across sources and
languages."

---

## 8. The single most dangerous competitor

**Descript.**

Not the best-funded (Runway, $860M), not the largest (OpusClip, 440M
self-claimed clips), not the most trusted by professional editors (Adobe).
Descript is the most dangerous because it is the only company that has
*already shipped SelekyT's thesis*: an AI that selects what is worth keeping,
a human who overrides it in a real timeline, and an export of that decision as
an editable FCPXML/EDL/XML/AAF timeline a professional NLE can open. SelekyT's
three claimed differentiators — editorial judgement, human control, NLE
interoperability — are Descript's shipped product, three years ago, with $100M
behind it and an API/MCP that lets AI agents drive it without a human opening
the app at all.

The reason this is survivable, and the reason it is still dangerous rather
than terminal: Descript's editorial intelligence is *clip-level and opaque*.
It has no ranked candidate list, no published scoring criteria, no rejection
reasons, no provenance, and its timeline export deliberately discards
effects, transitions, titles, speed and images. Descript sells "edit video by
editing a doc"; it has never sold "here is what I judged worth keeping and
here is why." That gap is SelekyT's entire defensible position — and it is a
*documentation and judgement* gap, not a technology gap, which is precisely
the kind a well-funded incumbent closes in one release.

Runners-up, for completeness: **OpusClip** is the most dangerous on data and
distribution (it holds the clip-level performance data that §4's learning axis
would need, and already exports to two NLEs); **Adobe's AI Assistant** is the
most dangerous on native distribution (first-pass assembly inside the NLE the
editor already owns, at no extra cost). One further note that bears directly
on SelekyT's India/Telugu thesis: Mirage's March 2026 raise is explicitly
earmarked to "expand in high-growth Asian markets," with only 25% of its
revenue from the US — the regional-language advantage is not unopposed.

## 9. The one thing SelekyT must prove

**That a professional editor can open a SelekyT FCPXML or EDL in a real NLE,
and that the first-pass cut it finds there is one they would rather keep than
redo.**

Everything else in this map is a feature comparison. This is the one claim on
which the whole positioning rests, and it is currently unverified in both
halves. The format half is gap G2 in `docs/AI_EXECUTION_STATE.md` — no NLE has
ever opened an exported timeline, and the FCPXML schema SelekyT writes (1.11,
`engine/nle_export.py:45`) is two versions behind Apple's current 1.13, with
no confirmation that FCP 12.3 still imports it. The judgement half is gap G6 —
three clips from one source passing `tools/editorial_audit.py`. Neither half
is a marketing problem; both are evidence problems, and both are cheap to
start on: Resolve is free, reads CMX 3600, and is the cheapest first import
test available.

Until that evidence exists, the honest summary of SelekyT's position is: a
genuinely unclaimed wedge (transparent story-unit ranking + editable-timeline
export + local-first provenance), occupied by a product with no users, no
pricing, no compliance posture, and an export that has never been opened by
the tools it targets.

---

## Source list

**Required competitors.** opus.pro (`/pricing`, `/about`, `/api`) ·
help.opus.pro (`/docs/article/import-to-adobe-premiere`, `/import-to-davinci-resolve`,
`/9947095-clip-anything.md`, `/virality-score.md`, `/brand-vocabulary.md`) ·
opus.pro/blog/opusclip-celebrates-30m-in-funding-and-the-launch-of-clipanything ·
businessinsider.com/opusclip-softbank-vision-fund-2-funding-valuation-2025-3 (headline only) ·
trustpilot.com/review/opus.pro · descript.com (`/pricing`, `/about`, `/download`) ·
help.descript.com (`/export-and-share/timeline-exports.md`, `/repurpose/create-clips-from-your-content.md`,
`/getting-started/underlord-beta-your-ai-co-editor-in-descript.md`, `/api-and-mcp/mcp.md`,
`/llms.txt`) · descript.com/blog/article/descript-season-5-squadcast-joins-descript-easy-reliable-remote-recording-editing-in-one-place ·
techcrunch.com/2022/11/15/ai-powered-media-editing-app-descript-lands-fresh-cash-from-openai/ ·
riverside.com (`/pricing`, `/video-editor`, `/mobile`, `/mac-app`,
`/university-videos/editor-premiere-export`) · techcrunch.com/2021/04/22/podcasting-platform-riverside-fm-raises-9-5m/ ·
captions.ai (`/pricing`, `/help/llms.txt`, `/help/whats-new`, `/help/docs/captions/timeline`) ·
mirage.app/about-us · techcrunch.com/2025/09/04/captions-rebrands-as-mirage-expands-beyond-creator-tools-to-ai-video-research/ ·
techcrunch.com/2026/03/24/mirage-raises-75m-to-continue-building-models-for-its-ai-video-editing-app-captions/ ·
adobe.com (`/products/premiere.html`, `/creativecloud.html`, `/products/firefly.html`,
`/products/premiere/ai-video-editing.html`) ·
blog.adobe.com/en/publish/2026/09/08/generate-create-directly-in-your-timeline-with-new-ai-powered-innovations-in-premiere-after-effects ·
community.adobe.com/announcements-727/meet-your-new-assistant-editor-ai-assistant-in-premiere-pro-is-now-in-public-beta-1629317 ·
helpx.adobe.com (Premiere EDL, AAF, OMF and FCP migration docs — cited at URL level; bodies 403) ·
runway.com (`/`, `/pricing`, `/enterprise`, `/plugins/premiere-pro`) ·
help.runwayml.com · techcrunch.com/2026/02/10/ai-video-startup-runway-raises-315m-at-5-3b-valuation-eyes-more-capable-world-models/ ·
techcrunch.com/2026/05/15/runway-started-by-helping-filmmakers-now-it-wants-to-beat-google-at-ai/ ·
vizard.ai (`/pricing`, `/about-us`) · help.vizard.ai · klap.app (`/`, `/pricing`) ·
veed.io (`/pricing`, `/api`, `/tools/auto-video-editor/ai-clip-generator`) ·
support.veed.io (`/articles/10531327`, `/articles/11652474`, `/articles/11589317`,
`/articles/16342833`, `/articles/10804541`, `/articles/11848535`, `/articles/9616498`) ·
techcrunch.com/2022/02/02/veed-an-online-video-editing-platform-picks-up-35m-from-sequoia/ ·
kapwing.com (`/pricing`, `/help/how-to-use-kapwings-repurpose-tool/`, `/help/about-the-kapwing-mcp/`,
`/help/is-kapwing-free/`) · techcrunch.com/2019/09/24/kapwing/

**Emerging and NLE-native.** autocut.com (`/`, `/pricing`) · simonsaysai.com (`/pricing`,
`/final-cut-pro`, `/assemble`, `/avid-media-composer`, `/convert/srt-to-avid-subcap`,
`/about`) · reduct.video (`/`, `/pricing`, `/case-studies`, `/integrations`,
`/product/multicam`, `/product/redaction`) · help.reduct.video (Premiere, FCP,
Resolve import articles) · techcrunch.com/2021/02/11/reduct-video-funding/ ·
blackmagicdesign.com (`/products/davinciresolve`, `/products/davinciresolve/studio`,
`/products/davinciresolve/textbasedediting`, `/api/support/latest-stable-version/davinci-resolve`) ·
documents.blackmagicdesign.com/UserManuals/DaVinci_Resolve_21_Reference_Manual.pdf (4,444 pp.) ·
avid.com (`/media-composer`, `/products/mediacentral`) · community.avid.com ·
apple.com/final-cut-pro/ · support.apple.com/guide/final-cut-pro/whats-new-ver19637ebab/mac ·
support.apple.com/125519 · gling.ai (`/`, `/pricing`,
`/blog/integrating-gling-with-your-existing-video-editing-workflow`) ·
timebolt.io (`/`, `/pricing`, `/final-cut-pro-export`,
`/jump-cutter-adobe-premiere`, `/jump-cut-export-davinci-resolve`) ·
taja.ai (`/`, `/pricing`, `/about-us`) · wisecut.ai (`/`, `/pricing`) ·
submagic.co/pricing · pictory.ai (`/pricing`, `/about-us`,
`/blog/pictory-raises-2-1-million-seed-financing-led-by-fuse`) ·
crayo.ai/pricing · 2short.ai/pricing · recast.studio/pricing

**Open source.** api.github.com/repos/WyattBlue/auto-editor ·
raw.githubusercontent.com/WyattBlue/auto-editor/master/README.md ·
api.github.com/repos/cobanov/autocut · api.github.com/search/repositories?q=fcpxml+silence

**Secondary (used only for version history and corporate status).**
en.wikipedia.org/wiki/Final_Cut_Pro · /Adobe_Premiere_Pro · /Media_Composer ·
/Avid_Technology · /Symphony_Technology_Group · /Comparison_of_video_editing_software ·
help.descript.com/llms.txt (index only) · opus.pro/sitemap.xml · trustpilot.com
reviews for descript.com, riverside.fm, captions.ai, vizard.ai

**Checked and failed — do not cite.** avid.com/ada and ~30 related Avid URLs
(404 — "Avid Ada" is unconfirmed) · developer.apple.com FCPXML reference (404) ·
exchange.adobe.com (JS shell, no AutoCut listing found) · web.archive.org
(tool-blocked) · crunchbase.com (403) · capcut.com/pricing (HTTP 451
geo-blocked) · autopod.net (ECONNREFUSED) · businessinsider.com article body
(headline only) · any Adobe feature named "Edit Check" (no trace exists).

---

## Appendix — four premises this research contradicts

Recorded because each one was believed before the research, and a map that
keeps them is worse than no map.

1. **"SelekyT's FCPXML 1.11 export is current."** It is the FCP 10.7-era
   schema. Apple is at FCPXML 1.13 (FCP 11.0, 13 Nov 2024) and Final Cut Pro
   12.3. Whether 12.3 still imports 1.11 is **UNVERIFIED** — and it compounds
   gap G2, since no NLE has ever opened any SelekyT export. **[RESEARCHED for
   the schema line; the import question is UNVERIFIED]**
2. **"No competitor combines editorial ranking with NLE export."** False.
   Descript does. **[RESEARCHED]** The true statement is narrower: no
   competitor publishes *story-unit ranking with auditable criteria* **and**
   timeline export, and SelekyT's own evidence for its side of that is one
   source.
3. **"Avid Ada" exists.** Not verifiable by any means available here. **Do not
   name it in a deck.** **[RESEARCHED as an absence]**
4. **"Our FCPXML export is proprietary differentiation."** The Rust and Python
   FCPXML emitters descend from `cobanov/autocut` under the author's explicit
   permission, with behavioural parity pinned in `docs/autocut-parity.md`, and
   the free `auto-editor` exports to four NLEs where SelekyT exports two
   formats. **[VERIFIED]**
