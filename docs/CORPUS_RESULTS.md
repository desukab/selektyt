# Corpus results — first run, 2026-09-19

**This document describes what the corpus run found on the day it ran.** It is
a historical record, not a current status: two of the seven sources it lists
as delivering would no longer ship under the current code, because the
confidence gate now measures a signal the earlier version filled in with a
constant, and two more still deliver the same *number* of clips but ship
different ones, because the candidate scan that made the committed selection
stopped at a fixed cap and never searched the back half of a long source. For
whether a given run still reproduces — and the named cause when it does not —
read `docs/CORPUS_BENCHMARK_STATUS.md` and run
`tools/corpus_reproducibility.py`, which checks this against the current code
instead of trusting this page.

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

Every OK source was also audited with `tools/nle_check.py`, with the `pycmx`
independent EDL parse executing rather than skipping. **This section has been
wrong twice, and the second wrong version is the more instructive one.**

The first version of this paragraph said the interchange audit reported relink
failures on all seven *because `corpus_run.py` deletes the source media*, and
called that an audit artifact rather than an export defect. That explanation
does not survive checking the code. `corpus_run.py`'s cleanup is

```python
shutil.rmtree(media_dir / media.name, ignore_errors=True)
```

and `fetch()` writes a regular **file**. `shutil.rmtree` on a file raises
`NotADirectoryError`, which `ignore_errors=True` swallowed, so the media was
never deleted by the harness at all. (Verified directly: the file survives.)
The sources that are gone were removed out of band for disk, not by the tool —
which means `--keep-media` was not doing anything, and "re-run with
`--keep-media` to exercise the relink checks" was advice about a switch that
had no effect. Fixed to `unlink()`; `tests/test_corpus_run.py` now asserts the
media really is gone and that the source probe survives it.

The second, worse problem: the relink checks that *did* run were resolving media
paths from the wrong base. Every corpus EDL carries a **repo-root-relative**
`* FROM CLIP:` — `corpus/media/emwcon-panel-2018.webm` — written verbatim from
the path the CLI was handed, while sitting in `project/timeline/01.edl`. An NLE
resolves a relative FROM CLIP against the **timeline's own directory**, so all
seven projects would have imported with every clip offline and the editor
staring at a relink dialog for a file that never moved. `nle_check.py` checked
`Path(p).exists()` from *its own* working directory, which was the repo root —
the same directory the emitter had written the relative path from. Both sides
resolved the same broken string the same lucky way, so the audit passed an
export that a real NLE rejects. The FCPXML side has absolutised since an earlier
run hit exactly this; the EDL side did not.

Both are fixed, and both fixes are pinned by tests that fail on the pre-fix code:
the EDL now carries an absolute path and refuses to export one it cannot resolve
(mirroring the guarantee `render_fcpxml` already had), and the audit resolves a
relative FROM CLIP against the timeline's directory, as an NLE does. The
`NOT RUN` verdict is now derived from the media the artifacts actually name
rather than from a separate path in `pipeline_state.json`, which carried the
same working-directory assumption.

`tools/editorial_audit.py` **failed on four of the seven** — `emwcon-panel-2018`,
`ooi-vertical`, `pentagon-esper-briefing`, `royal-society-cloke`. Two different
defects were mixed in that count, and only one of them was real:

- **Selection bounds landing inside a word** — a real defect, fixed in
  `engine/edit_plan.py` (both bounds now snap back to the enclosing word
  boundary). `end 115.86s splits ' arrays,'/' traveling'` was *not* this
  defect, though this paragraph once attributed it here: the boundary at
  115.86 s is inside neither word.
- **Cuts reported as splitting a sentence** — four of the failures were that,
  and **all four were false positives**, corrected 2026-09-22. The check read
  sentence membership off the candidate's own `text`, which is
  `" ".join(segment.text)` and merges under-punctuated segments, so a clean
  cut between two utterances six seconds apart read as a hole in one
  sentence. Every one of the four removed a pause and lost no word. See
  `docs/TECHNICAL_AUDIT.md`, "CI does not run the tools".

**That fix has since been confirmed on one of the four**, and the confirmation
exposed a second defect the first audit could not see.

`royal-society-cloke` was re-run with `--keep-media` after the word-alignment
fix. The four word-split failures are gone — clips 02/03/04 and the cross-clip
check all pass. But clip 01 still failed, on something else: its last caption
cue read `Somebody`, a word that appears in the SRT but **not in the
transcript**, and its cue ended at 52.485 s on a 52.421 s render. The selection
ends at 235.237 s; the word `Somebody` spans 235.220–235.380 s, so it
*straddles* the bound. `_word_aligned` correctly snaps that end back to the
word's start for the render — the render is right — but `build_blocks` in
`engine/caption_director.py` still built captions from the un-snapped
`candidate.words`, so the caption track described audio the deliverable does not
contain. Fixed: a word whose span crosses `candidate.end` is now excluded.
Regression test in `tests/test_captions.py`, verified to fail on the pre-fix
code.

After both fixes, `royal-society-cloke` is the first corpus source where both
audits are fully green:

```
clip 01     PASS  silence share 6.8%; rendered 52.42s, 33 cues
clip 02     PASS  silence share 10.7%; rendered 27.50s, 21 cues
clip 03     PASS  silence share 6.8%; rendered 21.26s, 14 cues
clip 04     PASS  silence share 7.8%; rendered 26.26s, 14 cues
cross-clip  PASS  no duplicated sentences
ALL CHECKS PASS
```

and `tools/nle_check.py` on the same retained media reports **62/62 PASS**,
including `edl media exists` and `fcpxml media relinkable`.

**That 62 was itself a false pass, and is the reason the count is now worth
something.** It was measured before the path-resolution fix above, so `edl
media exists` passed only because the audit and the emitter shared a working
directory. Re-audited with a relative FROM CLIP resolved the way an NLE resolves
it, the same project reported `edl media exists` **FAIL on all four clips** —
`project/timeline/corpus/media/royal-society-cloke.webm` does not exist. The
EDLs were then re-emitted with `tools/regenerate_timelines.py` (see below), and
the audit passes 62/62 for real: `01 edl media exists … 3 clip path(s)`, no
`NOT RUN`, no working-directory dependence.

`pycmx` parses every EDL independently of the project venv; timelines agree with
both rendered presets to 0.53 frames, and EDL↔FCPXML to 0.00 frames.

**What is still not established by this.** The other three sources were
re-audited, and they no longer fail — but the reason they *used* to is worth
being precise about, because it separates two different fixes:

| Source | After re-emit | Remaining failure |
|---|---|---|
| `emwcon-panel-2018` | — | none |
| `ooi-vertical` | — | none |
| `pentagon-esper-briefing` | — | none |

**These three rows used to report mid-sentence split failures, and those
findings were false.** The audit derived sentence membership by splitting the
candidate's own `text` on terminal punctuation, but that text is
`" ".join(segment.text)` and transcript segments often carry no terminal
punctuation — on `pentagon-esper-briefing` clip 02 two segments six seconds
apart join into one "sentence", so a clean cut between utterances read as a
mid-sentence split. The check now fires only when a sentence *loses a word* to
a boundary, which none of these cuts does: each removed 0.5–1.4 s of gap
between two transcript-adjacent words and lost no word at all. The three
sources are re-audited green as of 2026-09-22, in the suite rather than by hand
(`tests/test_editorial_audit.py::test_the_audit_verdict_on_the_committed_corpus_is_the_recorded_baseline`).
The false-positive diagnosis and the mutation record are in
`docs/TECHNICAL_AUDIT.md` under "CI does not run the tools".

The distinction that paragraph made is unaffected and still stands: the
word-split fix lives in `engine/edit_plan.py::_word_aligned`, which runs
**upstream** of where `selections.json` is written, so a selection committed
before that fix still holds the pre-fix bounds and re-emitting captions from it
reproduces the pre-fix captions byte-for-byte. The straddle fix, by contrast,
is purely caption-side, which is why it *was* recoverable this way —
`pentagon-esper-briefing` clip 04 was rewritten and passes, its last word ` U`
spanning 922.120–922.960 s against an end of 922.862 s.

So: the caption-straddle fix is confirmed on two sources. The word-split fix is
confirmed on one (`royal-society-cloke`, fully re-run). **The other three still
need real re-runs**, and no claim in this document says otherwise.

**The committed captions are themselves a snapshot three days behind the
emitter**, measured 2026-09-22. Regenerating from the persisted
`selections.json` rewrites **79 of the 81** committed caption files: the
corpus's captions were written 2026-09-19, before the one-width-policy caption
work of 2026-09-21/22, so the committed cue breaks and emphasis marks are not
what the current code would write. `tools/regenerate_captions.py` is not wrong
— its byte-identity claim is against *a fresh run*, and that was verified by
running the pipeline and regenerating into the tree it wrote: byte-identical,
`0 of 2 caption track(s) changed`. The committed *pixels* still match the
committed sidecars (OCR of `royal-society-cloke` clip 01 at 3.4 s reads "it's
going to happen more often," as one cue, which the current emitter would have
split after "more" at 3.252 s), so the corpus is self-consistent — just three
days old. Regenerating the sidecars without re-rendering would break that
agreement, which is worse than leaving them, and re-rendering needs media the
repo does not redistribute. The regenerated captions still pass the audit on
every source, so this is a freshness gap, not a defect.

Both figures in that paragraph are pinned by tests, and both guards were
mutation-verified rather than assumed: removing the one-frame gap between
cues moves the count 79 → 80 and fails
`tests/test_corpus_reproducibility.py::test_the_committed_captions_are_what_the_current_emitter_writes`
at `assert 80 <= 79`; wiring `time.time()` into the emitted cue text fails
`tests/test_pipeline_e2e.py::test_regenerating_captions_is_byte_identical_to_a_fresh_run`
at `0 of 2 caption track(s) changed`. A mutation to the cue tail's 0.08 s
floor changes nothing on this corpus and passes both — correctly, because
that floor is dominated by the clamp to the following cue's start on every
clip here, so it is a no-op against this data rather than a test that cannot
see it.

## The finding that matters more than the pass rate

**Two of the seven "OK" sources have transcripts this repository cannot stand
behind**, and the mechanical audits cannot see either one. The count needs
reading carefully, because the two are not the same kind of claim:

- **One is a measurement.** `telugu-tutorial` has a coverage collapse — 4.5% of
  a 339.6 s file — and that is checkable by arithmetic alone.
- **One is an admitted gap, not a defect.** `hadhazy-protest` has grammatical
  Hungarian over 87% of its audio, and nothing here can say whether the words
  are right. It is in this list because an *unverified* transcript cannot be
  shipped as a usable one either, not because it has been shown to be wrong.

(This count was three in an earlier draft, which wrongly included
`hindi-baiga-interview`; that source is one of the four *failures* —
NO_STORIES_PASSED with 0 clips — not one of the seven that delivered.)

- `telugu-tutorial` produced exactly **1 clip** whose text is
  `నినాన్త్డందాన ంపెర్లిస్లిని. పాత్న్న్న్త్త్నవై సస్కకన్సి.` — fluent-looking
  Telugu script that is phonetic noise. It passes the caption-transcript
  equality check *because the check compares captions against the transcript,
  not against reality*. The transcript is wrong, so the captions faithfully
  reproduce a wrong transcript. Geometry, timing, EDL/FCPXML agreement: all
  pass. The clip is garbage.
  **"Phonetic noise" is now a measurement, not a reading.** The claim above was
  an editorial judgment by someone who does not speak Telugu. It has been
  replaced by a score against a reference corpus: OpenSLR SLR66 (Telugu, `te_in`,
  female speakers), 50 utterances / 3.46 min, transcribed with the same model
  and the same decode parameters the pipeline uses — **WER 151.08%, CER
  127.40%**, and **0 of 50** utterances within the 25% bar (best single
  utterance 71.4%). WER above 100% means the output carries *more* error words
  than the reference contains, so the model is not transcribing Telugu at all;
  it is emitting plausible-looking script. The reading judgment happened to be
  right, but a right guess is still a guess, and this is what replaces it
  (`corpus/wer/runs/telugu-small.json`, manifest and provenance committed at
  `corpus/wer/telugu-slr66-female/`). A `medium` run on the same corpus is in
  flight to measure how much of this is model size.
  **This specific failure is now caught.** The source's transcript covers 4.5%
  of a 339.6 s file that is 72.6% speech-energy audio; `engine/coverage.py`
  compares those two numbers and aborts the run before selection or render,
  which is the check `docs/TRANSCRIPTION_EVALUATION.md` §8 recommendation 3
  proposed and is now implemented and measured across all eleven sources
  (`corpus/wer/runs/coverage-2026-09-19.json`). Re-run against the same
  artifacts, the verdict is `coverage_collapse`. The remaining gap is that
  *this* source's media is needed to confirm it end to end — see the next
  actions.
- `hadhazy-protest` transcribes 126 s of outdoor Hungarian interview into 38
  short fragments that are **individually grammatical Hungarian** covering
  109.7 s (87%) of the file. **Whether that transcript is accurate is not
  established by anything in this repo.** An earlier version of this document
  called it "fluent-looking phonetic noise" that "does not reconstruct what
  was said"; that characterization was a reading judgment with no reference
  transcript, no WER and no Hungarian speaker behind it, and it is retracted
  here rather than left standing as though it were a measurement. What *is*
  measurable is that this source does not have telugu's failure shape — there
  is no coverage collapse to catch. See
  `docs/TRANSCRIPTION_EVALUATION.md` §5.4, which reached this position first
  and more carefully than this paragraph did.
- `hindi-baiga-interview` belongs to the *failure* column, not this one: it got
  far enough to fail, with 3 segments of low-quality Devanagari and no complete
  standalone story passing the editorial gate. It is listed here because it is
  part of the same non-English finding, not because it counted among the
  deliverables.

This is the single most important result of the run, and it is the reason
`docs/BLINDED_PROTOCOL.md` refuses to let mechanical metrics stand in for
human judgment. **A machine audit of a bad transcript passes, because the
audit has no ground truth to compare against.** The WER criteria P8/P9 in
`docs/EVALUATION.md` are the ones that would have caught this. They have been
run against English LibriSpeech but not against any of these sources, and
this run is the evidence that the gap is not academic.

The `small` model is inadequate for non-English at the quality the product
implies. That is a model-choice finding, measurable by re-running these
sources at `medium` and `large-v3`, and it is the cheapest follow-up in this
document.

## The four failures, each diagnosed

**`telugu-launch` — NO_SPEECH_DETECTED, and the diagnosis was wrong.** The
source has speech. The `.ogv` container is structurally damaged (non-monotonic
DTS, mis-marked keyframes); a clean remux of the same file transcribes to 11
segments. The pipeline's "no audible spoken content" error therefore conflated
two unrelated causes — genuinely speechless media and an unreadable container —
and told a user their source had no speech when it had plenty. This was the
most misleading failure mode in the product.

**Fixed, and the fix is narrower than the error was.** The diagnosis now
measures the peak of the extracted audio first, because that level is what
separates the two cases, and it reports the number rather than only a verdict:

* **silent extraction** (peak below −50 dBFS, or absent) — no container
  repair can help, so no remux is attempted, and the message says the track is
  digital silence. `alice-1903` and `roman-portrait-trailer` land here, which
  is where they belong.
* **real level with no transcript** — the pipeline stream-copies the source
  into a fresh container with regenerated timestamps and re-transcribes from
  that. The copy is never a re-encode, so the recovery cannot alter the
  content, and the recovered file becomes the media the renders and timelines
  seek — otherwise the fix would repair the transcript and break the cut.

One attempt, one strategy. The evidence for the remux is this single source;
a second invented fallback would be speculation dressed as a retry loop, and a
loop here is also how a genuinely speechless source pays for minutes of
copying that cannot help it.

**Still unconfirmed on the real source.** Its media is gone, so the recovery
path has been verified only against synthetic containers and the unit tests.
Confirming it needs `telugu-launch` re-downloaded — a remux and a re-transcode,
not a full pipeline run.

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

## Four real defects the corpus found, all fixed and pinned

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
3. **Every corpus EDL would have imported with every clip offline.** All
   seven OK sources shipped a repo-root-relative `* FROM CLIP:` inside a
   timeline that lives two directories below the repo root, so an NLE
   resolving it against the timeline's own directory finds nothing. The
   FCPXML alongside each one was correct — it has absolutised since an
   earlier run hit this exact failure — so the two formats disagreed about
   where the media was, and only the format nobody had opened was wrong.
   Fixed at the emitter (`_from_clip_path`, which refuses to ship a path it
   cannot make absolute) and at the audit, which now resolves a relative FROM
   CLIP against the timeline's directory instead of its own working
   directory. Two tests pin it; both fail on the pre-fix code. The shipped
   files are regenerated without a re-run by
   `tools/regenerate_timelines.py`, and the fact that every FCPXML came back
   byte-identical is the evidence that the re-emit reproduced the same
   timeline rather than a new one.
4. **The corpus harness never deleted the media it claimed to.**
   `shutil.rmtree` on a regular file is a silent no-op under
   `ignore_errors=True`, so `keep_media=False` left every source on disk and
   the `NOT RUN` branch above was guarding against a condition that never
   occurred. Fixed to `unlink()`, and `tests/test_corpus_run.py` asserts the
   deletion actually happens and — the reason this mattered — that the
   recorded frame rate outlives the media. That probe write was itself
   silently dropped on every re-run until the same session, because it was
   guarded on a directory `run_one` had just `rmtree`d.

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
- **WER is measured for English and unmeasured for everything else.**
  `docs/TRANSCRIPTION_EVALUATION.md` §3 reports 3.38% on a 50-clip /
  1036-word LibriSpeech sample at the `small` model — so the English baseline
  exists and is respectable. The non-English findings above are still
  impressions from reading transcripts, not scores, and English WER on clean
  read speech says nothing about an outdoor Hindi interview. Until P8/P9 are
  run against a non-English ground-truth corpus, "the `small` model is
  inadequate for non-English" is an observation, not a measurement.
- **The Telugu container bug is fixed; the Telugu *coverage* bug is not.** The
  misdiagnosis is repaired as described above. `telugu-tutorial` is a separate
  and still-open failure: its transcript covers 6% of the energy-VAD speech
  region, which the coverage check now catches and refuses, but the check
  detects the symptom and the model choice is the probable cause.
- **Frame rate coverage is still narrow.** The corpus added 25 fps
  (`royal-society-cloke`, `hindi-baiga-interview`, `emwcon-panel-2018`), 30 fps
  and 29.97 fps. 23.976, 50 and 59.94 remain untested, and no 4K/H.265 source
  was attempted — that axis is still open and is still the largest
  unquantified risk named in `docs/BUSINESS_MODEL.md` §9.
- **Six of the seven OK sources still ship broken EDLs.** The path fix is in
  the emitter, but the artifacts on disk were written by the old emitter, and
  only `royal-society-cloke` has been re-emitted — the one source whose media
  is still on disk. The other six carry relative `* FROM CLIP:` paths
  (`emwcon-panel-2018`, `hadhazy-protest`, `nasa360-ep5`, `ooi-vertical`,
  `pentagon-esper-briefing`, `telugu-tutorial`), and
  `tools/regenerate_timelines.py` refuses to run without the media, which is
  the correct refusal. Confirming them needs the media re-downloaded and the
  tool re-run, not a full pipeline re-run — but it does need the disk.
- **No NLE has opened any of this.** `edl media exists` passing is an audit
  statement about path resolution, not evidence an editor imported the
  sequence. It is stronger than it was — it now resolves the way an NLE does —
  but it is still a model of an import, not an import.

## Exact next actions, in priority order

1. **The Telugu coverage collapse is caught in code, and the gate's own
   blindness is fixed; what remains is a real end-to-end run.** Two things
   changed. First, the pipeline path — abort before render,
   `--skip-coverage-check` override, the measurement in `report.json` — is now
   pinned by `tests/test_coverage.py` at the corpus's own scale (a 340 s
   extraction against a 16 s transcript, the real failure's 339.6 s / 4.5%),
   not only by a check on the retained extraction. Second, the gate itself was
   defective: it hardcoded the energy VAD, the chain's last-choice, level-blind
   backend, so on `hadhazy-protest` (−43.3 dBFS) its own instrument read 0.0%
   speech and the verdict was `not_computable` on a source whose transcript
   covers 87.1% of it. The gate now takes its audio truth from the same chain
   the pipeline prefers; re-measured across all 11 sources, the two quiet
   sources move `not_computable` → `ok` and nothing moves the other way
   (`corpus/wer/runs/coverage-2026-09-27.json`). **Still open:** no real
   end-to-end run has hit the abort on the real source — that needs one
   re-run of `telugu-tutorial` with `--keep-media`, and it remains the single
   highest-confidence-per-byte action open.
2. Fix the no-speech error to distinguish an unreadable container from a
   speechless source — remux to a clean container and retry before reporting
   "no audible spoken content". `telugu-launch` is the case: 95.5%
   speech-energy audio, and the run reported no speech at all. **Implemented
   in code; unconfirmed on the real source**, whose media is gone. Re-downloading
   it is a remux plus a re-transcribe, not a full run.
3. Re-download the six sources whose media is gone and re-emit their EDLs with
   `tools/regenerate_timelines.py`, so the shipped artifacts match the fixed
   emitter. No re-transcode needed; the media is the only thing missing, and
   it is needed one source at a time to stay inside the disk limit.
4. Re-run the three sources whose `selections.json` still holds pre-fix
   word-split bounds (`emwcon-panel-2018`, `ooi-vertical`,
   `pentagon-esper-briefing`), which a re-emit cannot repair.
5. Score WER against `corpus/manifest.json`'s C10 non-English audio-only
   corpora (`docs/EVALUATION.md` §2.2), which are the ground truth the video
   sources lack. The C9 English half is done — 3.38% on `small`, three runs,
   `corpus/wer/runs/` — so this item has narrowed from "WER in general" to
   "WER on non-English".
6. Add a 23.976, a 50 and a 59.94 source, and one 4K/H.265 source.
