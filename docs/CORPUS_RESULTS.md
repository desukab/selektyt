# Corpus results — first run, 2026-09-19

This is the first time SelekyT has been run on more than one source. It was
run by `tools/corpus_run.py` over `corpus/manifest.json`; the raw record is
`corpus/results.jsonl` (one JSON object per source, including every failure),
and every number below is read out of those artifacts, not asserted.

**Result: 7 of 11 sources produced deliverables, 4 did not.** All eight
required axes were attempted.

| Source | Axis | Status | Segments | Clips | Lang |
|---|---|---|---|---|---|
| nasa360-ep5 | longform-multi-speaker | **OK** | 425 | 6 | en |
| emwcon-panel-2018 | longform-multi-speaker | **OK** | 842 | 5 | en |
| pentagon-esper-briefing | news | **OK** | 278 | 5 | en |
| royal-society-cloke | podcast | **OK** | 156 | 4 | en |
| ooi-vertical | documentary | **OK** | 26 | 3 | en |
| hadhazy-protest | difficult-audio | **OK** | 38 | 3 | hu |
| telugu-tutorial | non-roman-script | **OK** | 4 | 1 | te |
| hindi-baiga-interview | interview | NO_STORIES_PASSED | 3 | 0 | hi |
| telugu-launch | non-roman-script | NO_SPEECH_DETECTED | 0 | — | te |
| alice-1903 | difficult-audio | NO_SPEECH_DETECTED | 0 | — | en |
| roman-portrait-trailer | creator | NO_SPEECH_DETECTED | 0 | — | en |

Every OK source was also audited: `tools/nle_check.py` reports **0 FAIL** on
all seven, with the `pycmx` independent EDL parse executing rather than
skipping. `tools/editorial_audit.py` passes on all seven.

## The finding that matters more than the pass rate

**Three of the seven "OK" sources are not actually usable**, and the
mechanical audits cannot see it.

- `telugu-tutorial` produced exactly **1 clip** whose text is
  `నినాన్త్డందాన ంపెర్లిస్లిని. పాత్న్న్న్త్త్నవై సస్కకన్సి.` — fluent-looking
  Telugu script that is phonetic noise. It passes the caption-transcript
  equality check *because the check compares captions against the transcript,
  not against reality*. The transcript is wrong, so the captions faithfully
  reproduce a wrong transcript. Geometry, timing, EDL/FCPXML agreement: all
  pass. The clip is garbage.
- `hadhazy-protest` transcribes Hungarian into short fragments
  (`Azért akarjunk így, hogy nagyon jó.` — "That's why we want it this way,
  that it's very good") that are individually plausible Hungarian but, read
  in sequence, do not reconstruct what was said. 38 segments from 126 s of
  outdoor interview.
- `hindi-baiga-interview` got far enough to *fail*: 3 segments of
  low-quality Devanagari, and no complete standalone story passed the
  editorial gate.

This is the single most important result of the run, and it is the reason
`docs/BLINDED_PROTOCOL.md` refuses to let mechanical metrics stand in for
human judgment. **A machine audit of a bad transcript passes, because the
audit has no ground truth to compare against.** The WER criteria P8/P9 in
`docs/EVALUATION.md` — never measured, on either source — are the ones that
would have caught this, and this run is the evidence that they are not
academic.

The `small` model is inadequate for non-English at the quality the product
implies. That is a model-choice finding, measurable by re-running these three
sources at `medium` and `large-v3`, and it is the cheapest follow-up in this
document.

## The four failures, each diagnosed

**`telugu-launch` — NO_SPEECH_DETECTED, and the diagnosis is wrong.** The
source has speech. The `.ogv` container is structurally damaged (non-monotonic
DTS, mis-marked keyframes); a clean remux of the same file transcribes to 11
segments. The pipeline's "no audible spoken content" error therefore conflates
two unrelated causes — genuinely speechless media and an unreadable container —
and tells a user their source has no speech when it has plenty. This is the
most misleading failure mode in the product and it is still open.

**`roman-portrait-trailer` — NO_SPEECH_DETECTED, and this one is correct.**
The file is a music-and-motion reel with no spoken content. The pipeline
rejects it cleanly. This is the criterion-P12 case working as designed.

**`alice-1903` — NO_SPEECH_DETECTED, also correct.** A 1903 silent film.
Note the framing: this was filed under *difficult-audio* in the manifest, but
a silent film is not difficult audio, it is absent audio. The axis needs a
source with *degraded* speech, and `hadhazy-protest` is the one that supplies
it — `alice-1903` is a negative test that happens to sit in the wrong row.

**`hindi-baiga-interview` — NO_STORIES_PASSED.** Transcription succeeded (3
segments) but no candidate met the standalone-story gate. Whether that is the
gate correctly rejecting unusable material, or the gate being too strict for
a 54-second source with sparse speech, cannot be determined from this run —
it needs a human who speaks Hindi to read the transcript against the audio.

## Two real defects the corpus found, both fixed and pinned

1. **The rendered deliverable shipped with no audio stream.** On the OOI
   source (AV1/Opus WebM), input seeking past ~15 s dropped the audio stream
   on 7 of 8 sampled positions while the container provably held 7601 audio
   packets across all 152 s. ffmpeg reported no error; `engine/qa.py` caught
   it and failed the run. Fixed in `engine/render.py` by probing each part
   for audio and falling back to output seeking. Pinned by a test that fails
   on the pre-fix code.
2. **The audit's source-interval check was comparing the wrong thing.** It
   compared each EDL's in/out against the *selection span*, but the EDL
   describes the *retained intervals* — dead air is removed before export, so
   the first event legitimately starts after the selection start. On OOI
   clip 01 this reported a 26-frame disagreement about an export that was
   correct. Fixed in `tools/nle_check.py` to compare against the edit plan's
   retained span. Verified still-catches-real-errors by perturbing the plan.

## Determinism, re-verified on a fresh download

`nasa360-ep5` was run twice from two independent downloads of the same URL,
weeks of intervening commits apart. The selections are identical:

```
prior run : 414.300-439.160, 327.860-346.280, 654.940-671.380
corpus run: 414.300-439.160, 327.860-346.280, 654.940-671.380
```

Criterion P13 holds on a real re-run, not only in the unit test.

## What this run does not establish

- **No human has judged any of this.** Seven sources passing mechanical audits
  is evidence the machinery is sound across containers, frame rates, aspect
  ratios and languages. It is zero evidence that a human editor would ship
  any of these cuts. `docs/BLINDED_PROTOCOL.md` is the design that would
  settle it, and it has not been run.
- **WER is still unmeasured.** The non-English findings above are impressions
  from reading transcripts, not scores. Until P8/P9 are run against a
  ground-truth corpus, "the `small` model is inadequate" is an observation,
  not a measurement.
- **The Telugu container bug is unfixed.** The misdiagnosis described above
  is recorded, not repaired.
- **Frame rate coverage is still narrow.** The corpus added 25 fps
  (`royal-society-cloke`, `hindi-baiga-interview`, `emwcon-panel-2018`), 30 fps
  and 29.97 fps. 23.976, 50 and 59.94 remain untested, and no 4K/H.265 source
  was attempted — that axis is still open and is still the largest
  unquantified risk named in `docs/BUSINESS_MODEL.md` §9.

## Exact next actions, in priority order

1. Re-run the three non-English sources at `medium` and `large-v3` and
   measure whether the transcript quality that a human can verify improves.
   This is the cheapest test in this document and it addresses the finding
   that the audits cannot see.
2. Fix the no-speech error to distinguish an unreadable container from a
   speechless source — remux to a clean container and retry before reporting
   "no audible spoken content".
3. Score WER against `corpus/manifest.json`'s C9/C10 audio-only corpora
   (`docs/EVALUATION.md` §2.2), which are the ground truth the video sources
   lack.
4. Add a 23.976, a 50 and a 59.94 source, and one 4K/H.265 source.
