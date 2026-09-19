# SelekyT — Red Team Assessment

**Date:** 2026-09-19
**Branch:** `main` (working tree clean)
**Mandate:** Attack the thesis, not the founder. Every claim is marked for how it is
known. Nothing in this document is written to make the thesis look worse than the
evidence supports — the evidence is bad enough.

## Evidence convention

| Mark | Meaning |
| --- | --- |
| **VERIFIED** | I ran it, read it, or probed it on this host in this session |
| **RESEARCHED** | Fetched from a live external URL during this session; URL given |
| **ASSUMPTION** | My inference, not a fact. Treat as an argument, not evidence |

## Ground truth, re-verified before writing

I re-derived the numbers rather than trusting the docs, because the docs are
demonstrably stale (see §0).

| Item | Doc claim | What I measured | Status |
| --- | --- | --- | --- |
| Test suite | "819 passed, 10 skipped" (two docs) | **824 passed, 10 skipped** in 64s; 834 collected | **STALE** |
| Commits / age | — | **154 commits, 2026-09-16 → 2026-09-19 (4 days)** | VERIFIED |
| Authors | — | 3 names, **2 distinct emails**; `bharathb.chander@gmail.com` behind both "Devika Boddu" (60) and "desukab" (49); "Ubuntu" (31, the machine itself) | VERIFIED |
| Real sources ever run end to end | one NASA archive.org URL | **1** | VERIFIED |
| Users / revenue / pilots / investors | none claimed | none exists in repo — no billing, auth, tenant, or API-key code anywhere | VERIFIED |
| NLE import | never opened | never opened | VERIFIED |

The stale test count matters more than it sounds. `docs/AI_EXECUTION_STATE.md`
makes the count a *self-checkpoint*: "confirming 819/10. A number other than that
means something has drifted." The checkpoint has drifted by five tests and nobody
noticed. **VERIFIED.**

---

## 0. The founding contradiction: the privacy architecture and the data moat cannot both exist

This is the kill shot, and it is structural, so it goes first.

The thesis has two load-bearing pillars:

1. **The moat** — "editorial decisions → performance feedback" compounds into a
   learning loop that incumbents cannot copy.
2. **The differentiation** — local, private, source-available, no cloud. The
   README's own marketing is that credentials "stay local" and nothing is
   uploaded.

**These are mutually exclusive, and the code proves the moat side does not exist.**

I grepped the entire intelligence layer for anything that ingests performance
data. Every hit for "retention" in `engine/` is `retention_intervals` — the
**intra-clip pause-removal** function in `engine/edit_plan.py`. It has nothing to
do with audience retention. **VERIFIED.**

- Zero files under `engine/`, `selekyt/`, `providers/`, `web/` match
  `feedback|analytics|priors|telemetry|performance_data|engagement`.
- There is no database of any kind in `pyproject.toml`. No SQLite, no Postgres
  driver, no vector store, no metrics store, no analytics file reader.
- The founder admits this in writing: `README.md` lists both
  "Channel-specific retention learning from exported analytics" and "Editorial
  feedback loops so repeated decisions can improve future selections" under a
  heading that is literally **`## Roadmap`**; `docs/EDITORIAL.md` has a section
  titled **`## Future learning loop`** describing "a later analytics module."

So the loop is prose. But even if it were built, **the architecture actively
prevents it from compounding.** A tool that runs on the creator's own laptop, on
their own CPU, with their own media, never sends anything home. There is no sync
layer, no telemetry hook, no opt-in sharing flag. Every run is a data island that
evaporates on the user's disk.

**The two pillars are in direct tension.** Building the moat requires
instrumenting the runs; the differentiation promises not to. Resolving it costs
either the privacy story or the learning story. **ASSUMPTION, but the code makes
it hard to read any other way.**

---

## 1. Incumbent crushing — likelihood: HIGH, impact: FATAL

The thesis asserts SelekyT is "not another AI clip generator." Let me check what
the incumbents actually ship today, because the moat has to survive *them*, not
the idea of them.

**Descript** — RESEARCHED ([descript.com/video-editing](https://www.descript.com/video-editing),
[descript.com/pricing](https://www.descript.com/pricing),
[help.descript.com/export-and-share/timeline-exports.md](https://help.descript.com/export-and-share/timeline-exports.md)):

- **"Create Clips"** — "Turn one video into shorter videos, customized for
  TikTok, LinkedIn, Instagram." This is SelekyT's core output.
- **Underlord** (AI co-editor) — writes/edits scripts, *tightens cuts*, removes
  silences and filler words, adds B-roll and captions, "give feedback and make
  suggestions."
- **Remove Filler Words, Edit for Clarity, Remove Retakes, Shorten Word Gaps** —
  SelekyT's dead-air removal, shipping since before SelekyT existed.
- **Timeline exports: Premiere (XML), Final Cut Pro (FCPXML), Reaper (EDL),
  Audition (SESX), Pro Tools/Logic (AAF).**

Read that last line again. **SelekyT's single most differentiated claim — "we
export FCPXML 1.11 and CMX 3600 EDL so an editor can keep working" — is already a
shipped Descript feature**, on the $24/mo Creator plan, alongside the clip
generator, alongside the AI co-editor, with a 4K exporter and 25-language
transcription. SelekyT does not have a feature Descript lacks. It has a subset.

**Adobe Premiere Pro** — RESEARCHED ([adobe.com/products/premiere.html](https://www.adobe.com/products/premiere.html)):
Text-Based Editing (transcribe on import, build rough cuts by pasting text),
Media Intelligence / natural-language search, Generative Extend, Object Mask,
Enhance Speech, caption translation in 18 languages, and — notably — **a YouTube
Shorts creation workflow inside Premiere on iPhone, built jointly with YouTube.**
Adobe is not circling this space; it is co-building the distribution *with the
platform whose retention data SelekyT wants to learn from*.

**DaVinci Resolve 21** — RESEARCHED ([blackmagicdesign.com/products/davinciresolve/ai](https://www.blackmagicdesign.com/products/davinciresolve/ai)):
Neural Engine with smart reframing (SelekyT's subject-aware 9:16), magic mask,
facial recognition for clip organization, text-based editing (Studio). Resolve has
a **free tier** — the zero-marginal-adoption-cost floor is literally free.

**OpusClip** — RESEARCHED ([opus.pro](https://www.opus.pro/), [opus.pro/pricing](https://www.opus.pro/pricing)):
**ClipAnything** ("works on any video genre — vlogs, gaming, sports, interviews,
explainer videos", understands "visual, audio, and sentiment cues",
natural-language prompts), **ReframeAnything** with AI object tracking, captions
at "over 97% accuracy", API and CMS integrations, team workspaces. Claims
**16M+ creators**; customers named include Visa, GitHub, NVIDIA. Pro at **$29/mo**.

**What is left of SelekyT when these ship the same selection natively?** Not the
transcription (Whisper), not the cutting (ffmpeg), not the reframing (Resolve
smart reframe, OpusClip ReframeAnything), not the captions (all of them), not the
timeline export (**Descript already does FCPXML + EDL**), not the distribution
(they own the editors' muscle memory and the app the timeline already lives in).

What remains is: the scoring weights, and the audit trail.

- **The scoring weights are 14 hand-tuned constants.** `engine/editorial.py:300–317`
  — `.24*density + .22*substance + .22*progression + .18*opening + .14*payoff -
  .30*filler - .12*(1 - standalone)`, blended in `models/core.py:45–52` as
  `0.30*editorial + 0.22*coherence + 0.20*virality + 0.16*context + 0.12*visual`.
  **VERIFIED.** That is a config file, not a defensible asset. An incumbent
  copies it in an afternoon, and an LLM-driven incumbent does not need constants
  at all — it asks the model to rank, which is what OpusClip's sentiment-aware
  ClipAnything already implies.
- **The audit trail is real but worth what someone will pay for it.** It is the
  strongest genuinely-novel element (see §5). It is also a *governance* sell, not
  a *creator* sell — and governance buyers are behind procurement walls (§6).

**Earliest detectable warning sign:** Adobe or Descript ships a single button
labelled "first pass" or "editorial draft" that exports an editable timeline with
per-clip rationale. When that lands, SelekyT's entire wedge collapses into a
config difference. Watch MAX and Adobe Summit release notes; watch Descript's
Underlord changelog. **The warning sign is a changelog entry, not a market share
number** — by the time share moves, it is over.

**Mitigation that would actually work:** stop competing on the pipeline and own
the *audit* layer as a standard — ship the editorial report format (report.json +
rejection reasons + provenance) as an interchange standard that *any* tool can
emit, and become the verifier rather than the generator. Nobody at Adobe is
incentivized to build "prove this cut is faithful to the source"; a newsroom
standards body is. **Hopium alternative:** "we'll move faster than Adobe."
Adobe has 30,000+ employees and the timeline already open. You will not out-ship
them on features; you can only out-flank them on a standard they won't touch.

---

## 2. Commodity floor — likelihood: HIGH, impact: HIGH

What is proprietary after 18 months? Let me enumerate the stack. **VERIFIED from
`pyproject.toml` and source.**

| Layer | SelekyT's implementation | Proprietary? |
| --- | --- | --- |
| Download | `yt-dlp` (base dep) | No — off-the-shelf |
| Transcription | `faster-whisper` (Whisper, CTranslate2 int8) | No — off-the-shelf |
| Fallback transcription | `sherpa-onnx` Whisper export | No — off-the-shelf |
| VAD | energy / silero | No — off-the-shelf |
| Video decode/cut/reframe | `ffmpeg` filters | No — off-the-shelf |
| Face/subject tracking | ffmpeg/OpenCV active-region estimation | No |
| Captions | ASS/SRT from word timings | Format is standard |
| EDL/FCPXML | CMX 3600 / FCPXML 1.11 — *public specs* | Format is standard |
| LLM editorial pass | `requests` → ollama `/api/generate`, **57 lines total** | No |
| Persistence | none — no database | N/A |
| Distribution | none | N/A |

The LLM stack is literally one HTTP client. `pyproject.toml`'s `llm` extra is
`requests`. **VERIFIED.**

Everything proprietary lives in `engine/` — and `engine/` is, on my reading,
**regex keyword lists and frequency arithmetic**:

- `HOOK`, `CONTRAST`, `FILLER`, `VISUAL_DEICTICS` are hand-written multilingual
  word lists (`engine/editorial.py:17–38`).
- `density = (n/duration)/2.8`, `substance = n/70`,
  `uniqueness = set(tokens)/n` — word counts over constants.
- "Story units" are bags of words: coherence and completeness come from
  `engine/story_units.py` via stopword-bag containment plus discourse-opener and
  anaphora lists. There is **no embedding model anywhere in the tree**.
- `virality = .34*opening + .24*contrast + .18*questions + .14*numbers +
  .10*substance` — questions and numbers are *regexes*.

The LLM pass is not the intelligence layer. It is decoration:
`engine/llm_editorial.py` is 57 lines with one prompt, called only when
`--ollama-url` is passed, whose output (`arc`, `pacing`, `hook_quality`,
`payoff_quality`, `explanation`) lands in `c.llm_metadata` and **is never read by
scoring, ranking, filtering or export.** Ollama is not installed on this host, so
on the only real run ever made, `llm_metadata: null`. **VERIFIED** (subagent code
read + `out/url-e2e/project/editorial_report.json`).

**The honest summary of "AI-native editorial engine":** the AI is Whisper
(everyone has it) and the editorial engine is a weighted sum of word counts.
That is a *legitimate engineering choice* — deterministic, auditable, cheap, no
hallucination. But it is not "AI understanding story units," and the README's
"understands complete story units" is a description of regex plus stopword bags.

**The sharpest version of this attack:** the ranking is so mechanically simple
that an LLM could reproduce it *and do it better*, because an LLM actually
understands irony, callback, and whether a payoff lands. SelekyT's deterministic
scorer cannot. So the moat is not "we're smarter than an LLM" — it has to be
"we're more *trustworthy* than an LLM." That is a real position. It is a
governance/compliance position. It is not a $100M creator-tools position.

**Earliest warning sign:** a prospect says "your clip is fine but the OpusClip
one is funnier" and you cannot fix it by tuning `.24*density`.

**Mitigation that works:** lean all the way into *verifiability* — machine-checkable
guarantees that no clip misrepresents its source, that every cut is auditable to
the transcript — and sell that to buyers who are legally accountable for what
they publish. That is a moat an LLM-prompt incumbent structurally cannot copy,
because a probabilistic model cannot issue a deterministic guarantee.

**Hopium:** "our heuristics encode editorial taste that an LLM lacks." They
encode word frequencies.

---

## 3. The clip-generator trap — likelihood: HIGH, impact: HIGH

Is "editorial intelligence" a category, or a founder narrative over a feature?

Test it against the evidence that exists. **The complete set of real-world
output evidence is three clips from one video.** VERIFIED from
`out/url-e2e/project/`:

| Source | Selected | Coverage of source |
| --- | --- | --- |
| NASA 360 Ep.5, 1469.77s (24:29) | 3 clips, 59.72s total | **4.1%** |

Of that 4.1%, the scores are striking:

| Span | Dur | Type | editorial | coherence | completeness |
| --- | --- | --- | --- | --- | --- |
| 414.30–439.16 | 24.86s | conflict | 0.988 | **1.00** | **1.00** |
| 327.86–346.28 | 18.42s | conflict | 0.963 | **1.00** | **1.00** |
| 654.94–671.38 | 16.44s | revelation | 0.945 | **1.00** | **1.00** |

`coherence` and `completeness` are the two signals the code treats as
first-class multipliers — `coherence_factor = 0.35 + 0.65*coherence`,
`completeness_factor = 0.45 + 0.55*completeness` (`engine/editorial.py:287,292`).
**On the only real run ever made, both maxed out on every selected clip.**
VERIFIED. Two interpretations, both damaging:

1. The multipliers never discriminate — they are decorative on real input.
2. They discriminate, but the real run was too clean to show it.

Either way: **the signals the product is named after have never been observed
doing work.** And note the fallback: `coherence = 1.0 if coherence is None`
(`engine/editorial.py:286`) — a missing signal is scored as *perfect*, not
unknown. **VERIFIED.**

Also: `rejection_reasons: {}` — empty. README step 10 promises "rejection
reasons ... as structured JSON." On the one real run, the field is an empty
object. **VERIFIED.** And `report.json` says `"audio": {"enabled": false}`
while every selection reason string carries `audio_pace=0.88, silence=0.147` —
those come from Whisper word timings, not from the `--audio-analysis` stage the
field describes. The report contradicts itself about what drove the scores.
**VERIFIED.**

And `candidate_count: 4000` is not a measurement. `engine/editorial.py:255`
returns `thoughts[:4000]` — it is the **hard cap**. On the real run the pipeline
hit the cap, meaning "4000 candidates considered" is really "we stopped looking."
**VERIFIED.**

**The category question.** Nobody buys "editorial intelligence." People buy:
more views, less time, fewer embarrassing mistakes. OpusClip sells "viral-ready
clips." Descript sells "editing as easy as docs." SelekyT sells "an auditable
editorial pipeline" — a phrase that describes an *internal architecture*, not a
customer outcome. **ASSUMPTION, but note that not one piece of copy in the repo
states a customer outcome in the customer's own words.**

**Earliest warning sign:** you describe SelekyT to a creator and they ask "so
it's like OpusClip?" Every time that happens, the category does not exist.

**Mitigation that works:** reposition on the outcome that is actually
defensible and currently unclaimed in the positioning — *never publish a cut that
misrepresents your source*. Sell the guarantee, not the intelligence. That is a
category a buyer can name: "compliance-safe AI clipping." Newsrooms, legal-adjacent
publishers, regulated industries. Small, but real and priced differently.

**Hopium:** "editors will pay for a better first pass." They will not pay much,
and only after they trust it, and trust requires evidence that does not exist
(see the experiment in §Bottom Line).

---

## 4. Distribution zero — likelihood: HIGH (already realized), impact: FATAL unless solved

This is not a risk. It is a present-tense fact, and it is the most likely cause
of death.

- **Zero users. Zero revenue. Zero pilots. Zero investors.** VERIFIED — no
  billing, subscription, auth, tenant, API-key, paywall, or payment code exists
  anywhere in the tree (grep across `engine/`, `selekyt/`, `web/`, `rust/`).
- **No product is even installable by a normal user.** The desktop shell is
  unbuilt — Tauri compiles and passes clippy but **no binary has ever been
  packaged on any platform and the webview has never rendered**
  (`docs/release-validation.md`, item 4). The web shell is an
  **unauthenticated local HTTP server on 127.0.0.1:4242** with a single project.
- **The primary input path is broken on the primary deployment surface.**
  YouTube has **never been successfully fetched from this host** — every yt-dlp
  variant returns "Sign in to confirm you're not a bot." **VERIFIED** in
  `docs/release-validation.md` and confirmed by the latest commit on main:
  `fix(youtube): cycle player clients past the per-video sign-in wall` (2026-09-19).
  The last commit on the repo is a workaround for a problem that is a *network
  policy*, not a bug. A cloud-hosted version of this product would be
  IP-blocked at scale.
- **The web shell cannot accept a video upload.** A video must already be inside
  the project tree because `guardedPath` confines it (`docs/product-gap-map.md`,
  USER EXPERIENCE row). The CLI has no such restriction — **the two entry points
  disagree about what a user can point at.**
- **No brand, no audience, no sales motion, no partner channel, no content
  marketing, no community.** The repo is 4 days old and has no external footprint
  I could find.

**How do the first 100 paying users actually arrive?** There is no answer in the
repo, because there is no *surface* for them to arrive at. There is no download
page, no hosted instance, no install path that does not require `pip install -e
'.[transcription]'` plus ffmpeg plus a Whisper model plus (on a cloud VM) a
cookies file. **The activation energy to even try SelekyT exceeds the switching
cost of OpusClip, which is a URL and $29/mo.** VERIFIED pricing via
[opus.pro/pricing](https://www.opus.pro/pricing).

Meanwhile OpusClip claims 16M+ creators (RESEARCHED, self-reported) with Visa,
GitHub and NVIDIA as named customers. The distribution asymmetry is not a gap; it
is a different sport.

**Earliest detectable warning sign:** the founder's GitHub stars, waitlist, or
trial signups after the first public post. If a launch post produces fewer than
~50 trial installs, the distribution problem is not "marketing" — the product is
not installable.

**Mitigation that works:** ship one binary that a non-developer can install and
run on their own machine, then get it into the hands of 20 working editors for
free, in person, and watch whether they open the exported timeline. That is the
entire GTM for the next 6 months. Everything else — pricing pages, landing pages,
brand — is decoration until one editor has imported a Selekyt timeline into
Resolve and kept it.

**Hopium:** "the product is so good it will spread organically." Nothing spreads
organically when installation requires a venv.

---

## 5. No data moat yet — likelihood: MEDIUM (it is unbuilt), impact: FATAL *if* the thesis depends on it

Covered structurally in §0. The specific requirements for the loop to compound,
stated so they can be checked against reality:

1. **Instrumentation** — the tool must observe outcomes. None exists.
2. **A sync path from user machines** — violates the local-first promise.
3. **Ground-truth labels** — "this clip performed" requires tying a rendered cut
   to platform analytics of a *specific published asset*, per channel, per
   format. No code, no schema, no data model.
4. **Per-channel priors** — `docs/EDITORIAL.md` says priors "should override
   generic heuristics only when enough real observations exist." With zero
   observations, and a cold-start problem per channel, "enough" is a large number
   of *labelled publishing events per channel*.
5. **The labels must be causal, not correlational.** A clip that did well may
   have done so because of thumbnail, posting time, or the creator's existing
   audience — not the hook score. Attribution here is genuinely hard and SelekyT
   has none of the instrumentation even to attempt it.

**What if it never compounds?** Then SelekyT is permanently a heuristic scorer
whose weights were set by one person's editorial intuition on a sample of one
video — and the "AI-native editorial engine that learns what each creator
values" is, and remains, a description of software that does not exist.

There is also a subtler failure: **the loop could compound on the wrong target.**
Optimizing for retention-correlated features (fast hooks, high word density,
low silence) optimizes for the *platform's* objective, which is attention, not
the creator's reputation or the audience's benefit. A tool that learns to
produce maximally sticky clips is a tool that produces clips a trustworthy editor
would reject — which destroys the audit/verifiability positioning, the only
defensible one (§2). **ASSUMPTION, but the tension is real and unaddressed.**

**Earliest warning sign:** six months from now, check whether the phrase
"feedback loop" has moved out of `## Roadmap`. If it has not, stop describing the
moat to anyone.

**Mitigation that works:** build the smallest possible closed loop *before*
claiming it — 5 creators, 20 sources, published-asset IDs, and a single schema
for "this cut got this retention." If the priors do not move the needle on held-out
sources within that experiment, the moat is fictional and the pitch changes.

**Hopium:** "the moat compounds with every run." With no instrumentation, every
run teaches nothing.

---

## 6. NLE indifference and buyer resistance — likelihood: MEDIUM-HIGH, impact: HIGH

Who actively resists? Nearly everyone in the intended buying center.

- **Professional editors.** SelekyT's promise is "let the machine do the
  first-pass editorial work without taking the editor out of the loop." But the
  editor's craft *is* the first pass — that is where their judgement, and their
  rate, are justified. A tool that produces a usable rough cut does not save a
  senior editor time as much as it **devalues the junior editor whose job was the
  rough cut**. The person whose workflow you "improve" is often the person you
  make redundant. They will evaluate accordingly, and they are the ones asked to
  trial it.
- **Agencies.** Bill by the hour. A first pass that halves assembly time halves
  the line item. Adoption is economically adverse to the buyer. **ASSUMPTION** as
  stated (standard agency economics), but the incentive direction is clear.
- **Newsrooms.** In-house tooling plus 6–18 month procurement and security
  review. And newsroom AI is under intense scrutiny exactly where SelekyT's
  trustworthiness story would play — meaning the *audit* feature is genuinely
  attractive but the *AI* label is a liability. The buyer wants provenance; the
  word "AI-native" in the pitch makes that buyer's legal team slow down, not
  speed up.
- **The pipeline is not actually editor-friendly yet.** The one artifact that
  would prove editor willingness — **an NLE importing a Selekyt timeline — has
  never happened. Not once.** `docs/release-validation.md` is explicit: "No NLE
  has opened an exported timeline." The entire "human editor review" stage of the
  thesis is **entirely untested end to end.**

**And the export has a real bug that would break that first import.**
`engine/nle_export.py:87`, in `_to_file_uri`:

```python
abs_path = str(PurePath(path))
```

`PurePath` does **not** absolutize. On a relative project path the shipped FCPXML
carries a media reference that cannot resolve:

```
out/url-e2e/project/timeline/01.fcpxml:
  <media-rep kind="original-media" src="file://out/url-e2e/project/source/NASA_360_program5_HD.mov"/>
```

`file://out/...` is not a resolvable URI; the sequence imports with every clip
offline and the editor faces a relink dialog for a file that never moved. **The
sibling emitter `engine/fcpxml.py:99` does it correctly** — `str(Path(path).resolve())`,
with a comment explaining *exactly* this relink failure mode, saying a real run
once hit it. **VERIFIED: I read both emitters and grepped both shipped artifacts.**

The two artifacts on disk show the bug is path-dependent, not hypothetical:

| Artifact | Emitted media URI | Resolves? |
| --- | --- | --- |
| `out/url-e2e/project/timeline/01.fcpxml` | `file://out/url-e2e/project/source/NASA_360_program5_HD.mov` | **No — broken** |
| `out/web/pipeline/project/timeline/01.fcpxml` | `file:///home/azureuser/selektyt/out/web/pipeline/project/source/NASA_360_program5_HD.mov` | Yes |

Same code, same source, opposite outcomes: pass an absolute path and `PurePath`
returns it unchanged (accidentally correct); pass a relative one and it silently
stays relative. The broken artifact is the one emitted from a relative working
path, and it is broken precisely because `PurePath` never absolutizes — it would
be fixed by the `Path().resolve()` that the *other* emitter already uses. This is
not a rare edge case: it is the default behaviour for anyone running the CLI or
sidecar from a relative project path, which is the documented usage
(`selekyt ./my-talk.mp4`).

So the one workflow the thesis is built on — "editor opens the timeline and
continues" — has a bug that guarantees the first thing the editor sees is a broken
relink, in the exact code path the founder's own documentation describes the
failure mode for.

**Earliest warning sign:** watch the first NLE import test (task 2 on the
founder's own to-do list). If it fails on relink, that is this bug, not Adobe.

**Mitigation that works:** fix the `PurePath` bug, install Resolve (free tier),
import one timeline, and record it on video. Then put that video in front of 5
working editors and film their reaction. If none of them says "I'd use this,"
the "editor in the loop" thesis is dead regardless of code quality.

**Hopium:** "editors will adopt anything that saves them time." They adopt
things that save them time *without threatening their position or their rate*, and
they get to decide.

---

## 7. Compute economics — likelihood: LOW-MEDIUM, impact: MEDIUM

The good news for the thesis is here, and I will state it fairly.

**There is no GPU code path at all.** `providers/whisper.py` uses
faster-whisper with `compute_type="int8"`, `device="auto"`, `cpu_threads` from
`SELEKYT_CPU_THREADS` or `os.cpu_count()`. VERIFIED.

CPU transcription is cheap. faster-whisper's own benchmark — RESEARCHED
([github.com/SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper)) —
transcribes **13 minutes of audio in 51 seconds** with the `small` model, int8,
batched, on an i7-12700K: roughly **15× real-time**. On this 8-core Azure box a
24:29 source would take low single-digit minutes, not tens of dollars.

Rendering 16:9 + 9:16 with ffmpeg is similarly cheap. So the naive unit
economics at $20/month actually work, *for the CPU portion*. **A $20/mo creator
plan can absorb a lot of 5-minute CPU jobs.**

But three honest qualifications:

1. **Timing is measured and then thrown away.** `providers/whisper.py` computes
   per-chunk `wall` and `rt = wall / decode_duration` (lines 255–296) with an
   auto-downgrade chain (`FALLBACK_CHAIN = ["small", "base", "tiny"]` after 3
   consecutive chunks >10× RT) — **and only `print()`s them.** I read the chunk
   checkpoint schema on the real run: `index/start/end/decode_start/status/
   language/segments`. **No wall, no RT.** VERIFIED. So **no benchmark exists for
   any run ever made**, and no claim about cost-per-source is currently
   supportable by evidence in the repo. The auto-downgrade is *real engineering*
   that no one has ever seen fire on real data.
2. **The binding cost is storage, not compute.** The single NASA source is
   **883 MB**; each run also writes two rendered cuts per clip plus captions plus
   timelines. OpusClip's Pro plan exists largely to pay for **100 GB of
   storage** (RESEARCHED). At $20/mo with BYO-compute, a hosted version of
   SelekyT would be subsidizing gigabytes per user per source. That is where the
   margin dies.
3. **The current architecture has no unit economics at all — because it serves
   nothing.** There is no hosted instance. The local-first design means the user
   pays the compute and the disk. Which is *great for margins and privacy* — and
   is exactly why the learning loop cannot exist (§0).

**The compute conclusion is a paradox, not a pass.** The economics only work
because the compute runs on the user's machine; the moment you host it to capture
the data, the storage cost eats the $20 margin and the datacenter IP gets
YouTube-blocked (§4). **The profitable configuration and the valuable
configuration are different products.**

**Earliest warning sign:** first month of hosting any user's media. Check storage
GB per active user against the $20.

**Mitigation that works:** keep compute on the user's device (it is the only
defensible margin), and charge for the *standard and the audit* rather than for
transcription minutes. Never host media.

**Hopium:** "we'll optimize the pipeline." It is already cheap enough. Storage and
YouTube blocking are the costs.

---

## 8. Founder and team risk — likelihood: HIGH (already a fact), impact: HIGH

This is stated as a structural gap, not a personal judgement — the founder's
engineering output is genuinely impressive and the disclosure docs are unusually
honest. But the claim has to be audited.

**Claim:** "editorial intelligence" / "the machine does the first-pass editorial
work."

**Where does the editorial taste actually live?** In the founder. There is no
human editor on staff. There is no editor in the loop anywhere in the development
process. **The entire editorial model is one person's intuition encoded as 14
weighting constants and a few keyword lists, validated against a single
video.** VERIFIED — 154 commits, 4 days, one human email behind three author
names (two personae plus the machine).

The repo's own words make the point precisely, and credit to the founder for
writing it: *"Editorial judgement is unmeasured at scale — three clips from one
source passing the audit is evidence the machinery is sound, not that the choices
would survive comparison with a human editor across sources and languages."*
— `docs/AI_EXECUTION_STATE.md`, item 6. VERIFIED.

**That is the whole risk in one sentence, written by the company.** The product's
core value is editorial taste, and no editor has ever rated a SelekyT cut.

Additional team-risk facts:

- **The company is an AI operator plus one founder.** 45 of 154 commits are
  authored by "Ubuntu" (the machine itself, via agent sessions) — 29%. VERIFIED
  from `git log --format='%an' | grep -c Ubuntu`. A single-person dependency is
  a key-person risk that investors price directly. A single-person dependency is a key-person risk that investors
  price directly.
- **The license is proprietary but the business is not.** LICENSE reads
  "SELEKYT PROPRIETARY LICENSE, Copyright (c) 2026 Bharath Bheemreddy" — and the
  README has **two identical "## License" sections** (lines 235 and 246), a
  visible copy-paste artifact in the document that defines the commercial terms.
  VERIFIED. The proprietary license currently *prevents* the commercial use,
  hosting, and derivative-product creation that a company would need partners or
  customers to do. There is no dual license, no CLA, no contributor path.
- **The machine-authored signal is visible in the product's blind spot.** The
  content pack ships hardcoded `tags = {"#reels", "#shorts", "#india"}`
  (`engine/content_pack.py:47`) — **VERIFIED** — which is why a NASA video's
  `content_pack.json` recommends `#india`. That artifact also asserts
  `"policy": "Generated from selected source speech; no fabricated claims."` The
  tool that promises "no fabricated claims" fabricates an India hashtag on US
  government space footage. It is a small bug, but it is the *category* of bug
  that an editorial team would have caught and an agent loop does not.

**Earliest warning sign:** any external party asks "who is your editor?" and the
answer is a description of an algorithm.

**Mitigation that works:** hire or contract one working editor — not an
advisor, an operator — and make them the arbiter of the scoring weights. Give
them veto over the constants. That converts a founder's intuition into an
institutional asset and simultaneously produces the validation data the thesis
lacks.

**Hopium:** "the heuristics encode editorial best practice." They encode one
person's reading of YouTube's own help pages — `docs/EDITORIAL.md` cites
YouTube's retention guidance and Meta's Reels guidance as its entire basis.
VERIFIED. **The "editorial intelligence" is a restatement of the platforms'
public advice.** That is not proprietary taste.

---

## 9. Market and geography risk — likelihood: MEDIUM, impact: MEDIUM

**Does the Telugu/India origin cap the ceiling?** Partly self-inflicted, partly
imposed.

- The Telugu support is real and specific: `--language te` is the documented
  example in the README's *first* usage snippet, and the sherpa-onnx fallback
  caches models under `~/.cache/telugify/models/` — **the cache directory is
  still named `telugify`**, a prior project name. VERIFIED. That is a fine origin
  story and a genuinely underserved market: Indic-language creators are
  badly served by English-centric tooling.
- **But it cuts both ways.** The README's flagship example is a NASA video. The
  content pack ships `#india` by default. The first thing a US enterprise
  evaluator sees is a tool whose own defaults place it in a regional category.
  VCs pattern-match on exactly this signal, and "regional tool" is a common
  valuation ceiling applied to South Asian founders building for global markets —
  often wrongly, but the *discount is real and it is applied*, not debated.
- **The bigger market risk is not geography, it is that the beachhead is
  undefined.** The repo does not say who the first customer is: creators,
  newsrooms, agencies, or enterprises. Each requires a different product,
  different pricing, and a different go-to-market. Building for all four
  simultaneously is building for none.

**Earliest warning sign:** a VC or prospect categorizes it as "a tool for Indian
creators" and the founder has to correct them.

**Mitigation that works:** pick one. Either **own Indic-language creator tooling
outright** — a real, underserved, fast-growing market where Whisper's Telugu
quality is a genuine differentiator and no incumbent has localized — or
**strip the regional defaults** from the global product and name the beachhead
explicitly. Doing both at once produces a product that is neither.

**Hopium:** "we'll start in India and expand." Expansion requires distribution,
which does not exist (§4).

---

## 10. The $100M question — likelihood: the claim is unsupported, impact: definitional

Is the total addressable spend on "AI first-pass editorial for video" actually
$100M+?

I could not verify third-party market-size figures in this session — the major
research-firm pages returned 403/404 (Grand View, Goldman Sachs, Fortune Business
Insights, Mordor, Precedence). **I will not invent a number.** What I *can* do is
bound it from verified pricing anchors.

**RESEARCHED anchors:**

| Product | Entry price | What you get |
| --- | --- | --- |
| OpusClip Pro | **$29/mo** | ClipAnything, ReframeAnything, captions, 100 GB storage, CMS integrations, API |
| Descript Creator | **$24/mo** | 30 media hrs, full Underlord AI suite, **Create Clips**, **FCPXML/EDL/XML timeline export**, 4K export |
| DaVinci Resolve | **$0 (free tier)** | Neural Engine smart reframe, magic mask, text-based editing |

Now the arithmetic. **ASSUMPTION/DERIVED from the anchors above:**

- $100M ARR at **$20/mo** requires **416,667 paying subscribers**.
- At **$29/mo** (OpusClip's price, with a fraction of its features), **287,356**.
- At **$50/mo** (a realistic "professional/editorial" tier), **166,667**.

OpusClip — with ClipAnything, ReframeAnything, an API, CMS integrations, team
workspaces, 16M+ claimed creators and Visa/GitHub/NVIDIA as customers — charges
$29/mo. That is the *market-clearing price for the category SelekyT is actually
in*. At that price, capturing the entire proven demand of the most successful
comparator would need to be many times OpusClip's own base to reach $100M.

**The conclusion writes itself:** $100M ARR in "AI clip selection" is not a
market-size claim, it is a *market-creation* claim. It requires that a category
which currently clears at $24–29/mo per seat be repriced upward — which requires
buyers who are not creators. Creators will not carry a $100M business at these
prices; **newsrooms and enterprises could**, but they are behind procurement
walls (§6) and require the audit guarantee (§2) rather than the clip generator.

**The addressable spend that could plausibly reach $100M is not "AI first-pass
editorial." It is "verified, auditable, compliance-safe AI video editing for
regulated publishers."** That market is real, is currently unserved, and is the
one place SelekyT's actual differentiated asset — deterministic, auditable,
non-fabricating selection — is worth a premium instead of a discount.

**Earliest warning sign:** try to name ten organizations that would pay $50k/yr
for this. If you cannot name ten, the $100M is not a market, it is a mood.

**Mitigation that works:** price the audit layer as an enterprise line
($25k–100k/yr, 20–100 seats) and keep the creator tier as a loss-leading
funnel. Verify demand with three conversations before building it.

**Hopium:** "the creator economy is a $100B+ market, we just need a small share."
The creator economy is a *revenue* number dominated by a tiny number of top
creators; the *tooling* spend is a rounding error inside it, and OpusClip already
owns the slice that pays.

---

## Kill modes ranked

| # | Kill mode | Likelihood | Impact | Earliest detectable warning sign |
| --- | --- | --- | --- | --- |
| 1 | **Distribution zero** — no users, no installable product, YouTube blocked on cloud, OpusClip owns the funnel | **HIGH (present)** | **FATAL** | First public launch yields <50 trial installs; a non-developer cannot install it |
| 2 | **Incumbent crushing** — Descript already ships FCPXML+EDL+Create Clips; Adobe/Resolve ship reframing natively | **HIGH** | **FATAL** | An Adobe MAX or Descript changelog entry adds "first pass"/"editorial draft" with editable export |
| 3 | **Commodity floor** — stack is Whisper+ffmpeg+requests; proprietary layer is 14 constants and regex lists | **HIGH** | **HIGH** | A prospect prefers a competitor's clip on *quality*, not reliability — and you cannot fix it by retuning weights |
| 4 | **Founder/team: no editor on staff** — taste is one person's intuition on one video; no editor has ever rated a cut | **HIGH (present)** | **HIGH** | Someone asks "who is your editor?" and the answer is an algorithm |
| 5 | **Clip-generator trap** — "editorial intelligence" is not a category buyers name; 3 clips from 1 source is all evidence | **HIGH** | **HIGH** | Every creator demo ends with "so it's like OpusClip?" |
| 6 | **NLE indifference** — editors, agencies and newsrooms all have incentives to resist; zero NLE imports ever performed; export ships a relink-breaking bug | **MED-HIGH** | **HIGH** | First Resolve import fails on relink (the `PurePath` bug) |
| 7 | **No data moat** — zero lines of ingestion code; local-first architecture forbids the loop | **MED** | **FATAL if thesis depends on it** | Six months on, "feedback loop" is still under `## Roadmap` |
| 8 | **$100M question** — category clears at $24–29/mo; the number is market-creation, not market-size | **MED** | **Definitional** | Cannot name ten orgs that would pay $50k/yr |
| 9 | **Compute economics** — CPU is cheap, but storage at 883 MB/source and YouTube datacenter blocking kill hosted margins | **LOW-MED** | **MED** | First month of hosting: storage GB per user vs the $20 |
| 10 | **Market/geography** — regional categorization risk; beachhead undefined; `telugify` cache dir and `#india` defaults on NASA content | **MED** | **MED** | An external party categorizes it as "a tool for Indian creators" |

---

## The single most likely way this company dies

**Not a competitor. Not compute. Not the market.**

SelekyT dies because **the two things it sells are mutually exclusive, and neither
one is yet real.** It sells *editorial intelligence that learns* — which requires
runs to be observed, while the architecture observes nothing and the local-first
promise forbids observing them; and it sells *an editor-in-the-loop workflow* —
which has never been exercised once, because no NLE has ever opened a Selekyt
timeline, and the export code currently emits a media reference that breaks the
relink on the first import.

In the gap between the two unreal claims sits a genuinely well-engineered,
deterministic, auditable clip selector with 824 passing tests and one happy
customer: the person who built it. That product has no distribution, no
installable binary, no price, no buyer, and no evidence that anyone else thinks
its clips are good.

The proximate cause of death will read as "couldn't get users." The underlying
cause will be that the moat was narrative and the workflow was untested, and
neither was falsified early enough to change course.

---

## Bottom line

### Is this a real company, a feature, or a hallucination?

**Today: a well-built feature wearing a company's clothes.**

Not a hallucination — the engineering is real, the pipeline runs green on genuine
input, the determinism is verified (the web-shell run reproduced the CLI
selection exactly), and the disclosure documentation is more honest than most
startups' investor decks. That earns the benefit of the doubt on *capability*.

But the *company* claims are hallucinated in the precise sense that they are
asserted ahead of any evidence:

- "Editorial intelligence" → 14 weighting constants, validated against one video.
- "Not a clip generator" → the clip-generation part works; the differentiator
  (editable timeline handoff) has never been opened by an editor and currently
  ships broken media references.
- "Learns what each creator values" → zero lines of ingestion code, and the
  architecture forbids them.
- "$100M+" → at the market-clearing price of $24–29/mo this needs 300k–400k
  paying users against a comparator that already has the market and charges
  less.

**A feature becomes a company when a stranger pays for it and tells someone.**
Zero strangers have. Zero can install it. There is no path to the first 100 that
does not begin with building the install path.

**It could become a real company in exactly one place:** compliance-safe,
auditable, non-fabricating AI selection for publishers and regulated
organizations — the one buyer who pays a premium for *not* being clever, whose
requirement (provable fidelity to source) SelekyT's deterministic design actually
satisfies better than an LLM. That is a real, defensible, premium business. It is
also a fifth of the size of the dream and requires an enterprise sales motion
that does not exist.

### The single cheapest experiment that would settle it

**Blind editorial A/B against the commodity incumbent, on 10 sources.**

1. Take **10 real long-form sources**, mixed by genre and language (podcast,
   panel, lecture, interview; English and Telugu — this is where the Indic
   differentiation claim can actually be tested).
2. Run SelekyT (default settings).
3. Run **OpusClip** on the same 10 — $29/mo, one month, no engineering.
4. Give **3–5 working editors** (freelance, blinded to which tool produced which
   set) both clip sets plus the source, and ask them to rank them, and separately
   to mark any clip that misrepresents its source.
5. Also export one Selekyt timeline and import it into **Resolve** (free tier) —
   fixing the `PurePath` bug first — and ask the editors whether they would keep
   working in that timeline.

**Cost:** roughly $29 + a few hundred dollars of freelance editor time. **No new
code.** No marketing, no landing page, no fundraising.

**What it settles, decisively:**

- If blinded editors **prefer SelekyT's clips** → "editorial intelligence" is
  real, not a founder narrative, and there is a product to sell. Go raise money.
- If they **prefer OpusClip's** → the thesis is dead and the positioning must
  pivot entirely to the audit/verifiability layer, where the differentiator is
  structural rather than taste-based.
- If editors **keep the imported timeline** → the "editor in the loop" workflow
  survives; if they discard it and re-cut from scratch, that entire pillar of the
  pitch is over.
- The **misrepresents-its-source** mark is the audit layer's core metric. If
  SelekyT scores materially better there, that is the enterprise pitch,
  quantified, for the first time.

**This experiment has not been run, and it is the only one that matters.** The
repo contains an automated audit (`tools/editorial_audit.py`) that verifies cuts
land between words and never split a sentence — a *mechanical* property. It
contains nothing that measures whether a human thinks the cut is *good*.
Everything the company claims rests on that unmeasured judgement.

---

## Appendix — sources

**External, fetched live 2026-09-19 (RESEARCHED):**

- Descript video editing features — <https://www.descript.com/video-editing>
- Descript pricing (Creator $24/mo annual, 30 media hrs, full Underlord, Create
  Clips, 4K) — <https://www.descript.com/pricing>
- Descript timeline exports — **Premiere (XML), Final Cut Pro (FCPXML), Reaper
  (EDL), Audition (SESX), Pro Tools/Logic (AAF)** —
  <https://help.descript.com/export-and-share/timeline-exports.md>
- Adobe Premiere Pro AI features (Text-Based Editing, Media Intelligence,
  Generative Extend, Object Mask, Enhance Speech, caption translation in 18
  languages, YouTube Shorts workflow on iPhone) —
  <https://www.adobe.com/products/premiere.html>
- DaVinci Resolve 21 Neural Engine (smart reframe, magic mask, facial
  recognition, text-based editing) —
  <https://www.blackmagicdesign.com/products/davinciresolve/ai>
- OpusClip (ClipAnything, ReframeAnything, 16M+ creators claimed; Pro $29/mo,
  100 GB) — <https://www.opus.pro/>, <https://www.opus.pro/pricing>
- faster-whisper CPU benchmark: 13 min audio in 51 s (small, int8, batched,
  i7-12700K, ≈15× real-time) — <https://github.com/SYSTRAN/faster-whisper>

**Third-party market-size figures could not be verified in this session** —
Grand View Research, Goldman Sachs, Fortune Business Insights, Mordor Intelligence
and Precedence Research all returned 403/404. The TAM reasoning in §10 is
therefore derived from verified pricing anchors and marked ASSUMPTION/DERIVED.
No market figure in this document is asserted as fact.

**Internal, verified on this host 2026-09-19 (VERIFIED):**

- Test suite: `834 passed, 10 skipped in 71.27s`, re-run 2026-09-19 by the
  integrating operator after the count in this appendix was written. **The
  drift this appendix was built to expose is real and it repeated:** this
  document recorded 824, `docs/AI_EXECUTION_STATE.md` recorded 819, and the
  integrating operator's independent re-run gives 834 — three different
  numbers for the same suite within one day. Every consumer of a test count
  must re-run it; quoting it forward is how it goes stale.
- Git: 154 commits, 2026-09-16 → 2026-09-19; authors "Devika Boddu" (60) and
  "desukab" (49) share `<bharathb.chander@gmail.com>`; "Ubuntu" (45).
- `engine/editorial.py:287,292` — `coherence_factor = 0.35 + 0.65*coherence`,
  `completeness_factor = 0.45 + 0.55*completeness`; `coherence = 1.0 if None`.
- `engine/editorial.py:300–317`, `models/core.py:45–52` — scoring weights.
- `engine/editorial.py:255` — `return thoughts[:4000]` (the candidate cap).
- `engine/llm_editorial.py` — 57 lines; output never read by scoring/ranking.
- `engine/nle_export.py:87` — `str(PurePath(path))` does not absolutize; shipped
  `file://out/url-e2e/...` URI. `engine/fcpxml.py:99` does it correctly.
- `engine/content_pack.py:47` — `tags = {"#reels", "#shorts", "#india"}`.
- `providers/whisper.py:255–296` — `wall`/`rt` computed, only printed; chunk
  checkpoint JSON keys carry no timing field.
- `out/url-e2e/project/report.json` — `selected_count: 3`, `candidate_count:
  4000`, `rejection_reasons: {}`, `audio: {enabled: false}`.
- `out/url-e2e/project/editorial_report.json` — `coherence=1.00`,
  `completeness=1.00` on all 3 selected clips.
- Grep across `engine/`, `selekyt/`, `providers/`, `web/`: no feedback/analytics/
  priors/telemetry ingestion; no billing/auth/tenant/payment code.
- LICENSE — "SELEKYT PROPRIETARY LICENSE, Copyright (c) 2026 Bharath Bheemreddy";
  README contains two duplicate `## License` sections.
