# SelekyT Metrics

**Date: 2026-09-19.** This document defines the metrics SelekyT will hold itself
to, and states the current value of each.

Two rules govern it, and they are the reason it is worth reading:

1. **A metric with no current value is fine. A metric with an invented value is a
   defect.** Where a number cannot be computed, it says `N/A` and the reason.
2. **Every value below was computed by the author on 2026-09-19** from the one
   existing run at `corpus/nasa360-ep5_run/project/` (NASA 360 Episode 5, public
   domain, 1469.77 s, 29.97 fps, two-host unscripted speech), or it says how it
   would be computed. Nothing is projected, extrapolated, or "expected."

   That path is version-controlled, so every command below can be re-run from a
   checkout. It was not always: this document previously cited
   `out/web/pipeline/project/`, a gitignored working directory that is absent
   from a clean clone and that held a run predating several later fixes. The
   committed corpus run is the same source with the same numbers — confirmed by
   re-running the commands against it, not by assumption — and pointing at it
   is what makes "VERIFIED" a claim a reader can check. Whether that run still
   reproduces under the *current* code is a separate question, answered in
   `docs/CORPUS_BENCHMARK_STATUS.md`.

**The state of the venture that makes section B what it is:** SelekyT has **zero
users, zero revenue, zero pilots, and zero investors**. It has been run end-to-end
exactly once, on one video. Therefore every business metric is undefined, and the
reason is always the same.

---

## Evidence markers

| Marker | Meaning |
| --- | --- |
| **VERIFIED** | Computed by the author from the repo on 2026-09-19, from a file the repository tracks. The file or command is named. |
| **NOT MEASURED** | Nobody has measured it. No number exists. Any figure quoted for it would be invented. |
| **N/A** | The metric is structurally undefined — no users, no revenue, no pilot — not merely unmeasured. |
| **ASSUMPTION** | Reasoning, not data. Discardable without breaking the argument. |

---

# A. Product-quality metrics — computable TODAY

All values in this section are computed from the single existing run. **Sample
size: one source, six clips, twelve renders.** That is the honest scope of every
number below, and no aggregation over one sample is a trend.

Section A4 is the exception and says so in its own header: its figures are
measured across the whole committed corpus — 11 sources, of which 7 exported a
timeline, 27 clips — because the corpus rather than one run carries the
interchange artifacts. Where an A4 metric is measurable only on the one run it
says so, and where the corpus media is absent from the checkout it reports the
gap as a SKIP rather than a PASS.

## A1 — Yield and coverage

### A1.1 Selection yield — 6 clips per 24:29 of source (14.7 clips/hour)

**Definition:** shipped clips divided by source duration. Measures whether the
editorial engine finds enough shippable material in a source.

**Command:**

```sh
cd /home/azureuser/selektyt && .venv/bin/python -c "
import json
er=json.load(open('corpus/nasa360-ep5_run/project/editorial_report.json'))
print(len(er), 'clips')"
```

**Current value: 6 clips from 1469.77 s = 14.7 clips per hour of source.**
VERIFIED. `report.json` independently records `selected_count: 6` and
`candidate_count: 4000`, so the yield is 6 shipped from 4,000 scored candidates.

**How to read it:** a pilot-scale episode of 45–90 min at this yield implies
11–22 clips per episode, which brackets the 5–10 clip baseline `CUSTOMER_DISCOVERY.md`
assumes a studio currently delivers. **ASSUMPTION** on that baseline — no editor
was interviewed.

### A1.2 Source coverage — 6.83% of the source's runtime

**Definition:** Σ(shipped selection duration) ÷ source duration. The fraction of
the long-form video that became short-form output.

**Command:**

```sh
cd /home/azureuser/selektyt && .venv/bin/python -c "
import json
er=json.load(open('corpus/nasa360-ep5_run/project/editorial_report.json'))
sel=sum(c['end']-c['start'] for c in er)
print('%.2fs of 1469.77s = %.2f%%' % (sel, 100*sel/1469.77))"
```

**Current value: 100.44 s of 1469.77 s = 6.83%.** VERIFIED. `qa.json` reports the
same figure as `source_coverage.coverage_pct: 6.8` with `overlap_count: 0`.

**How to read it:** coverage is *intended* to be low. The engine ranks and
discards; it is not a summarizer. A rising coverage number is not obviously good —
it may mean the editorial gate is loosening. Track alongside A2.3 (duplicate
rate), which is the failure mode coverage growth usually accompanies.

### A1.3 Selection non-overlap — 0.000 s

**Definition:** total temporal overlap among shipped selections. Two clips
covering the same source seconds is wasted output and a confused deliverable.

**Current value: 0.000 s across all 6 selections.** VERIFIED, computed by
intersecting the sorted `(start, end)` intervals from `editorial_report.json`.
`qa.json` independently reports `overlap_count: 0`.

---

## A2 — Editorial quality

### A2.1 Score distribution — total_score 0.716 to 0.926

**Definition:** range and spread of the final ranking score across shipped clips,
decomposed into `editorial_score`, `virality_score`, `context_score`,
`visual_potential` and `confidence`.

**File:** `corpus/nasa360-ep5_run/project/editorial_report.json`.

**Current value (VERIFIED):**

| Component | Range across 6 shipped clips |
| --- | --- |
| `total_score` | 0.716 – 0.926 |
| `editorial_score` | 0.758 – 0.988 |
| `virality_score` | 0.309 – 0.769 |
| `context_score` | 0.877 – 1.000 |
| `visual_potential` | 0.45 – 0.85 |
| `confidence` | 0.9 for all six as committed (see below) |

**`confidence` needs two numbers, and confusing them is the easy mistake to
make.** The committed `editorial_report.json` records `0.9` for all six clips,
because the run predates the change that made this field a measurement; `0.9`
was a literal the context gate could never fail. Re-scoring the committed
transcript with the current code measures **0.916 – 0.961** on the same six
spans, and that is the figure to quote when describing what the product does
today. The span-by-span comparison is `tools/corpus_reproducibility.py`, and
`docs/CORPUS_BENCHMARK_STATUS.md` records which runs this affects and which it
does not.

`story_type` labels shipped: `conflict`, `conflict`, `revelation`, `revelation`,
`fact`, `story`.

**How to read it:** every shipped clip passed the context/truth guard
(`rejected_by_context_guard: 0`, `context_score ≥ 0.877`), and every clip carries
a human-readable `reason` string decomposing its score. The committed artifact's
clip 01 reads:

```
Structural editorial score: opening=0.75; progression=0.95; payoff=1.00;
density=1.00; fillers=0; standalone=1.00; repetition=0.18; coherence=1.00;
completeness=1.00; audio_pace=0.88; silence=0.147; dead_air=0.00.
```

The current code emits the same digits with the sentence in front of them:

```
conflict candidate that lands a clear closing thought. Structural editorial
score: opening=0.75; progression=0.95; payoff=1.00; density=1.00; fillers=0;
standalone=1.00; repetition=0.18; coherence=1.00; completeness=1.00;
audio_pace=0.88; silence=0.147; dead_air=0.00.
```

**That decomposed reason string is the metric that matters most for the editor
beachhead** — it is the auditability the positioning depends on, and it is
per-clip, not per-run. The sentence names the signal that most sets the clip
apart and the one that most limits it, so two clips with different profiles read
differently rather than differing only in their digits; the labelled numeric
block that follows it is unchanged, so the measurement an editor would challenge
is still right there.

**Limit:** `report.json` records `rejected_by_context_guard: 0` and
`truncated_by_limit: 5`. Because the run was capped at 6, the shipped clips are
the top of a ranked list that was truncated, not a set that survived a hard
gate. **A rejection rate of zero on one run is not evidence the gate is
discriminating** — see the warning in `docs/PILOT_PROGRAM.md` F3.

The 5 that count is a claim the report can now back and could not then: a fresh
run carries those candidates under `bench`, with the same fields a shipped clip
has minus the word timings a re-render needs. **The committed corpus report does
not have that key**, because it is a frozen historical benchmark of the code that
ran at the time (see `docs/CORPUS_BENCHMARK_STATUS.md`) and is not regenerated —
the absence is a date, not a defect. Regenerating that one run would add it.

### A2.2 Dead-air removal rate — 1.33% of selected span removed

**Definition:** Σ(source span − edited duration) ÷ Σ(source span), where the edit
plan's `intervals` are the speech regions actually concatenated after pause
classification.

**Command:**

```sh
cd /home/azureuser/selektyt && .venv/bin/python -c "
import json
ep=json.load(open('corpus/nasa360-ep5_run/project/edit_plan.json'))
s=sum(e['source_duration'] for e in ep); r=sum(e['removed_seconds'] for e in ep)
print('removed %.2fs of %.2fs = %.2f%%' % (r, s, 100*r/s))"
```

**Current value: 1.34 s removed of 100.44 s = 1.33%.** VERIFIED. Per clip: only
clip 02 removed anything (1.34 s, splitting its span into 2 intervals); the other
five shipped as single intervals with 0.00 s removed.

**How to read it:** the engine conservatively preserves intentional pauses
(`--keep-pauses` is the default) rather than enforcing a fixed cut rate. A 1.33%
removal rate on a well-paced interview is the expected behaviour, not a weak one —
the `silence share` metric below is where aggressive removal would show up. The
metric earns its keep on sources with real dead air, which this one mostly lacks.

### A2.3 Cross-clip duplicate sentence rate — 0 duplicates

**Definition:** whether any two shipped clips repeat the same sentence. This is
the classic autonomous-clipper failure mode: five "clips" that are one sentence
re-cut five ways.

**Command:**

```sh
cd /home/azureuser/selektyt && .venv/bin/python tools/editorial_audit.py corpus/nasa360-ep5_run/project
```

**Current value: 0 duplicates — `cross-clip PASS no duplicated sentences`, exit
code 0.** VERIFIED.

### A2.4 Silence share per clip — 3.6% to 40.8%

**Definition — read this before using any number in this section.** Despite the
name, `silence share` **does not measure audio.** `tools/editorial_audit.py:check_silence_share`
computes it entirely from transcript word timings overlaid on the edit plan's
kept intervals: `share = 1 - spoken_overlap / kept_duration`. It is a
*transcript-word coverage* figure, not an acoustic measurement. Audio is never
decoded. VERIFIED — the function is lines 205–219 of that file.

**This distinction is not pedantic, and clip 05 proves it.** The metric reports
clip 05 at 40.8%, implying ~7 s of non-speech in a 17.68 s clip. I measured the
actual audio in that window and the implication is false:

- The gap is **6.26 s at 101.56→107.82 s** in the source, between the words
  "Ever." and "And". VERIFIED from `transcript.json`. In render time the same
  window is **6.700→12.840 s** (6.14 s), between the last cue ending 6.700 s and
  the next starting 12.840 s. VERIFIED from `captions/05.srt`.
- Acoustic measurement of that window: **mean −31.4 dB, max −14.1 dB, and 0.00 s
  of silence at `-40dB:d=0.25`** — the region is *loud throughout*. VERIFIED,
  `ffmpeg -af volumedetect` / `silencedetect` on the extracted window.
- Spectral flatness is 0.0066–0.0123 with near-uniform RMS across all 7 s —
  sustained tonal content, i.e. a **music bed**, not speech and not silence.
  VERIFIED, per-second FFT over the extracted window.
- Standalone re-transcription of that window with the same `small`/int8/beam-1
  model the pipeline uses returns only "Ever." and nothing else — so the
  pipeline did not drop or mis-time speech; the model correctly has no words
  where a music bed plays.

**The exact sample-level measurement, reproducible from a checkout** (added
2026-09-22; this is the measurement the audit now performs itself on the shipped
render, replacing the filter-based figures above where the two disagree):

Decode the render once to 16 kHz mono 16-bit PCM and count samples by threshold
in Python rather than parsing a filter's formatted output, because a number a
filter printed is not a number this audit can re-derive:

```sh
ffmpeg -y -v error -i corpus/nasa360-ep5_run/project/facebook/05.mp4 \
  -ac 1 -ar 16000 -c:a pcm_s16le /tmp/05.wav
```

| Window | Duration | RMS | Peak | Below −60 dBFS | Below −45 dBFS |
| --- | --- | --- | --- | --- | --- |
| speech 0.000→6.700 s | 6.70 s | −31.56 dB | −14.41 dB | <0.1 s | 1.94 s |
| **word-free 6.700→12.840 s** | 6.14 s | **−32.26 dB** | **−17.24 dB** | **<0.1 s** | **1.28 s** |
| speech 12.840→17.651 s | 4.81 s | −30.32 dB | −13.68 dB | <0.1 s | 1.11 s |
| whole render | 17.66 s | **−31.40 dB** | **−13.68 dB** | **0.80 s (4.5%)** | **4.33 s (24.6%)** |

The word-free window is *not* the quietest part of the render: both speech
halves sit at a similar or slightly louder RMS, and the window's peak (−17.24 dB)
is 3.2 dB below the render's peak. A bed under dialogue is what that looks like.

A 0.5 s profile of the word-free window rises from −40.03 dB at 6.70 s to about
−30.6 dB by 9.70 s and then holds in the −30 to −33 dB band to the end — a bed
swelling in, not a burst of silence with noise around it.

Note the two "0.00 s" figures answer different questions and both are true.
`silencedetect -40:d=0.25` sums *continuous runs* of at least 0.25 s under its
threshold, and the render's sub-threshold spans are all shorter than that; the
sample count below an absolute −60 dBFS floor totals 0.80 s across the whole
17.66 s render. Quoting either without its definition is how the clip first got
called a pause and then dead air. The audit's line quotes the absolute floor and
names it.

**Conclusion:** the 40.8% is real as a *word-coverage* number and wrong as a
*silence* number. The transcript is sound, the word timestamps are sound, and
the render is sound; the metric's name is the defect.

**Current value (VERIFIED, from `tools/editorial_audit.py`):**

| Clip | Silence share | Rendered | Cues |
| --- | --- | --- | --- |
| 01 | 14.7% | 24.86 s | 20 |
| 02 | 10.3% | 17.10 s | 13 |
| 03 | 3.6% | 16.45 s | 12 |
| 04 | 7.6% | 14.01 s | 11 |
| 05 | **40.8%** | 17.65 s | 10 |
| 06 | 17.9% | 9.08 s | 8 |

Mean silence share 15.8%. `qa.json`/`pacing_report.json` report a run-level
`silence_pct: 8.2` and `filler_pct: 0.0`.

The *Rendered* column is the wall-clock duration of the committed
`facebook/NN.mp4` render, measured from the container rather than estimated from
the edit plan. It differs from the edit plan's `edited_duration` by up to
0.03 s (clip 02: 17.10 s rendered against 17.08 s planned) because the render
snaps to whole frames; the plan's figure is the one to quote when describing the
cut, the rendered figure is the one to quote when describing the deliverable.

**How to read it:** clip 05's 40.8% is a music bed, not dead air — see the
measurement above. The previous reading of this metric ("a deliberate pause
inside a cut is editorial, not dead air") was an inference made without
listening, and it was wrong; the audio in that window is loud and tonal.

**The metric should be renamed** `word_coverage_deficit` or similar, with the
threshold re-derived against measured audio on a larger corpus. The threshold was
corrected to 0.30 — criterion P5's actual bar, where the previous 0.55 was 2.75×
looser than the criterion it existed to enforce and so could never fire — and the
audit now performs the acoustic check itself instead of leaving the cause to
whoever reads the number, but the name still says something the computation does
not do. Until it is renamed, the safe reading is: a high value means *the
transcript has no words there*, and the cause is only stated once the render has
been decoded. A pilot
where the mean drifts far above ~15% on talk content is a signal to investigate,
not a verdict — and `filler_pct` drifting above zero is a signal the filler gate
has stopped firing.

**The second over-bar clip is a different finding, and that is the point.**
`telugu-tutorial_run` clip 01 measures 46.3% word-free, and decoding its render
gives 0.54 s = 4.6% below −60 dBFS of an 11.78 s render at **RMS −17.37 dB** —
loud, not silent, and louder than the speech-bearing NASA window. Two causes fit
those numbers: a music bed, or Telugu speech the transcript missed. Telugu ASR
quality is a known gap (P9; two of six benchmark languages pass), so the second
explanation is the live hypothesis here, and the fix is in transcription, not in
the cut. The audit prints the same `REVIEW` for both clips and does not guess
which one it is — the level is quoted precisely because that is what separates
them, and only a listener can close it. VERIFIED, same decode and sample math as
above on `corpus/telugu-tutorial_run/project/facebook/01.mp4`.

### A2.5 Pacing — 3.62 words/s, 1.00 payoff rate, 0.45 s hook latency

**Definition:** speech rate, hook latency (time to the first hook), payoff
presence, and cue-count distribution.

**File:** `corpus/nasa360-ep5_run/project/pacing_report.json` (mirrored in
`qa.json → pacing`).

**Current value (VERIFIED):** `avg_words_per_second: 3.62`,
`avg_hook_latency: 0.45` s, `payoff_presence_rate: 1.00`,
`duplicate_overlap_rate: 0.00`, `avg_duration: 16.74` s, distribution
`<15s: 2, 15–30s: 4`, `filler_pct: 0.0`.

An independent recomputation from the SRT files (A3.2) gives 345 words over a
99.10 s caption span = **3.48 words/s**, which agrees with the reported 3.62 to
within 4% — a genuine cross-check between two different computations of the same
quantity. VERIFIED.

---

## A3 — Render and caption fidelity

### A3.1 Aspect-ratio correctness — 16:9 exact; vertical 0.5633 vs 0.5625 target

**Definition:** the *display* aspect ratio of each rendered preset, computed from
ffprobe's `sample_aspect_ratio` and storage dimensions — not from the storage
dimensions alone.

**Command:**

```sh
ffprobe -v error -show_entries stream=width,height,sample_aspect_ratio,display_aspect_ratio -of default=noprint_wrappers=1 corpus/nasa360-ep5_run/project/facebook/01.mp4
ffprobe -v error -show_entries stream=width,height,sample_aspect_ratio,display_aspect_ratio -of default=noprint_wrappers=1 corpus/nasa360-ep5_run/project/shorts/01.mp4
```

**Current value (VERIFIED, identical across all 6 clips):**

| Preset | Storage | SAR | DAR | vs target |
| --- | --- | --- | --- | --- |
| `facebook/NN.mp4` | 864 × 486 | 1:1 | **16:9** | exact |
| `shorts/NN.mp4` | 308 × 486 | 8:9 | **1232:2187** = 0.5633 | 0.15% from 9:16 (0.5625) |

**Why the vertical column matters:** the source is itself anamorphic (720 × 486,
SAR 8:9, DAR 320:243 — VERIFIED by probing `source/NASA_360_program5_HD.mov`).
A naive `ih*9/16` crop on an anamorphic source produced a 1:2 display until the
crop width was scaled by the sample aspect ratio; `docs/product-gap-map.md`
records this as a real bug found and fixed, and `tests/test_render.py` asserts the
display ratio to within 0.005. **Reporting storage dimensions here would report
the wrong number**, which is why this metric is defined on the display ratio.

### A3.2 Caption coverage and density — 74 cues, 345 words, 99.85% of render time

**Definition:** cue count and word rate of the shipped captions, and the fraction
of rendered duration actually covered by cues.

**Command:**

```sh
cd /home/azureuser/selektyt && .venv/bin/python -c "
import re
def tos(t):
    h,m,s=t.split(':'); s,ms=s.split(','); return int(h)*3600+int(m)*60+int(s)+int(ms)/1000
TC=re.compile(r'(\d+:\d\d:\d\d,\d\d\d) --> (\d+:\d\d:\d\d,\d\d\d)')
for i in range(1,7):
    s=open('corpus/nasa360-ep5_run/project/captions/%02d.srt'%i).read()
    c=TC.findall(s)
    print(i, len(c), tos(c[-1][1])-tos(c[0][0]))"
```

**Current value (VERIFIED):** 74 cues and 345 words across a 99.10 s caption span,
against 99.15 s of total rendered output = **99.95% coverage**, at
**3.48 words/s** (3.62 words/s as reported by the pacing report — 4% agreement).
Per clip: 20 / 13 / 12 / 11 / 10 / 8 cues at 24.86 / 17.08 / 16.44 / 14.00 /
17.66 / 9.06 s.

The coverage figure is span coverage, not sum-of-cue coverage: per clip it
measures from the first cue's start to the last cue's end, so the inter-cue
gaps are counted as covered. Summing the cue durations themselves gives 87.84 s
(88.6%) — a different, narrower question. The span is what the metric asks
("does the caption track run the length of the render"), but a reader wanting
the other number has it now.

**Caveat that must be stated with this number:** the cue counts and durations are
verified; **that the captions are actually burned into the pixels is not verified
by any test** — `tests/test_render.py:170-176` asserts only that the render file
exists and is non-empty (`docs/TECHNICAL_AUDIT.md`). The honest demonstration is to
play the MP4. Do not cite a test for this.

### A3.3 Caption format parity — 6/6 clips, ASS dialogue count == SRT cue count

**Definition:** the burned-in ASS and the sidecar SRT must carry the same cues, so
an editor who re-times one is not looking at a different cut.

**Current value: 6/6 clips match** — ASS Dialogue lines 20 / 13 / 12 / 11 / 10 / 8
equal the SRT cue counts exactly. VERIFIED, computed from `captions/NN.ass` and
`captions/NN.srt`.

### A3.4 Frame-rate preservation — 29.97 fps in, 29.97 fps out

**Definition:** rendered frame rate equals source frame rate, with no pull-up or
pull-down, since a rate change would break every timecode in the exported
timeline.

**Current value (VERIFIED):** source `r_frame_rate 2997/100` → all 12 renders
`avg_frame_rate 2997/100`. `tools/nle_check.py` opens with
`source frame rate PASS NASA_360_program5_HD.mov @ 29.9700 fps`.

**Coverage limit:** everything measured is 29.97 fps NTSC non-drop. 23.976, 24,
25, 50, 59.94 and drop-frame timecode are exercised in unit tests and **never by a
real encode** (`docs/NLE_COMPATIBILITY.md`, "Frame-rate and NTSC coverage is
thin"). This metric has no value for those rates.

### A3.5 QA pass rate — 12/12 renders, 0 errors, 0 warnings

**Definition:** fraction of shipped renders passing `engine/qa.py`.

**File:** `corpus/nasa360-ep5_run/project/qa.json` — `"ok": true`, `errors: []`,
`warnings: []`, per-file entries for all 12 MP4s.

**Current value: 12/12 pass.** VERIFIED. Silent tails: 0 across all 12.
`black_frame_pct`: 0.125 on clip 05 in *both* presets, 0.0 on the other ten.
`diversity: 0.442`.

**The critical caveat:** `TECHNICAL_AUDIT.md` defect #7 shows this metric cannot
detect a wrong output — `validate_output` checks existence, size, streams,
orientation and duration ≥ 1 s, never aspect ratio precisely, never duration
against the edit plan, never caption presence. **A 12/12 pass is therefore weak
evidence, and the stronger metrics in this document are A3.1, A3.3 and A4.**
Additionally `black_frame_pct` and `visual_diversity_score` swallow all exceptions
and return clean values, so the `0.442` diversity figure should be treated as
unreliable until that behaviour is fixed.

---

## A4 — Interchange fidelity (the metrics that carry the most weight)

These are the metrics that would have to be true for the editor beachhead to work,
and they are the only ones measured by tools *outside* SelekyT's own parsers.

Unlike A1–A3, **every figure in this section is corpus-wide**: 11 sources, of
which 7 exported a timeline, 27 clips, 27 EDLs, 27 FCPXMLs. The command above
audits one of those seven; the sweep over all of them is
`tests/test_nle_frame_deltas.py`, which is also what pins the numbers below. Two
limits of that corpus are stated where they bite and are not hidden behind a
round number: the source media is deleted from the checkout unless `corpus_run.py
--keep-media` is passed, so on five of the seven exporting sources the relink
checks SKIP; and the rendered MP4s are gitignored, so A4.2 is measured on this
machine and is not reproducible from a clean clone.

**Command:**

```sh
cd /home/azureuser/selektyt && PYTHONPATH=/tmp/nlelibs:tools .venv/bin/python tools/nle_check.py corpus/nasa360-ep5_run/project
```

### A4.1 Independent-parse acceptance — 6/6 EDLs

**Definition:** fraction of shipped EDLs accepted by `pycmx`, an independent CMX
3600 reader, precisely so SelekyT's own dependencies cannot influence the parse.
Validating SelekyT's output with SelekyT's parser proves nothing.

**Current value: 6/6 EDLs parse cleanly**, with event and channel-record counts
reported per file (1–2 events, 1–3 channel records). VERIFIED, exit code 0.

**Note:** `pycmx` is now a declared dependency — `pycmx>=1.5` is in the `nle`,
`dev` and `all` extras in `pyproject.toml`, and 1.5.1 is installed in this venv.
The 6/6 above was originally measured by loading the library from `/tmp/nlelibs`
outside the project venv for isolation; it now resolves from the venv. That
removes the dependency gap but **not** the wiring gap: `nle_check.py` is still
invoked by hand or by `tools/corpus_run.py` rather than by the pipeline, the web
shell or CI, so the 6/6 figure is real on demand and is still not enforced
anywhere automated (`TECHNICAL_AUDIT.md` §"CI does not run the tools").

### A4.2 Timeline-vs-render duration agreement — worst case 0.69 frames

**Definition:** |declared timeline duration − measured MP4 duration| in frames,
per preset. This is the only metric that proves the exported timeline describes
the video that was actually shipped.

**Current value (VERIFIED on this machine):** 54 measurements (27 clips × 2
presets, all 7 sources that exported a timeline), all PASS, worst case **0.69 frames**
(pentagon-esper-briefing clip 04: timeline 28.762 s vs render 28.785 s at
30 fps). 32 of the 54 pairs agree to **0.00 frames**; the 22 that do not fall
into three narrow bands — 0.53, 0.63 and 0.69 frames. The tolerance is 2 frames,
set by measuring the committed corpus rather than by taste, which makes it ~2.9×
the largest gap measured.

> **Provenance.** The EDLs and FCPXMLs are committed (27 of each), but **the
> rendered MP4s are not** — all 54 are gitignored, so this metric recomputes
> only where the renders are present. `tests/test_nle_frame_deltas.py` pins it
> when they are and skips with a stated reason when they are not; it does not
> silently pass. Read "VERIFIED" above as *verified here*, not as a claim a
> reader can re-run from a clean checkout. A4.3, by contrast, is reproducible
> from committed artifacts alone.

Per-clip timeline vs render, the sources that render: 0.00 f throughout on
nasa360-ep5 (all six clips) and telugu-tutorial; 0.63 f on ooi-vertical clip 01;
0.69 f on pentagon-esper-briefing clips 02/04/05 and emwcon-panel-2018 clip 05;
0.53 f on royal-society-cloke (all four clips, 25 fps).

**Residual imprecision, stated rather than tuned away:** the 22 non-exact pairs
are each ~21 ms short regardless of frame rate, which is container overhead
rather than a cut disagreement — the timeline declares a whole frame count and
the MP4 carries the muxer's own duration. A shortfall of 0–1 frame is therefore
systematic (`docs/NLE_COMPATIBILITY.md`).

<!-- stale-begin: the figures below are quoted only as the error being
     corrected; tests/test_nle_frame_deltas.py excludes this block so a
     correction note can record a wrong number without re-asserting it -->
> **Correction, 2026-09-20.** An earlier version of this section reported a
> worst case of 1.63 frames over "12 measurements (6 clips × 2 presets)" with a
> per-clip table of 24.858/24.892 (1.00f), 17.084/17.138 (1.63f),
> 16.450/16.450, 14.014/14.014, 17.651/17.684 (1.00f), 9.076/9.076. That table
> was measured on nasa360-ep5 alone and is not reproducible from the committed
> artifacts: re-running `tools/nle_check.py` on the same six clips now returns
> 0.00 frames for all of them, and 24.858 s and 17.084 s match no committed
> timeline at all. The numbers above are re-measured across all six sources
> that render, and are recomputed by `tests/test_nle_frame_deltas.py` so they
> cannot go stale again.
<!-- stale-end -->

### A4.3 EDL-vs-FCPXML agreement — 0.00 frames on all 27

**Definition:** |EDL total record span − FCPXML spine total|. The two formats are
emitted by separate code paths; their agreement is evidence both describe the same
cut.

**Current value: 0.00 frames on all 27 clips.** VERIFIED, and recomputed by
`tests/test_nle_frame_deltas.py`. The render is a third
independent path (an ffmpeg filter chain), and its agreement with both is A4.2.

### A4.4 Source-interval fidelity — worst case 1.00 frames

**Definition:** |EDL source in/out − the selection the cut was made from|. If this
drifts, the timeline points at the wrong part of the source media.

**Current value (VERIFIED):** all 54 PASS — 27 clips read in both formats —
worst case **1.00 frames** (royal-society-cloke clip 03, a two-interval cut,
25 fps). That is inside the 1.5-frame tolerance, which is set to that measured
worst case rather than chosen. nasa360-ep5 alone is tighter, 0.72 frames at clip
03's end point.

**Why the tolerance is 1.5 and not smaller:** 1.00 frames is a rounding boundary,
not a systematic drift — the selection is scored in seconds and the timeline in
frames at a rate that does not divide it. Tightening the tolerance below the
measured worst case would fail a correct export.

### A4.5 Media relinkability — 9/9 verified where media is retained, 18 NOT RUN

**Definition:** every FCPXML asset `src` is an absolute `file:///` URI to an
existing file, and no asset-clip reads past its asset's declared extent.

**Current value: 6/6 for the extent half; the relink half is NOT RUN on this
project.** `fcpxml clips within asset` PASSES on all six nasa360 clips — no clip
reads past its asset. `fcpxml media relinkable` **SKIPs on all six**, because
`corpus_run.py` deletes the source media unless `--keep-media` is passed, and
nasa360-ep5's media is not in this checkout. So the URI half of this metric is
unverified here, not confirmed; running the audit on a run built with
`--keep-media` is what would confirm it.

Corpus-wide, the URI half is measured where the media happens to be retained —
two sources, pentagon-esper-briefing and royal-society-cloke, 9 assets — and
PASSES 9/9, every `src` an absolute `file:///` URI to an existing file. The
other five exporting sources have no media in the checkout and SKIP the same way.
This matters because a relative asset URI was once a live defect that would have
imported with every clip offline (`NLE_COMPATIBILITY.md`, defect 1 — found on all
six timelines of a freshly regenerated run, i.e. the live path, not a stale
artifact), and the regression test for it
(`tests/test_nle_check.py::test_an_offline_media_reference_is_caught`) is what
actually holds that fix, since it does not depend on the corpus media being
present.

### A4.6 FCPXML structural validity — 6/6 well-formed, version 1.11

**Definition:** parses as XML, declares `version="1.11"`, uses only elements in
the 1.11 vocabulary, and has a spine that advances.

**Current value: 6/6 on every sub-check.** VERIFIED.

**What this explicitly is not:** it is not validation against Apple's published
XSD. Apple's schema could not be retrieved (candidate URLs returned 404/403), so
the vocabulary check is a guard over the elements SelekyT emits plus the
structural containers an NLE accepts. **A document can pass A4.6 and still be
rejected by Final Cut Pro.** (`docs/NLE_COMPATIBILITY.md`.)

### A4.7 NLE import success — **NOT MEASURED**

**Definition:** whether any real NLE opens a SelekyT timeline with all clips
online and the correct duration.

**Current value: NOT MEASURED — zero imports, ever.** No NLE is installed in any
environment SelekyT has run in. Not Final Cut Pro, not DaVinci Resolve, not
Premiere Pro, not Avid. This is the single largest unverified claim in the venture
and the precondition for the entire pilot program.

**Target, stated in advance:** sequence opens with all clips online and duration
matching the render to within 2 frames, with no relink dialog. Record the NLE name
and exact version.

**This is the metric the company should be judged on next.** It costs zero
capital. It has not been done.

---

## A5 — Pipeline and engineering health

### A5.1 Test suite — 1287 passed, 13 skipped in 260.7 s

**Command:**

```sh
cd /home/azureuser/selektyt && .venv/bin/python -m pytest -q
```

**Current value: `1287 passed, 13 skipped in 260.7 s`, exit code 0.** VERIFIED,
re-run 2026-09-21. The 13 skips are not one group, and treating them as one
hides the most important of them: **7** are `SELEKYT_PROXY_MEDIA`-gated
measurements that expect a ~239 MB recording that is not redistributable with
the repo — opt-in real-media measurements, so the suite reports green on every
CI run while the validation it names does not happen. **5** are WER records
that predate the `wer_err_total` field, and **1** is a language with no run
record. Only the last of those is arguably "environment".

**How to read it:** the number is large and green and it is the *weakest* number
in this document. `TECHNICAL_AUDIT.md` names specific tests that assert what the
code claims rather than what the output is: the burned-captions test asserts a
non-empty file, four tests assert a literal `True` constant, one reproducibility
test asserts only that two values are non-negative, and `test_editorial_rank.py:51`
disables its own assertion with `or True`. Thirteen tests skip, including the only
end-to-end HTTP test and the only Rust-sidecar parity test, and that count is
not stable — it moved 10 → 7 → 12 → 13 → 12 → 13 as gated real-media tests and
WER records arrived and departed, so a skip count is a snapshot of one run, not
a property of the commit. **Count the suite as
engineering hygiene, not as product quality.**

### A5.2 Audit coverage — 2 tools, 90+ checks, 0 wired into CI

**Definition:** whether the falsifiable audit tools actually run against shipped
output.

**Current value:** `tools/nle_check.py` (**92 checks, all PASS, exit 0**) and
`tools/editorial_audit.py` (7 checks, all PASS) both green when invoked manually —
and **referenced by nothing**: not `selekyt/pipeline.py`, not `selekyt/server.py`,
not `web/server.mjs`, and not `.github/workflows/ci.yml` (`TECHNICAL_AUDIT.md`,
VERIFIED by grep). The pipeline instead validates its own EDL with its own
emitter's validator, which is exactly the self-validation `nle_check.py` was
written to rule out.

**Target:** both tools run on every shipped project and in CI. The `pycmx`
dependency half of this landed — `pycmx>=1.5` is in the `nle`, `dev` and `all`
extras, so A4.1 executes instead of skipping — but the wiring half is still open.

### A5.3 Transcription provenance — 425 segments, 4,236 word-timed tokens

**File:** `corpus/nasa360-ep5_run/project/transcript.json`.

**Current value (VERIFIED):** `language: en`, `asr.engine: faster-whisper`,
`asr.model: small`, `asr.word_timestamps: "per-token"`, `asr.chunks: 5` with
`chunking: "5 min chunks (+15s overlap)"`, `num_threads: 4`, no fallback fired
(`fallback_reason: null`, `downgrade_reason: null`), 425 segments, 4,236
word-timed tokens.

**How to read it:** chunked decode of a genuinely long source with no observed
boundary artefacts in any downstream cut (`docs/product-gap-map.md`). Word-level
timing is the foundation of every caption and every timecode in this document.

### A5.4 Disk cost per project — ~952 MB, 93% source

**Definition:** disk consumed per source, split into retained intermediates and
deliverables.

**Current value (VERIFIED on this host):** ~952 MB per project — source 884 MB,
transcription ~49 MB, deliverables `facebook/` 11.90 MB + `shorts/` 7.20 MB +
captions/timelines 0.04 MB. **The deliverables are ~19 MB; 93% of the cost is
the source copy and intermediates that nothing ever deletes** (no cleanup, GC or retention policy
exists anywhere in the codebase — `TECHNICAL_AUDIT.md`). Free disk on this host
is 4.6 GB of 29 GB.

**Target:** delete `source/` and `transcription/` once renders validate, taking
per-project cost to ~19 MB. Until then this metric caps pilot capacity — see
`docs/PILOT_PROGRAM.md` §6.

---

# B. Business metrics — all N/A today

Every metric below is defined so that it can be measured when there is something
to measure, and every current value is `N/A` for the same reason.

> **SelekyT has zero users, zero revenue, zero pilots, and zero investors. There
> is no customer, no transaction, no contract and no usage event of any kind to
> compute a business metric from.**

| Metric | Definition (so it is measurable later) | Current value | Reason |
| --- | --- | --- | --- |
| **Active users** (weekly / monthly) | Distinct users running ≥ 1 pipeline in the period | **N/A** | Zero users |
| **Paying customers** | Distinct accounts with a completed transaction | **N/A** | Zero revenue |
| **MRR / ARR** | Recurring revenue, monthly and annualized | **N/A** | Zero revenue; no pricing has ever been charged |
| **ARR per customer** | ARR ÷ paying customers | **N/A** | Zero revenue |
| **Churn rate** | Customers lost ÷ customers at period start | **N/A** | No customers to churn |
| **Customer acquisition cost** | Fully-loaded spend ÷ new customers acquired | **N/A** | No acquisition spend, no customers |
| **Activation rate** | % of new sign-ups completing one full pipeline run | **N/A** | No sign-ups |
| **Retention (D30 / W8)** | % of users returning 30 days / 8 weeks after first run | **N/A** | No users |
| **Net Promoter Score** | Standard 0–10 "would you recommend" scale | **N/A** | Nobody has been asked |
| **Pilot count** | Signed pilot agreements in execution | **N/A — zero** | No pilot program exists; `docs/PILOT_PROGRAM.md` is a design, not a record |
| **Pilot conversion** | Pilots → paying customers | **N/A** | Zero pilots |
| **Editor hours saved** | Before/after hours on selection and rough-cut, per episode | **NOT MEASURED** | No editor has ever used SelekyT. The 3–6 h/episode baseline in `CUSTOMER_DISCOVERY.md` is an ASSUMPTION from zero interviews |
| **Willingness to pay** | % stating they would pay ≥ $30/month | **N/A** | Nobody has been asked; no pricing experiment has run |
| **Clip performance (views / retention / engagement)** | Platform analytics on published SelekyT clips | **NOT MEASURED** | None of the 6 clips has ever been published anywhere |
| **Gross margin** | Revenue − delivery cost, per run | **N/A** | Zero revenue. Compute cost exists (~900 MB disk, CPU transcription) but attaches to nothing billable |
| **Burn / runway** | Monthly spend ÷ cash | **NOT MEASURED** | Zero investors and no recorded cash position; the author makes no claim about the company's finances |
| **Competitive win rate** | Head-to-head evaluations won | **NOT MEASURED** | No head-to-head comparison has ever been run against OpusClip, Descript or any NLE |
| **NLE import success (external)** | % of shipped timelines opening cleanly in a named NLE and version | **NOT MEASURED** | Zero imports, ever. Tracked as A4.7 until the first one is recorded |

### The three metrics that matter before any of the above can exist

1. **A4.7 — NLE import success.** NOT MEASURED. Zero capital to close. This is the
   precondition for the editor beachhead and for the pilot program.
2. **Editor hours saved.** NOT MEASURED. Requires one editor and one episode. This
   is the whole value proposition, and it is currently an assumption.
3. **S1 — Do editors open the timeline at all?** NOT MEASURED. If they take the
   MP4 and never open the EDL/FCPXML, the wedge has no value to the segment it was
   chosen for (`CUSTOMER_DISCOVERY.md` falsifier #1).

Until those three have real values, every business metric in the table above will
stay `N/A`, and that is the correct state to report. **A metric with no current
value is fine. A metric with an invented value is a defect.**

---

## Reproducing every product-quality number

```sh
cd /home/azureuser/selektyt
.venv/bin/python -m pytest -q                                            # A5.1
.venv/bin/python tools/editorial_audit.py corpus/nasa360-ep5_run/project       # A2.3, A2.4
PYTHONPATH=/tmp/nlelibs:tools .venv/bin/python tools/nle_check.py corpus/nasa360-ep5_run/project   # A4.1–A4.6
ffprobe -v error -show_entries stream=width,height,sample_aspect_ratio,display_aspect_ratio -of default=noprint_wrappers=1 corpus/nasa360-ep5_run/project/facebook/01.mp4   # A3.1
ffprobe -v error -show_entries stream=width,height,sample_aspect_ratio,display_aspect_ratio -of default=noprint_wrappers=1 corpus/nasa360-ep5_run/project/shorts/01.mp4      # A3.1
```

All of the above were run by the author on 2026-09-19 and exited 0 **except the
editorial audit, which exits 1 by design**. That is not a regression — it is the
audit firing on the one condition it was written for. Clip 05 is 40.8%
word-free, over P5's 30% bar, and since 2026-09-22 the audit decodes the shipped
render and classifies the window rather than asserting a cause from the word
timings: it prints `REVIEW` with the measured figures (0.80 s = 4.5% below
−60 dBFS, RMS −31.4 dB — content, not dead air) and still exits 1, because a clip
over the bar needs a human to listen to it before it ships. An over-bar clip with
corroborated dead air, or with no render decodable, stays a plain `FAIL`. The remaining
product-quality values are computed directly from the JSON artifacts under
`corpus/nasa360-ep5_run/project/` — `editorial_report.json`, `edit_plan.json`,
`report.json`, `qa.json`, `pacing_report.json`, `transcript.json` and
`captions/*.srt` — with the exact command given at each metric.
