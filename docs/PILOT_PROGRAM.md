# SelekyT Pilot Program — Design

**Date: 2026-09-19.** This is the design of a pilot program that **does not
exist**.

> **SelekyT has run zero pilots. It has zero users, zero revenue, zero pilot
> agreements, zero letters of intent, and zero conversations with any potential
> pilot customer.** No person has been contacted. Nothing below has happened, and
> no part of it may be described in the past tense. Read this document as a plan
> awaiting a first participant, not as a report on work with one.

The grounding for this plan is `docs/CUSTOMER_DISCOVERY.md`, which itself records
that **zero customers were interviewed**. Every statement about editor behaviour
below is either third-party research with a URL or an explicit assumption, and the
assumptions are marked discardable.

---

## 0. Precondition: the pilot cannot start before G2 closes

`docs/CUSTOMER_DISCOVERY.md` is unambiguous about this, and it is the load-bearing
constraint on the entire program:

> **Step 0 — zero cost, and it gates everything else.** Import the EDL and FCPXML
> already on disk from the NASA 360 run into DaVinci Resolve (free tier; it reads
> CMX 3600). ... Until one timeline opens cleanly in one real NLE, **no
> editor-facing claim in this document is testable.** Hours, not dollars.

The reason is structural, not bureaucratic. The beachhead is *professional editors
who buy an editable timeline*. The one thing that segment is being sold is the
thing that has never been opened in an NLE (`docs/NLE_COMPATIBILITY.md`:
**"No NLE has ever opened a SelekyT EDL or FCPXML"**, VERIFIED). Running a pilot
before that import works would spend the pilot's scarce credibility on a
demonstrably open question, and a failed first import in front of a real customer
is much harder to recover from than a failed import in private.

**G2 pass criterion, stated in advance:** the sequence opens in DaVinci Resolve
(free tier) with all clips online and its duration matching the rendered
`facebook/01.mp4` (24.892 s) to within two frames, with no relink dialog. Repeat
for Premiere Pro and Final Cut Pro. Record the NLE and exact version for each.
Until then, the program below is blocked, and the correct use of the time is the
import experiment, not outreach.

**Estimated cost of G2: zero capital, hours not days, one macOS or Windows
machine with a display** (Resolve on Linux needs an unsupported workaround or a
VM — `NLE_COMPATIBILITY.md`). This is the cheapest high-value experiment
available to the company and it is currently unrun. **NOT MEASURED.**

---

## 1. Target customer profile (from CUSTOMER_DISCOVERY.md, not invented)

The recommended beachhead, quoted from that document:

> **Primary beachhead: professional video editors and small post-production
> studios (2–10 people) that sell short-form social cuts as a service.**

That document chose this segment over six alternatives because it is the **only**
segment that wants both halves of the competitive intersection — autonomous
editorial selection *and* an editable NLE timeline — while OpusClip ships
selection without a timeline, Descript ships a timeline without autonomous
selection, and the NLEs ship neither (RESEARCHED, product pages read
2026-09-19). The full scoring matrix, the falsifiers, and the trap list are in
`docs/CUSTOMER_DISCOVERY.md` §2; **this program does not re-litigate that choice.**

### The concrete first-pilot profile

From `CUSTOMER_DISCOVERY.md` §3, verbatim in substance:

> A senior freelance video editor, or a 2–5 person post/social-cut studio, that:
>
> - runs **Adobe Premiere Pro or DaVinci Resolve** as its primary NLE;
> - serves **3 or more recurring clients** with **weekly long-form talk content**
>   — video podcasts, interviews, panels, webinars, conference talks (exactly the
>   unscripted two-host speech the NASA 360 validation run exercised, VERIFIED);
> - currently delivers, per episode, a 16:9 cut plus roughly 5–10 vertical clips
>   with captions, spending on the order of **3–6 hours per episode** on selection
>   and rough-cut;
> - is paid **per episode or on a monthly retainer**, so time saved lands directly
>   in margin;
> - has at least one person willing to **open an FCPXML/EDL and judge it**, and is
>   comfortable either running a CLI or letting SelekyT run the pipeline.

**Volume at pilot scale:** 3–5 client episodes per week, 45–90 minutes each,
producing roughly 20–30 short-form clips per week.

**Explicitly not the pilot:** a creator editing their own channel; an enterprise
L&D team; a newsroom; a podcaster already editing inside Descript. Each is a
documented trap in `CUSTOMER_DISCOVERY.md` §6, and each is excluded for a stated
reason (no NLE in the workflow; unreachable procurement; shrinking demand and a
values fight over machine-made editorial judgement; entrenched incumbent).

**The one assumption in this profile worth flagging as discardable:** the 3–6
hours per episode of selection-and-rough-cut effort. It is an **ASSUMPTION** from
`CUSTOMER_DISCOVERY.md` — no editor was interviewed and no time-and-motion data
was obtained. The pilot's first job is to replace it with a measured number. If
the true figure is under one hour, the value proposition collapses regardless of
how well the rest of the program performs.

---

## 2. What "pilot-ready" requires SelekyT to build

From `docs/product-gap-map.md` (capability status, VERIFIED) and
`docs/TECHNICAL_AUDIT.md` (defects, ranked by severity). Items are ordered by
whether they block the pilot or merely limit it.

### Blocking — must be done before the first pilot episode

| # | Gap | Evidence | Why it blocks a pilot |
| --- | --- | --- | --- |
| B1 | **No NLE import ever performed** | `NLE_COMPATIBILITY.md`; product-gap-map G2; CUSTOMER_DISCOVERY falsifier #2 | The pilot's central claim is untested at the exact point it is sold. See §0 above. |
| B3 | **Web shell is unauthenticated, origin-blind, and the path jail is bypassable** | TECHNICAL_AUDIT defects #1–#3: no bearer token, session, `Origin`/`Referer` or CORS check (`web/server.mjs:345-360`); `guardedPath` uses `String.prototype.startsWith` so `/selektyt-anything/secrets` escapes the jail (`:203-209`); yt-dlp's `media_info` blob — embedding the `Cookie` header — is persisted to `pipeline_state.json` and served by `/api/file` | If the pilot runs SelekyT's web shell on any network reachable from a browser, any page the editor visits can drive the pipeline via JSON CSRF, read arbitrary files under the repo root, and retrieve persisted cookie material. A studio on shared Wi-Fi is exactly that environment. |
| B4 | **Default install cannot transcribe** | TECHNICAL_AUDIT defect #6 / `pyproject.toml`: `faster-whisper` is only in the `[transcription]` extra | A pilot editor who runs `pip install selekyt && selekyt source.mp4` hits a crash at transcription. First-run experience is the whole pilot. |
| B5 | **The pilot's own QA cannot detect a wrong output** | TECHNICAL_AUDIT defect #7: `engine/qa.py:19-43` checks existence, size, streams, orientation and duration ≥ 1 s — never aspect ratio precisely, never duration vs the edit plan, never caption presence. `black_frame_pct` and `visual_diversity_score` swallow exceptions and report clean. | During a pilot, SelekyT must be able to tell *itself* that a run went wrong. The tools that would do it — `tools/nle_check.py`, `tools/editorial_audit.py` — exist, are falsifiable, and are wired into nothing (`TECHNICAL_AUDIT`, "built and never wired in"; `pycmx` is now a declared dependency (`pycmx>=1.5` in the `nle`, `dev` and `all` extras), so the check *can* run, but nothing is scheduled to invoke it, which is the actual defect). |

**Closed since this table was written:** the former **B2**, caption-style
render-cache staleness. The cache key now carries `SELEKYT_CAPTION_STYLE`, a
record with no style key reads as a miss rather than as a match against
`minimal`, and the style is written back into the record — pinned by five tests
in `tests/test_caption_style_cache.py`. The row is removed rather than left
stamped "fixed", because a blocking list that keeps its closed items stops
telling you what is open. Four items remain.

### Limiting — pilot can start, but these cap its scale and credibility

| # | Gap | Evidence | Effect on the pilot |
| --- | --- | --- | --- |
| L1 | **No video upload in the web shell** | product-gap-map G3; `web/server.mjs` `guardedPath` confines local media to the project root, while the CLI accepts any path — the two entry points disagree by design | The editor must place media inside the project tree or use the CLI. Acceptable for a technical pilot; a friction signal to watch. |
| L2 | **No packaged desktop binary** | product-gap-map G4; the Tauri shell compiles and passes clippy but no binary has been packaged on any platform and the webview has never rendered (`docs/tauri-termux-blocker.md`) | The pilot is a CLI-and-local-web-shell pilot. This is precisely why the beachhead is *editors*, the one segment for whom that packaging is acceptable rather than disqualifying. |
| L3 | **No disk cleanup, no GC, no retention policy** | TECHNICAL_AUDIT defect #8: ~900 MB of intermediates per project (92% of cost; the deliverables are ~10 MB), and `out/` already holds multiple runs against a 29 GB disk | **This is the constraint that sets pilot capacity on the current hardware** — see §4. The fix is deleting `source/` and `transcription/` once renders validate, which nothing does. |
| L4 | **No concurrency, single-project state** | TECHNICAL_AUDIT defects #12–#13: one serial sidecar; `pipeline_state.json` read-modify-write with no lock; the web shell hardcodes one project directory | Two concurrent pilot runs clobber one state file by construction. A pilot must serialize episodes, or the project directory must become a parameter first. |
| L5 | **Two live FCPXML emitters with divergent policy** | TECHNICAL_AUDIT defect #9: `engine/nle_export.py` (the pipeline, what the user receives) vs `engine/fcpxml.py` (the web sidecar's preview). One raises on an unresolvable path, the other silently falls back | The web preview can disagree with the shipped file. In a pilot where the editor previews then exports, this is the most likely source of "the cut I got is not the cut I saw." |
| L6 | **YouTube only partially observed — one fetch, no artifact kept** | product-gap-map INPUT row: the datacenter IP range is bot-blocked on most videos; the URL path is verified end-to-end against archive.org instead | If a pilot editor's source is a private or members-only YouTube URL, that path is unverified *here*, though a residential IP should be unaffected. Have the editor supply a downloaded file. |
| L7 | **Editorial judgement exercised on one source** | `AI_EXECUTION_STATE.md` records editorial judgement as unmeasured at scale; the repo holds exactly one real end-to-end run | Not a build item — a measurement item. The pilot exists to close it. |

### Not required for the pilot

SOC 2, SSO, DPA, multi-tenancy, and any enterprise compliance posture. Those gate
the enterprise segment, which `CUSTOMER_DISCOVERY.md` §2.7 calls a trap of timing
rather than fit — year 2–3, after a referenceable case exists. Building them now
would spend the pilot's budget on a segment the program has deliberately excluded.

---

## 3. Success criteria — decided before the pilot starts

These are fixed in advance so that a negative result cannot be re-framed as a
positive one after the fact. Each has a threshold, a measurement method, and a
deadline. Where a metric is undefined today, it is marked N/A — see
`docs/METRICS.md`.

### The four pilot success criteria

| # | Criterion | Threshold to pass | How it is measured | Current value |
| --- | --- | --- | --- | --- |
| S1 | **The timeline is actually used** | ≥ 3 of 5 pilot participants open the EDL/FCPXML in their NLE and make at least one edit inside it, rather than delivering the rendered MP4 as-is | Editor self-report plus a shared screen at the review call; the falsifier is a participant who takes the MP4 and never opens the timeline | N/A — zero pilots, zero participants |
| S2 | **The timeline imports cleanly** | 100% of pilot timelines open with all clips online, duration matching the render to within 2 frames, and no manual relink | `tools/nle_check.py` on every shipped project, plus the editor's report of what their NLE showed | N/A — no NLE has ever opened a SelekyT timeline (G2 open) |
| S3 | **Editor time saved on selection and rough-cut** | ≥ 50% reduction, self-reported per episode, across ≥ 3 consecutive episodes per participant | Before/after hours log kept by the editor; the 3–6 h/episode baseline is an ASSUMPTION that the log replaces | NOT MEASURED — no editor has ever used SelekyT |
| S4 | **Willingness to pay** | ≥ 3 of 5 participants state they would pay ≥ $30/month at pilot end | Direct question at the closing conversation, asked the same way every time | N/A — zero users, zero revenue |

These thresholds are taken from `CUSTOMER_DISCOVERY.md` §5 Step 2, which sets the
same pass criterion in advance: **"at least 3 of 5 editors report ≥50% of
rough-cut time saved and would pay ≥$30/month. Below that, kill or pivot before
building anything."** That document chose $30–60/mo to sit above OpusClip's
$15–29/mo paid tiers (RESEARCHED: opus.pro/pricing, 2026-09-19).

### Pilot operating metrics (reported weekly, not pass/fail)

- Clips shipped per episode vs the editor's current 5–10 baseline (ASSUMPTION).
- Editorial-selection rework: how many of the shipped clips did the editor keep
  unmodified, partially re-cut, or discard? Measured by asking, at the review
  call, for each clip.
- `editorial_audit.py` silence-share distribution across all pilot clips — this is
  computable today and is already computed for the NASA run (`docs/METRICS.md`).
- `nle_check.py` pass rate across all pilot projects — also computable today.
- Transcription wall-clock per episode hour, on the pilot's own hardware —
  **NOT MEASURED** today; the repo holds one run and nobody timed it.

### What a "pass" does and does not authorize

Passing S1–S4 authorizes: building L1 (video upload) and L2 (one packaged desktop
build), and beginning paid conversations. It does **not** authorize claiming NLE
compatibility beyond the specific NLEs and versions recorded in
`docs/NLE_COMPATIBILITY.md`, and it does not authorize quoting time savings from a
self-report of five editors as if it were a measured average.

---

## 4. Falsifiers — what would kill the thesis

Ordered by how quickly each would end the program, and taken directly from
`CUSTOMER_DISCOVERY.md` §4. Each is stated so that it can be observed and so that
observing it forces a decision rather than a discussion.

| # | Falsifier | What it means | Decision it forces |
| --- | --- | --- | --- |
| F1 | **Editors want finished MP4s, not timelines** | Pilot participants take the rendered clips and never open the EDL/FCPXML | The wedge has no value to the segment it was chosen for. Reposition as a clip generator against OpusClip (bad odds — funded incumbents with free tiers) or stop. |
| F2 | **The timeline does not import** | Relinking errors, wrong frame-rate handling, the "invalid edit with no respective media" class of failure Descript documents in its own export notes | The differentiator is unverified at the point of sale. **This is the largest open risk and it is currently wide open** — it is exactly what §0 blocks on. |
| F3 | **Editorial judgement does not survive real clients** | Selections need so much rework that no time is saved | The product fails on value regardless of segment fit. Note the direction of the risk: `editorial_audit.py` currently passes on every clip of the one existing run, which is evidence the audit is not yet stressed, not that judgement is good. |
| F4 | **Threat perception beats utility** | Editors read "AI first-pass editor" as replacement and refuse to trial | Adoption is blocked regardless of capability. Mitigation is positioning — ship the timeline, the editorial report and the provenance, never the clip as the deliverable — but that mitigation is itself a hypothesis. |
| F5 | **The intersection closes** | Descript adds autonomous selection, or OpusClip adds a timeline export | The wedge compresses to "open-source, self-hosted, auditable." A real but smaller position. Monitor monthly, not annually. |

A sixth falsifier is added by this program, from `TECHNICAL_AUDIT`:

| # | Falsifier | What it means | Decision it forces |
| --- | --- | --- | --- |
| F6 | **A pilot run ships a wrong output and nothing detects it** | QA's exception-swallowing metrics (B5) surface in front of a customer — the former caption-style cache bug (B2) that this row also named is fixed and pinned by `tests/test_caption_style_cache.py` | The program pauses to fix the feedback loop, because a pilot whose numbers cannot be trusted produces no usable evidence either way. |

---

## 5. The pilot, week by week

**Duration: 6 weeks per participant cohort, 5 participants, one cohort.**
No participant pays during the pilot. SelekyT runs the pipeline; the editor
supplies source material and judgement.

- **Week 0 (internal, before any participant is contacted).** Close G2 (§0). Fix
  B4 and the `guardedPath` prefix bypass in B3 — B2, the caption-style cache
  bug, is already closed and pinned. Wire `nle_check.py` and
  `editorial_audit.py` into the run so every pilot project is audited
  automatically. `pycmx` is already a declared dependency (`pycmx>=1.5` in the
  `nle`, `dev` and `all` extras), so that half of the item is closed; the open half
  is the invocation. Record the NLE, version and result of each
  import in `docs/NLE_COMPATIBILITY.md`. **If G2 fails here, the pilot does not
  start.**
- **Week 1.** Onboarding: one episode from one client of one participant. SelekyT
  runs the pipeline and returns the editorial report, the renders and the
  timeline. The editor's only task is to open the timeline and say what they see.
  No time logged yet — the baseline has not been established.
- **Weeks 2–4.** Full volume: 3–5 episodes per week, 45–90 minutes each. The
  editor logs before/after hours per episode and tags every shipped clip
  kept / re-cut / discarded. Weekly review call, 30 minutes, same questions every
  week.
- **Week 5.** Each participant opens their NLE live on a shared screen and makes
  one real edit inside a SelekyT timeline. This is the S1 measurement, and it is
  observed rather than self-reported.
- **Week 6.** Closing conversation: the S4 willingness-to-pay question, asked the
  same way to every participant. Then the go/no-go against §3.

**Recruitment channel** (from `CUSTOMER_DISCOVERY.md` §5 Step 1): a one-page offer
placed only in the communities professional editors already occupy — *"Send us one
long-form episode. We'll return an EDL/FCPXML you can open in Premiere or Resolve,
plus rendered cuts and the editorial report — free, in exchange for 15 minutes and
honest criticism."* The four things measured at that stage are reply rate per
community, whether the recipient opens and refines the timeline (F1),
self-reported hours saved (F3), and would-you-pay at $30–60/mo (S4).

**Cost of recruitment: under $50 and days, not weeks.** No paid acquisition, no
app-store presence, no procurement. That is the point of the beachhead choice.

---

## 6. Cost to run this pilot on the current hardware

Measured on this host, 2026-09-19 (VERIFIED):

| Resource | Measured state | Relevance |
| --- | --- | --- |
| CPU | AMD EPYC 7763, **4 cores visible** to the process | Transcription is CPU-only; `pipeline_state.json` records `num_threads: 4` on the existing run |
| Memory | 15.9 GB total, **7.7 GB available** | One pipeline fits comfortably; `EnergyVAD` reads a whole decoded WAV into memory (`TECHNICAL_AUDIT` L15), so concurrency is not free |
| GPU | **None** (`nvidia-smi` absent) | No accelerated transcription or rendering; the pilot is entirely CPU-bound |
| Disk | **29 GB total, 4.6 GB free (84% used)** | **This is the binding constraint** |
| Per-project footprint | ~900 MB, of which **92% is retained intermediates**; the deliverables (renders + captions + timelines) total ~10 MB. Source `NASA_360_program5_HD.mov` alone is 843 MB. | Pilot episodes are 45–90 min, i.e. 2–4× this footprint each |

### The arithmetic

One 24:29-minute episode costs ~900 MB of disk. A pilot week is 3–5 episodes at
45–90 minutes — call it **3–5 GB per week at the low end, more if intermediates
are never cleaned**, against 4.6 GB free.

**Conclusion: the pilot cannot run on this hardware as the code stands today.**
The system would fail on disk, silently, in `OSError` from `write_json_atomic`
mid-run after the checkpoint had already been written (`TECHNICAL_AUDIT` defect
#8) — a failure mode that loses a partially-delivered episode and produces no
actionable error for the pilot participant.

Two options, and the choice is a real one:

1. **Fix L3 first (recommended).** Delete `source/` and `transcription/` once the
   renders validate. Nothing does this today, and the fix is small. It takes
   per-project disk from ~900 MB to ~10 MB — a ~90× reduction — which makes the
   existing hardware sufficient for the whole six-week cohort with room to spare,
   and makes disk a non-issue for any future scale conversation.
2. **Add disk.** ~50 GB of attached storage would carry the cohort with
   intermediates retained. This treats the symptom and leaves the failure mode in
   place for every customer after the pilot.

**Transcription time is the other unknown, and it is genuinely unknown.** The
existing run decoded 425 segments / 4,236 word-timed tokens in five 5-minute
chunks with a 15-second overlap on 4 threads. **Nobody recorded the wall-clock.**
A 45–90 minute pilot episode is 2–4× that work, CPU-only, with no GPU. **NOT
MEASURED** — the pilot must time it in Week 1, because if a 90-minute episode
takes more than a working day on this hardware, the pilot needs either more cores
or a GPU, and that changes the pilot's cost from "effectively zero" to a real
number. Do not estimate this number in a meeting. Measure it.

### Pilot cost summary

| Item | Cost | Basis |
| --- | --- | --- |
| G2 import experiment (precondition) | **zero capital**, hours of one person's time on an existing macOS/Windows machine | `NLE_COMPATIBILITY.md` — DaVinci Resolve free tier |
| Recruitment | **under $50** | `CUSTOMER_DISCOVERY.md` §5 Step 1 — community outreach, no paid acquisition |
| Compute (this host, with L3 fixed) | **zero capital** — existing 4-core / 16 GB / no-GPU box | §6 arithmetic above |
| Compute (if transcription is too slow) | one GPU instance or more cores | **NOT MEASURED** — decide after Week 1 timing |
| People | 10–20 hours of work for the experiment phase; then weekly review calls | `CUSTOMER_DISCOVERY.md` §5; this document §5 |
| Total capital required before Week 1 | **approximately zero** | — |

The pilot is designed so that its most likely failure mode — *editors don't want a
timeline* (F1) — is discovered before any compliance, packaging or scale work is
spent. That sequencing is the single most important property of this plan, and it
is inherited unchanged from `CUSTOMER_DISCOVERY.md`.

---

## 7. What must never be said about this program

- That a pilot exists, has existed, or is underway. **Zero pilots.**
- That any editor has tested SelekyT, saved time, or offered to pay. **NOT
  MEASURED** — no editor has ever used it.
- That the program has participants, a waitlist, or pipeline. It has a design.
- That NASA, archive.org, or any tool vendor endorses it. The pilot's source
  material must be rights-permitted by the participant, and public-domain status
  is not an endorsement.
- That SelekyT is compatible with any NLE, until `docs/NLE_COMPATIBILITY.md`
  carries a real import result with an NLE name and version.

---

## 8. Cross-references

- `docs/CUSTOMER_DISCOVERY.md` — the beachhead recommendation, the scoring matrix,
  the falsifiers, and the sourcing appendix. This program implements §3–§5 of that
  document and does not alter its recommendation.
- `docs/NLE_COMPATIBILITY.md` — the open G2 gap, the four defects already found,
  and the exact import experiment that is this program's precondition.
- `docs/product-gap-map.md` — per-capability status: what is verified, what is
  code-reviewed only, and what is missing.
- `docs/TECHNICAL_AUDIT.md` — defects B3–B5 and L3–L5, ranked by severity, with
  file:line citations. (B2, the caption-style render-cache staleness, is closed
  and pinned by `tests/test_caption_style_cache.py`.)
- `docs/METRICS.md` — the metric definitions this program reports against, with
  the current values that are computable today and the N/A markers for those that
  are not.
- `docs/DEMO_RUNBOOK.md` — how to show the one existing run honestly while this
  program remains unstarted.
