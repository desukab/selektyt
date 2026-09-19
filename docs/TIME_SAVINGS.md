# SelekyT — The Time-Savings Claim

Prepared 2026-09-19. This is the **weakest claim in the venture**, and this document treats it
adversarially rather than as a selling point.

**Company status, stated plainly:** SelekyT has **zero users, zero revenue, zero pilots, and zero
investors**. There is no customer, no pilot, and no usage data of any kind behind anything below.

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

**Two things this number is not.**

1. **It is not a measured pipeline run.** No end-to-end wall clock for the pipeline has ever been
   recorded — `docs/BUSINESS_MODEL.md` §1 says so, and I did not run the pipeline (it takes hours).
   The 6.6 minutes is a **sum of component measurements**. Stages run serially on this host, so the
   sum is a fair estimate, but it is a reconstruction.
2. **It excludes the source download.** The 883,894,825-byte source is fetched over the network
   (VERIFIED still available: `https://archive.org/download/NASA_360_program5_HD/NASA_360_program5_HD.mov`
   returns 206, byte-exact match to the local file). Download time is unmeasured and would be
   additive on a real job.

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
| Delivered duration | 116.95 s total, 58.48 s per preset | **198.507 s total, 99.254 s per preset** |
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
| Cloud compute for the same job, `small` model | **$0.021 – $0.024** | COMPUTED from `docs/BUSINESS_MODEL.md` §4 (AWS `c7i.xlarge` $0.1785/h, 0.2678 RTF) |

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
5. **834 passing tests measure nothing about time.** VERIFIED: `.venv/bin/python -m pytest -q` →
   834 passed, 10 skipped in 101.39 s. Those tests assert correctness and determinism. They carry no
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
- Editorial quality is unmeasured and transcription accuracy has never been scored against a
  reference transcript on any source (see `docs/EVALUATION.md` §1.1 and §3-D4).

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
