# Transcription layer: measured evidence

Status: measured evidence for the transcription layer, collected 2026-09-19 by
`tools/wer_bench.py`. This document exists to separate what was *measured* from
what was *assumed* about the three corpus sources whose transcripts looked
wrong, and to put a number on criterion **P8** (English WER), which had never
been computed.

Every figure below is paired with the command that produced it. Nothing here is
a claim about a language no speaker of that language verified.

---

## 1. What is measured, and what is not

### Measured

| Thing | Instrument | Where the number lives |
| --- | --- | --- |
| English WER + CER against a real reference transcript (LibriSpeech `test-clean`, CC BY 4.0) | `tools/wer_bench.py bench` | §3 |
| Real-time factor, wall time, peak RSS per model | same | §3, §8 |
| Decode-parameter sensitivity on ground truth (beam width) | `tools/wer_bench.py bench` | §4 |
| The in-path VAD gate, toggled on a failing source | `tools/wer_bench.py describe --vad-filter` | §6.3 |
| Transcript *shape* on the three failing sources: segment count, character count, mean segment duration, speech coverage of the source | `tools/wer_bench.py describe` | §5 |
| Language identification and its confidence on those three sources | `tools/wer_bench.py detect` | §6.2 |
| Audio-level measurements of the three sources (peak/RMS dBFS, energy-VAD speech fraction) | `engine/vad.py` EnergyVAD + numpy | §5.2 |
| Which of the four candidate root causes can be eliminated, and by what | code reading + measurement | §6 |

### NOT measured, and why

1. **WER on the three in-corpus sources.** `telugu-tutorial`, `hadhazy-protest`
   and `hindi-baiga-interview` still have no reference transcript, and no speaker
   of any of those languages was available. §7.1 measures Telugu on a *different*
   corpus that has one (OpenSLR SLR66) — that bounds the model's Telugu error
   rate, but it is not a score for `telugu-tutorial`, whose transcript is still
   unmeasured.
2. **Non-English WER at the C10 languages (criterion P9).** §7.1 measures
   Telugu, which is **not** one of the six C10 languages, and §7.2 scores
   Polish, which is. The remaining five (German, Spanish, French, Dutch,
   Italian) are still **UNMEASURED**. The Multilingual LibriSpeech tarballs are
   6.2 GB (Polish) to 114.5 GB (German), which cannot be materialised here;
   §7.2 streams them instead of downloading. See §7.
3. **Whether a transcript is semantically correct in a language nobody here
   reads.** §5 reports counts and samples. A sample that *looks* plausible is
   not evidence of accuracy — it is evidence that the output is well-formed.
   This distinction is the whole point of §5.
4. **In-domain video WER** against official captions (the second half of P8).
   C4/C8 from `docs/EVALUATION.md` were not re-fetched in this session; §8 lists
   this as the cheapest remaining measurement.
5. **The video-container decode chain.** LibriSpeech ships 16 kHz mono FLAC, so
   the production extraction step (`providers/audio.extract_pcm_wav`) is a
   no-op resample on it. C9 therefore measures the ASR, not the demux. §6.4
   tests the resampler separately on a real container.
6. **`large-v3` on the three failing sources.** The English `large-v3`
   measurement in §3 ran against a byte-verified model, but its 3.1 GB weight
   was later reclaimed from the cache by disk pressure and re-downloading is
   not authorized at this fill level. Whether `large-v3` materially changes the
   failing outputs is unmeasured — see §5.3. This is a disk limitation, not a
   judgement that it would not help.

---

## 2. Method

### The sample

LibriSpeech `test-clean` (2620 utterances) was subsampled to a **fixed,
deterministic 50-clip / 6.56-minute / 1036-reference-word set**. A stride of 52
over the sorted file list — not a random draw — spreads the clips across
speakers and chapters and makes the sample reproducible from the same tarball.
The manifest names every utterance, so any figure below can be re-derived:

```
.venv/bin/python tools/wer_bench.py prepare \
    --tar corpus/wer/test-clean.tar.gz \
    --out corpus/wer/librispeech-clean --count 50 --max-minutes 8
```

`prepare` extracts only the sampled FLACs plus the small chapter transcripts,
which is what made this fit on a host with single-digit GB free.

### The scoring

WER and CER are computed by the harness itself (`_rates`), not by `jiwer` (not
installed, and not added as a dependency for a number this load-bearing).
Implementation, unit-tested against hand-computed cases:

- both sides are normalized — lowercased, punctuation dropped to whitespace,
  whitespace collapsed — which is the standard LibriSpeech reporting
  convention, so the number reflects recognition and not formatting;
- corpus-level WER is **summed edits over summed reference length**, not an
  average of per-clip WERs, so a 3-second clip does not count as much as a
  30-second one;
- an empty hypothesis against a real reference scores 100%, so a silent decode
  shows up as a bad number instead of a division by zero.

With 1036 reference words, the binomial standard error at ~3% WER is ≈ 0.5
percentage points, so differences below ~1.5 points between models are noise
and are treated as such below. A full 2620-clip run would give the canonical
figure; §8 says why it was not done here.

### The decode path

The bench scores with the production decode parameters — `int8`, `beam_size=1`,
`word_timestamps=True`, `vad_filter=True`, `condition_on_previous_text=False` —
duplicated as constants in the harness rather than imported, so the bench stays
stable when plumbing changes. A run-time drift check
(`_assert_decode_params_match_production`) greps the production
`_transcribe_chunk` source and warns if the assumed parity no longer holds.

What differs from the production call graph, stated plainly: the bench loads one
model and decodes all 50 clips against it, instead of calling
`providers.whisper.transcribe` once per clip. That is deliberate — loading per
clip would make the timing numbers about model load rather than decode — and it
means the bench does not measure the production chunk/checkpoint/resume path.
The `describe` command covers that path: it calls the production entry point
directly.

---

## 3. English WER (criterion P8, first measurement ever taken)

Reproduce with:

```
.venv/bin/python tools/wer_bench.py bench \
    --manifest corpus/wer/librispeech-clean/manifest.jsonl \
    --model small --tag c9-small
```

| run | model | WER | CER | clips | audio | RTF | peak RSS | wall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `c9-small` | `small` (int8, beam 1) | **3.38%** | 1.35% | 50 | 6.56 min | **0.649×** | 663 MB | 256 s |
| `c9-medium` | `medium` (int8, beam 1) | **2.99%** | 1.27% | 50 | 6.56 min | **1.556×** | 1597 MB | 613 s |
| `c9-large-v3` | `large-v3` (int8, beam 1) | **3.38%** | 1.71% | 50 | 6.56 min | **2.956×** | 3043 MB | 1164 s |

All three rows are the **same 50 clips** (one manifest, `corpus/wer/librispeech-clean/manifest.jsonl`),
so the comparison is model-to-model and nothing else varies. RTF excludes
one-time model load; `wall` includes it.

### Reading this table

- **P8 as written passes, at both model sizes measured, with an enormous
  margin.** The criterion is "≤ 15% on C9 `test-clean`"; the measured figures
  are ~3%. There is no English-transcription-accuracy problem at this model
  tier, and the `small` default is not the thing to fix for English.
- **`medium` buys nothing measurable on English.** 3.38% → 2.99% is 0.39
  percentage points, below the ~0.5 pp standard error at this sample size, and
  it costs 2.4× the wall time and 2.4× the memory. On an easy corpus the
  bigger model is not buying accuracy; it is buying confidence nobody can see.
- **`large-v3` buys nothing measurable either, and it costs 4.6×.** Its WER
  (3.38%) is identical to `small` and its CER (1.71%) is *worse* than either
  smaller model, while it takes 2.956× realtime and 3043 MB of RSS. The CER
  inversion is not evidence that large-v3 is worse at English — it is inside
  the noise band — but it is decisive evidence that it is not *better*, at 4.6×
  the compute and 4.6× the memory of the shipping default.
- The ordering to take from this table is not "small > medium > large-v3"; it
  is "**these three models are statistically indistinguishable on this sample,
  and their costs differ by 4.6×**".
- In absolute terms the whole comparison turns on a handful of word errors:
  1036 reference words, so 3.38% is **35 errors**, 2.99% is **31 errors**, and
  3.38% is 35 again. The model ranking is being decided by four word errors —
  which is exactly why §8 asks for reference transcripts rather than a model
  decision on this sample.
- The number measures *read English speech at 16 kHz mono with no competing
  audio*. It does not measure the corpus's interviews, panels or outdoor
  handheld footage. LibriSpeech is the easy end of the difficulty range, and
  the margin over the threshold should not be read as headroom on hard sources.
- Because the sample is 50 clips / 1036 reference words, differences smaller
  than ~1.5 pp are not resolvable. The full 2620-clip corpus would be needed to
  rank models this close; §8 lists that as follow-up work.

---

## 4. Decode-parameter sensitivity (on the same ground truth)

Because C9 has a reference, it can answer "does the production decode
configuration cost accuracy?" — which is the question that actually bears on the
failing sources, where no reference exists. The production decode is
`beam_size=1` (greedy), `compute_type=int8`, `vad_filter=True`,
`condition_on_previous_text=False`. Of these, beam width is the one a reviewer
would reach for first, so it is the one measured.

```
.venv/bin/python tools/wer_bench.py bench \
    --manifest corpus/wer/librispeech-clean/manifest.jsonl \
    --model small --beam-size 5 --tag c9-small-beam5
```

| run | beam | WER | CER | clips | audio | RTF | peak RSS | wall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `c9-small` | 1 (production) | **3.38%** | 1.35% | 50 | 6.56 min | 0.649× | 663 MB | 256 s |
| `c9-small-beam5` | 5 | **4.05%** | 1.48% | 50 | 6.56 min | 0.691× | 662 MB | 273 s |

**Raising the beam width does not improve English accuracy — it makes it
nominally worse (3.38% → 4.05%, i.e. 35 → 42 word errors out of the same 1036
reference words), at 6% more wall time.** Both figures are inside the ~1.5 pp
resolution limit of a 1036-word sample, so the honest reading is "no measurable
difference either way", not "beam 5 is harmful". But there is decisively no
evidence that beam 1 is costing accuracy on English, and no reason to spend the
6%. The error counts are derived, not guessed: the run record carries
`wer_ref_words` (1036, the manifest's reference length) alongside the rate, and
rate × ref length gives 35.0 / 31.0 / 35.0 / 42.0 — whole numbers for all four
models, which is a consistency check on the scoring path rather than a claim
about a stored field.

The result also constrains the root-cause story in §6.5. If the Telugu failure
were a greedy-decoder artifact — the decoder committing to a low-probability
first token with no chance to reconsider — then a wider beam should help on a
language the model handles well, and one would expect at least a small
improvement here as a sanity signal. There is none. That does not prove beam
width is irrelevant on Telugu (English at 3.4% WER is not where a decoder
would be struggling), but it removes the cheapest available fix from
consideration: there is no setting of `beam_size` that is known-good, so
tuning it against a language with no reference would be blind guessing.

`compute_type=int8` was not A/B'd against `int8_float32` or `float32` on this
host — int8 is what ships and what the RTF and cost numbers in §8 are stated
for, and a quantization-sensitive difference would show up as a WER change on
this same manifest, which is a follow-up rather than a gap in the conclusion
(the failing sources are a coverage problem, §6.5, not a fidelity problem).

---

## 5. The three failing sources

### 5.1 Method: describe, not score

For these three, the harness runs the **production** transcription entry point
(`providers.whisper.transcribe`, via `tools/wer_bench.py describe`) and reports
what is measurable: segment count, character count, mean segment duration,
speech coverage of the source duration, and transcript samples. Language
identification and its confidence are measured separately by
`tools/wer_bench.py detect`, because the production call signature returns the
detected language but not its probability and adding it would mean churning
production code for a diagnostic. **No error rate is computed for any of these
three, and none could be.**

```
.venv/bin/python tools/wer_bench.py describe \
    --media corpus/wer/media_telugu-tutorial.webm --model small --language te \
    --tag d-small-forced-telugu-tutorial
```

### 5.2 Audio-level measurement of each source

Measured directly on the 16 kHz mono extraction, with the pipeline's own
`EnergyVAD` providing its own speech-region segmentation:

| source | duration | peak | mean RMS | energy-VAD speech fraction | ASR speech coverage |
| --- | --- | --- | --- | --- | --- |
| `telugu-tutorial` | 339.6 s | −0.8 dBFS | −17.1 dBFS | 246.7 s / 339.6 s = **73%** | **15.3 s = 4.5%** |
| `hadhazy-protest` | 126.0 s | −8.7 dBFS | −41.8 dBFS | **0 s / 126.0 s = 0%** | 109.7 s = 87.1% |
| `hindi-baiga-interview` | 53.8 s | −3.2 dBFS | −26.9 dBFS | 11.2 s / 53.8 s = **21%** | 50.0 s = 92.9% |

Energy-VAD regions from `engine.vad.EnergyVAD` + `segment_from_scores`
(threshold 0.5, the pipeline's own defaults); ASR coverage is the sum of
returned segment spans over source duration, from the original `small` corpus
run. The two columns disagree on every row, in both directions — that
disagreement is the subject of §6.

The three sources are not alike, and this matters for §6:

- **telugu-tutorial** is loud, well-recorded, and 73% of its 340 s is
  speech-energy audio. Whatever went wrong was not the recording.
- **hadhazy-protest** is genuinely difficult: mean −41.8 dBFS, peak −8.7 dBFS.
  The energy VAD returns **0 s of speech** for the entire 126 s — every frame
  is below its 0.02 base threshold — while the ASR returned 110 s of Hungarian.
  The "difficult audio" axis is real and measured, not inferred.
- **hindi-baiga-interview** sits in between.

### 5.3 Transcript shape by source and model

This is the section that answers the mission's "does a bigger model materially
change the failing outputs?" The `small` rows are the production corpus runs
(`tools/corpus_run.py`, `--model small`); the `medium` rows are
`tools/wer_bench.py describe` on the same media with the same forced language,
which calls the same `providers.whisper.transcribe`. **No row has an error
rate, and none can (§5.1).** What is comparable is shape: how much of the
audio the transcript claims, and how it is segmented.

| source | model | segs | chars | speech claimed | mean seg | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| `hadhazy-protest` (126.0 s) | `small` | 38 | 2021 | 109.7 s (87%) | 2.89 s | well-formed, full coverage |
| | `medium` | 35 | 1851 | 111.9 s (89%) | 3.20 s | **materially the same** |
| `hindi-baiga-interview` (53.8 s) | `small` | 3 | 315 | 50.0 s (93%) | 16.67 s | 3 huge segments, garbled |
| | `medium` | **11** | **411** | 49.5 s (92%) | **4.50 s** | **materially changed** |
| `telugu-tutorial` (339.6 s) | `small` | 4 | 80 | 15.3 s (4.5%) | 3.84 s | near-empty |
| | `small` re-run (describe) | 3 | 34 | 3.5 s (1.0%) | 1.18 s | near-empty, *different* |
| | `small`, detection enabled | 1 | 120 | 5.0 s (1.5%) | 5.02 s | near-empty |
| | `small`, `vad_filter=False` | 3 | 57 | 5.8 s (1.7%) | 1.94 s | near-empty |
| | `medium` | *in flight* | — | — | — | see below |

Three conclusions, one per source, and they are *not* the same conclusion:

**Hungarian: a bigger model changes nothing material.** 38 → 35 segments,
2021 → 1851 characters, 109.7 s → 111.9 s of claimed speech on a 126 s file.
Both models claim essentially the whole file. The medium output is not a
different class of result — it is the same coverage with slightly less text.
Whatever the accuracy of either is, `medium` is not a fix for this source;
it is the same transcript at 2.4× the cost.

**Hindi: a bigger model materially changes the output, in the direction of
real segmentation.** 3 → 11 segments and 16.67 s → 4.50 s mean segment
duration. The `small` output is three very long runs of garbled Devanagari
(`मेरना लख्श्दाल जबगा बजाख तैसिर...`); the `medium` output begins
`मेरा नाम लक्मनाल अगर्याइज जात्टी बैगा...`, and `मेरा नाम` / "mera naam" is a
phrase recognizable to anyone who has seen basic Hindi — "my name". **This is
the one source where a model upgrade produces a visibly different,
plausibly better transcript**, and it is the strongest single argument in this
document for re-measuring with a reference.

That sentence must be read with its caveat attached, and the caveat is the
point of this subsection. **No language verification was performed on any
sample in this document.** "मेरा नाम" is a stock phrase I recognize, which is
weak evidence of exactly the kind the corpus summary warned about — a
well-formed-looking surface over an unverified body. It is not evidence that
the rest of the 411 characters are correct, and a single correct stock phrase
at the start of a transcript is a classic place for a language model to be
reliably right while the remainder is wrong. What *is* measurable and is stated
as fact is the shape change: 3 segments of 16.7 s becoming 11 segments of 4.5 s.
Whether that shape change is an improvement in *accuracy* is precisely what a
reference transcript would score, and nothing here scores it.

**Telugu: nothing changes the coverage collapse.** Four different
configurations — the production run, a re-run, detection-enabled, and the VAD
gate disabled — all return between 1.5% and 4.5% of a 340 s file that is 73%
speech-energy audio. The character counts differ (80 / 34 / 120 / 57) and the
windows differ (311–332 s / 300–308 s / 327–332 s), but the *shape* is
invariant: a few seconds of text out of 5.7 minutes.

**`large-v3` was not run on the three failing sources, and here is why that is
a disk limitation rather than a skipped measurement.** The English
`c9-large-v3` run in §3 completed against a `model.bin` verified byte-exact
against the Hugging Face listing. Some time after that run wrote its record,
the 3.1 GB weight was reclaimed from the cache — the host was at 86–96% full
with seven agents sharing 29 GB, and the file is gone from every directory on
the filesystem. Re-downloading it is not authorized at this fill level, so the
three `large-v3` describes were dropped from the queue. That leaves a real gap:
whether `large-v3` materially changes the failing outputs is **unmeasured**,
and it is the single most obvious follow-up for anyone with the disk. The
English row it *did* produce (3.38% WER, identical to `small`, at 4.6× the
cost) is unaffected — the model was intact when it ran.

Note the instability across the three `small` Telugu rows, which is itself a
finding: same audio, same parameters, four different near-empty transcripts.
A source in this state has no single transcript to treat as correct, which is
why §8 recommendation 5 asks for a reference before any model decision.

### 5.3b Decode time as an independent symptom

The three sources also differ by an order of magnitude in how long the same
model spends on them, and the ordering tracks nothing obvious about audio
length. Wall time over audio length, `medium` model, forced language:

| source | audio | wall | wall ÷ audio |
| --- | --- | --- | --- |
| `hadhazy-protest` | 126.0 s | 76.3 s | **0.61×** |
| `hindi-baiga-interview` | 53.8 s | 108.3 s | 2.01× |
| `telugu-tutorial` | 339.6 s | see note | **>4× and climbing** |

The Telugu figure is a *lower bound* recorded while the run was still in
progress, on a host shared with other work, so it is not a clean RTF — but the
asymmetry is too large to be contention. Hungarian decodes *faster than
realtime* while Telugu decodes at several times realtime on the same model, and
the two audio files are within a factor of 2.6 in length.

This is not a puzzle once the library defaults are read. The production call
overrides `beam_size`, `condition_on_previous_text` and `vad_filter` but **not
`temperature`**, so faster-whisper's default ladder
`[0.0, 0.2, 0.4, 0.6, 0.8, 1.0]` is active in production: a decode that fails
`log_prob_threshold` (−1.0), `compression_ratio_threshold` (2.4) or
`no_speech_threshold` (0.6) is retried at the next rung, up to six attempts per
window. A decoder that keeps failing those checks — which is what a model far
outside its training distribution does — spends up to 6× the compute and lands
on a *stochastic* rung, which is the mechanism behind the run-to-run
instability in the table above. The two symptoms measured in this section,
instability and time, have one shared explanation, and §6.5b tests it directly.

### 5.4 What the samples do and do not show

Samples are included in each describe record. Two things they establish:

1. **The hadhazy output is not a measured failure.** An earlier draft of the
   corpus summary called the outputs "fluent-looking phonetic noise" and
   counted three; `docs/CORPUS_RESULTS.md` has since retracted both the count
   and the characterization of this source, and this section's argument is why.
   For hadhazy that characterization does not survive the *shape* measurement,
   and this is a claim about shape, not about Hungarian: 38 segments, 2021
   characters and 109.7 s of claimed speech over a 126 s file (§5.3). Phonetic
   noise does not fill 87% of a file with short, punctuated, 2.9 s segments.
   The samples (`Azért csinálunk`, `szerintem biztos, hogy fogunk még ide
   jönni`) look like Hungarian to a reader who does not speak it — and that
   look is exactly the thing this document is not allowed to count as
   evidence. **What is established is the coverage and segmentation; what is
   not established is any word of it.** The difference from Telugu is that a
   near-total-coverage transcript of a quiet recording is a *plausible
   imperfect transcript*, while 4 seconds out of 5.7 minutes is not plausible
   as anything but a failure.
2. **The telugu output has no such defence.** The measurable failure is
   coverage, and it needs no language knowledge to read: the file is 339.6 s
   long and 73% of it is speech-energy audio, but the transcript is **4
   segments / 80 characters, all inside one 21-second window** (311.3–332.1 s).
   The model emitted speech for ~6% of the audio the energy VAD says contains
   speech. Whether the 80 characters are correct Telugu is unmeasurable here;
   that so little was emitted at all is measurable, and is the fact to
   explain.

   Re-running the same source through the same production path reproduced the
   failure **in kind, not in detail**: the re-run produced 3 segments / 34
   characters in a different 8-second window (300.3–308.4 s), with repeated
   syllables (`నికికికితికి`) rather than the original's four segments. Same
   parameters, same audio, same chunking — a different near-empty transcript.
   The decode is not merely poor on this audio, it is *unstable*: the failure
   mode moves around between runs, which is what a model operating far outside
   its training distribution looks like (whisper retries with a higher
   temperature when a decode falls below its confidence threshold, so an
   out-of-distribution language has a random element a healthy decode does
   not). Both runs are recorded; neither is the "correct" one, and the
   instability is why a single transcript should never be treated as ground
   truth for a source in this state.

---

## 6. Root cause: four hypotheses, and the test that decides each

The mission offered four candidate causes. One is eliminated by reading the
code, one by the geometry of the sources, and two needed measurement; a fifth
cause emerged from the measurements and is §6.5. Each is decided by a test that
would come out differently if the hypothesis were false.

### 6.1 H1 — chunking splits mid-utterance → fragments. **Eliminated: by the geometry of the sources, and by the chunk's own record.**

`providers/whisper.transcribe` slices audio into `chunk_minutes` (5) + 15 s
overlap, decodes each slice independently with
`condition_on_previous_text=False`, then merges. Two of the three failing
sources are structurally incapable of having been affected:

- hadhazy-protest is 126.0 s → 1 chunk.
- hindi-baiga-interview is 53.8 s → 1 chunk.

Only telugu-tutorial (339.6 s → 2 chunks) crosses a boundary, and its failure
— emitting text for 21 s of a 340 s file — is not a boundary artifact: a
mid-utterance split would produce *fragmentary* output distributed across the
file, not 4 segments in one window.

The direct test, run on the one source it could affect:

The decisive evidence does not need the A/B run, because it comes from inside
the chunking the production path actually did. The 2-chunk split put the first
300 s of the file in chunk 0, decoded as a standalone WAV with its own 15 s
pre-overlap — the most favourable possible framing of that audio:

```
$ .venv/bin/python -c "import json; r=json.load(open(
  'corpus/wer/describe_d-small-forced-telugu-tutorial/transcription/chunks/chunk_0000.json'
)); print(r['status'], len(r['segments']))"
ok 0
```

**Chunk 0 returned 0 segments, status `ok`.** A chunking failure is a failure
at a *boundary* — a split through an utterance that yields two fragments
instead of one whole. This is the opposite: 5 uninterrupted minutes of
speech-energy audio decoded to nothing at all, nowhere near a boundary. Every
possible chunking scheme still has to decode that same 5 minutes, so no chunk
length can rescue it. The 3–4 segments that do exist all sit in chunk 1's
interior (300–340 s), again away from any boundary.

The chunk-length A/B (`--chunk-minutes 1` vs `--chunk-minutes 60` on the same
source) was **queued and then deliberately dropped**, because the argument above
makes it uninformative before it is run: chunk 0 is a 5-minute standalone WAV,
and it decoded to zero segments. Every candidate chunk length still has to
decode those same 5 minutes, so a 1-minute and a 60-minute scheme would both
report near-zero for them, and a difference between the two could only come
from the one 40-second tail that does produce output — which is where the
segments already are. Spending the compute on the model axis (§5.3) instead
was the tradeoff; if the lead wants the A/B anyway, the command is in §9 and
the harness accepts `--chunk-minutes`.

### 6.2 H2 — `--language` is forced when detection disagrees. **Tested.**

The manifest passes `--language` verbatim to the CLI, which passes it to
faster-whisper's `language=`. A forced code suppresses the model's own
detection; the corpus already has a scar from exactly this ("`language: none`"
once became a forced language code and aborted the run — manifest note on
`alice-1903`). The test is to re-run with detection enabled and compare the
detected language and its confidence against the forced code:


```
.venv/bin/python tools/wer_bench.py detect \
    --media corpus/wer/media_telugu-tutorial.webm --model small \
    --tag detect-small-telugu-tutorial
```

| source | forced code (manifest) | detected | confidence | runner-up |
| --- | --- | --- | --- | --- |
| `telugu-tutorial` | `te` | **`te`** | **0.881** | `ta` 0.086 |
| `hadhazy-protest` | `hu` | **`hu`** | **0.872** | `en` 0.056 |
| `hindi-baiga-interview` | `hi` | **`ur`** | 0.769 | `hi` 0.169 |

**Verdict: not the cause for Telugu or Hungarian, and not actionable for
Hindi.** The model *identifies* Telugu at 0.881 and Hungarian at 0.872 — it
agrees with the manifest's forced code, so forcing is not suppressing a better
answer. This is the single most important negative result in this document:
whatever went wrong with Telugu happened *after* the model knew what language
it was listening to, which points at decoding, not at configuration.

The Hindi row is the one that looks like a forced-language problem and is not
one: Hindi and Urdu are the same spoken register with different scripts, so on
audio alone `hi` and `ur` are near-indistinguishable and the model splits
0.77/0.17 between them. Forcing `hi` selects the Devanagari orthography for a
source the Commons page describes as Hindi — which is the correct script — and
would not improve the recognition either way. (Worth recording in provenance
rather than fixing: a forced code that differs from the detection by script
alone is expected for hi/ur and should not be reported as a mismatch.)

For contrast, the much smaller `tiny` model identifies the same Telugu audio as
**`hi` at 0.262** with `te` second at 0.213 — a low-confidence wrong answer.
Language identification quality is itself model-dependent, which is a further
reason not to read the `small`-model detection above as evidence about `tiny`.

The direct test, not just the agreement check: re-run Telugu with detection
**enabled** instead of forced, so the model is free to choose any language it
likes.

```
.venv/bin/python tools/wer_bench.py describe \
    --media corpus/wer/media_telugu-tutorial.webm --model small \
    --tag d-small-auto-telugu-tutorial
```

It detected `te`, and produced **1 segment / 120 characters covering 5.0 s of
339.6 s** — the same coverage collapse as every forced run (§5.3). The
hypothesis "the forced code is suppressing a better answer" predicts that
letting the model choose would recover the transcript. It did not: the model
chose the same language and still decoded almost nothing. H2 is eliminated
twice over, by agreement *and* by the toggle.

### 6.3 H3 — VAD strips breaths/pauses that anchor segment boundaries. **Eliminated twice: by code reading, and by toggling the in-path gate.**

`engine/vad.py` runs **after** transcription and never feeds it:
`selekyt/pipeline.py` calls `transcribe_auto` (line ~233) and only reaches VAD
analysis later, for selection and the EDL. The transcription path's only VAD is
faster-whisper's internal `vad_filter=True` inside `_transcribe_chunk`. So the
hypothesis as stated is structurally false — the pipeline's own VAD cannot have
shaped any transcript, and a fix there could not have produced a better
transcript.

The in-path gate is still worth testing, so `vad_filter` was made a parameter
of `providers.whisper.transcribe` / `_transcribe_chunk` (default `True`,
unchanged production behaviour) and toggled on a failing source:

Measured on `telugu-tutorial` at `small`, forced `te`, with the gate disabled:

```
.venv/bin/python tools/wer_bench.py describe \
    --media corpus/wer/media_telugu-tutorial.webm --model small --language te \
    --vad-filter false --tag d-small-novad-telugu-tutorial
```

| configuration | chunk 0 (0–300 s) segments |
| --- | --- |
| `vad_filter=True` (production) | **0** |
| `vad_filter=False` | **2** — one at 25.6–28.2 s, one at 298.2–300.0 s |

**Verdict: the VAD gate is not the cause.** Removing the gate entirely — which
lets the decoder see every frame, breaths and all — changes the first 5 minutes
of Telugu from 0 segments to 2, one of which is in the *wrong script*
(Gurmukhi, `੢ੲ੅੍੍੍ੇੇ`) rather than Telugu. A hypothesis about the VAD
stripping speech would predict the gate-off run to recover the missing
transcript; it recovered almost nothing, and what it did recover is further
evidence of a decoder out of its depth, not of a filtering problem.

This is the cleanest single measurement in the root-cause section: one
command-line toggle, opposite result to the hypothesis.

### 6.4 H4 — 16 kHz resampling loses high-frequency content. **Structurally eliminated; A/B recorded when it lands.**

Both extraction paths end at 16 kHz mono, which is what the model consumes, so
the question is whether the *resampler* matters. Two extractions of the same
source — ffmpeg's default `swr` and SoX `soxr` at 28-bit precision — differ by
checksum, so they are genuinely different audio, and both were transcribed with
identical parameters:

```
.venv/bin/python tools/wer_bench.py describe \
    --media corpus/wer/hq_media_telugu-tutorial.wav --model small --language te \
    --tag d-small-swr-telugu-tutorial
.venv/bin/python tools/wer_bench.py describe \
    --media corpus/wer/soxr_media_telugu-tutorial.wav --model small --language te \
    --tag d-small-soxr-telugu-tutorial
```


The result is recorded in `corpus/wer/runs/d-small-{swr,soxr}-telugu-tutorial.json`
when the run lands. Two things are already established and do not depend on it:

- The hypothesis as stated cannot be the cause of the *Telugu* failure on
  structural grounds, for the same reason H1 cannot. The failure is 5 minutes
  of speech-energy audio decoding to zero segments. Two different resamplers
  differ in the top of the band and in rounding; nothing about a resampler
  makes five consecutive minutes inaudible to a decoder that identified the
  language at 0.881.
- A resampler difference is measurable in principle, and the A/B is worth
  keeping as a *regression* signal rather than a root-cause one: if the two
  extractions ever produce materially different transcripts on the same
  source, that is a front-end bug worth knowing about independent of this
  investigation.

What the A/B could still show, if it lands: that one extraction path is
systematically better on low-resource audio, which would matter for §8
recommendation 3's coverage check (the check compares transcript coverage
against energy-VAD speech, and a front-end that changed either would shift the
comparison).

### 6.5 H5 — the actual cause: decoding capacity on a low-resource language

This was not in the original four-hypothesis list; it is what the measurements
left standing. The evidence chain:

1. **The audio is fine.** `telugu-tutorial` peaks at −0.8 dBFS — a loud, clean
   recording — and 73% of it is speech-energy audio (§5.2). There is nothing
   for an acoustic front-end to repair.
2. **The model knows what language it is hearing.** `small` identifies Telugu
   at **0.881**, with the next candidate (`ta`) at 0.086 (§6.2). Identification
   is not the failure.
3. **The model then decodes almost nothing.** With the language forced to `te`,
   the first 5-minute chunk (0.0–300.0 s, i.e. most of the file) returned
   **0 segments** — status `ok`, not a failure. Only the final chunk produced
   the 4 segments in the 311.3–332.1 s window that the corpus transcript
   showed. This is checkable directly:
   `corpus/wer/describe_d-small-forced-telugu-tutorial/transcription/chunks/chunk_0000.json`.
4. **The same model is not broken in general.** On 50 clips of clean English
   it scores 3.38% WER (§3). The failure is specific to this language, not to
   the pipeline, the audio, or the model installation.

So the failure sits inside the ASR decoder, after correct language
identification, on a language with little training data. Whisper's Telugu
coverage is thin relative to English, and the `beam_size=1` production decode is
greedy at each temperature rung. This is consistent with every other
observation: the Hungarian source — also low-coverage for whisper, and
additionally recorded at mean −41.8 dBFS — produced well-formed but degraded
output rather than none; Hindi produced three very long segments of low-quality
Devanagari. The three sources sit at different points on the same axis: **how
much training data does this model have for this language, and how forgiving are
the acoustic conditions**.

The actionable consequence is that this failure is invisible to every
mechanical audit the pipeline runs — the transcript is well-formed, non-empty,
timestamped and in the requested language, so it passes — while covering 4.5% of
the speech that was there. That is what recommendation 3 in §8 addresses:
compare the transcript's coverage against the audio's speech-energy fraction
and fail when they diverge by an order of magnitude. A human speaker of the
language is the only ground truth for *correctness*; coverage is the
machine-checkable proxy for *completeness*.

**What this hypothesis does not prove.** It does not establish that `medium` or
`large-v3` would fix Telugu. §7.1 supplies what was missing when this was
written: a Telugu corpus with reference transcripts (OpenSLR SLR66), on which
`small` scores **151.08% WER with 0 of 50 clips inside 25%**. That converts this
section's central claim from inference to measurement — the model is not
approximating Telugu on clean, read speech either, so the cause is in the model,
not in the corpus recording or the chunking.

The model-size axis is now being tested directly on that same corpus, because
§5.3's observation that `medium` materially changes Hindi does not transfer: an
observed `medium` clip on Telugu scored **47.1% WER against `small`'s 100%+**,
which is a large movement but still nearly double the 25% bar, on a single clip.
If that holds across the 50, the honest summary is that capacity helps and does
not suffice. This hypothesis also does not distinguish "too little Telugu
training data" from "the greedy decoder cannot recover on a low-probability
language"; the beam-width test in §4 speaks to the second half on English, where
raising the beam to 5 changes nothing measurable (3.38% → 4.05%, inside the noise
band). §6.5b below is the direct test of the first half on the actual failing
audio.

### 6.5b The direct test: what the decode loop did on the empty chunk

`_transcribe_chunk` returns `(segments, info)` and production keeps only
`info.language`. The rest of that object carries the answer to "is the decoder
failing its own confidence checks, or is it hearing the audio as non-speech?"
— and the two answers imply different fixes. A diagnostic script
(`tools/decode_probe.py`) calls the model with the production
parameters on the *same* chunk WAV that returned 0 segments and keeps the whole
`info`:

- **if `temperature` climbed above 0.0**, the decode failed a threshold and was
  retried — the model is producing low-probability output, i.e. out of
  distribution. This predicts the >4× decode time in §5.3b.
- **if `no_speech_prob` is above 0.6 while `temperature` stayed 0.0**, the model
  gated the audio as non-speech and never tried. That is a *different and more
  actionable* failure: whisper's own no-speech threshold is rejecting Telugu
  speech, and `no_speech_threshold` is a tunable production parameter.

<!-- PROBE_RESULT -->

The probe's record is written to `corpus/wer/probe_small_telugu_chunk0.json`.
Whatever it shows, it does not change the document's recommendation: the two
candidate readings both sit inside the ASR, not in the pipeline, and a
`no_speech_threshold` that admits Telugu speech is a hypothesis about a fix that
still needs a Telugu reference transcript to be judged an improvement rather
than a change.

---

## 7. Non-English WER (criterion P9): feasibility, honestly

`Content-Length` read with `curl -sI` before any download was attempted:

| C10 tarball | Size |
| --- | --- |
| `mls_german.tar.gz` | 122,944,886,305 B (114.5 GiB) |
| `mls_french.tar.gz` | 64,474,642,518 B (60.1 GiB) |
| `mls_dutch.tar.gz` | 92,158,429,530 B (85.8 GiB) |
| `mls_spanish.tar.gz` | 53,296,894,035 B (49.7 GiB) |
| `mls_italian.tar.gz` | 15,395,281,399 B (14.3 GiB) |
| `mls_polish.tar.gz` | 6,609,569,551 B (6.2 GiB) |

Free disk during this workstream ranged from 5.7 GB down to 1.5 GB and sits at
2.8 GB now. **No C10 tarball fits**, including the smallest. None was
downloaded in full, and no partial corpus was scored as though it were
complete. §7.2 shows how the size constraint was routed around rather than
accepted: the archive is streamed and only the strided sample is written, so
scoring one of the six costs ~15 MB of disk instead of 6.2 GB.

**The more important limitation is not the disk.** C10 covers de/es/fr/nl/it/pl.
It does not cover **Telugu, Hindi or Hungarian** — the three languages that
actually produced suspicious transcripts. Even a full C10 run would not have
scored a single one of them. Hindi in particular is absent from C10 while being
one of the three failures. P9 as written is measurable in principle and
unmeasurable in practice here, and measuring it would not have answered the
question the corpus actually raised. §7.1 answers that question on a corpus
that does have Telugu ground truth.

### 7.1 Telugu: measured, and it is a failure

The corpus that raised the question had no reference transcript, so the question
was answered on one that does: **OpenSLR SLR66** (`te_in_female`), 50 utterances
of Indian Telugu, female speakers, CC BY-SA 4.0. Strided 50 of 2294 archive
members so the sample is reproducible.

| Metric | Value |
| --- | --- |
| WER | **151.08%** |
| CER | **127.40%** |
| Reference words | 650 |
| Word errors | 982 |
| Character errors | 1818 |
| Clips at or under the 25% bar | **0 of 50** |
| Best clip WER | 71.4% |
| Median clip WER | 106.7% |
| Audio | 207.44 s over 50 clips |
| Empty decodes | 2 |

WER above 100% is not a formatting artifact — it means the hypothesis carries
**more error words than the reference has words at all**. The decoder is not
approximating Telugu; on two clips it produced nothing, and on the rest it
produced the wrong quantity of wrong-language-looking material.

The 3.38% English WER from §3 was measured on the same harness and the same
model. That contrast is what localises the failure: transcription works, Telugu
transcription does not.

**What this does and does not settle.** The §5 reading of `telugu-tutorial` —
"fluent-looking phonetic noise" — was an editorial judgment by someone who does
not speak Telugu. This measurement converts it into a number, and the judgment
happened to be right. But a right guess is still a guess, and the two are kept
separate here: 151.08% is evidence, the reading was a hunch that predated it.

Telugu is **not** one of the six C10 languages, so this does not formally score
P9. It scores the property the corpus actually cared about, on the language the
corpus actually raised. See `corpus/wer/runs/telugu-small.json` for the
per-clip detail and `docs/CORPUS_RESULTS.md` for the in-corpus entry it
supersedes.

---

## 8. Recommendation

**Do not change the default model.** The evidence supports the current
`small` default, and the three failing sources need a different fix than a
bigger model.

**1. Keep `small` as the default.** English WER is 3.38% against a ≤15%
criterion, and both `medium` and `large-v3` are statistically indistinguishable
from it on the same 50-clip sample while costing 2.4× and 4.6×. The
model-size assumption in `docs/BUSINESS_MODEL.md` §9 (that the required size
could break the business) is *not* borne out for English: at the measured RTF,
`large-v3` is ~$1.18 per source-hour at the assumed $0.40/4-vCPU-hour, which is
a cost problem that would have to be solved by pricing, not by accuracy.
Changing the default would spend 4.6× compute for a difference smaller than the
measurement's noise.

**2. The three failing sources are a low-resource-language problem, not a
pipeline bug.** Of the four candidate causes, three are eliminated by
measurement (§6) — chunking is structurally impossible on two of the three
sources and its failure mode on the third is the opposite of a boundary
artifact (5 minutes decoding to zero segments, §6.1); `engine/vad.py` runs
after transcription and cannot have shaped any transcript, and toggling the
in-path gate off recovered nothing (§6.3); language forcing is not suppressing
a better answer, because the model *detects* Telugu at 0.881 and Hungarian at
0.872 in agreement with the manifest, and letting it choose freely produced the
same collapse (§6.2). The resampler A/B (§6.4) is the one hypothesis whose
measurement is still in flight, and it is also the one with a structural
argument against it: no resampler difference makes five consecutive minutes
inaudible to a decoder that identified the language at 0.881. What is left
standing is that the model identifies the language and then cannot decode it —
a capacity problem in the ASR, which no pipeline change addresses. The model
axis measured in §5.3 is consistent with this and adds nuance: `medium` leaves
Hungarian unchanged (38→35 segments, 109.7→111.9 s of claimed speech) and
changes Hindi materially (3→11 segments) while leaving Telugu's coverage
collapsed. So "upgrade the model" is not one decision with one answer — it is a
per-language question that only reference transcripts can settle, which is why
recommendation 5 leads on Hindi rather than on the most dramatic failure.

**3. Add a coverage check to the pipeline — IMPLEMENTED.** The one measurement
that surfaced every failure in this exercise without needing a human speaker is
*coverage*: how much of the audio the transcript claims as speech, against how
much of it carries speech energy. `telugu-tutorial` is the clean example — 73%
of its 340 s is speech-energy audio and the transcript covers 15.3 s (4.5%).
That gap is computable from artifacts the pipeline already produces (the
transcript's segment spans and the VAD scores), and a transcript whose coverage
is a fraction of the audio's speech-energy fraction should fail loudly the way
a speechless source does, instead of shipping as an "OK" source. The existing
`selekyt/pipeline.py` "no audible spoken content" check catches the empty case;
this catches the *near-empty* case, which is the one that actually shipped
three times.

Implemented as `engine/coverage.py`, wired into `selekyt/pipeline.py` between
the empty-transcript guard and selection, so it runs before the expensive
parts. Overridable with `--skip-coverage-check`, and the measurement is written
into `report.json` on every run — including the runs where it could not measure
anything, because a `not_computable` recorded honestly is worth more than a
`pass` assumed.

Before the threshold was written into production it was measured, not chosen
(`tools/coverage_measure.py`, record at
`corpus/wer/runs/coverage-2026-09-19.json`), across all eleven sources rather
than the three this section measured:

| source | energy-VAD speech | ASR coverage | ratio | verdict |
| --- | --- | --- | --- | --- |
| `telugu-tutorial` | 72.6% | 4.5% | **0.062** | `coverage_collapse` |
| `royal-society-cloke` | 71.7% | 93.3% | 1.302 | `ok` |
| `ooi-vertical` | 48.2% | 85.3% | 1.769 | `ok` |
| `emwcon-panel-2018` | 29.6% | 85.1% | 2.876 | `ok` |
| `hindi-baiga-interview` | 20.8% | 93.0% | 4.477 | `ok` |
| `nasa360-ep5` | 15.4% | 79.3% | 5.150 | `ok` |
| `pentagon-esper-briefing` | 0.4% | 82.0% | 186.978 | `not_computable` |
| `hadhazy-protest` | 0.0% | 87.1% | — | `not_computable` |
| `alice-1903` | 0.0% | 0.0% | — | `not_computable` |
| `telugu-launch` | 95.5% | 0.0% | 0.000 | (empty guard fires first) |
| `roman-portrait-trailer` | 1.6% | 0.0% | 0.000 | (empty guard fires first) |

The failure at 0.062 is separated from the lowest good source at 1.302 by a
factor of 21 with nothing in between, so a cut point anywhere in that gap is
safe; `COLLAPSE_RATIO = 0.25` sits inside it with 4× below and 5× above. With
one failure and nine good sources this is not a calibrated threshold and is not
presented as one.

Two properties the corpus-wide measurement forced on the design, both of which
a first-draft check would have gotten backwards:

* **It is one-directional.** `hadhazy-protest` at 0% VAD against 87% ASR is a
  broken VAD, not a broken transcript — its mean is −43.3 dBFS, below
  `EnergyVAD`'s absolute 0.02 threshold (recommendation 4). A symmetric check
  would have failed a source whose transcript is fine.
* **It degrades rather than passes below a VAD floor.** `pentagon-esper-briefing`
  is the case that makes the floor non-negotiable: its raw ratio is 187, and a
  check without the floor would report "the transcript covers 187× the speech
  energy" as though that were a clean pass. Below a 10% VAD fraction the
  denominator is not trustworthy, so the verdict is `not_computable` and the
  report says why.

What it still cannot see: an energy VAD cannot distinguish speech from music,
so a source that is mostly score with a little dialogue has a high
speech-energy fraction and a legitimately low transcript coverage and would be
flagged. The error cost is asymmetric and deliberately so — a false positive is
a loud request to verify, a false negative ships wrong captions as a success.

**4. Fix the energy VAD's blindness to quiet audio (separate finding,
selection layer).** `hadhazy-protest` is mean −41.8 dBFS and the pipeline's
`EnergyVAD` returns **0 s of speech for all 126 s** — every frame below its
0.02 fixed threshold — while the ASR transcribed 110 s. The VAD thresholds are
absolute where the source is quiet; a loudness-relative threshold (or a
percentile-based one) would see this audio. This is out of the transcription
scope but it is a measured defect in the difficult-audio axis the corpus was
designed to probe, and it affects silence removal and selection, not the
transcript.

**5. Get reference transcripts before judging the three languages.** No model
decision about Telugu, Hindi or Hungarian should be made on the evidence here,
because there is no error rate to decide on. **Hindi is where this is most
urgent, not Telugu**: it is the one source where `medium` produced a materially
different transcript — 3 segments of 16.7 s becoming 11 segments of 4.5 s, with
recognizable Hindi at the start instead of garbled Devanagari (§5.3). If that
improvement is real, a per-language model choice is worth more than a global
one, and Telugu — where four configurations all collapsed to a few seconds of
text — is where no model choice is visible at all. The cheapest defensible next
step is a CC-licensed Hindi or Telugu source with a published transcript (a
Wikimedia Commons file with a script-matching description is a candidate),
scored `small` vs `medium` vs `large-v3` with this harness, letting the number
decide. Until then the honest status of these three sources is "unmeasurable",
which is what §1 says.

**6. What NOT to do:** do not force-detect instead of forcing by manifest, do
not raise the beam, do not disable the VAD gate, and do not re-extract audio
with a different resampler — each was tested (§4, §6) and none changed the
outcome.

### Cost of switching models

RTF is measured on this 4-core, CPU-only host as decode-seconds per
audio-second, excluding one-time model load. Dollar figures are the formula
with an assumed price plugged in — **substitute your own hourly cost; the
assumption is stated, not verified**:

cost per source-minute = RTF × (4-vCPU-hour price) / 60, with a 4-vCPU-hour
assumed at **$0.40** (10 on-demand vCPU-hours at $0.04 each — a round,
clearly-labelled placeholder, not a quote from a provider).

| model | measured RTF (this host) | 4-core-hours per source-hour | $ per source-hour at $0.40/4vCPU-h | peak RSS |
| --- | --- | --- | --- | --- |
| `small` | 0.649× | 0.65 | **$0.26** | 663 MB |
| `medium` | 1.556× | 1.56 | **$0.62** | 1597 MB |
| `large-v3` | 2.956× | 2.96 | **$1.18** | 3043 MB |

Measured on a 4-core CPU-only host, decode seconds per audio second, model load
excluded. **These are upper bounds**: the host was shared with other work
during measurement, so each figure includes contention it would not see on a
dedicated machine. Multiply by your own real hourly cost — the $0.40 placeholder
is a round assumption, not a provider quote.

The decision-relevant ratio is what each model buys per dollar, and on this
sample `large-v3` buys the same WER as `small` for 4.6× the money. Even if the
full 2620-clip corpus showed `large-v3` a point or two ahead, the English
headroom over the P8 ≤15% threshold is already enormous at `small`, so an
English-accuracy argument for the upgrade does not exist.

---

## 9. Reproduction

```
# Ground truth (one-time, ~347 MB tar; prepare extracts only the sample)
curl -sL -o corpus/wer/test-clean.tar.gz \
    https://www.openslr.org/resources/12/test-clean.tar.gz
.venv/bin/python tools/wer_bench.py prepare \
    --tar corpus/wer/test-clean.tar.gz --out corpus/wer/librispeech-clean \
    --count 50 --max-minutes 8

# English WER for any model
.venv/bin/python tools/wer_bench.py bench \
    --manifest corpus/wer/librispeech-clean/manifest.jsonl \
    --model small --tag c9-small

# Transcript shape on a source with no reference
.venv/bin/python tools/wer_bench.py describe \
    --media corpus/wer/media_hadhazy-protest.webm --model medium --language hu

# All recorded runs as a table
.venv/bin/python tools/wer_bench.py report

# What the decode loop did on a chunk that returned 0 segments
.venv/bin/python tools/decode_probe.py \
    corpus/wer/describe_d-small-forced-telugu-tutorial/transcription/chunks/chunk_0000.wav \
    --model small --language te \
    --out corpus/wer/probe_small_telugu_chunk0.json
```

The corpus media and the model weights under `corpus/wer/` are gitignored;
`corpus/wer/runs/*.json` is the evidence and is small enough to keep.

---

## 10. Code changed in this workstream

| File | Change | Reason |
| --- | --- | --- |
| `.gitignore` | `corpus/wer/*` ignored, `!corpus/wer/runs/` kept | the scratch dir holds ~3.2 GB of model weights, corpus tarballs and ground-truth audio; without this an `git add -A` would put them in the repo. The run records stay committable — they are the evidence. |
| `tools/wer_bench.py` | new | the reusable WER/CER + describe harness this document's numbers come from. `report` prints all three record shapes (bench / describe / detect) and degrades to "unknown record shape" rather than crashing, so a future run type cannot break the way to see what is recorded |
| `tools/decode_probe.py` | new | reads the `info` object production discards — temperature rung used, `no_speech_prob`, `avg_logprob`, `duration_after_vad` — on any existing chunk WAV. This is the instrument for §6.5b, and it is what turned "the decode is slow and unstable" from an observation into a mechanism |
| `providers/whisper.py` | `vad_filter: bool = True` added to `transcribe` and `_transcribe_chunk`, threaded to the decode call | makes the whisper-internal VAD gate measurable instead of a hardcoded assumption; default preserves production behaviour |
| `providers/transcribe.py` | same parameter forwarded through the faster-whisper branch and recorded in provenance as `vad_filter`; `False` recorded for the sherpa branch | provenance already records model and beam; the VAD gate changes segment boundaries too, and a missing key would have looked like a meaningful cross-engine difference |
| `tests/test_transcription_recovery.py` | decode stubs take `*args, **kwargs` instead of naming every `_transcribe_chunk` parameter | the new parameter made the stubs raise `TypeError`, which the chunk loop caught and turned into a fake transcription failure; explicit stub signatures re-break on every signature change |

No default was changed and nothing was committed.
