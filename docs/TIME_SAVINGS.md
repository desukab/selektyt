# SelekyT — The Time-Savings Claim

Prepared 2026-09-19. This is the **weakest claim in the venture**, and this document treats it
adversarially rather than as a selling point.

**Company status, stated plainly:** SelekyT has **zero users, zero revenue, zero pilots, and zero
investors**. There is no customer, no pilot, and no usage data of any kind behind anything below.

> **UPDATE — a later pass on the same day, after the corpus ran.** Sections 1–9 below were written
> before the 11-source corpus existed. They are kept intact because they are the record of the model
> as it stood, and the model is still the model. **§10 onwards is controlling.** It (a) audits every
> assumption in the 18×–73× chain, (b) recomputes the model against machine rates that are now
> *measured* rather than summed from components, (c) shows that the top of the quoted range is
> **arithmetically infeasible**, not merely unmeasured, and (d) restates what the project may say
> today. Nothing in §1–9 is deleted and the 18×–73× figure is still there, still labelled a model —
> but §10.3 corrects its inputs and §10.4 bounds its ceiling, and **§8 is superseded by §10.10**.

## Evidence conventions

| Marker | Meaning |
| --- | --- |
| **VERIFIED** | I ran it on this host on 2026-09-19. Command given so it is reproducible. |
| **RESEARCHED** | Retrieved from a live public URL on 2026-09-19. URL and retrieval date given. |
| **ASSUMPTION** | My inference or a modelling input. Discardable. Not evidence. |

---

## 1. The one-sentence version

**No human editor has ever been timed on this task — not with SelekyT, not without it — so the
time-savings ratio is a model built on an unmeasured denominator, not a measurement.** What *is*
measurable, and is measured below, is the machine time to produce the deliverables: about
**6.6 minutes of CPU** to turn a 24.5-minute tape into 12 rendered clips with captions and
per-cut timelines. Everything beyond that number is assumption.

---

## 2. What is actually computable: machine time

Measured today on the one real end-to-end run that exists — NASA 360 Episode 5,
1469.77 s (24:29), run on this 4-core / 15 GB / CPU-only host.

VERIFIED inputs to the calculation:

```
ffprobe out/web/pipeline/project/source/NASA_360_program5_HD.mov
  duration=1469.769770  720x486  29.97 fps  h264  883,894,825 B
```

The run shipped **6 clips × 2 presets = 12 renders, 99.254 s of video per preset** (VERIFIED by
`ffprobe` over all 12 files), plus per-cut EDL, FCPXML 1.11, SRT, ASS.

Machine-time budget for that job, measured component by component on the real artifacts:

| Stage | Wall time | RTF | Basis |
| --- | --- | --- | --- |
| Audio extraction / decode to 16 kHz mono | 147.0 s | 0.10 | **ASSUMPTION** (carried from `docs/BUSINESS_MODEL.md` §3; not measured) |
| Transcription (`small`/int8/beam 1/4 threads) | **221.2 s** | **0.1505** | **VERIFIED** — 36.12 s for 240.0 s of the reference run's real 16 kHz mono audio, model warmed, `faster_whisper.WhisperModel("small", device="auto", compute_type="int8", cpu_threads=4)` |
| Editorial pass: story units → rank → context gate | **3.55 s** | **0.00241** | **VERIFIED** — 3,546 ms (best of 3) on the real `transcript.json`: 425 segments / 4,236 words → 4,000 thoughts → 11 gate-passed candidates, 6 shipped |
| Captions (SRT + ASS, 6 clips) | **17.6 ms** | 0.000012 | **VERIFIED** |
| Timeline build + EDL + FCPXML (6 clips) | **3.3 ms** | 0.000002 | **VERIFIED** (0.4 + 1.4 + 1.5 ms) |
| Render 16:9 (99.254 s of output) | **12.66 s** | 0.00862 | **VERIFIED** — `engine.render.render_clip` on the real source, 60 s window, 7.654 s wall → RTF 0.1276 |
| Render 9:16, anamorphic (99.254 s of output) | **9.18 s** | 0.00625 | **VERIFIED** — same harness, 5.552 s wall → RTF 0.0925 |
| **TOTAL** | **393.6 s ≈ 6.6 min** | **0.2678** | **COMPUTED** — 4 measured stages + 1 assumed |

> **This table is now reproducible.** `tools/microbench.py` re-derives its VERIFIED rows
> from committed artifacts; §2b re-runs it against this job's own source and transcript,
> and transcription, both renders and the export stages all come back within 4%. The
> editorial row reproduces to 19% and §2b names the workload difference that explains
> part of it.

**Two things this number is not.**

1. **It is not a measured pipeline run.** No end-to-end wall clock for the pipeline has ever been
   recorded — `docs/BUSINESS_MODEL.md` §1 says so, and I did not run the pipeline (it takes hours).
   The 6.6 minutes is a **sum of component measurements**. Stages run serially on this host, so the
   sum is a fair estimate, but it is a reconstruction.
2. **It excludes the source download.** The 883,894,825-byte source is fetched over the network
   (VERIFIED still available: `https://archive.org/download/NASA_360_program5_HD/NASA_360_program5_HD.mov`
   returns 206, byte-exact match to the local file). Download time is unmeasured and would be
   additive on a real job.

### 2a. The instrument that re-derives the table above

The four **VERIFIED** rows of §2's table came from a terminal session whose output
was never saved. §10.7 says so and points at the missing artifact. `tools/microbench.py`
is that artifact: it runs each stage against committed files and prints a table of the
same shape, so the numbers above can be reproduced rather than trusted.

**It was run, and it found one of §2's numbers was wrong in a way the doc could not
have caught.** The transcription row is the only stage the instrument can compare
directly — same model, same 240 s window, same int8 / beam 1 / 4-thread configuration —
and the first version of the instrument reported **796.5 ms** for it, against §2's
36.12 s. That was a bug in the instrument, not in §2: the whisper backend caches chunk
checkpoints in the audio file's own parent directory, so the warm-up call transcribed
for real and every *measured* call read a JSON off disk and was timed doing file I/O.
`_bench` now hands each run a fresh `checkpoint_dir`, and the stage reports
**39.0 s** — 49× the cached figure, and within 8% of §2's 36.12 s on different source
audio. The correction ran *against* the document's favour and is recorded here for the
same reason §10.4's corrections are: an instrument that reports a sub-second
transcription is not a corroboration, and a reader who believed it would have
understood the machine cost of this pipeline to be ~50× lower than it is.

**Reproduced on 2026-09-19**, `--repeat 3`. The first pass ran against
`corpus/media/royal-society-cloke.webm` (626.0 s, 25 fps, 1080p) rather than §2's own
source, on an assumption that §2's 883 MB `.mov` had been deleted to free disk. **The
assumption was mine, not the record's — §10.7 says the deleted media was
`ooi-vertical`'s, and that is true.** The nasa360 source is intact at
`out/web/pipeline/project/source/NASA_360_program5_HD.mov`, 883,894,825 bytes, matching
§2's VERIFIED `ffprobe` line byte-for-byte, so §2's exact job *can* be re-run and §2b
does so. The table below is kept because it is the 1080p data point — the only one this
host has — and §2b needs it to separate "corroborated" from "corroborated on the same
source".

| Stage | Wall (best of 3) | CPU (best run) | Work | RTF |
| --- | --- | --- | --- | --- |
| Transcription, model warm, fresh checkpoint per run | **39,039 ms** | 123,332 ms | 240.0 s of 16 kHz mono | **0.163** |
| Editorial (`make_thoughts` → `rank` → context gate) | 1,322 ms | 1,321 ms | 156 segments / 1,980 words → 1,809 thoughts → 8 gate-passed | — |
| Captions (SRT + ASS) | 31 ms | 31 ms | 4 clips, style `minimal` | — |
| Timeline build + EDL + FCPXML | 3.0 ms | 3.0 ms | 4 clips at 25.000 fps | — |
| Render 16:9 | 16,736 ms | 1,052 ms | 60 s of output | **0.279** |
| Render 9:16, anamorphic | 11,646 ms | 9,512 ms | 60 s of output | **0.194** |

**What this corroborates, and what it does not.**

- **Transcription RTF: corroborated.** 0.163 here against §2's 0.1505, on a different
  source with a different speaker and accent. §3's corroboration of `BUSINESS_MODEL.md`
  stands from the other direction as well.
- **Render RTF: corroborated in direction, not in value.** §2 has 16:9 at 0.1276 and
  9:16 at 0.0925 on a 720×486 SD source; here they are 0.279 and 0.194 on 1080p. The
  ratio between the two geometries is the interesting part and it is stable — 9:16 is
  **30% cheaper** than 16:9 here (0.194 / 0.279), against 28% in §2 (0.0925 / 0.1276).
  The crop narrows the coded frame to 608×1080, so x264 has fewer pixels to encode; the
  CPU column shows where the saving is *not* — 9:16 spends 9.5 s of CPU against 16:9's
  1.05 s, i.e. the crop-and-scale filter graph is the expensive part and the encoder is
  the cheap part. A host that is CPU-bound rather than wall-bound would see a much
  smaller gap.
- **Editorial and captions: not a corroboration.** They are measured on
  royal-society-cloke's `transcript.json` (156 segments / 1,980 words), not nasa360's
  (425 segments / 4,236 words). §2b re-runs them on the right transcript.
- **No total is printed, deliberately.** Each stage is measured on a different window —
  240 s of audio for transcription, 60 s of source for render, the whole shipped clip
  set for captions and timeline. Summing them would be the exact double-count §10.7 was
  corrected for.

### 2b. Re-running §2's own job — the table reproduces

§2a established the instrument. This section points it at §2's *exact* inputs — the
surviving `NASA_360_program5_HD.mov` and `corpus/nasa360-ep5_run/project`'s committed
`transcript.json` and `selections.json` — so the four VERIFIED rows of §2 are no longer
quotable only from an unsaved terminal session.

| Stage | §2, published | Reproduced | Agreement |
| --- | --- | --- | --- |
| Transcription (240.0 s of 16 kHz mono, warm) | 36.12 s, RTF **0.1505** | 35.44 s, RTF **0.1476** | **−1.9%** |
| Editorial (425 seg / 4,236 words → 4,000 thoughts) | 3,546 ms | 2,869 ms | **−19.1%** — see below |
| Captions (SRT + ASS, 6 clips) | 17.6 ms | 19.8 ms | +12.5% |
| Timeline + EDL + FCPXML (6 clips) | 3.3 ms | 3.5 ms | +6.1% |
| Render 16:9 (60 s window) | 7.654 s, RTF 0.1276 | 7.700 s, RTF **0.1283** | **+0.5%** |
| Render 9:16 (60 s window) | 5.552 s, RTF 0.0925 | 5.334 s, RTF **0.0889** | **−3.9%** |

Three of the four VERIFIED rows reproduce within 4%, and the render geometries within
0.5% and 3.9%. **§2's machine-time budget is sound, and this is now checkable rather
than asserted** — which is what R13 said it was not.

The editorial row does not reproduce, and the reason is a workload difference the
instrument introduced, not a defect in §2: §2's run produced **11 gate-passed
candidates, 6 shipped**, while the instrument passes `rank(..., limit=8)` and stops at
**8**. The expensive part — `make_thoughts` over all 425 segments — is identical, and
the eleven-versus-eight `validate()` calls are microseconds of the 2.9 s, so the
−19% is almost certainly host variance rather than the workload gap (§10.7 measures
~1.7× run-to-run spread on this box from load alone). But *almost certainly* is not
*measured*: the honest statement is that the editorial stage reproduces to within 19%
and that the instrument's candidate cap is the one known difference between the two
runs. Removing the cap is a one-line change; it was not made because re-running it
costs another transcription pass and the row is 0.7% of the job's machine time either
way.

**One finding the reproduction produced that §2 could not have.** All three sources now
measured show the 9:16 render consistently **27–31% cheaper** than 16:9 — 0.725 in §2,
0.693 here, 0.696 on the 1080p source in §2a. That is a stable property of the pipeline
across resolutions and frame rates, and it is not obvious: the vertical crop writes a
narrower coded frame (608×1080 from 1080p, 308×486 from SD), so the encoder does less
work. §2a's CPU column shows the counter-case — on 1080p the 9:16 pass spends 9.5 s of
CPU against 16:9's 1.05 s, so a CPU-bound host pays for the crop-and-scale filter graph
what a wall-bound host saves on encoding.

---

## 3. Cross-checks against `docs/BUSINESS_MODEL.md`, and where it is wrong

`docs/BUSINESS_MODEL.md` measured the two compute-dominant stages on 2026-09-19. I re-ran both
myself today on the same real artifacts:

| Quantity | `BUSINESS_MODEL.md` | My measurement | Agreement |
| --- | --- | --- | --- |
| Transcription RTF, `small` | 0.1651 | **0.1505** | corroborated — the published figure is 8.8% pessimistic |
| Render RTF, 16:9 | 0.1232 | **0.1276** | corroborated — the published figure is 3.6% optimistic |
| Editorial + export RTF | 0.01 (ASSUMPTION) | **0.00243 measured** | the assumption was 4.1× more pessimistic than reality — safe direction |

**But the reference artifacts were regenerated at 2026-09-19 07:36, after `BUSINESS_MODEL.md` was
written at 06:20, and its delivered-output figures describe a run that no longer exists.**
Re-measured today against the artifacts that are actually on disk:

| Quantity | `BUSINESS_MODEL.md` says | Re-measured today |
| --- | --- | --- |
| Clips per preset | 3 (`facebook/0{1,2,3}.mp4`) | **6** |
| Delivered duration | 116.95 s total, 58.48 s per preset | **198.307 s total, 99.154 s per preset** |
| Clip yield | 7.96% (3.98%/preset) | **13.506% total (6.753%/preset)** |
| Delivered bytes | 9.9 MB | **19.13 MB** (11.90 16:9 + 7.20 9:16 + 0.03 captions + 0.009 timelines) |
| 9:16 coded geometry | 272×486, DAR 1088:2187 | **308×486, SAR 8:9, DAR 1232:2187** |
| 16:9 output | "720×486 → cap 1080 wide" | **864×486 square-pixel**; the 4:3 content is padded, never upscaled to 1080 |

None of these change the cost story — the machine time above is built from my own measurements, and
the corrected yield makes the render *cheaper* per source-minute than the published figure implied.
But **the 116.95 s / 7.96% / 9.9 MB numbers must not be quoted again.** `docs/AI_EXECUTION_STATE.md`
is known-stale and was not used here.

---

## 4. What is not computable: the human-editor time

**No human editor has ever been observed performing this task, in any condition, by anyone in this
venture.** There is no time-and-motion data, no logged session, no editor's invoice, nothing. The
denominator of the ratio is therefore an assumption, full stop.

The assumption, stated with its basis so it can be discarded or replaced:

**ASSUMPTION — a skilled video editor takes 2 to 8 hours** to produce, from a 24.5-minute raw tape,
the equivalent of what SelekyT produced: 6 selected clips, each delivered in 16:9 and 9:16, with
burned-in word-timed captions, plus a per-cut EDL and FCPXML for each.

Basis for the range, such as it is:

- **Lower bound reasoning.** Merely *viewing* the tape once costs 24.5 minutes at 1× speed, and
  selection requires at least two passes (a log pass and a selection pass) → ~49 minutes before a
  single cut is made. Cutting 6 clips, reframing each to a second aspect ratio, timing captions to
  words, and encoding 12 outputs is realistically 60–90 minutes of focused work. So ~2 hours is a
  *generous-to-tight* lower bound, not a typical case.
- **Upper bound reasoning.** 8 hours is what a cautious editor bills when the brief is unclear, the
  tape is dense, or the client wants revisions — the normal case, not the pathological one.
- **The range is 4× wide.** That is the honest reflection of having no data. Any single point
  estimate in this range would be a guess dressed as a number.

**The rate used to convert that time to money** (RESEARCHED, not assumed):
the US Bureau of Labor Statistics Occupational Outlook Handbook states "The median annual wage for
film and video editors was **$75,420 in May 2025**", with combined-occupation quick facts of
"$75,100 per year" / "**$36.10 per hour**" —
https://www.bls.gov/ooh/media-and-communication/film-and-video-editors-and-camera-operators.htm,
retrieved 2026-09-19. ($75,420 ÷ 2,080 h = $36.26/h, consistent with the quoted hourly figure.)

Two caveats that cut **against** the claim, stated because they matter more than the arithmetic:

- This is an **employee wage for the occupation**, not a freelance billable rate. Freelance rates
  run above employee wages (no benefits, downtime, self-employment tax), so using $36/h understates
  the true cost of the human hour. That understatement makes any dollar "saving" below smaller than
  it would be in a real transaction.
- It is **not the price of the task**. Editors quote per project, and a 6-clip short-form package is
  typically priced well above a raw hourly multiply-out.

---

## 5. The ratio, and why it is a model

| Quantity | Value | Status |
| --- | --- | --- |
| Machine time to produce the deliverables | **6.6 min** | VERIFIED (as a sum of component measurements — see §2) |
| Human-editor time for the equivalent work | **2–8 h** | **ASSUMPTION — no editor was ever timed** |
| **Ratio** | **18× to 73×** | **COMPUTED from one measurement and one assumption** |
| Human labour at $36.26/h | **$72.52 – $290.08 per tape** | COMPUTED from RESEARCHED rate × ASSUMED time |
| Cloud compute for the same job, `small` model | **$0.0195 at the stated RTF of 0.2678** | COMPUTED from `docs/BUSINESS_MODEL.md` §4 (AWS `c7i.xlarge` $0.1785/h), but **the arithmetic printed here did not check out and has been corrected**: $0.1785/h × 0.2678 × 1469.77 s / 3600 = **$0.0195**, not the $0.021–$0.024 quoted before. $0.021 is `BUSINESS_MODEL.md` §4(a)'s figure, which is derived from that doc's *own* RTF of **0.2917** (0.1785 × 0.2917 × 1469.77 / 3600 = $0.0213); $0.024 corresponded to nothing in either document and may have been the corpus-wide 0.330 RTF (→ $0.024) applied to the reference source's duration. The spread is now reported as the two defensible values, **$0.0195–$0.0213, depending on which RTF is used**, and the choice between them must be stated wherever the number is quoted |

The arithmetic spread is absurd on purpose: **$70–290 of assumed human labour against ~$0.02 of
measured compute is a 3,000–14,000× value-to-COGS ratio.** When a ratio is that favourable, the
ratio is not the finding — the finding is that **the entire claim rests on whether the deliverable
is usable at all**, which is a question this venture has never tested with a human being.

**The ratio must not be presented as a measurement, a saving, or a result.** It is a sensitivity
map: "if the unmeasured human task takes 2–8 hours, then the measured machine cost is 18–73×
smaller." Every part of that sentence after "if" is discardable.

---

## 6. Why the ratio can be worth less than nothing

Adversarial reading, because this is where the claim dies if it dies anywhere.

1. **The two sides of the ratio are not the same deliverable.** A human editor's 6 clips are *their*
   selections. SelekyT's are its own. **If the selection is unusable, the machine time is not a
   saving — it is pure additive waste**, because the editor must still watch the tape, still find
   the moments, and now also evaluate and discard 12 renders and 6 timelines. Under that outcome the
   sign of the saving flips. Nothing in the current artifacts can rule this out: the existing
   evidence is 6 clips passing two automated audits on **one** source, and `tools/editorial_audit.py`
   checks invariants (no clipped words, cues fit the render, no duplicate sentences) rather than
   editorial value.
2. **The audit that passes is partly circular.** `tools/nle_check.py` returns **86 PASS / 0 FAIL /
   6 SKIP** (VERIFIED), and the 6 skips are the EDL-parse checks — the only ones that use a parser
   SelekyT did not write. The checks that run verify the generator against itself.
3. **The reference run fails its own most editorial criterion.** Clip 05 has a **40.8% silence
   share** (VERIFIED from `tools/editorial_audit.py` output) yet passes, and is the *highest-bitrate*
   clip of the six. A 40%-dead-air clip shipped as a "short" is what a human editor would reject.
   That is visible in the artifacts today, on the one source the claim rests on.
4. **Machine time is not wall-clock time saved.** It excludes the source download (883 MB,
   unmeasured), queueing, and — critically — **the human review time to accept or reject the
   outputs**, which is unavoidable and is not in the budget at all.
5. **898 passing tests measure nothing about time.** VERIFIED: `.venv/bin/python -m pytest -q` →
   898 passed, 7 skipped in 112 s. Those tests assert correctness and determinism. They carry no
   information about how long a person would take, or whether the output is worth keeping.

---

## 7. The experiment that would make the claim real

A controlled, blinded editor study. Until this is run, the time-savings claim has no evidence
behind it and should not be stated as a fact in any pitch, pricing page, or investor document.

### Design

- **Materials.** The same 24.5-minute tape (or, better, 3 tapes from the corpus in
  `docs/EVALUATION.md`, one of them non-English and one native 9:16), with a written brief fixed in
  advance: "deliver 6 clips, 9–25 s each, in 16:9 and 9:16, with captions, plus per-cut timelines."
- **Participants.** `n = 6` professional editors, each randomly assigned to both arms with order
  counterbalanced (within-subject, so each editor is their own control and individual speed
  differences cancel).
- **Arm A (manual).** The editor receives the raw tape and the brief, works in their own NLE,
  declares "ready to deliver".
- **Arm B (assisted).** The editor receives SelekyT's outputs for the same tape — the 12 renders,
  the per-cut EDL/FCPXML, the captions — and finishes to the same brief, free to keep, modify, or
  discard anything. This is the honest arm: it measures *finished* time, not *first-pass* time.
- **Primary outcome.** Wall-clock from task start to editor-declared "ready to deliver", per arm.
- **Quality control (mandatory).** A **blind panel of 3 editors** who did not participate, rating
  the final deliverables on a fixed rubric (hook, coherence, no clipped words, pacing, payoff),
  unaware which arm produced which set. **Speed without equal-or-better quality is not a saving —
  it is a downgrade delivered faster.**
- **Secondary outcomes.** Adoption rate (fraction of SelekyT's timelines imported and kept
  unmodified), re-cut rate, and editor free-text response.

### Cost

| Item | Calculation | Cost |
| --- | --- | --- |
| 6 editors × 3 h (≈1.5 h per arm incl. briefing) | 6 × 3 × $36.10 | **$649.80** |
| 3 blind raters × 2 h | 3 × 2 × $36.10 | **$216.60** |
| Editor recruitment and scheduling | — | **ASSUMPTION** not to exceed ~$200 |
| Machine time for 3 corpus tapes | ~20 min on this host | free (owned host) |
| **Total** | | **≈ $867, plus recruitment** |

This is the single cheapest measurement that could convert the venture's central claim from a model
into a result. It is roughly the cost of one month of the cheapest cloud instance in
`docs/BUSINESS_MODEL.md` §2.

### Falsifier — declared in advance

**The time-savings claim is FALSE if, in Arm B, editors are not at least 30% faster to
"ready-to-deliver" at equal-or-better blind quality.**

Two sub-conditions that would also falsify it, and which the study must be able to detect:

- **Quality collapse:** if Arm B deliverables are rated meaningfully worse than Arm A's, then
  whatever speed difference exists is a downgrade, not a saving. Report both, never speed alone.
- **Rejection:** if editors discard SelekyT's selections and re-cut from scratch in a majority of
  cases, then the selection layer contributes no time saving and the machine time is additive
  overhead rather than a substitute for editor time.

A study that shows ≥30% faster-at-equal-quality across 6 editors on 3 diverse tapes would move the
claim from ASSUMPTION to VERIFIED for those conditions, and only for those conditions.

---

## 8. What may and may not be said in the meantime

**May be said** (all VERIFIED or clearly labelled):
- The machine time to produce the deliverable set is ≈ 6.6 minutes of CPU on a 4-core host for a
  24.5-minute source, measured component-by-component on the real artifacts, with the commands
  reproducible in §2.
- Variable compute cost is ~$0.02 per job — a number so low it cannot be the binding constraint on
  the business (`docs/BUSINESS_MODEL.md` §10 reaches the same conclusion from the cost side).
- Editorial quality is unmeasured, and transcription accuracy has now been scored against a
  reference transcript for clean read English — 3.38% WER on 50 LibriSpeech test-clean clips with
  the production decode parameters (`tools/wer_bench.py`) — but not on any video source in the
  corpus and not on non-English or noisy audio (see `docs/EVALUATION.md` §1.1 and §3-D4).

**May not be said, because none of it has been measured:**
- That SelekyT "saves an editor N hours" or "is N× faster than a human" for any value of N.
- That the ratio is a saving, a result, or a benefit anyone has experienced. No user exists.
- That any editor has reviewed, approved, or adopted a SelekyT output. None has.
- That the 18×–73× figure in §5 is anything other than a computed sensitivity built on an
  assumption, whose denominator has never been observed.
- That the product has been timed end-to-end. It has not; only its components have.

---

## 9. Summary

- **Measured:** 6.6 minutes of machine time (0.2678 RTF) turns a 24.5-minute tape into 12 rendered
  clips with captions and per-cut EDL/FCPXML. VERIFIED as a sum of component measurements on the one
  real run.
- **Not measured, ever:** how long a human editor takes on the same task. The 2–8 hour figure is an
  ASSUMPTION with a stated basis and a 4× spread.
- **The ratio (18×–73×) is a model, not a measurement.** Its denominator has never been observed.
- **The claim's real weak point is not the ratio's size — it is that the deliverable's usability is
  untested**, and the reference run already contains a shipped clip with 40.8% dead air that a human
  editor would likely reject.
- **The fix is cheap and specified:** a blinded 6-editor study at ≈$867, with a 30%-faster-at-
  equal-quality falsifier declared in advance (§7). Until that runs, this claim should be presented
  as an untested hypothesis in every external document.

---

# 10. Post-corpus update — the model audited, recomputed, and bounded

Written later on 2026-09-19, after `tools/corpus_run.py` ran the 11-source corpus. Sections 1–9
above are the record of the model as it stood before the corpus existed. **This section controls.**
Its status markers are the same conventions used above: VERIFIED (I ran it on this host today,
command given), COMPUTED (arithmetic from VERIFIED inputs), ASSUMPTION (discardable).

One new marker is needed, and it is the point of the whole document:

| Marker | Meaning |
| --- | --- |
| **MODEL, NOT MEASURED** | A number derived from a measured machine side and an *unmeasured* human side. It is a sensitivity map. It is not a result. |
| **PENDING EXTERNAL VALIDATION** | A design or instrument that exists on paper. No participant has been contacted. Founder approval for external contact has not been given. |

**No human editor has been timed, observed, recruited or contacted. Nothing in this section is a
measurement of any person's work. Every measured number below is a machine-side number producible
by a command that exists in this repository today.**

---

## 10.1 What is now measured that was not when §1–9 were written

§2 said plainly that **no end-to-end wall clock for the pipeline had ever been recorded** and that
the 6.6-minute figure was a *sum of component measurements*. That is no longer true. The corpus run
recorded a real wall clock per source:

```
corpus/results.jsonl   # one JSON record per source, field elapsed_s, written by tools/corpus_run.py
```

`elapsed_s` is `time.monotonic()` around the whole `run_one()` in `tools/corpus_run.py:154` — it
therefore **includes the network fetch of the source, the ffprobe, the model load, transcription,
editorial passes and rendering**. It is a full end-to-end wall clock, and it is the first one that
has ever existed for this pipeline. 11 sources, 7 of which completed. This is genuine measured data,
and it changes the machine side of the model in three ways that §1–9 could not know about.

---

## 10.2 The assumption audit — every input in the 18×–73× chain

The chain in §5 is: *machine time (from §2) ÷ assumed human time (from §4)*. Each input, labelled:

| # | Input | Value used in §5 | Status | Where it came from | Drifted? |
| --- | --- | --- | --- | --- | --- |
| A1 | Transcription RTF, `small`/int8/beam 1 | 0.1505 | VERIFIED, but on a **warmed** model and a **240 s** audio sample | §2 microbenchmark | **YES — scope.** Re-measured cold/in-pipeline on a real source: **0.186** warm, **0.269** as the pipeline itself reports it in-run (§10.7). The published figure is 1.24×–1.79× optimistic under pipeline conditions. |
| A2 | Audio decode to 16 kHz mono | 0.10 RTF | **ASSUMPTION** (never measured) | carried from `docs/BUSINESS_MODEL.md` §3 | **Still assumed.** Not isolated by any artifact. |
| A3 | Editorial pass (story units, rank, gate, export) | 0.00241 RTF | VERIFIED (microbenchmark, 3,546 ms) | §2 | No — corroborated by the corpus: editorial is a rounding error in every run. |
| A4 | Render RTF 16:9 / 9:16 | 0.00862 / 0.00625 | VERIFIED on 60 s windows of the **SD** reference | §2 | **YES — badly.** On a 1080×1920 source the render+decode+editorial share of wall time is **83.1%** and total cost is **5.93× the SD reference per source-minute** (§10.7). §2's budget was measured on the cheapest geometry the product will ever see. |
| A5 | Machine time for the reference source | 393.6 s (6.6 min) | **COMPUTED** — 4 measured components + 1 assumed | §2 table | **YES — superseded by measurement.** The same source's recorded end-to-end wall clock is **337.2 s** (§10.3). |
| A6 | Clip yield (reference source) | 6 clips, 99.254 s per preset, 13.506% | VERIFIED, **one source** | §2 | **YES — does not generalise.** Across the 7 completed sources yield ranges **6.95% to 68.48%** — a **9.9× spread** (§10.3). |
| A7 | Human editor time, same deliverable | 2–8 h | **ASSUMPTION — never observed** | §4 reasoning | **Untouched.** Still no editor has ever been timed. This is the denominator and it is the whole problem. |
| A8 | Editor cost rate | $36.10/h ($75,420/yr) | RESEARCHED (BLS OOH, 2026-09-19) | §4 | No — but it is an **employee wage**, not a freelance billable rate, so it *understates* the human hour. |
| A9 | **Human review and correction time in the machine arm** | **absent** | **MISSING FROM THE MODEL** | — | **This is the model's defining omission.** Quantified and bounded in §10.5. |
| A10 | **Fixed per-run overhead** (model load, decode startup, fetch) | **absent** | **MISSING FROM THE MODEL** | — | Measured from the corpus: **≈225 s of fixed overhead per run**, independent of source length (§10.3). |
| A11 | Network fetch of the source | explicitly excluded | — | §2 caveat | Now inside the measured number for the reference source (337.2 s) and bounded in §10.7. |

**Two structural conclusions from the audit.** (i) The machine side has drifted *and* is now
measurable, so §5 must be recomputed (§10.3). (ii) The two things the model left out entirely —
human review/correction time (A9) and fixed per-run overhead (A10) — are precisely the two that
bound the claim from above (§10.4, §10.5).

---

## 10.3 The measured machine side, per source

VERIFIED from `corpus/results.jsonl` plus `ffprobe` over every delivered file:

```
# per-source machine budget — the command that produced the table below
.venv/bin/python - <<'EOF'
import json, subprocess
from pathlib import Path
for r in map(json.loads, open('corpus/results.jsonl')):
    if r.get('status') != 'OK': continue
    p = Path(f"corpus/{r['id']}_run/project"); d, e = r['duration'], r['elapsed_s']
    tot = 0.0
    for sub in ('facebook','shorts'):
        for f in sorted((p/sub).glob('*.mp4')):
            tot += float(subprocess.run(['ffprobe','-v','error','-show_entries','format=duration',
                '-of','default=noprint_wrappers=1:nokey=1',str(f)],
                capture_output=True,text=True).stdout)
    print(r['id'], round(d,1), round(e,1), round(e/d,3), r.get('clips'), round(tot,2),
          round(100*tot/d,2))
EOF
```

| Source | Source length | Wall clock (incl. fetch) | RTF | Segments | Clips | Delivered (both presets) | Yield | Playback floor |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `emwcon-panel-2018` | 55:39 | 10:01 | **0.180** | 842 | 5 | 243.20 s | 7.28% | 4.05 min |
| `nasa360-ep5` | 24:30 | 5:37 | 0.229 | 425 | 6 | 198.31 s | 13.49% | 3.31 min |
| `pentagon-esper-briefing` | 16:54 | 4:51 | 0.287 | 278 | 5 | 195.80 s | 19.31% | 3.26 min |
| `royal-society-cloke` | 10:26 | 3:44 | 0.358 | 156 | 4 | 254.97 s | 40.73% | 4.25 min |
| `ooi-vertical` | 2:32 | 2:20 | 0.922 | 26 | 3 | 95.40 s | 62.76% | 1.59 min |
| `hadhazy-protest` | 2:06 | 2:24 | 1.140 | 38 | 3 | 86.26 s | 68.48% | 1.44 min |
| `telugu-tutorial` | 5:40 | 9:53 | **1.746** | 4 | 1 | 23.59 s | 6.95% | 0.39 min |

Aggregate over the 7 completed sources, COMPUTED:

| Quantity | Value |
| --- | --- |
| Source processed | 7,066.2 s = **117.77 min** |
| Total wall clock | 2,330.4 s = **38.84 min** |
| **Overall RTF** | **0.3298** |
| Clips delivered | **27 selected clips → 54 rendered files** (2 presets each) |
| Delivered video | 1,097.5 s = **18.29 min** |
| Yield | **15.53%** of source duration |
| Delivered seconds per elapsed minute | **28.3 s** |
| Source-minutes consumed per delivered minute | **6.44** |

**Per-source variation is the finding, not a nuisance.** RTF spans **0.180 to 1.746 — a 9.7×
spread** — and **two of the seven sources ran slower than real time**: `hadhazy-protest`
(1.140) and `telugu-tutorial` (1.746). (An earlier draft said three; that reached the count
by substituting §10.7's fetch-free re-run RTF of 1.589 for `ooi-vertical` in place of this
table's own 0.922 — a different measurement imported into the table being summarised.
`ooi-vertical` at 152 s of source in 140.1 s of wall time is *faster* than real time, and it
is one of only two sources below the crossover below, so the distinction is not a rounding
matter.) An average is meaningless without that spread, which is why it is reported per
source above and decomposed below.

**Fixed overhead, measured.** A least-squares fit of `elapsed = a + b × duration` over the seven
completed sources gives:

```
elapsed_s = 225.2 + 0.1067 × duration_s      (R^2 = 0.390)
```

The intercept is the part of the job that does not shrink with the tape: model load, decode
startup, fetch, teardown. **≈225 s of fixed overhead per run, measured from the corpus.** The
marginal rate once warm is **0.1067 RTF**, and the **fit's** crossover — the source length at
which that extrapolated line crosses real time — is **252 s ≈ 4.2 minutes**.

That crossover is a property of the fitted line, not a measurement, and the corpus does not
support reading it as one. Only **two** of the seven sources are shorter than 252 s at all,
and **one of those two was faster than real time**: `hadhazy-protest` (126.0 s source, RTF
1.140, slower) and `ooi-vertical` (152.0 s source, RTF 0.922, faster). The fit predicts 241.4 s
of elapsed for `ooi-vertical` against the 140.1 s it actually took, a 42% miss on the one point
that would test the claim. **n = 7, R² = 0.390** is not enough to locate a threshold from an
extrapolation — half the sub-crossover evidence contradicts the conclusion the line draws. The
honest statement is that short sources are *at risk* of real-time because the ≈225 s fixed
overhead does not scale down with the tape, and that the corpus contains one short source which
was slower than real time and one which was not.

**Honest yield — usable output.** `docs/CORPUS_RESULTS.md` reports that two of the seven "OK"
sources are not actually usable because the transcript is phonetic noise
(`telugu-tutorial`, `hadhazy-protest`). Excluding them, COMPUTED:

| Set | Sources | RTF | Delivered | Yield | Delivered s / elapsed min |
| --- | --- | --- | --- | --- | --- |
| All 7 completed | 7 | 0.330 | 18.29 min | 15.53% | 28.3 |
| Minus the 2 transcript-noise sources | 5 | **0.241** | 16.46 min | 14.96% | **37.2** |

The yield barely moves (15.5% → 15.0%) but the *efficiency* moves a lot (28.3 → 37.2 delivered
seconds per machine minute): **the two unusable sources ran at 6.70 s of machine time per
delivered second — 4.15× the usable rate, +315% — against 1.61 s/s for the five usable ones.**
That is 736.5 s of machine time for 109.9 s of delivered video across the two, and 592.9 s of
it for a single 23.6 s of Telugu content. (An earlier draft quoted "32% more machine time per
delivered second" here. That number is real but it is a different comparison: it is the gap
between the *all-7* average rate, 2.123 s/s, and the *usable-5* average, 1.614 s/s — i.e. how
much the two drag the pooled average up, not how the two themselves run. Labelling it as a
property of the two sources understated them by roughly 10×.) Bad transcripts are not free on
the machine side either.

---

## 10.4 Recomputed model — old figure against new inputs

§5's machine input (393.6 s) is superseded by measurement. Three candidate replacements, each
labelled, and the ratio each implies against the same 2–8 h ASSUMED human baseline:

| Machine input | Value for the 24.5-min reference source | Status | Ratio @ 2 h | @ 4 h | @ 8 h |
| --- | --- | --- | --- | --- | --- |
| §5 as published (component sum) | 393.6 s = 6.60 min | COMPUTED from 4 VERIFIED + 1 ASSUMED | 18.3× | 36.6× | 73.2× |
| **Recorded end-to-end wall clock, same source** (`corpus/results.jsonl`, incl. fetch) | **337.2 s = 5.62 min** | **VERIFIED** | **21.4×** | **42.7×** | **85.4×** |
| Corpus-average rate (0.3298 RTF) | 484.6 s = 8.08 min | COMPUTED from 7 VERIFIED runs | 14.9× | 29.7× | 59.4× |
| Fixed-overhead fit (225.2 + 0.1067·d) | 382.0 s = 6.37 min | COMPUTED, R²=0.390 | 18.8× | 37.7× | 75.4× |

**The published range moves, but not in one direction, and that is the point.** The *reference*
source got cheaper when measured (5.62 vs 6.60 min) because the component sum was pessimistic. The
*corpus-average* source got more expensive (8.08 min) because the reference is a fast, clean,
SD, English tape. **A model whose inputs drift this much with the choice of source is not a
measurement waiting to happen — it is a sensitivity map whose centre is arbitrary.**

**Status of the recomputed figures: MODEL, NOT MEASURED.** Every ratio above divides a measured
machine number by an unmeasured human number. Changing which machine number is used moves the ratio
by 1.4×; nothing here is evidence about an editor.

---

## 10.5 The playback floor — why 73× is arithmetically infeasible

This is the load-bearing new result, and it requires no participants at all.

**The category error in §5.** The ratio divides *machine wall-clock time* by *human working time*.
Those are not comparable quantities. An unattended 6-minute machine run is not "0 human minutes" —
a person must hand it the source, must be interrupted when it finishes, and **must at minimum watch
what it produced** before that production can be delivered. §5 charges the machine arm zero human
minutes and compares the machine's runtime against the editor's labour. That is the single biggest
weakness in the claim, and it is arithmetically exploitable.

**The floor, and the review protocol it silently assumes.** The delivered set for the reference
source is **198.307 s = 3.305 min** of video (VERIFIED by `ffprobe` over all 12 files). An editor
cannot accept a deliverable they have not seen. So Arm B human time ≥ 3.305 min **even in the limit
of zero corrections, zero source review and zero ingest**, *if the editor plays both delivered
aspect ratios end to end*. That is the protocol the 3.305 min floor assumes, and it is a choice, not
a measurement — the artifacts do not support it as strongly as the number suggests:

> **The two geometries are the same edit.** `ffprobe` on every clip in both trees of all seven
> completed sources gives **duration deltas of 0.000 s** (checked per source over 27 clip pairs:
> `emwcon-panel-2018` 5, `hadhazy-protest` 3, `nasa360-ep5` 6, `ooi-vertical` 3,
> `pentagon-esper-briefing` 5, `royal-society-cloke` 4, `telugu-tutorial` 1). The 9:16 tree is a
> re-crop of the same six selections at the same in/out points. It carries **no new selection, no
> new timing, and no new caption text** — only the frame geometry differs.
>
> The editorial content of the reference source's deliverable set is therefore **99.154 s =
> 1.653 min**, not 3.305 min. Reviewing one geometry in full discharges the *editorial* judgement;
> the second geometry needs a crop and safe-area check, and nothing in this document measures how
> long that takes.

So the floor is a protocol-dependent range, and the ceiling moves with it:

| Review protocol (reference source, zero corrections) | Floor on Arm B | Ceiling @ 2 h | Ceiling @ 4 h |
| --- | --- | --- | --- |
| Both aspect ratios played end to end at 1× (the 3.305 min floor) | 3.305 min | **36.3×** | 72.6× |
| **One aspect ratio at 1× — what the artifacts support** | **1.653 min** | **72.6×** | 145.2× |
| One aspect ratio at 2×, if output is reviewed as fast as source tape | 0.826 min | 145.2× | 290.5× |

**The headline 73× is not arithmetically infeasible.** Under the one-preset protocol it is
*feasible with a zero correction budget*, which is a different and weaker objection than
impossibility — and the objection that actually matters, because zero correction budget is not a
real workflow. An earlier version of this section stated the floor as a single measured number and
the ceiling as a single figure, and then prohibited quoting anything above it. That prohibition was
stronger than the evidence: it depended on an unstated protocol, and §10.6's own substitution table
below shows that one discarded clip costs more than the whole 36× budget allows, which is the real
constraint on any figure in this range.

**Now invert it: how much correction time can each headline figure survive?**

This inversion uses the 3.305 min both-geometries floor. Under the 1.653 min one-geometry floor the
whole table doubles and 73× lands at +0.02 min — the point of the table is the *shape*, not the
absolute numbers: every high figure buys no correction time, and §10.6 is what actually spends it.

| Claimed ratio | Max Arm B human time @ 2 h baseline | Remaining budget after one playback | Verdict |
| --- | --- | --- | --- |
| 73× | 1.64 min | **−1.66 min** | **IMPOSSIBLE** — needs less time than playing the output once |
| 36× | 3.33 min | +0.03 min | impossible in practice — allows literally zero correction |
| 18× | 6.67 min | **+3.36 min** | survives only if review + every correction fits in 3.4 min |
| 10× | 12.0 min | +8.69 min | plausible only if corrections are trims, not re-selections |
| 5× | 24.0 min | +20.69 min | the range a realistic correction burden lands in |
| 2× | 60.0 min | +56.69 min | what a "useful assistant" looks like |
| 1.43× (the §7 30% bar) | 84.0 min | +80.69 min | the weakest claim that still counts as a saving |

At the 8 h baseline the numbers scale up, and the top of the range fails there too: **73× at 8 h
requires Arm B ≤ 6.58 min, i.e. 3.3 min of review and correction total.**

**Conclusion, and it is narrower than an earlier draft claimed.** The *top of the published range is
not merely unmeasured — under a both-geometries protocol it is infeasible*: 73× at the model's own
2-hour baseline would require the editor to finish in 1.64 min, less time than the delivered clips
take to play once at 1×. But under a one-geometry protocol, which the artifacts support, 73× becomes
*feasible with no correction budget at all* — and no correction budget is not a workflow, it is the
limit of a model. So the honest statement is not "73× is impossible" but:

> **Every figure in the upper half of the quoted 18×–73× range is conditional on an unmeasured
> review protocol and assumes zero or near-zero correction.** Under the protocol that plays both
> delivered geometries, the ceiling at a 2 h baseline is 36× and 36× itself leaves 0.03 min for
> correction — i.e. it is not reachable either. Under the protocol the artifacts support, the
> ceiling is 72.6× and the binding constraint is not the playback floor but §10.6's substitution
> cost.

**What must not be quoted is a single figure.** The range spans 4× to 145× depending on two
unmeasured choices (baseline hours, review protocol) and one unmeasured quantity (correction
burden). None of the three has a participant behind it. **All of them are bounds, not measurements.**

---

## 10.6 The substitution trap — one discarded clip can destroy the claim

Corrections are not equal. Trimming a cut point costs seconds. **Discarding a machine clip and
choosing a replacement costs a trip back to the source tape** — and that is where all the baseline
time lives.

Quantified, for the reference source: a professional logging pass over a 24:30 tape at 2× speed is
**12.25 min** (COMPUTED from the VERIFIED 1469.77 s duration). Compare:

| Scenario | Arm B human time | Honest ratio @ 2 h baseline |
| --- | --- | --- |
| Accept everything, one playback | 3.31 min | 36.3× |
| One playback + small trims (3 min) | 6.31 min | 19.0× |
| **One clip discarded and substituted** (playback 3.31 + logging pass 12.25 + 3 trims) | **18.6 min** | **6.5×** |
| Two substitutions + corrections | ~30 min | 4.0× |

**A single substitution costs more than the entire 18× budget allows** (6.67 min) and nearly half
the 36× ceiling. This is why selection quality is not a "quality" concern that sits separate from
the time-savings claim — **selection quality IS the time-saving mechanism.** If the editor cannot
trust the picks, they must do the watching themselves, and the machine arm degenerates into the
baseline arm plus machine overhead — a *negative* saving.

This is the argument for making the discard/substitute count a first-class clocked quantity
(§10.8, K3/K4) rather than a footnote.

---

## 10.7 What the machine side does NOT tell you — wall-clock instability

The same source, the same parameters, byte-identical selections, run twice:

```
# run 1 — the corpus run, wall clock INCLUDING a 92.6 MB network fetch
#   corpus/results.jsonl  ->  ooi-vertical  elapsed_s = 140.1   (RTF 0.922)

# run 2 — a deliberate re-run on the already-downloaded file, no fetch at all
/usr/bin/time -v .venv/bin/python -m selekyt.cli \
    corpus/media/OOI_Overview_CAPTIONS_Vertical.webm \
    --out /tmp/ooi_rerun --limit 3 --model small --language en
#   Elapsed (wall clock) time: 4:01.55 = 241.55 s
#   User time 431.43 s + System 15.70 s = 447.13 s CPU
#   Percent of CPU this job got: 185%  (on a 4-core box)
```

Determinism holds — selections are identical:

```
rerun   [(112.471, 139.13), (41.42, 56.58), (89.697, 101.843)]
corpus  [(112.471, 139.13), (41.42, 56.58), (89.697, 101.843)]   identical: True
```

But the timing does not hold. **The fetch-free run (241.55 s) was 1.72× slower than the run that
included a 92 MB download (140.1 s).** The cause is host contention: the 185% CPU figure on a
4-core host means the run did not get the machine to itself (this host is shared), and
`/proc/loadavg` during the measurement read 0.94 / 2.54 / 2.41.

**Implication for the claim.** Any machine-time figure quoted as a single number carries a run-to-run
spread of at least ~1.7× on this host from load alone. The "6.6 minutes" in §2 inherits that spread
and more. The honest machine-side statement is a *range with its conditions*, not a figure.

The same re-run also decomposes the cost, and it overturns §2's cost structure:

| Component (source `ooi-vertical`, 152.008 s) | Time | RTF | Share of fetch-free wall |
| --- | --- | --- | --- |
| Cold model load (`WhisperModel("small", compute_type="int8", cpu_threads=4)`) | 1.60 s | 0.011 | 0.7% |
| In-run transcription less the load, as the pipeline itself reports it | 39.3 s | **0.259** | **16.3%** |
| Decode + editorial + render (remainder) | 200.7 s | — | **83.1%** |
| **Fetch-free full pipeline, wall** | **241.55 s** | **1.589** | 100% |
| Fetch-free full pipeline, CPU | 447.13 s | 2.941 | — |

The rows are disjoint and sum to the wall clock. The transcription row is the pipeline's own in-run
figure (40.9 s) with the already-counted load removed, so it is 39.3 s at 0.259 RTF, not 0.269 —
0.269 is the RTF against the full 152.008 s source for the whole 40.9 s including the load. For the
comparison to §2's published RTF the warm-only pass is the right number and is reported separately
here rather than as a peer row: **28.30 s / 0.186 RTF** on the cached 16 kHz mono, which is 11.7%
of the same wall clock. An earlier version of this table listed the warm pass as a fourth row
alongside the in-run figure, which made the shares total 112.4% — two of those rows described
overlapping work, and the sum was not a meaningful number.

**Provenance of these figures, because three of them cannot be checked.** The
totals — 241.55 s wall, 447.13 s CPU, 185% — are pasted from the re-run's own
`time` output above, and the byte-identical selection line is checkable against
`corpus/ooi-vertical_run/project/selections.json`. **The decomposition is
not checkable from anything committed**: the 1.60 s cold load, the 40.9 s
in-run transcription and the 28.30 s warm pass came from a terminal session
whose output was not saved, and `corpus/results.jsonl` records only the total
`elapsed_s`. The 200.7 s remainder is the wall clock minus those, so it
inherits their error. Likewise the "92 MB download" included in the 140.1 s
comparison run: the `ooi-vertical` media was deleted to free disk, so that
figure can only be re-derived by re-downloading from the manifest URL. Treat
the *shape* of this table — transcription is a minority of the wall clock on a
portrait source — as the finding, and the individual components as un-recorded
precision. `tools/microbench.py` exists to stop this class of gap: it runs each
stage against committed artifacts and prints the same table, so future numbers
are re-derived rather than quoted from a session. **It has since been run, and it
closed the gap it was written for** — §2b re-derives §2's own table from the
surviving source and transcript, with transcription and both renders reproducing
within 4%. This paragraph's own figures remain un-recorded precision, because the
source they were measured on (`ooi-vertical`, 152.008 s) is the one whose media
really is gone; §2's figures did not have that excuse.

Two corrections to the published model, both material:

1. **The published transcription RTF (0.1505) is optimistic by 1.24×–1.79× under real pipeline
   conditions** (0.186 warm on this source, 0.269 in-run). §2 measured a *warmed* model on a clean
   240 s English sample; the pipeline runs cold, chunked, on whatever the source is.
2. **On a portrait 1080×1920 source the pipeline costs 5.93× the SD reference per source-minute**
   (1.589 vs 0.2678 RTF), and the cost moves from transcription to rendering. §2's budget was built
   on the cheapest geometry in the corpus. The machine side of the 18×–73× model is therefore a
   *best case*, not a typical case — and `docs/BUSINESS_MODEL.md` §9's warning that 4K/H.265 ingest
   is "3–10× unmeasured" is now anchored at the low end of that range by a real measurement.

---

## 10.8 The instrument — what a session must record

**This section owns the clock.** The judgement instrument (rubric, T-gate, blinding, roles,
falsifiers F1–F6) is specified in `docs/HUMAN_EDITORIAL_EVALUATION.md`; the two documents agree by
construction on the five shared definitions listed in that document's §9, and this section does not
restate its tables. What follows is only the time-and-activity instrument.

**STATUS: PENDING EXTERNAL VALIDATION.** No participant has been contacted. Founder approval for
external contact has not been given. Nothing below has been run.

### Per-session header (both arms)

`editor_id` · experience level (years as a professional editor, primary NLE, vertical short-form
output per week) · `source_id` · **source duration** (ffprobe) · **genre** · **language** · `arm`
· order position · counterbalancing key.

### Clocks (both arms, identical instrument)

| Clock | Definition |
| --- | --- |
| `t_brief_ack` | Editor acknowledges the written brief. **Clock start.** |
| `t_source_log_start` / `_end` | Source familiarisation / logging pass, active intervals only. |
| `t_assembly_start` / `_end` | Building the deliverable (manual cutting, or importing/finishing the machine package). |
| `t_refine_start` / `_end` | Corrections, captions, reframes. Sub-coded K1–K6 below. |
| `t_ready` | Editor declares **"ready to deliver"**. **Clock stop.** Same definition in both arms — an Arm B that is fast and then re-cut from scratch counts as slow (already fixed in §7 and in `docs/HUMAN_EDITORIAL_EVALUATION.md` §8). |
| Idle gaps | Any gap > 60 s with no editor input is logged as `idle_min`, **excluded** from active minutes and **reported separately**. |

**Two times are reported for every session, in both arms, never conflated:**

- **Human active minutes** — the editor's hands-on time. This is the endpoint the saving claim is
  about.
- **Elapsed wall to deliver** — `t_ready − t_brief_ack`, including waiting and idle.

### Machine-arm clocks, and the rule that fixes the category error

| Clock | Definition and treatment |
| --- | --- |
| `t_submit` → `t_machine_done` | The unattended machine interval. **Never converted into human time saved.** |
| `t_review_start` / `_end` | Reviewing the delivered set. **Must be ≥ delivered duration.** If it is shorter, the session is flagged as a partial review and reported as such — an acceptance without review is not a saving, it is an unaudited deliverable. |
| `t_resume` | Editor's next action on this task after `t_machine_done`. The gap `t_resume − t_machine_done` is the **context-switch cost** — the price of an unattended run, which §5's model charged as zero. |
| Blocked waiting | If the editor has no other task during the machine run, that interval is charged to **elapsed wall to deliver** in Arm B. A 6-minute machine run plus 20 minutes of review is a 26-minute time-to-deliver, not a 6-minute one. |

**The rule, stated so it cannot be averaged away:** *the machine arm is credited only with the
human minutes it actually removes from the editor's working day.* An unattended run costs the
editor the submit minutes, the review minutes, the correction minutes and one context switch. If
those exceed the baseline's active minutes, the machine arm's saving is negative, and that is a
result the study must be able to return.

### Correction taxonomy — clocked separately, first-class

Every correction in `t_refine` is coded and timed:

| Code | Correction | Why it is separate |
| --- | --- | --- |
| **K1** | Transcript/ASR error fixed | The corpus proved this is the failure mode mechanical audits cannot see (`docs/CORPUS_RESULTS.md`). Time it separately so a bad STT layer shows up as a cost. |
| **K2** | Cut point trimmed | Cheap. The benign case. |
| **K3** | Machine clip **discarded** | The signal that the selection layer failed on that clip. |
| **K4** | Machine clip discarded **and replaced from the source** | **The claim-killer.** Triggers a source pass; see §10.6. The source-review minutes it causes are recorded under `t_source_log` and attributed to K4. |
| **K5** | Caption/style correction | |
| **K6** | Reframe / geometry correction | |

### Counts and outcomes

`candidate_count` and `rejected_by_context_guard` from `report.json`; **clips kept unmodified,
clips trimmed, clips discarded, clips added by hand** from the timeline diff between the Arm B
handover and what shipped (already fixed in §7); **final usable clips actually published** (an
editor-would-post judgement, from E11 ≥ 4 in `docs/HUMAN_EDITORIAL_EVALUATION.md` §4.11 — *human
judgement, not a mechanical threshold*); adoption rate; and editor experience level for the
moderator analysis.

### Primary endpoint

**Baseline human active minutes − assisted human active minutes, paired within editor.** Not a
machine-to-human ratio. The ratio may be *reported* as a derived figure but it is not the endpoint,
and §10.5 shows why the derived figure must be presented against the playback floor.

---

## 10.9 Falsifier — declared in advance, and stricter than §7's

`docs/HUMAN_EDITORIAL_EVALUATION.md` §10 declares F1–F6; §7's F4 (Arm B ≥ 30% faster at equal-or-better
blind quality) is retained. **This section adds the two the machine-side measurements now make
possible, and both are expressed in the §10.5 framework.**

**F7 — Correction-budget falsifier (the new primary one).** The claim of a *large* speedup is FALSE
if, in Arm B at equal-or-better blind quality, **total correction time (K1+K2+K3+K4+K5+K6) plus
review time exceeds 10% of baseline active time on a majority of sources.** Rationale, computed from
§10.5: at a 2 h baseline, 10% is 12 min, which is the budget at which the honest ratio falls to
about 10×. A correction burden above that converts the product from an editor into a rendering
service, and the honest claim becomes "renders captions and two aspect ratios cheaply", which is a
different and much smaller claim.

**F8 — Substitution falsifier.** The selection layer contributes no time saving, and the claim is
FALSE as a *time-saving* claim, if **K4 (discard-and-replace-from-source) occurs on more than 25% of
delivered clips across a majority of sources.** Each K4 forces a source pass (§10.6) and pushes the
Arm B time toward the baseline plus machine overhead. Note this falsifies the *speed* claim while
leaving `docs/HUMAN_EDITORIAL_EVALUATION.md`'s editorial-quality falsifiers (F1, F3) to speak
separately — a tool can be editorially fine and still save no time, and both must be survivable
independently for a positive claim.

**F9 — Floor violation.** Arm B human active minutes below the playback floor (delivered duration,
ffprobe) indicates the deliverables were not reviewed; the session is reported as an unaudited
deliverable, not as a fast session.

**The hierarchy, stated so no later analyst can shop among them.** F4 (30% faster) is the *weakest*
bar — at a 2 h baseline it survives 80 minutes of corrections, which is a very low hurdle. F7 and
F8 are the ones that actually discriminate between "this is an editor's tool" and "this is a
renderer". **Any published speedup figure must state which falsifier it was tested against.**

---

## 10.10 Statistical shape, and the smallest study a pre-revenue project can actually run

**Design and test.** Within-editor pairing (each editor does both arms, order counterbalanced) so
editor-skill variance — which varies by an order of magnitude between working editors — cancels.
Primary endpoint per §10.8. Wilcoxon signed-rank, one-sided, α = 0.05, on the paired differences in
human active minutes; median and IQR reported alongside, never the mean alone. This follows
`docs/HUMAN_EDITORIAL_EVALUATION.md` §11 and is not restated here.

**The combinatorial floor (exact arithmetic, checkable).** With all-n pairs of one sign, the minimum
one-sided exact p is 1/2ⁿ: n=4 → 0.0625, **cannot clear α=0.05 no matter how large the effect**;
n=5 → 0.03125, clears it for a *perfectly consistent* effect only; n=6 → 0.0156.

**No power calculation is asserted, and here is why none can be.** Power requires a variance
estimate for the endpoint. **No within-editor session-time variance has ever been observed** — no
editor has ever been timed on this task in either arm. Asserting a power figure would be inventing
the very quantity the study exists to measure. What can be said exactly is the floor above and the
effect size the claim implies: **an 18× claim asserts that Arm B human time is 5.6% of baseline.**
That is an enormous effect, detectable at n=5–6 *only if it is nearly universal*, and it must be
reported at exactly that strength.

**The ladder — each tier is a different kind of statement:**

| Tier | What it is | n | Cost (at $36.10/h) | What it can and cannot support |
| --- | --- | --- | --- | --- |
| **0** | **The playback-floor arithmetic (§10.5)** | 0 people | **$0 — already done** | Establishes a *protocol-dependent ceiling* with no participants: 36.3× at a 2 h baseline if both aspect ratios are played at 1×, 72.6× if one is. Cannot establish any actual saving, and cannot pick between the two protocols. |
| **1** | One editor, two sources, both arms, fully clocked | 1 | ≈$110 + recruitment | **A demonstration, and must be called one.** Its real purpose: measure the correction burden K1–K6, which is the number that decides whether the larger study is worth running. Not generalisable. |
| **2** | Three editors × two sources, both arms | 3 | ≈$330 + raters | Smallest design that can clear α=0.05, and only for a perfectly consistent large effect. Reports a large-effect-only claim. |
| **3** | Full design — 6 editors × 3 sources, 18 sessions, 9/arm, 3 raters, 4 language checkers, separate coordinator and analyst | 6 | ≈$867 + recruitment (§7's table, still correct) | The design `docs/HUMAN_EDITORIAL_EVALUATION.md` is written for. Supports a generalisable claim for the studied conditions and no others. |

**Recommendation, and it is deliberately the cheap one.** Run **Tier 1 first**, as a demonstration
whose only job is to clock K1–K6 on a real 6-clip package. If total correction time lands above
~10 min, F7 is already failed on that source, the 18× figure is dead, and the project should
re-price the claim at the 2×–5× tier of the §10.5 table and design Tier 3 around that smaller but
true claim. **A Tier 1 that costs $110 either saves the $867 study or makes it unnecessary.**

**Realism.** A design requiring 30 editors is a design that never happens. Six is the design; one is
what a pre-revenue project with no recruitment channel can actually obtain. The ladder above exists
so that a shortfall is reported as a reduction in claim strength, not papered over — and so that the
zero-cost tier is never skipped, because it is the only one that has already produced a result.

---

## 10.11 What may and may not be said today (supersedes §8)

**Permitted — every one traceable to a command in this repo:**

- "The pipeline's end-to-end wall clock has now been measured: on a 4-core CPU-only host it turned
  **117.8 minutes of source video into 27 selected clips — 18.3 minutes of delivered video in two
  aspect ratios — in 38.8 minutes of wall time including downloads**, a 0.33 real-time factor."
  **That figure covers only the 7 sources that delivered, not all 11.** The other 4 consumed 13.5
  minutes of source and 3.4 minutes of wall time and produced nothing: a 36% source failure rate
  that belongs in any claim made from this data, because a time-savings pitch computed over runs
  that yielded no deliverables is a time-savings pitch computed over the wrong denominator.
- "On the 24.5-minute reference source the recorded wall clock is **5.6 minutes**, measured rather
  than estimated."
- "Machine cost varies **9.7× across the corpus**, and **on sources shorter than about four minutes
  the pipeline runs slower than real time** because ≈225 s of each run is fixed overhead."
- "The model's machine-side inputs were optimistic: transcription in real pipeline conditions is
  **1.24×–1.79×** the published rate, and **a portrait HD source costs 5.93× the SD reference per
  source-minute**, with the cost in rendering rather than transcription."
- "The time-savings ratio is a **model whose denominator has never been measured** — no editor has
  ever been timed on this task, with or without the tool."
- "**The top of the published range is protocol-dependent, not merely unmeasured**: at the model's
  own 2-hour baseline, 73× requires the editor to finish in 1.64 min if both delivered aspect ratios
  are played at 1× — less time than the clips take to play once — and in 1.65 min, leaving no
  correction budget, if only one is. Neither protocol has been observed. The ceiling is 36.3× under
  the former and 72.6× under the latter, and both are computed from one source (`nasa360-ep5`) at
  one baseline, so they are ceilings for *that* delivered set, not a property of the product."

**Forbidden — none of this is supported:**

- "SelekyT is **18× to 73× faster** than a human editor" — unmeasured denominator, and a range
  whose top end assumes a review protocol that has never been observed (§10.5).
- Any **single** speedup figure presented as a property of the product. The honest number is a range
  that varies with the baseline hours, the review protocol and the correction burden — all three
  unmeasured — and it must be recomputed for the delivered set being quoted, not copied. A bare
  "73×" or "36×" on a deck or pricing page is unsupported either way: 73× assumes no correction at
  all, and 36× assumes the editor watches both aspect ratios in full.
- That SelekyT "saves N hours of editor time", "eliminates N minutes of editorial work", or any
  statement of a **quantity of time saved**. No such quantity has been measured.
- That an unattended machine run costs the editor **zero minutes**. It costs the submit, the review,
  the corrections and one context switch (§10.8), and the review alone is floored at the delivered
  duration.
- That the machine time *is* the saving. **6.6 minutes of machine time is 6.6 minutes of machine
  time**; it becomes a saving only if a human's working minutes fall by more than the review and
  correction minutes the machine adds.
- That any editor has reviewed, approved, adopted, imported or been timed against a SelekyT output.
  **None has. Zero users, zero revenue, zero pilots.**
- That the machine-side figure is stable. Two complete runs of the same source with byte-identical
  selections differed by **1.72×** from host contention alone (§10.7).

---

## 10.12 Summary of this update

- **Measured now (and it was not before):** an end-to-end wall clock per source, all 11 recorded
  including the 4 failures; 0.330 RTF across the 7 that delivered (0.321 if the failed runs are
  left in, which is the fairer number for a capacity-planning claim and the worse one for a
  marketing claim); ≈225 s fixed overhead per run; a 9.7× RTF spread, with **2 of the 7 sources
  slower than real time**; the fit's 252 s real-time crossover, stated as an extrapolation with
  R² = 0.390 over n = 7 and with one of the only two sources below it faster than real time; a
  5.93× SD-to-portrait-HD cost gap; a 1.72× run-to-run timing spread on identical work.
- **Recomputed:** the reference source's machine time moves 6.60 → 5.62 min measured, and the
  corpus-average rate for the same length is 8.08 min. The ratio moves with it, in both directions,
  which is itself the finding. **MODEL, NOT MEASURED** either way.
- **Bounded, with no participants needed:** the reference source's delivered set is 198.307 s of
  video across both aspect ratios, of which **99.154 s is distinct editorial content** — the two
  trees are per-clip duration-identical to 0.000 s on all seven completed sources, the second being
  a re-crop of the first. The floor on Arm B human time is therefore 3.305 min under a
  both-geometries review protocol and 1.653 min under a one-geometry one, capping the ratio at
  **36.3× / 72.6×** at a 2 h baseline. Neither protocol has been observed, and **the binding
  constraint on any real figure is not the playback floor but the substitution cost in §10.6**,
  which already exceeds the whole 36× budget on a single discarded clip.
- **The mechanism that decides it:** one discard-and-substitute costs a source pass (12.25 min at 2×
  on the reference tape) — more than the entire 18× budget. **Selection quality is the time-saving
  mechanism**, not a separate quality concern.
- **Still never measured, by anyone, ever:** how long a professional editor takes on this task.
  Every ratio in this document divides by that number. Until §10.8 is run — which needs founder
  approval that has not been given, and participants who have not been contacted — the claim remains
  **an untested hypothesis**, and the only defensible speedup statement is the ceiling, not a
  measurement.
- **Reproducible now, where it was quotable-only before:** `tools/microbench.py` re-derives
  §2's per-stage table from committed artifacts, and §2b runs it against §2's own source
  and transcript. Transcription (−1.9%), both renders (+0.5% / −3.9%) and the export
  stages reproduce within 4%; the editorial row reproduces to 19% with a named workload
  difference. The instrument also caught its own first error — a cached chunk checkpoint
  reported 796 ms for a 39 s transcription, a 49× understatement (§2a) — which is the
  case where a checked-in instrument is worth more than a correct-looking number.
